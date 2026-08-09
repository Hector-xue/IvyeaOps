import { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from "react";
import { useSearchParams } from "react-router-dom";
import {
  getSkill,
  readFile,
  writeLinkedFile,
  updateSkill,
  deleteSkill,
  SkillDetail,
} from "../../api/skill";
import { useAutoSave, SaveStatus } from "../../hooks/useAutoSave";
import FileTree from "./FileTree";
import ConfirmDialog from "./ConfirmDialog";
import { errText } from "../../lib/errText";

// Code-split the editor: CodeMirror6 + language packs weigh ~240KB gzip,
// we only pay that on first skill open, not on stats/list pages.
const CodeEditor = lazy(() => import("./CodeEditor"));
const SnapshotPanel = lazy(() => import("./snapshots/SnapshotPanel"));

export type SkillEditorProps = {
  name: string;
  onClose: () => void;
  /** Called after the skill has been moved to trash, so the list can refresh. */
  onDeleted?: () => void;
};

function fmtDate(iso: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function statusLabel(s: SaveStatus, err: string | null, lastSavedAt: Date | null): string {
  switch (s) {
    case "idle":
      return lastSavedAt ? `已保存 ${lastSavedAt.toLocaleTimeString("zh-CN")}` : "就绪";
    case "dirty":
      return "未保存改动…";
    case "saving":
      return "保存中…";
    case "saved":
      return lastSavedAt ? `已保存 ${lastSavedAt.toLocaleTimeString("zh-CN")}` : "已保存";
    case "error":
      return `保存失败：${err ?? "未知错误"}`;
  }
}

function statusTone(s: SaveStatus): string {
  return s === "error" ? "err" : s === "saved" || s === "idle" ? "ok" : "warn";
}

/**
 * Rebuild the full SKILL.md text from the structured detail + a fresh body.
 * We preserve the frontmatter ordering by round-tripping it; if no frontmatter
 * was present, we skip the fence entirely.
 */
function assembleSkillMd(detail: SkillDetail, body: string): string {
  const fm = detail.frontmatter ?? {};
  const keys = Object.keys(fm);
  if (keys.length === 0) return body;
  const lines: string[] = ["---"];
  for (const k of keys) {
    const v = (fm as Record<string, unknown>)[k];
    if (typeof v === "string") {
      // Quote if it contains : or starts with a special char.
      const needsQuote = /[:#&*!|>'"%@`]/.test(v) || /^\s|\s$/.test(v);
      lines.push(`${k}: ${needsQuote ? JSON.stringify(v) : v}`);
    } else {
      lines.push(`${k}: ${JSON.stringify(v)}`);
    }
  }
  lines.push("---", "");
  return lines.join("\n") + body;
}

export default function SkillEditor({ name, onClose, onDeleted }: SkillEditorProps) {
  const [params, setParams] = useSearchParams();
  const activePath = params.get("file") ?? "SKILL.md";
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [mode, setMode] = useState<"edit" | "snapshots">("edit");

  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(true);
  const [detailErr, setDetailErr] = useState<string | null>(null);

  const [fileContent, setFileContent] = useState<string>("");
  const [baseline, setBaseline] = useState<string>("");
  const [loadingFile, setLoadingFile] = useState(false);
  const [fileErr, setFileErr] = useState<string | null>(null);
  const [isBinary, setIsBinary] = useState(false);

  const readOnly = detail ? !detail.editable : true;

  // Track dirty files across switches so the file tree can show blue dots.
  const [dirtySet] = useState<Set<string>>(() => new Set());
  const [, forceRender] = useState({});

  const markDirty = useCallback((path: string, dirty: boolean) => {
    const had = dirtySet.has(path);
    if (dirty && !had) { dirtySet.add(path); forceRender({}); }
    else if (!dirty && had) { dirtySet.delete(path); forceRender({}); }
  }, [dirtySet]);

  // ------- Load skill detail (once per name) -------
  useEffect(() => {
    let alive = true;
    setLoadingDetail(true);
    setDetailErr(null);
    setDetail(null);
    getSkill(name)
      .then((d) => { if (alive) setDetail(d); })
      .catch((e) => { if (alive) setDetailErr(errText(e, "加载失败")); })
      .finally(() => { if (alive) setLoadingDetail(false); });
    return () => { alive = false; };
  }, [name]);

  // ------- Load the current file whenever name/file changes -------
  useEffect(() => {
    if (!detail) return;
    let alive = true;
    setLoadingFile(true);
    setFileErr(null);

    if (activePath === "SKILL.md") {
      // The backend serves SKILL.md content pre-parsed in the detail payload.
      const text = assembleSkillMd(detail, detail.content_body);
      setFileContent(text);
      setBaseline(text);
      setIsBinary(false);
      setLoadingFile(false);
      return;
    }
    readFile(name, activePath)
      .then((f) => {
        if (!alive) return;
        setFileContent(f.content);
        setBaseline(f.content);
        setIsBinary(f.is_binary);
      })
      .catch((e) => {
        if (!alive) return;
        setFileErr(errText(e, "文件加载失败"));
      })
      .finally(() => { if (alive) setLoadingFile(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, activePath, detail]);

  // ------- Auto-save -------
  const onSave = useCallback(async (value: string) => {
    if (!detail) throw new Error("detail not loaded");
    if (activePath === "SKILL.md") {
      // We only edit the body text in the editor; keep frontmatter as-is.
      // Split off leading fence if the user preserved/changed it, else treat
      // the whole buffer as body.
      const { frontmatter, body } = splitFrontmatter(value, detail.frontmatter);
      await updateSkill(name, frontmatter, body);
    } else {
      await writeLinkedFile(name, activePath, value);
    }
  }, [detail, name, activePath]);

  const autoSave = useAutoSave({
    value: fileContent,
    baseline,
    onSave,
    debounceMs: 600,
    enabled: !readOnly && !isBinary && !loadingFile && !fileErr,
  });

  // Mirror dirtiness into the cross-file set for visual indicators.
  useEffect(() => {
    markDirty(activePath, fileContent !== baseline);
  }, [activePath, fileContent, baseline, markDirty]);

  // Reset status when switching files; flush the previous one first.
  const prevPathRef = useRef(activePath);
  useEffect(() => {
    if (prevPathRef.current !== activePath) {
      autoSave.flush().finally(() => {
        autoSave.reset();
        prevPathRef.current = activePath;
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePath]);

  // Warn on tab close if dirty.
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirtySet.size > 0) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirtySet]);

  const setFilePath = (p: string) => {
    const next = new URLSearchParams(params);
    if (p === "SKILL.md") next.delete("file");
    else next.set("file", p);
    setParams(next, { replace: true });
  };

  const linkedFiles = detail?.linked_files ?? [];

  // -------------------- UI --------------------

  if (loadingDetail) {
    return <aside className="sks-detail"><div className="sks-loading">加载中…</div></aside>;
  }
  if (detailErr || !detail) {
    return (
      <aside className="sks-detail">
        <header className="sks-detail-head">
          <span className="n">{name}</span>
          <button className="tbtn" onClick={onClose}>✕</button>
        </header>
        <div className="sks-error">⚠ {detailErr ?? "未知错误"}</div>
      </aside>
    );
  }

  return (
    <aside className="sks-detail sks-editor">
      <header className="sks-detail-head">
        <span className="n">
          {name}
          {readOnly && <span className="sks-badge" style={{ background: "rgba(239,68,68,.12)", color: "var(--red)" }}>只读</span>}
          {detail.pinned && <span className="sks-badge">PINNED</span>}
        </span>
        <div className="sks-head-right">
          {mode === "edit" && (
            <span className={"sks-save-status " + statusTone(autoSave.status)}>
              {statusLabel(autoSave.status, autoSave.error, autoSave.lastSavedAt)}
            </span>
          )}
          {mode === "edit" ? (
            <button
              className="tbtn"
              onClick={async () => { await autoSave.flush(); setMode("snapshots"); }}
              title="查看和恢复历史快照"
            >
              快照
            </button>
          ) : (
            <button className="tbtn" onClick={() => setMode("edit")} title="返回编辑">编辑</button>
          )}
          {mode === "edit" && (
            <>
              <button className="tbtn" onClick={() => autoSave.flush()} disabled={readOnly} title="Ctrl+S">保存</button>
              <button
                className="tbtn danger"
                onClick={() => setConfirmingDelete(true)}
                disabled={readOnly}
                title="移到回收站"
              >
                删除
              </button>
            </>
          )}
          <button className="tbtn" onClick={onClose} title="关闭">✕</button>
        </div>
      </header>

      {confirmingDelete && (
        <ConfirmDialog
          title="删除 Skill"
          message={
            <>
              将 <b>{name}</b> 移到回收站，7 天内可恢复。
            </>
          }
          confirmLabel="删除"
          danger
          onConfirm={async () => {
            await deleteSkill(name);
            onDeleted?.();
            onClose();
          }}
          onClose={() => setConfirmingDelete(false)}
        />
      )}

      {readOnly && (
        <div className="sks-notice">
          这是 {detail.source || "系统"} skill，只读不可编辑。
        </div>
      )}

      {mode === "snapshots" ? (
        <Suspense fallback={<div className="sks-loading">快照加载中…</div>}>
          <SnapshotPanel
            name={name}
            onBack={() => setMode("edit")}
            onRestored={() => {
              // Force a reload of the current skill detail + file by bumping a key.
              // Simpler path: just reload the page from the server-state we already
              // have — re-fetch detail by nuking it and re-requesting.
              setDetail(null);
              setLoadingDetail(true);
              setDetailErr(null);
              getSkill(name)
                .then((d) => setDetail(d))
                .catch((e) => setDetailErr(errText(e, "加载失败")))
                .finally(() => setLoadingDetail(false));
              // Reset dirty tracking — buffers are now stale vs disk.
              dirtySet.clear();
              forceRender({});
              setMode("edit");
            }}
          />
        </Suspense>
      ) : (
        <div className="sks-editor-body">
          <div className="sks-editor-tree">
            <FileTree
              files={linkedFiles}
              selected={activePath}
              onSelect={setFilePath}
              dirty={dirtySet}
            />
          </div>
          <div className="sks-editor-pane">
            {loadingFile && <div className="sks-loading">文件加载中…</div>}
            {fileErr && <div className="sks-error">⚠ {fileErr}</div>}
            {!loadingFile && !fileErr && isBinary && (
              <div className="sks-empty">二进制文件({activePath})无法在线编辑。</div>
            )}
            {!loadingFile && !fileErr && !isBinary && (
              <Suspense fallback={<div className="sks-loading">编辑器加载中…</div>}>
                <CodeEditor
                  value={fileContent}
                  onChange={setFileContent}
                  path={activePath}
                  readonly={readOnly}
                  onSaveShortcut={() => autoSave.flush()}
                  onBlur={() => autoSave.flush()}
                />
              </Suspense>
            )}
          </div>
        </div>
      )}

      <footer className="sks-editor-foot">
        <span>category: <b>{detail.category || "(顶层)"}</b></span>
        <span>files: <b>{detail.file_count}</b></span>
        <span>updated: <b>{fmtDate(detail.updated_at)}</b></span>
      </footer>
    </aside>
  );
}

/**
 * Minimal-yet-safe frontmatter splitter. If the buffer still starts with a
 * --- ... --- fence we parse it; otherwise we fall back to the original
 * frontmatter from the server. This preserves keys the editor didn't touch.
 */
function splitFrontmatter(
  text: string,
  fallback: Record<string, unknown>,
): { frontmatter: Record<string, unknown>; body: string } {
  if (!text.startsWith("---")) {
    return { frontmatter: fallback, body: text };
  }
  const end = text.indexOf("\n---", 3);
  if (end === -1) return { frontmatter: fallback, body: text };
  const header = text.slice(3, end).trim();
  const bodyStart = text.indexOf("\n", end + 4);
  const body = bodyStart === -1 ? "" : text.slice(bodyStart + 1);
  const fm: Record<string, unknown> = {};
  for (const line of header.split("\n")) {
    const m = /^([A-Za-z0-9_-]+)\s*:\s*(.*)$/.exec(line);
    if (!m) continue;
    const [, k, raw] = m;
    let v: unknown = raw.trim();
    // Booleans / numbers / JSON-ish values.
    if (v === "true") v = true;
    else if (v === "false") v = false;
    else if (typeof v === "string" && v.startsWith("{") || (typeof v === "string" && v.startsWith("["))) {
      try { v = JSON.parse(v as string); } catch { /* keep string */ }
    } else if (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v)) {
      v = Number(v);
    } else if (typeof v === "string" && ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'")))) {
      v = v.slice(1, -1);
    }
    fm[k] = v;
  }
  return { frontmatter: fm, body };
}
