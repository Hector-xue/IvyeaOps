/**
 * 右下角的悬浮球。
 *
 * ── 它现在只是个外壳 ──────────────────────────────────────────────────────
 * 以前这里是**另一份**对话实现：只有流式正文和一行工具叙述 —— 没有审批档位、
 * 没有写操作确认卡、没有执行步骤流、没有模型切换、没有附图和 @ 引用。而任务台
 * 那边一直在长。两份各自演化的结果就是"同一个 Agent，换个入口能力差一大截"，
 * 用户的原话是"连审批确认的功能都没有"。
 *
 * 所以这一版不再逐个补功能，而是**直接渲染任务台本体**（`<Console embedded />`）。
 * 差距一次性抹平，而且以后任务台加什么，悬浮球自动就有 —— 这是构造上的一致，
 * 不是靠人记得两边都改。
 *
 * 外壳自己只管三件事：悬浮球（可拖、记位置）、面板头（新会话/历史/刷新/关闭）、
 * 历史会话列表。
 */
import {
  type CSSProperties, type PointerEvent as ReactPointerEvent,
  useEffect, useRef, useState,
} from "react";
import { createPortal } from "react-dom";
import { Bot, History, Loader2, Plus, RefreshCw, Trash2, X } from "lucide-react";
import {
  ivyeaAgentStatus,
  ivyeaChatSessionDelete,
  ivyeaChatSessions,
  ivyeaServiceStart,
  type IvyeaAgentStatus,
  type IvyeaChatSession,
} from "../api/ivyeaAgent";
import Console from "../pages/workbench/Console";
import "../styles/ivyea-agent-dock.css";
import { errText } from "../lib/errText";

const FAB_SIZE = 52;
const FAB_MARGIN = 12;
const FAB_POS_KEY = "ivyea-agent-fab-pos";

type FabPosition = { x: number; y: number };

function clampFabPosition(x: number, y: number): FabPosition {
  if (typeof window === "undefined") return { x, y };
  const maxX = Math.max(FAB_MARGIN, window.innerWidth - FAB_SIZE - FAB_MARGIN);
  const maxY = Math.max(FAB_MARGIN, window.innerHeight - FAB_SIZE - FAB_MARGIN);
  return {
    x: Math.min(Math.max(FAB_MARGIN, x), maxX),
    y: Math.min(Math.max(FAB_MARGIN, y), maxY),
  };
}

function formatSessionTime(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return sameDay ? `${hh}:${mm}` : `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`;
}

function sessionTitle(item?: IvyeaChatSession | null): string {
  if (!item) return "未命名会话";
  const raw = String((item as any).title || item.preview || "").trim();
  return raw ? raw.slice(0, 28) : "未命名会话";
}

