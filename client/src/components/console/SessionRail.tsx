/**
 * 侧边栏的「工作区 + 会话」区 —— 对标 MyLevis 左下角那块。
 *
 * 会话正文在 agent 那边，这里只列条目：点一条打开、双击改名、悬停删除。
 * 工作区是 ops 侧的分组概念，删分组不会删里面的会话。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CONSOLE_SESSIONS_CHANGED,
  consoleSessionDelete,
  consoleSessionPatch,
  consoleSessions,
  consoleWorkspaceCreate,
  consoleWorkspaceDelete,
  notifyConsoleSessionsChanged,
  type ConsoleSessionRow,
  type ConsoleWorkspace,
} from "../../api/ivyeaAgent";

const DEFAULT_WS = "默认工作区";
const OPEN_KEY = "ivyea-ops.console.ws-open";

function relTime(ts: number): string {
  if (!ts) return "";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

export default function SessionRail({
  collapsed,
  activeSessionId,
  onNavigate,
}: {
  collapsed: boolean;
  activeSessionId: string;
  onNavigate?: () => void;
}) {
  const navigate = useNavigate();
  const [rows, setRows] = useState<ConsoleSessionRow[]>([]);
  const [spaces, setSpaces] = useState<ConsoleWorkspace[]>([{ name: DEFAULT_WS, path: "", builtin: true }]);
  const [loaded, setLoaded] = useState(false);
  const [agentDown, setAgentDown] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem(OPEN_KEY) || "{}"); } catch { return {}; }
  });
  const [renaming, setRenaming] = useState("");
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [newWs, setNewWs] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const d = await consoleSessions();
      setRows(d.sessions || []);
      setAgentDown(d.agent_available === false);
      setSpaces(d.workspaces?.length ? d.workspaces : [{ name: DEFAULT_WS, path: "", builtin: true }]);
    } catch {
      // 左栏拿不到列表不该打扰用户 —— 任务台本身照常能用
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const h = () => void load();
    window.addEventListener(CONSOLE_SESSIONS_CHANGED, h);
    return () => window.removeEventListener(CONSOLE_SESSIONS_CHANGED, h);
  }, [load]);

  useEffect(() => {
    if (renaming) inputRef.current?.focus();
  }, [renaming]);

  const toggle = (name: string) => {
    const next = { ...open, [name]: open[name] === false };
    setOpen(next);
    try { localStorage.setItem(OPEN_KEY, JSON.stringify(next)); } catch { /* ignore */ }
  };

  const openSession = (id: string) => {
    navigate(`/console?session=${encodeURIComponent(id)}`);
    onNavigate?.();
  };

  const commitRename = async (id: string) => {
    const title = draft.trim();
    setRenaming("");
    if (!title) return;
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, title } : r)));   // 乐观
    try {
      await consoleSessionPatch(id, { title });
      notifyConsoleSessionsChanged();
    } catch {
      void load();          // 失败就用服务端那份覆盖回来
    }
  };

  const remove = async (row: ConsoleSessionRow) => {
    if (!window.confirm(`删除会话「${row.title}」？对话内容会一并删除，且无法恢复。`)) return;
    setRows((prev) => prev.filter((r) => r.id !== row.id));
    try {
      await consoleSessionDelete(row.id);
      if (row.id === activeSessionId) navigate("/console");
    } catch {
      void load();
    }
  };

  const addWorkspace = async () => {
    const name = newWs.trim();
    setAdding(false);
    setNewWs("");
    if (!name || name === DEFAULT_WS) return;
    try {
      await consoleWorkspaceCreate(name);
      await load();
    } catch { /* 名字冲突等，静默；列表会保持原样 */ }
  };

  const dropWorkspace = async (name: string) => {
    if (!window.confirm(`删除工作区「${name}」？里面的会话会回到默认工作区，不会被删除。`)) return;
    try {
      await consoleWorkspaceDelete(name);
      await load();
    } catch { /* ignore */ }
  };

  if (collapsed) return null;
  if (!loaded) return <div className="sb-ws-empty">载入会话…</div>;

  const byWorkspace = (name: string) =>
    rows.filter((r) => (r.workspace || DEFAULT_WS) === name);

  return (
    <div className="sb-workspace">
      <div className="ns sb-ws-head">
        <span>工作区</span>
        <button className="sb-ws-add" title="新建工作区" onClick={() => setAdding(true)}>+</button>
      </div>

      {adding && (
        <input
          className="sb-ws-input"
          autoFocus
          placeholder="工作区名称，回车确认"
          value={newWs}
          onChange={(e) => setNewWs(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void addWorkspace();
            if (e.key === "Escape") { setAdding(false); setNewWs(""); }
          }}
          onBlur={() => { setAdding(false); setNewWs(""); }}
        />
      )}

      {spaces.map((ws) => {
        const items = byWorkspace(ws.name);
        const isOpen = open[ws.name] !== false;
        return (
          <div key={ws.name} className="sb-ws-group">
            <button className="sb-ws-title" onClick={() => toggle(ws.name)}>
              <span className="sb-ws-caret">{isOpen ? "▾" : "▸"}</span>
              <span className="sb-ws-name" title={ws.path || undefined}>{ws.name}</span>
              <span className="sb-ws-count">{items.length}</span>
              {!ws.builtin && (
                <span
                  className="sb-ws-del"
                  title="删除工作区（会话不会被删）"
                  onClick={(e) => { e.stopPropagation(); void dropWorkspace(ws.name); }}
                >✕</span>
              )}
            </button>

            {isOpen && (items.length === 0 ? (
              <div className="sb-ws-empty">
                {agentDown ? "IvyeaAgent 未就绪，会话列表暂时读不到。" : "暂无会话"}
              </div>
            ) : items.map((r) => (
              <div
                key={r.id}
                className={"sb-sess" + (r.id === activeSessionId ? " active" : "")}
                onClick={() => renaming !== r.id && openSession(r.id)}
                onDoubleClick={() => { setRenaming(r.id); setDraft(r.title); }}
                title={r.preview || r.title}
              >
                {renaming === r.id ? (
                  <input
                    ref={inputRef}
                    className="sb-ws-input"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void commitRename(r.id);
                      if (e.key === "Escape") setRenaming("");
                    }}
                    onBlur={() => void commitRename(r.id)}
                    onClick={(e) => e.stopPropagation()}
                  />
                ) : (
                  <>
                    <span className="sb-sess-title">{r.title}</span>
                    <span className="sb-sess-time">{relTime(r.updated)}</span>
                    <span
                      className="sb-sess-del"
                      title="删除会话"
                      onClick={(e) => { e.stopPropagation(); void remove(r); }}
                    >✕</span>
                  </>
                )}
              </div>
            )))}
          </div>
        );
      })}
    </div>
  );
}
