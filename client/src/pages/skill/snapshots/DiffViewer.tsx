import { useEffect, useState } from "react";
import Modal from "../Modal";
import { diffSnapshot, SnapshotDiff, SnapshotDiffFile } from "../../../api/skill";
import { errText } from "../../../lib/errText";

export type DiffViewerProps = {
  name: string;
  snapshotId: string;
  onClose: () => void;
};

/**
 * Colorize a single unified-diff line. We use three cheap prefix checks
 * (`+`, `-`, `@@`) — everything else is rendered plain.
 */
function diffLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "dv-meta";
  if (line.startsWith("@@")) return "dv-hunk";
  if (line.startsWith("+")) return "dv-add";
  if (line.startsWith("-")) return "dv-del";
  return "";
}

const MAX_LINES_PER_FILE = 5000;

export default function DiffViewer({ name, snapshotId, onClose }: DiffViewerProps) {
  const [diff, setDiff] = useState<SnapshotDiff | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    setLoading(true);
    diffSnapshot(name, snapshotId)
      .then((d) => {
        if (!alive) return;
        setDiff(d);
        // Backend only returns changed files, so auto-expand the first one.
        if (d.files.length > 0) setExpanded(new Set([d.files[0].path]));
      })
      .catch((e: any) => {
        if (alive) setErr(errText(e, "加载 diff 失败"));
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [name, snapshotId]);

  const toggle = (path: string) => {
    setExpanded((cur) => {
      const next = new Set(cur);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  return (
    <Modal
      title={`快照对比 · ${snapshotId.slice(0, 8)}…`}
      onClose={onClose}
      width={820}
      footer={<button className="tbtn" onClick={onClose}>关闭</button>}
    >
      {loading && <div className="sks-loading">加载中…</div>}
      {err && <div className="sks-error">⚠ {err}</div>}
      {diff && (
        <div className="dv-files">
          {diff.files.length === 0 && <div className="sks-empty">没有文件变更</div>}
          {diff.files.map((f) => (
            <FileSection
              key={f.path}
              file={f}
              expanded={expanded.has(f.path)}
              onToggle={() => toggle(f.path)}
            />
          ))}
        </div>
      )}
    </Modal>
  );
}

function FileSection({
  file,
  expanded,
  onToggle,
}: {
  file: SnapshotDiffFile;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { path, status, diff } = file;
  // "deleted" means: was in the snapshot, not in live → rendered as "removed".
  const badgeClass =
    status === "added" ? "added"
    : status === "deleted" ? "removed"
    : "modified";
  const badgeLabel = status === "deleted" ? "removed" : status;
  return (
    <div className="dv-file">
      <div className="dv-file-head" onClick={onToggle}>
        <span className="arrow">{expanded ? "▾" : "▸"}</span>
        <span className={"dv-change " + badgeClass}>{badgeLabel}</span>
        <span className="path">{path}</span>
      </div>
      {expanded && (
        <div className="dv-file-body">
          {!diff || diff.trim() === "" ? (
            <div className="sks-empty" style={{ fontSize: 11 }}>(无 diff 文本)</div>
          ) : (
            <DiffBody text={diff} />
          )}
        </div>
      )}
    </div>
  );
}

function DiffBody({ text }: { text: string }) {
  const lines = text.split("\n");
  const truncated = lines.length > MAX_LINES_PER_FILE;
  const view = truncated ? lines.slice(0, MAX_LINES_PER_FILE) : lines;
  return (
    <>
      <pre className="dv-pre">
        {view.map((line, i) => (
          <span key={i} className={"dv-line " + diffLineClass(line)}>
            {line || " "}{"\n"}
          </span>
        ))}
      </pre>
      {truncated && (
        <div className="sks-empty" style={{ fontSize: 10 }}>
          过长已截断，仅显示前 {MAX_LINES_PER_FILE} 行
        </div>
      )}
    </>
  );
}
