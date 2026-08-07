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
  SOURCE_LABEL,
  SOURCE_PATH,
  type ConsoleSessionRow,
  type ConsoleSource,
  type ConsoleWorkspace,
} from "../../api/ivyeaAgent";

const DEFAULT_WS = "默认工作区";
const OPEN_KEY = "ivyea-ops.console.ws-open";
const SRC_KEY = "ivyea-ops.console.src-filter";
const PAGE = 30;

/** 来源筛选的可选项。"" = 全部。 */
const SOURCE_FILTERS: { key: "" | ConsoleSource; label: string }[] = [
  { key: "", label: "全部" },
  { key: "console", label: SOURCE_LABEL.console },
  { key: "assistant", label: SOURCE_LABEL.assistant },
  { key: "brain", label: SOURCE_LABEL.brain },
];

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
  const [src, setSrc] = useState<"" | ConsoleSource>(
    () => (localStorage.getItem(SRC_KEY) as ConsoleSource) || "");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [renaming, setRenaming] = useState("");
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [newWs, setNewWs] = useState("");
  const [newWsPath, setNewWsPath] = useState("");
  const [wsErr, setWsErr] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // 「加载更多」是把 limit 调大重取整段，而不是把新一页拼到旧数组后面。
  // 拼接看着更省流量，但一旦有会话被改名/删除/被新一轮顶到前面，两段就会错位、
  // 出现重复或漏条。会话本来就只有几百条，整段重取更稳。
  const load = useCallback(async () => {
    try {
      const d = await consoleSessions("", limit, src, debouncedQ);
      setRows(d.sessions || []);
      setAgentDown(d.agent_available === false);
      setTotal(d.total ?? (d.sessions || []).length);
      setHasMore(!!d.has_more);
      setSpaces(d.workspaces?.length ? d.workspaces : [{ name: DEFAULT_WS, path: "", builtin: true }]);
    } catch {
      // 左栏拿不到列表不该打扰用户 —— 任务台本身照常能用
    } finally {
      setLoaded(true);
      setLoadingMore(false);
    }
  }, [src, limit, debouncedQ]);

  // 搜索防抖：每敲一个字就打一次接口，会把服务端和自己都拖慢
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQ(q.trim()), 260);
    return () => window.clearTimeout(t);
  }, [q]);

  // 换筛选条件/换关键词都要回到第一页，否则会停在上一次翻到的深度上，
  // 看着像"搜出来的结果莫名其妙很多"
  useEffect(() => { setLimit(PAGE); }, [src, debouncedQ]);

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

  // 三个板块共用会话库，但各自的界面并不等价（AI 问答不带工具、知识库带引证），
  // 所以点一条会话要回到它**本来的**板块，而不是一律拽进任务台。
  //
  // 知识库那条是**镜像**：agent 里的正文只是副本，引证还在 brain 自己的库里，
  // 所以要把 agent id 反解回 brain 的 session_id，让 /brain 去读带引证的那一份。
  const openSession = (row: ConsoleSessionRow) => {
    const src = (row.source || "console") as ConsoleSource;
    const path = SOURCE_PATH[src] || "/console";
    const id = src === "brain" ? row.id.replace(/^imp-brain-/, "") : row.id;
    navigate(`${path}?session=${encodeURIComponent(id)}`);
    onNavigate?.();
  };

  const pickSource = (key: "" | ConsoleSource) => {
    setSrc(key);
    try { localStorage.setItem(SRC_KEY, key); } catch { /* ignore */ }
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
    if (!name || name === DEFAULT_WS) { setAdding(false); setNewWs(""); return; }
    try {
      await consoleWorkspaceCreate(name, newWsPath.trim());
      setAdding(false); setNewWs(""); setNewWsPath(""); setWsErr("");
      await load();
    } catch (e: any) {
      // 目录非法/越权要说清楚，不能静默吞掉让人以为建成了
      setWsErr(e?.response?.data?.detail || "创建失败");
    }
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

      <input
        className="sb-ws-input sb-sess-search"
        placeholder="搜索会话…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Escape") setQ(""); }}
      />

      <div className="sb-src-filter">
        {SOURCE_FILTERS.map((f) => (
          <button
            key={f.key || "all"}
            type="button"
            className={"sb-src-chip" + (src === f.key ? " active" : "")}
            onClick={() => pickSource(f.key)}
          >{f.label}</button>
        ))}
      </div>

      {adding && (
        <>
          <input
            className="sb-ws-input"
            autoFocus
            placeholder="工作区名称"
            value={newWs}
            onChange={(e) => setNewWs(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void addWorkspace();
              if (e.key === "Escape") { setAdding(false); setNewWs(""); setNewWsPath(""); setWsErr(""); }
            }}
          />
          <input
            className="sb-ws-input"
            placeholder="绑定目录（可选，仅管理员）"
            value={newWsPath}
            onChange={(e) => setNewWsPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void addWorkspace();
              if (e.key === "Escape") { setAdding(false); setNewWs(""); setNewWsPath(""); setWsErr(""); }
            }}
          />
          {wsErr && <div className="sb-ws-err">{wsErr}</div>}
          <div className="sb-ws-hint">回车创建 · Esc 取消。绑了目录，Agent 的文件操作就在那个目录里。</div>
        </>
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
                onClick={() => renaming !== r.id && openSession(r)}
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
                    {r.source && r.source !== "console" && (
                      <span className={"sb-sess-src src-" + r.source}>
                        {SOURCE_LABEL[r.source as ConsoleSource]}
                      </span>
                    )}
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

      {debouncedQ && rows.length === 0 && (
        <div className="sb-ws-empty">没有匹配「{debouncedQ}」的会话。</div>
      )}

      {hasMore && (
        <button
          className="sb-sess-more"
          disabled={loadingMore}
          onClick={() => { setLoadingMore(true); setLimit((n) => n + PAGE); }}
        >
          {loadingMore ? "载入中…" : `加载更多（已显示 ${rows.length} / ${total}）`}
        </button>
      )}
    </div>
  );
}
