/**
 * 目录选择器 —— 给「新建工作区」挑绑定目录用。
 *
 * ── 为什么不直接复用 agents 那个 FolderBrowserModal ──────────────────────
 * 它是 Tailwind 写的，而 agents 子树的 Tailwind 是**三重隔离**过的（作用域限定在
 * 那棵子树里）。把它挂到主侧栏这种非隔离区域，class 全部匹配不上，会渲染成一坨
 * 没样式的裸 div。所以这里复用的是**接口**（/browse-filesystem、/create-folder），
 * 外观按 workbench 自己的 .modal-* 规范重写。
 *
 * ── 权限 ────────────────────────────────────────────────────────────────
 * 后端两个接口都是 require_admin_actor。非管理员点不到这个入口（调用方不渲染按钮），
 * 万一还是 403 了，这里如实说"仅管理员"，而不是弹一句看不懂的报错。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, CornerLeftUp, Folder, FolderPlus, Loader2, Pencil } from "lucide-react";
import { browseFolders, createFolder, type FolderEntry } from "../../api/ivyeaAgent";
import { errText } from "../../lib/errText";

/**
 * 长路径从**头部**截断，只留末尾几段。
 *
 * 别用 CSS 的 `direction:rtl` 来做这件事 —— 它确实能把省略号打到开头，但同时会把
 * 前导的 "/" 当成中性字符甩到行尾：`/root` 显示成 `root/`。路径里的分隔符位置是有
 * 含义的，显示错了比截断了还糟。按分隔符切段自己拼，没有 bidi 这回事。
 */
export function shortPath(p: string, max = 46): string {
  if (!p || p.length <= max) return p;
  const sep = p.includes("\\") && !p.includes("/") ? "\\" : "/";
  const parts = p.split(/[\\/]/).filter(Boolean);
  let out = "";
  for (let i = parts.length - 1; i >= 0; i--) {
    const next = sep + parts[i] + out;
    if (next.length > max - 1 && out) break;
    out = next;
  }
  return "…" + out;
}

export default function FolderPicker({
  onPick,
  onClose,
}: {
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [path, setPath] = useState("");
  const [parent, setParent] = useState("");
  const [items, setItems] = useState<FolderEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [editingPath, setEditingPath] = useState(false);
  const [pathDraft, setPathDraft] = useState("");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  const go = useCallback(async (to?: string) => {
    setLoading(true);
    setErr("");
    try {
      const d = await browseFolders(to);
      setPath(d.path);
      setParent(d.parent || "");
      setItems(d.suggestions || []);
      // 换目录后列表滚回顶部 —— 不回滚的话进到一个短目录里会看见一片空白，
      // 像是"这个目录是空的"。
      if (listRef.current) listRef.current.scrollTop = 0;
    } catch (e: any) {
      setErr(e?.response?.status === 403
        ? "只有管理员能浏览服务器目录。你可以让管理员建好工作区，或直接手填路径。"
        : errText(e, "读不到这个目录"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void go(); }, [go]);

  // Esc 关闭。放在 document 上而不是容器上 —— 焦点可能在列表里的任意一行。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const visible = items.filter((it) => showHidden || !it.name.startsWith("."));

  const submitNewFolder = async () => {
    const name = newName.trim();
    if (!name) { setCreating(false); return; }
    try {
      // 路径分隔符跟着当前路径走：Windows 上 path 是 C:\… ，硬拼 "/" 会建出
      // 一个名字里带斜杠的目录。
      const sep = path.includes("\\") && !path.includes("/") ? "\\" : "/";
      const full = path.endsWith(sep) ? path + name : path + sep + name;
      await createFolder(full);
      setCreating(false);
      setNewName("");
      await go(full);
    } catch (e: any) {
      setErr(errText(e, "建不了这个文件夹"));
    }
  };

  return (
    <div className="modal-bd" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card fp-card" onMouseDown={(e) => e.stopPropagation()}>
        <div className="m-head">
          <span className="m-title">选择工作区目录</span>
        </div>

        {/* 当前路径。铅笔切成输入框 —— 目录很深时一层层点进去太慢，
            而且用户往往已经知道完整路径。 */}
        <div className="fp-path">
          {parent !== "" && (
            <button className="fp-up" title="上一级" onClick={() => void go(parent)}>
              <CornerLeftUp size={14} />
            </button>
          )}
          {editingPath ? (
            <input
              className="fp-path-input"
              autoFocus
              value={pathDraft}
              placeholder="输入完整路径后回车"
              onChange={(e) => setPathDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { setEditingPath(false); void go(pathDraft.trim()); }
                if (e.key === "Escape") { e.stopPropagation(); setEditingPath(false); }
              }}
              onBlur={() => setEditingPath(false)}
            />
          ) : (
            <>
              <span className="fp-path-text" title={path}>{shortPath(path) || "主目录"}</span>
              <button
                className="fp-pencil"
                title="直接输入路径"
                onClick={() => { setPathDraft(path); setEditingPath(true); }}
              >
                <Pencil size={13} />
              </button>
            </>
          )}
        </div>

        <div className="fp-list" ref={listRef}>
          {loading ? (
            <div className="fp-state"><Loader2 className="fp-spin" size={15} /> 读取中…</div>
          ) : err ? (
            <div className="fp-state fp-err">{err}</div>
          ) : visible.length === 0 ? (
            <div className="fp-state">这个目录下没有子文件夹{items.length ? "（隐藏项已折起）" : ""}。</div>
          ) : visible.map((it) => (
            <button key={it.path} className="fp-row" onDoubleClick={() => void go(it.path)}
                    onClick={() => void go(it.path)}>
              <Folder className="fp-ico" size={15} />
              <span className="fp-name">{it.name}</span>
              <ChevronRight className="fp-chev" size={14} />
            </button>
          ))}
        </div>

        <div className="m-foot fp-foot">
          {creating ? (
            <input
              className="fp-new-input"
              autoFocus
              placeholder="新文件夹名称"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submitNewFolder();
                if (e.key === "Escape") { e.stopPropagation(); setCreating(false); setNewName(""); }
              }}
              onBlur={() => void submitNewFolder()}
            />
          ) : (
            <button className="fp-newbtn" onClick={() => setCreating(true)}>
              <FolderPlus size={14} /> 新建文件夹
            </button>
          )}
          <label className="fp-hidden">
            <input type="checkbox" checked={showHidden}
                   onChange={(e) => setShowHidden(e.target.checked)} />
            显示隐藏文件
          </label>
          <span className="fp-spacer" />
          <button className="tbtn" onClick={onClose}>取消</button>
          <button className="tbtn tbtn-acc" disabled={!path || loading}
                  onClick={() => { onPick(path); onClose(); }}>
            选这个目录
          </button>
        </div>
      </div>
    </div>
  );
}