export default function IvyeaAgentDock() {
  const [open, setOpen] = useState(false);
  /*
   * 打开过一次就**一直挂着**（关闭只用 CSS 藏起来）。
   * 卸载会把正在跑的那一轮连同上下文一起丢掉 —— 而悬浮球最典型的用法恰恰是
   * "问一句，收起来接着干活，回头再看结果"。反过来，没打开过就不挂：任务台那
   * 一份启动时要拉技能、预设、能力目录，没人用的时候不该付这份钱。
   */
  const [mounted, setMounted] = useState(false);
  const [fabPos, setFabPos] = useState<FabPosition | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      const raw = window.localStorage.getItem(FAB_POS_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as Partial<FabPosition>;
      if (!Number.isFinite(parsed.x) || !Number.isFinite(parsed.y)) return null;
      return clampFabPosition(Number(parsed.x), Number(parsed.y));
    } catch {
      return null;
    }
  });
  const [status, setStatus] = useState<IvyeaAgentStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const [sessions, setSessions] = useState<IvyeaChatSession[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [resetSignal, setResetSignal] = useState(0);
  const [error, setError] = useState("");

  const fabDragRef = useRef({
    dragging: false, moved: false, suppressClick: false,
    startX: 0, startY: 0, originX: 0, originY: 0,
  });

  const online = !!status?.available;
  const statusTone = status?.available ? "ok" : status?.ok === false ? "bad" : "idle";
  const currentModel = status?.health?.model?.label || status?.health?.model?.model || "未连接";
  const fabStyle: CSSProperties | undefined = fabPos
    ? { left: fabPos.x, top: fabPos.y, right: "auto", bottom: "auto" }
    : undefined;

  const loadStatus = async () => {
    setLoadingStatus(true);
    try {
      setStatus(await ivyeaAgentStatus());
    } catch (e: any) {
      setStatus({ ok: false, available: false, base_url: "", token_configured: false,
                  error: errText(e, "状态加载失败") });
    } finally {
      setLoadingStatus(false);
    }
  };

  const loadSessions = async () => {
    setLoadingHistory(true);
    try {
      const d = await ivyeaChatSessions(30);
      setSessions(d.sessions || []);
    } catch (e: any) {
      setError(errText(e, "历史会话加载失败"));
    } finally {
      setLoadingHistory(false);
    }
  };

  useEffect(() => { void loadStatus(); }, []);

  useEffect(() => {
    if (!open) return;
    void loadStatus();
    // agent 没起来时先拉一把再说 —— 悬浮球的用户多半不会去系统配置点「启动」。
    if (status && !status.available) ivyeaServiceStart().catch(() => void 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    const onResize = () => setFabPos((prev) => {
      if (!prev) return prev;
      const next = clampFabPosition(prev.x, prev.y);
      try { window.localStorage.setItem(FAB_POS_KEY, JSON.stringify(next)); } catch { /* noop */ }
      return next;
    });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // ── 悬浮球拖拽 ────────────────────────────────────────────────────────────
  const onFabPointerDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    fabDragRef.current = {
      dragging: true, moved: false, suppressClick: false,
      startX: e.clientX, startY: e.clientY,
      originX: fabPos?.x ?? rect.left, originY: fabPos?.y ?? rect.top,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onFabPointerMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = fabDragRef.current;
    if (!drag.dragging) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (Math.abs(dx) + Math.abs(dy) < 4 && !drag.moved) return;
    drag.moved = true;
    drag.suppressClick = true;
    setFabPos(clampFabPosition(drag.originX + dx, drag.originY + dy));
  };

  const onFabPointerUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = fabDragRef.current;
    if (!drag.dragging) return;
    drag.dragging = false;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* 浏览器可能已释放 */ }
    if (drag.moved) {
      const next = clampFabPosition(e.currentTarget.getBoundingClientRect().left,
                                    e.currentTarget.getBoundingClientRect().top);
      setFabPos(next);
      try { window.localStorage.setItem(FAB_POS_KEY, JSON.stringify(next)); } catch { /* noop */ }
    }
  };

  const onFabClick = () => {
    if (fabDragRef.current.suppressClick) {
      fabDragRef.current.suppressClick = false;
      return;
    }
    setOpen((v) => {
      if (!v) setMounted(true);
      return !v;
    });
  };

  // ── 头部动作 ──────────────────────────────────────────────────────────────
  const newSession = () => {
    setSessionId("");
    setResetSignal((n) => n + 1);
    setShowHistory(false);
  };

  const openHistory = () => {
    setShowHistory(true);
    void loadSessions();
  };

  const pickSession = (id: string) => {
    setSessionId(id);
    setShowHistory(false);
  };

  const removeSession = async (id: string) => {
    try {
      await ivyeaChatSessionDelete(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (id === sessionId) newSession();
    } catch (e: any) {
      setError(errText(e, "删除会话失败"));
    }
  };

  // 挂到 document.body（脱离 #root 的 zoom）：FAB/面板是 position:fixed 悬浮层，放在
  // zoom 里会导致 fixed 相对被缩放的 #root 定位、且拖拽坐标错乱（拉不到真边缘）。
  // 挂 body 后回到纯视口坐标系；主题/字体变量在 documentElement 上，照样继承。
  return createPortal(
    <>
      <button
        className={`ivyea-agent-fab ${statusTone}`}
        style={fabStyle}
        onPointerDown={onFabPointerDown}
        onPointerMove={onFabPointerMove}
        onPointerUp={onFabPointerUp}
        onPointerCancel={onFabPointerUp}
        onClick={onFabClick}
        title="Ivyea Agent"
        aria-label="Ivyea Agent"
      >
        <Bot size={22} />
        <span className="ivyea-agent-fab-dot" />
      </button>

      {mounted && (
        <section
          className="ivyea-agent-panel"
          aria-label="Ivyea Agent"
          aria-hidden={!open}
          style={open ? undefined : { display: "none" }}
        >
          <header className="ivyea-agent-head">
            <div className="ivyea-agent-brand">
              <span className="ivyea-agent-mark"><Bot size={17} /></span>
              <div>
                <div className="ivyea-agent-title">Ivyea Agent</div>
                <div className="ivyea-agent-sub">
                  {online ? currentModel : status?.error || "本地服务未连接"}
                </div>
              </div>
            </div>
            <div className="ivyea-agent-head-actions">
              <button className="ivyea-agent-icon-btn" onClick={newSession} title="新会话">
                <Plus size={16} />
              </button>
              <button className={"ivyea-agent-icon-btn" + (showHistory ? " active" : "")}
                      onClick={() => (showHistory ? setShowHistory(false) : openHistory())}
                      disabled={loadingHistory} title="历史会话">
                {loadingHistory ? <Loader2 size={15} className="spin" /> : <History size={15} />}
              </button>
              <button className="ivyea-agent-icon-btn" onClick={() => void loadStatus()}
                      disabled={loadingStatus} title="刷新">
                <RefreshCw size={15} className={loadingStatus ? "spin" : ""} />
              </button>
              <button className="ivyea-agent-icon-btn" onClick={() => setOpen(false)} title="收起">
                <X size={16} />
              </button>
            </div>
          </header>

          {error && <div className="ivyea-agent-error">{error}</div>}

          <div className="ivyea-agent-body">
            {/*
             * 任务台本体。**永远挂着**，历史列表用浮层盖在它上面 ——
             * 用条件渲染切换会把正在跑的那一轮卸载掉，而"发出去之后翻一下历史"
             * 是再正常不过的动作。
             */}
            <Console
              embedded
              sessionId={sessionId}
              onSessionChange={setSessionId}
              resetSignal={resetSignal}
            />

            {showHistory && (
              <div className="ivyea-agent-history-view">
                <div className="ivyea-agent-history-head"><span>历史会话</span></div>
                <div className="ivyea-agent-history-list">
                  {sessions.length === 0 ? (
                    <div className="ivyea-agent-history-empty">
                      {loadingHistory ? "正在加载…" : "暂无历史会话"}
                    </div>
                  ) : sessions.map((item) => (
                    <div key={item.id} className="ivyea-agent-history-row">
                      <button
                        className={"ivyea-agent-history-item" + (item.id === sessionId ? " active" : "")}
                        onClick={() => pickSession(item.id)}
                        title={sessionTitle(item)}
                      >
                        <span>{sessionTitle(item)}</span>
                        <em>{formatSessionTime(item.updated) || "最近"} · {item.turns || 0} 轮</em>
                      </button>
                      <button
                        className="ivyea-agent-history-del"
                        onClick={() => void removeSession(item.id)}
                        title="删除会话"
                        aria-label="删除会话"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      )}
    </>,
    document.body,
  );
}
