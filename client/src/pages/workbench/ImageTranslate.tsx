import { useEffect, useRef, useState } from "react";
import {
  listLangs, listWorkspace, uploadToWorkspace, deleteWorkspaceImage, translateImage,
  listFolders, createFolder, renameFolder, deleteFolder, moveImage,
} from "../../api/imageTranslate";
import { lockBodyScroll } from "../../lib/scrollLock";

interface Lang { code: string; lang: string; locale: string; label: string; }
interface Img {
  id: string; url: string; source: string; lang: string;
  parent_id: string; project_id: string; original_name: string; folder_id: string; created_at: number;
}
interface Folder { id: string; name: string; count: number; }
interface TResult { code: string; id?: string; url?: string; error?: string; }
interface BatchGroup { sourceId: string; sourceUrl: string; results: TResult[]; }

const ALL = "ALL", UNFILED = "UNFILED";
const SOURCE_BADGE: Record<string, { text: string; bg: string }> = {
  upload: { text: "上传", bg: "#3b82f6" },
  listing: { text: "Listing", bg: "#16a34a" },
  translation: { text: "翻译", bg: "#a855f7" },
};

export default function ImageTranslate() {
  const [langs, setLangs] = useState<Lang[]>([]);
  const [workspace, setWorkspace] = useState<Img[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [unfiledCount, setUnfiledCount] = useState(0);
  const [activeFolder, setActiveFolder] = useState<string>(ALL);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [translating, setTranslating] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [batches, setBatches] = useState<BatchGroup[]>([]);
  const [msg, setMsg] = useState<string>("");
  const [preview, setPreview] = useState<string>("");
  const [dragId, setDragId] = useState<string>("");
  const [dropKey, setDropKey] = useState<string>("");
  const fileRef = useRef<HTMLInputElement>(null);

  const langOf = (code: string) => langs.find((l) => l.code === code);

  async function refreshWorkspace() { try { setWorkspace(await listWorkspace()); } catch { /* ignore */ } }
  async function refreshFolders() {
    try { const d = await listFolders(); setFolders(d.folders); setUnfiledCount(d.unfiled_count); } catch { /* ignore */ }
  }

  useEffect(() => {
    (async () => {
      try { setLangs(await listLangs()); } catch { /* ignore */ }
      await Promise.all([refreshWorkspace(), refreshFolders()]);
    })();
  }, []);

  // Lightbox: lock background scroll + Esc to close.
  useEffect(() => {
    if (!preview) return;
    const release = lockBodyScroll();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setPreview(""); };
    window.addEventListener("keydown", onKey);
    return () => { release(); window.removeEventListener("keydown", onKey); };
  }, [preview]);

  const visible = workspace.filter((w) =>
    activeFolder === ALL ? true : activeFolder === UNFILED ? !w.folder_id : w.folder_id === activeFolder);

  async function onUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true); setMsg("");
    try {
      for (const f of Array.from(files)) {
        const item = await uploadToWorkspace(f);
        // If filtering a real folder, move the new upload into it so it shows up there.
        if (activeFolder !== ALL && activeFolder !== UNFILED) {
          try { await moveImage(item.id, activeFolder); } catch { /* ignore */ }
        }
      }
      await Promise.all([refreshWorkspace(), refreshFolders()]);
    } catch (e: any) {
      setMsg("上传失败：" + (e?.response?.data?.detail || e?.message || "未知错误"));
    }
    setUploading(false);
    if (fileRef.current) fileRef.current.value = "";
  }

  async function onDelete(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirm("删除这张图片？")) return;
    try {
      await deleteWorkspaceImage(id);
      setSelectedIds((p) => { const n = new Set(p); n.delete(id); return n; });
      await Promise.all([refreshWorkspace(), refreshFolders()]);
    } catch { /* ignore */ }
  }

  function toggleSelect(id: string) {
    setSelectedIds((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }
  function toggleLang(code: string) {
    setPicked((p) => { const n = new Set(p); n.has(code) ? n.delete(code) : n.add(code); return n; });
  }

  // ── Folders ──
  async function onNewFolder() {
    const name = prompt("新建文件夹名称：")?.trim();
    if (!name) return;
    try { const f = await createFolder(name); await refreshFolders(); setActiveFolder(f.id); } catch (e: any) { alert(e?.response?.data?.detail || "创建失败"); }
  }
  async function onRenameFolder(f: Folder, e: React.MouseEvent) {
    e.stopPropagation();
    const name = prompt("重命名文件夹：", f.name)?.trim();
    if (!name || name === f.name) return;
    try { await renameFolder(f.id, name); await refreshFolders(); } catch { /* ignore */ }
  }
  async function onDeleteFolder(f: Folder, e: React.MouseEvent) {
    e.stopPropagation();
    if (!confirm(`删除文件夹「${f.name}」？里面的图片会移到「未分类」，不会被删除。`)) return;
    try {
      await deleteFolder(f.id);
      if (activeFolder === f.id) setActiveFolder(ALL);
      await Promise.all([refreshFolders(), refreshWorkspace()]);
    } catch { /* ignore */ }
  }

  // ── Drag image → folder ──
  async function onDropToFolder(folderId: string) {
    setDropKey("");
    const ids = dragId && selectedIds.has(dragId) ? Array.from(selectedIds) : (dragId ? [dragId] : []);
    setDragId("");
    if (ids.length === 0) return;
    try {
      for (const id of ids) await moveImage(id, folderId);
      await Promise.all([refreshWorkspace(), refreshFolders()]);
    } catch { /* ignore */ }
  }

  // ── Batch translate (one request per source image, languages parallel within) ──
  async function onTranslate() {
    const sources = Array.from(selectedIds);
    if (sources.length === 0 || picked.size === 0) return;
    setTranslating(true); setBatches([]); setMsg(""); setProgress({ done: 0, total: sources.length });
    const langArr = Array.from(picked);
    const acc: BatchGroup[] = [];
    for (let i = 0; i < sources.length; i++) {
      const sid = sources[i];
      const src = workspace.find((w) => w.id === sid);
      try {
        const res = await translateImage(sid, langArr);
        acc.push({ sourceId: sid, sourceUrl: src?.url || "", results: res });
      } catch (e: any) {
        acc.push({ sourceId: sid, sourceUrl: src?.url || "", results: langArr.map((c) => ({ code: c, error: e?.response?.data?.detail || "失败" })) });
      }
      setBatches([...acc]);
      setProgress({ done: i + 1, total: sources.length });
    }
    await Promise.all([refreshWorkspace(), refreshFolders()]);
    const ok = acc.reduce((s, g) => s + g.results.filter((r) => r.url).length, 0);
    const tot = sources.length * langArr.length;
    setMsg(`完成：${ok}/${tot} 张生成成功（${sources.length} 源图 × ${langArr.length} 语言）`);
    setTranslating(false); setProgress(null);
  }

  const dropProps = (key: string, folderId: string) => ({
    onDragOver: (e: React.DragEvent) => { e.preventDefault(); setDropKey(key); },
    onDragLeave: () => setDropKey((k) => (k === key ? "" : k)),
    onDrop: () => onDropToFolder(folderId),
  });

  const folderChip = (key: string, label: string, count: number, opts?: { f?: Folder; droppable?: boolean }) => {
    const on = activeFolder === key, hot = dropKey === key;
    return (
      <div key={key} onClick={() => setActiveFolder(key)}
        {...(opts?.droppable ? dropProps(key, opts.f ? opts.f.id : "") : {})}
        style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, padding: "3px 8px", borderRadius: 13, cursor: "pointer",
          border: hot ? "1px dashed var(--acc)" : on ? "1px solid var(--acc)" : "1px solid var(--b)",
          background: hot ? "var(--acc)" : on ? "var(--acc)" : "var(--bg1)", color: (hot || on) ? "#fff" : "var(--t)", whiteSpace: "nowrap" }}>
        <span>{label}</span><span style={{ opacity: .7 }}>{count}</span>
        {opts?.f && (
          <>
            <span onClick={(e) => onRenameFolder(opts.f!, e)} title="重命名" style={{ marginLeft: 2, opacity: .75 }}>✎</span>
            <span onClick={(e) => onDeleteFolder(opts.f!, e)} title="删除" style={{ opacity: .75 }}>×</span>
          </>
        )}
      </div>
    );
  };

  return (
    <div style={{ padding: 16, height: "100%", display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <div style={{ fontSize: 18, fontWeight: 700, color: "var(--t)", fontFamily: "var(--font)" }}>一键图片翻译</div>
        <div style={{ fontSize: 12, color: "var(--t3)", marginTop: 4 }}>
          多站点卖家：一套图 → 多语言 → 多站点。上传或从图片工作区选图（可多选批量），选目标站点语言，一键翻译图上文字（产品/版式/配色不变）。
        </div>
      </div>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "minmax(300px, 1fr) minmax(320px, 1.05fr)", gap: 12, minHeight: 0 }}>
        {/* ── 图片工作区 ── */}
        <div style={{ display: "flex", flexDirection: "column", border: "1px solid var(--b)", borderRadius: 8, overflow: "hidden", minHeight: 0 }}>
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--b)", display: "flex", alignItems: "center", justifyContent: "space-between", background: "var(--bg2)" }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--t)" }}>图片工作区</span>
            <button className="tbtn" style={{ fontSize: 11 }} disabled={uploading} onClick={() => fileRef.current?.click()}>
              {uploading ? "上传中…" : "＋ 上传图片"}
            </button>
            <input ref={fileRef} type="file" accept="image/*" multiple style={{ display: "none" }} onChange={(e) => onUpload(e.target.files)} />
          </div>

          {/* Folder bar */}
          <div style={{ padding: "8px 10px", borderBottom: "1px solid var(--b)", display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
            {folderChip(ALL, "全部", workspace.length)}
            {folderChip(UNFILED, "未分类", unfiledCount, { droppable: true })}
            {folders.map((f) => folderChip(f.id, f.name, f.count, { f, droppable: true }))}
            <button onClick={onNewFolder} title="新建文件夹"
              style={{ fontSize: 11, padding: "3px 8px", borderRadius: 13, border: "1px dashed var(--b)", background: "transparent", color: "var(--t3)", cursor: "pointer" }}>＋文件夹</button>
            <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--t3)" }}>拖图到文件夹可移动</span>
          </div>

          <div style={{ flex: 1, overflowY: "auto", padding: 10 }}>
            {visible.length === 0 ? (
              <div style={{ color: "var(--t3)", fontSize: 12, textAlign: "center", padding: "32px 8px", lineHeight: 1.7 }}>
                这里还没有图片。<br />上传图片，或在 Listing 工作台生成图片（会自动入库）。
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(96px, 1fr))", gap: 8 }}>
                {visible.map((img) => {
                  const badge = SOURCE_BADGE[img.source] || { text: img.source, bg: "#6b7280" };
                  const isSel = selectedIds.has(img.id);
                  return (
                    <div key={img.id} draggable onDragStart={() => setDragId(img.id)} onClick={() => toggleSelect(img.id)}
                      title={img.source === "translation" ? `翻译结果（${langOf(img.lang)?.label || img.lang}）` : (img.original_name || "")}
                      style={{ position: "relative", cursor: "pointer", borderRadius: 6, overflow: "hidden",
                        border: isSel ? "2px solid var(--acc)" : "1px solid var(--b)", aspectRatio: "1", background: "var(--bg2)" }}>
                      <img src={img.url} alt="" style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
                      <span style={{ position: "absolute", top: 3, left: 3, fontSize: 9, color: "#fff", background: badge.bg, padding: "1px 5px", borderRadius: 4 }}>
                        {badge.text}{img.source === "translation" && img.lang ? ` ${img.lang}` : ""}
                      </span>
                      {isSel && <span style={{ position: "absolute", bottom: 3, left: 3, fontSize: 11, color: "#fff", background: "var(--acc)", borderRadius: 10, width: 16, height: 16, lineHeight: "16px", textAlign: "center" }}>✓</span>}
                      <button onClick={(e) => { e.stopPropagation(); setPreview(img.url); }} title="预览"
                        style={{ position: "absolute", bottom: 2, right: 2, width: 18, height: 18, lineHeight: "16px", textAlign: "center", fontSize: 11, color: "#fff", background: "rgba(0,0,0,.55)", border: "none", borderRadius: 4, cursor: "pointer" }}>⤢</button>
                      <button onClick={(e) => onDelete(img.id, e)} title="删除"
                        style={{ position: "absolute", top: 2, right: 2, width: 18, height: 18, lineHeight: "16px", textAlign: "center", fontSize: 12, color: "#fff", background: "rgba(0,0,0,.55)", border: "none", borderRadius: 4, cursor: "pointer" }}>×</button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* ── 翻译面板 ── */}
        <div style={{ display: "flex", flexDirection: "column", border: "1px solid var(--b)", borderRadius: 8, overflow: "hidden", minHeight: 0 }}>
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--b)", fontSize: 13, fontWeight: 600, color: "var(--t)", background: "var(--bg2)", display: "flex", justifyContent: "space-between" }}>
            <span>翻译</span>
            <span style={{ fontSize: 11, color: "var(--t3)", fontWeight: 400 }}>已选 {selectedIds.size} 张源图</span>
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
            {/* selected sources strip */}
            <div>
              <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 6 }}>源图（左侧点选可多选批量）</div>
              {selectedIds.size === 0 ? (
                <div style={{ fontSize: 12, color: "var(--t3)", padding: "12px 0" }}>← 从左侧工作区点选一张或多张图</div>
              ) : (
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {Array.from(selectedIds).map((id) => {
                    const w = workspace.find((x) => x.id === id); if (!w) return null;
                    return <img key={id} src={w.url} alt="" onClick={() => setPreview(w.url)} style={{ width: 52, height: 52, objectFit: "cover", borderRadius: 4, border: "1px solid var(--b)", cursor: "zoom-in" }} />;
                  })}
                </div>
              )}
            </div>

            {/* target langs */}
            <div>
              <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 6 }}>目标站点 / 语言（可多选）</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {langs.map((l) => {
                  const on = picked.has(l.code);
                  return (
                    <button key={l.code} onClick={() => toggleLang(l.code)}
                      style={{ fontSize: 11, padding: "4px 10px", borderRadius: 14, cursor: "pointer", fontFamily: "var(--font)",
                        border: on ? "1px solid var(--acc)" : "1px solid var(--b)", background: on ? "var(--acc)" : "var(--bg1)", color: on ? "#fff" : "var(--t)" }}>
                      {l.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <button className="tbtn" disabled={selectedIds.size === 0 || picked.size === 0 || translating}
              style={{ fontSize: 13, padding: "8px 0", fontWeight: 600,
                background: (selectedIds.size === 0 || picked.size === 0) ? "var(--bg2)" : "var(--acc)",
                color: (selectedIds.size === 0 || picked.size === 0) ? "var(--t3)" : "#fff",
                cursor: (selectedIds.size === 0 || picked.size === 0 || translating) ? "not-allowed" : "pointer" }}
              onClick={onTranslate}>
              {translating
                ? `翻译中… ${progress ? `${progress.done}/${progress.total} 张` : ""}`
                : `一键翻译 → ${selectedIds.size} 张 × ${picked.size} 语言 = ${selectedIds.size * picked.size} 张`}
            </button>
            {translating && <div style={{ fontSize: 10, color: "var(--t3)" }}>每张图约 1-3 分钟，逐张处理中，请勿关闭页面。</div>}
            {msg && <div style={{ fontSize: 11, color: "var(--t3)" }}>{msg}</div>}

            {/* results grouped by source */}
            {batches.map((g) => (
              <div key={g.sourceId} style={{ borderTop: "1px solid var(--b)", paddingTop: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  {g.sourceUrl && <img src={g.sourceUrl} alt="" onClick={() => setPreview(g.sourceUrl)} style={{ width: 32, height: 32, objectFit: "cover", borderRadius: 4, border: "1px solid var(--b)", cursor: "zoom-in" }} />}
                  <span style={{ fontSize: 11, color: "var(--t3)" }}>翻译结果（已自动存入工作区）</span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(110px, 1fr))", gap: 8 }}>
                  {g.results.map((r) => (
                    <div key={r.code} style={{ border: "1px solid var(--b)", borderRadius: 6, overflow: "hidden", background: "var(--bg2)" }}>
                      <div style={{ fontSize: 10, padding: "4px 6px", color: "var(--t)", borderBottom: "1px solid var(--b)" }}>{langOf(r.code)?.label || r.code}</div>
                      {r.url ? (
                        <>
                          <img src={r.url} alt="" onClick={() => setPreview(r.url!)} style={{ width: "100%", display: "block", aspectRatio: "1", objectFit: "cover", cursor: "zoom-in" }} />
                          <a href={r.url} download style={{ display: "block", fontSize: 10, textAlign: "center", padding: "4px 0", color: "var(--acc)", textDecoration: "none" }}>下载</a>
                        </>
                      ) : (
                        <div style={{ fontSize: 10, color: "#dc2626", padding: 8 }}>{r.error || "失败"}</div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Lightbox */}
      {preview && (
        <div onClick={() => setPreview("")}
          style={{ position: "fixed", inset: 0, zIndex: 9999, background: "rgba(0,0,0,.8)", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
          <img src={preview} alt="" style={{ maxWidth: "92vw", maxHeight: "92vh", objectFit: "contain", borderRadius: 6, boxShadow: "0 8px 40px rgba(0,0,0,.5)" }} />
          <button onClick={() => setPreview("")} style={{ position: "fixed", top: 16, right: 20, fontSize: 24, color: "#fff", background: "transparent", border: "none", cursor: "pointer" }}>×</button>
        </div>
      )}
    </div>
  );
}
