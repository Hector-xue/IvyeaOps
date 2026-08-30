/**
 * 侧边栏的「工作区 + 会话」区 —— 对标 MyLevis 左下角那块。
 *
 * 会话正文在 agent 那边，这里只列条目：点一条打开、双击改名、悬停删除。
 * 工作区是 ops 侧的分组概念，删分组不会删里面的会话。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Folder, FolderOpen, FolderPlus, Search, SlidersHorizontal } from "lucide-react";
import { useAuth } from "../../App";
import FolderPicker, { shortPath } from "./FolderPicker";
import RunningMark from "./RunningMark";
import {
  CONSOLE_SESSIONS_CHANGED,
  consoleSessionDelete,
  consoleSessionPatch,
  consoleSessions,
  consoleWorkspaceCreate,
  consoleWorkspaceDelete,
  ivyeaLiveSessions,
  notifyConsoleSessionsChanged,
  SOURCE_LABEL,
  SOURCE_PATH,
  type ConsoleSessionRow,
  type ConsoleSource,
  type ConsoleWorkspace,
} from "../../api/ivyeaAgent";
import { errText } from "../../lib/errText";

const DEFAULT_WS = "默认工作区";
const OPEN_KEY = "ivyea-ops.console.ws-open";
const SRC_KEY = "ivyea-ops.console.src-filter";
// 弹性区大约能装十几条。给 30 只会让"加载更多"永远不出现、滚动条永远很长。
const PAGE = 15;

/** 来源筛选的可选项。"" = 全部。 */
const SOURCE_FILTERS: { key: "" | ConsoleSource; label: string }[] = [
  { key: "", label: "全部" },
  { key: "console", label: SOURCE_LABEL.console },
  { key: "assistant", label: SOURCE_LABEL.assistant },
  { key: "brain", label: SOURCE_LABEL.brain },
  { key: "cli", label: SOURCE_LABEL.cli },
];

/**
 * 会话时间：**纯日期数字**，不用「3 小时前」这种相对说法。
 *
 * 左栏一屏几十条，每条都挂一句「几小时前 / 几天前」时，这些字在视觉上和会话标题
 * 抢注意力 —— 而它们本来只是排序的副产品，用户扫列表时并不读它。数字短、形状统一，
 * 扫过去成一列，不打断读标题。
 *
 * 今天的只给时分（今天发生的事，日期是多余的）；今年的给月/日；跨年补两位年份。
 */
function sessionTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const two = (n: number) => String(n).padStart(2, "0");
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
  if (sameDay) return `${two(d.getHours())}:${two(d.getMinutes())}`;
  const md = `${two(d.getMonth() + 1)}/${two(d.getDate())}`;
  return d.getFullYear() === now.getFullYear() ? md : `${two(d.getFullYear() % 100)}/${md}`;
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
  // 搜索框和来源筛选默认收起，各由头部一个图标唤出。
  // 它们常年摊在那儿时，「工作区」这一块从上到下是"标题 / 满宽输入框 / 一排 chip /
  // 列表"四层，每层都在喊 —— 而九成时间用户只是想看一眼列表。
  const [searchOpen, setSearchOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [picking, setPicking] = useState(false);
  // 此刻正在跑的会话 id。**独立于列表**：列表几十条、要读会话文件，不能每几秒拉一次；
  // 这一份读的是 agent 内存里的活轮登记，便宜到可以按秒问。
  const [live, setLive] = useState<Set<string>>(new Set());
  const { role } = useAuth();
  const isAdmin = role === "admin";

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

  /**
   * 谁在跑 —— 每 5 秒问一次，页面切到后台就停。
   *
   * 这是左栏那枚闪烁标记的数据源。它必须是"真的有一轮在跑"，不能拿"最近更新过"
   * 凑数：一条十分钟前跑完的会话和一条正在跑的会话，用户要做的事完全不同。
   *
   * `available: false`（agent 没起 / 版本太老）时**保持上一次的显示**，不清空 ——
   * "问不到"不等于"没有在跑的"，清空会让正在执行的会话看着像已经停了。
   */
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const tick = async () => {
      if (document.visibilityState === "visible") {
        try {
          const d = await ivyeaLiveSessions();
          if (alive && d?.available !== false) {
            setLive(new Set((d.sessions || []).map((x) => String(x.id))));
          }
        } catch {
          // 问不到就保持原样，下一次再问
        }
      }
      if (alive) timer = window.setTimeout(tick, 5000);
    };
    void tick();
    return () => { alive = false; window.clearTimeout(timer); };
  }, []);
  useEffect(() => {
    let retry = 0;
    const h = (e: Event) => {
      const want = (e as CustomEvent<{ expectId?: string }>).detail?.expectId;
      void load().then(() => {
        // 补取一次（只补一次，避免开着一个失败的会话在那儿空转）。两件事都要等：
        //   · 新会话是 agent 侧落库的，「开始」事件比落库早一拍，这一次可能还没有它；
        //   · 标题是这一轮跑完后由模型起的（服务端后台线程），比这次取回来晚几秒。
        // 所以**不管行在不在都补一次** —— 只看"在不在"的话，标题永远要等下次刷新
        // 才更新，用户看到的还是那句"帮我看下这个"。
        if (!want) return;
        window.clearTimeout(retry);
        retry = window.setTimeout(() => { void load(); }, 3500);
      });
    };
    window.addEventListener(CONSOLE_SESSIONS_CHANGED, h);
    return () => { window.clearTimeout(retry); window.removeEventListener(CONSOLE_SESSIONS_CHANGED, h); };
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
      setWsErr(errText(e, "创建失败"));
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

  // 有没有非任务台来源，决定要不要摆那一行筛选 chip。看的是**当前拿到的这批**
  // 会话；筛选本身走服务端，所以这里只是"要不要给入口"。
  const showSourceFilter = src !== "" || rows.some((r) => r.source && r.source !== "console");
  const onlyDefaultWorkspace = spaces.length === 1 && spaces[0].name === DEFAULT_WS;

  const byWorkspace = (name: string) =>
    rows.filter((r) => (r.workspace || DEFAULT_WS) === name);

  // 有没有哪个**展开的**工作区还没加载完 —— 只有这时候「加载更多」才会让画面变化。
  // 用服务端给的真实条数比，而不是比全局 total：全局还有剩余，不等于剩余的那些
  // 属于任何一个展开的组。
  const canLoadMore = spaces.some((ws) => {
    const expanded = onlyDefaultWorkspace ? true : open[ws.name] !== false;
    if (!expanded) return false;
    return byWorkspace(ws.name).length < (ws.count ?? Number.MAX_SAFE_INTEGER);
  });

  return (
    <div className="sb-workspace">
      <div className="ns sb-ws-head">
        <span>工作区</span>
        {/* 三个图标顶在标题右侧：搜索 / 来源筛选 / 新建工作区。
            用图标而不是常驻控件，是为了让这一块只剩"标题 + 列表"两层。 */}
        <button
          className={"sb-ws-act" + (searchOpen || q ? " on" : "")}
          title="搜索会话"
          onClick={() => {
            const next = !searchOpen;
            setSearchOpen(next);
            if (!next) setQ("");           // 收起就清掉，否则列表被一个看不见的词过滤着
          }}
        ><Search size={14} /></button>
        {showSourceFilter && (
          <button
            className={"sb-ws-act" + (filterOpen || src ? " on" : "")}
            title="按来源筛选"
            onClick={() => setFilterOpen((v) => !v)}
          ><SlidersHorizontal size={14} /></button>
        )}
        <button className="sb-ws-act" title="新建工作区" onClick={() => setAdding(true)}>
          <FolderPlus size={14} />
        </button>
      </div>

      {/* 放大镜不是装饰：这个框满宽、无内容时就是一块空板子，光靠占位文字
          挂在左上角读不出"这是搜索"。图标钉在左侧把它锚住。
          pointer-events:none —— 图标压在输入框上方，不挡点击。 */}
      {searchOpen && (
      <div className="sb-sess-search-wrap">
        <Search className="sb-sess-search-icon" aria-hidden />
        <input
          className="sb-ws-input sb-sess-search"
          autoFocus
          placeholder="搜索会话…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          // 第一下 Esc 清词、第二下收起。直接收起会把用户刚敲的词一起吞掉，
          // 而清空往往才是他想要的。
          onKeyDown={(e) => {
            if (e.key !== "Escape") return;
            if (q) setQ(""); else setSearchOpen(false);
          }}
        />
      </div>
      )}

      {/*
        * 来源筛选只在**真的有一种以上来源**时出现。
        * 196px 宽塞不下 4 个 chip，实测换行成 2 行；而绝大多数人只用任务台，
        * 那两行就是纯占地方 —— 还把会话又往下推了一截。
        */}
      {showSourceFilter && (filterOpen || !!src) && (
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
      )}

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
          {/* 绑定目录：能选就别让人手打。路径打错不会当场报错，要等 Agent 真去读写
              文件时才炸，那时候用户早忘了自己填过什么。浏览接口仅管理员可用
              （后端 require_admin_actor），所以非管理员这里退回纯输入框。 */}
          {isAdmin ? (
            <button
              className={"sb-ws-pick" + (newWsPath ? " has" : "")}
              onClick={() => setPicking(true)}
              title={newWsPath || "选一个目录绑给这个工作区"}
            >
              <Folder size={13} />
              <span className="sb-ws-pick-t">{newWsPath ? shortPath(newWsPath, 26) : "选择绑定目录（可选）"}</span>
              {newWsPath && (
                <span
                  className="sb-ws-pick-x"
                  title="不绑目录"
                  onClick={(e) => { e.stopPropagation(); setNewWsPath(""); }}
                >✕</span>
              )}
            </button>
          ) : (
            <div className="sb-ws-hint">绑定目录仅管理员可设置。</div>
          )}
          {wsErr && <div className="sb-ws-err">{wsErr}</div>}
          <div className="sb-ws-hint">回车创建 · Esc 取消。绑了目录，Agent 的文件操作就在那个目录里。</div>
        </>
      )}

      {spaces.map((ws) => {
        const items = byWorkspace(ws.name);
        const isOpen = onlyDefaultWorkspace ? true : open[ws.name] !== false;
        return (
          <div key={ws.name} className={"sb-ws-group" + (onlyDefaultWorkspace ? " flat" : "")}>
            {/* 只有「默认工作区」一个分组时，这个标题+计数+折叠箭头是纯噪音：
                没有别的组可切，折叠它也没有意义。 */}
            {!onlyDefaultWorkspace && (
            <button className="sb-ws-title" onClick={() => toggle(ws.name)}>
              <span className="sb-ws-caret">{isOpen ? "▾" : "▸"}</span>
              {/* 展开态用打开的文件夹 —— 折叠箭头本身很小，图标跟着换态，
                  扫一眼就知道哪个组是开的。 */}
              {isOpen ? <FolderOpen className="sb-ws-ico" size={14} />
                      : <Folder className="sb-ws-ico" size={14} />}
              <span className="sb-ws-name" title={ws.path || undefined}>{ws.name}</span>
              {/* 计数取服务端给的**真实**条数，不是当前页里的条数。
                  拿 items.length 当计数时，一个有 211 条会话的工作区在只加载了
                  60 条时显示成 60 —— 看着像会话丢了。 */}
              <span className="sb-ws-count">{ws.count ?? items.length}</span>
              {!ws.builtin && (
                <span
                  className="sb-ws-del"
                  title="删除工作区（会话不会被删）"
                  onClick={(e) => { e.stopPropagation(); void dropWorkspace(ws.name); }}
                >✕</span>
              )}
            </button>
            )}

            {isOpen && (items.length === 0 ? (
              <div className="sb-ws-empty">
                {agentDown ? "IvyeaAgent 未就绪，会话列表暂时读不到。"
                  : (src || debouncedQ) ? (
                  /* **空列表要说清楚是被筛掉的。** 来源筛选存在 localStorage 里，
                     跨会话一直生效 —— 用户上次点过一次「智能体」，之后新开的
                     任务台会话就再也不出现，而界面上什么都不说，看起来就是
                     "我的对话丢了"。 */
                  <>
                    没有匹配的会话
                    <button className="sb-clear-filter" onClick={() => {
                      pickSource("");
                      setQ("");
                    }}>
                      清除筛选{src ? `（当前只看「${SOURCE_FILTERS.find((f) => f.key === src)?.label || src}」）` : ""}
                    </button>
                  </>
                ) : "暂无会话"}
              </div>
            ) : items.map((r) => (
              <div
                key={r.id}
                className={"sb-sess" + (r.id === activeSessionId ? " active" : "")}
                onClick={() => renaming !== r.id && openSession(r)}
                onDoubleClick={() => { setRenaming(r.id); setDraft(r.title); }}
                /* 终端会话额外把它开在哪个目录挂进 tooltip —— 那是终端会话唯一能
                   区分彼此的上下文（都叫同一个首句摘要的会话，看目录才知道是哪个
                   项目里跑的）。不占版面，所以只进 title。 */
                title={r.cwd ? `${r.preview || r.title}\n目录：${r.cwd}` : (r.preview || r.title)}
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
                    {(live.has(r.id) || r.running) && (
                      /* 正在跑 —— 标题左边一枚会动的标记。列表里的时间只能说"最近
                         动过"，而"现在正在跑"是完全不同的一件事（能不能关页面、要不要
                         等它、该不该再发一句，全看这个）。
                         形状和状态坞里"这一步正在做"共用一个组件：同一件事只能有
                         一种说法。 */
                      <RunningMark className="sb-sess-live" title="正在执行" />
                    )}
                    <span className="sb-sess-title">{r.title}</span>
                    {r.source && r.source !== "console" && (
                      <span className={"sb-sess-src src-" + r.source}>
                        {SOURCE_LABEL[r.source as ConsoleSource]}
                      </span>
                    )}
                    <span className="sb-sess-time">{sessionTime(r.updated)}</span>
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

      {/* **折叠了就别再问「加载更多」。** 它此前只看 hasMore，于是出现过这种画面：
          唯一有会话的工作区是折叠的，旁边一个空工作区展开着写"暂无会话"，底下还挂着
          「加载更多（已显示 60 / 211）」—— 点了什么都不会变，因为多出来的会话全落进
          那个折叠的组里。判据改成"有没有哪个展开的组还没加载完"。 */}
      {hasMore && canLoadMore && (
        <button
          className="sb-sess-more"
          disabled={loadingMore}
          onClick={() => { setLoadingMore(true); setLimit((n) => n + PAGE); }}
        >
          {loadingMore ? "载入中…" : `加载更多（已显示 ${rows.length} / ${total}）`}
        </button>
      )}

      {picking && (
        <FolderPicker onClose={() => setPicking(false)} onPick={(p) => setNewWsPath(p)} />
      )}
    </div>
  );
}
