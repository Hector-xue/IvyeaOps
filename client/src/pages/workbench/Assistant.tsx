/**
 * AI 写作 / 问答 —— 「不带工具的那一档」。
 *
 * 会话**存在 agent 的会话库里**，和任务台、知识库共用一份（source=assistant），
 * 所以左栏能一起列、刷新能恢复、换台机器也还在。页面本身保留，是因为它的定位
 * 就是"纯聊，不许动工具"：`use_tools:false` + `inject_retrieval:false`。
 *
 * agent 掉线时自动退回老的 /api/assistant/chat（多provider兜底链）—— 只是那一轮
 * 不会进会话库。宁可少存一条，也不能让 AI 问答跟着 agent 一起躺下。
 */
import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { streamChat, type ChatMsg } from "../../api/assistant";
import {
  answerResetDiscards,
  consoleSessionImport,
  consoleSessions,
  ivyeaAgentChatStream,
  ivyeaChatSession,
  notifyConsoleSessionsChanged,
  consoleSessionDelete,
  type ConsoleSessionRow,
} from "../../api/ivyeaAgent";
import { MarkdownReport } from "../../lib/reportFormat";
import { stripInjected } from "../../lib/stripInjected";
import { useStickToBottom } from "../../lib/useStickToBottom";

const STORAGE = "ivyea-ops-assistant-chat";
const SESSIONS_KEY = "ivyea-ops-assistant-sessions";
const IMPORTED_KEY = "ivyea-ops-assistant-imported-v1";
const SYSTEM_PROMPT =
  "你是亚马逊运营助手，用中文清晰作答；需要时用 Markdown（表格/列表/标题）写出可直接复制的文档。";

interface Turn { role: "user" | "assistant"; content: string }
interface LegacySession { id: string; title: string; turns: Turn[]; updatedAt: number }

/** 老版本把历史存在 localStorage 里。只读一次，导入完就不再依赖它。 */
function loadLegacySessions(): LegacySession[] {
  try {
    const r = localStorage.getItem(SESSIONS_KEY);
    const v = r ? JSON.parse(r) : [];
    return Array.isArray(v) ? v : [];
  } catch { return []; }
}

export default function Assistant() {
  const navigate = useNavigate();
  const location = useLocation();
  const urlSession = new URLSearchParams(location.search).get("session") || "";

  const [sessions, setSessions] = useState<ConsoleSessionRow[]>([]);
  const [currentId, setCurrentId] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  const refreshSessions = useCallback(async () => {
    try {
      const d = await consoleSessions("", 60, "assistant");
      setSessions(d.sessions || []);
    } catch {
      // 列不出历史不该拦着聊天，静默即可
    }
  }, []);

  // 老的 localStorage 历史搬一次家。**按 id 幂等**（服务端按 id 覆盖写），
  // 所以这里的 localStorage 标记只是省一次网络请求，不是正确性的依赖 ——
  // 换浏览器再导一次也不会出现重复会话。
  useEffect(() => {
    let alive = true;
    (async () => {
      const legacy = loadLegacySessions().filter((x) => x?.id && x.turns?.length);
      if (legacy.length && !localStorage.getItem(IMPORTED_KEY)) {
        try {
          const r = await consoleSessionImport("assistant", legacy.map((x) => ({
            id: String(x.id).replace(/[^A-Za-z0-9_-]/g, ""),
            created: Math.floor((x.updatedAt || Date.now()) / 1000),
            messages: x.turns
              .filter((t) => t?.content?.trim())
              .map((t) => ({ role: t.role, content: t.content })),
          })).filter((x) => x.id && x.messages.length));
          localStorage.setItem(IMPORTED_KEY, String(r.count));
          if (alive && r.count) setNote(`已把 ${r.count} 条本地历史对话搬进会话库`);
          notifyConsoleSessionsChanged();
        } catch {
          // 导入失败不标记，下次进页面再试
        }
      }
      if (alive) await refreshSessions();
    })();
    return () => { alive = false; };
  }, [refreshSessions]);

  // ?session= 恢复一条历史会话（左栏点选、刷新、分享链接都走这里）
  useEffect(() => {
    if (!urlSession || urlSession === currentId) return;
    let alive = true;
    abortRef.current?.abort();
    ivyeaChatSession(urlSession)
      .then((d) => {
        if (!alive) return;
        const msgs = (d?.session?.messages || []) as { role: string; content: string }[];
        setTurns(msgs
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m) => ({ role: m.role as "user" | "assistant", content: stripInjected(String(m.content || "")) }))
          .filter((t) => t.content));
        setCurrentId(urlSession);
        setErr("");
      })
      .catch((e: any) => {
        if (!alive) return;
        setErr(e?.response?.status === 403 ? "这条会话不属于你" : "打开会话失败");
        navigate("/assistant", { replace: true });
      });
    return () => { alive = false; };
  }, [urlSession, currentId, navigate]);

  // 跟随滚动按用户意图判（wheel/touch/键），不按滚动位置判 —— 位置判据在流式
  // 输出下必输，用户往上翻的那一下会被下一个 token 抢先拍回底部。见 lib/useStickToBottom。
  useStickToBottom(bodyRef, [turns, streaming]);

  /** 老通道：agent 不在时的兜底，本轮不进会话库。 */
  const sendLegacy = async (base: Turn[], signal: AbortSignal) => {
    const msgs: ChatMsg[] = [
      { role: "system", content: SYSTEM_PROMPT },
      ...base.map((t) => ({ role: t.role, content: t.content } as ChatMsg)),
    ];
    await streamChat(msgs, (e) => {
      if (e.type === "token") {
        setTurns((prev) => {
          const n = [...prev];
          n[n.length - 1] = { role: "assistant", content: n[n.length - 1].content + e.text };
          return n;
        });
      } else if (e.type === "error") {
        setErr(e.detail);
      }
    }, signal);
  };

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setErr(""); setNote("");
    const base: Turn[] = [...turns, { role: "user", content: text }];
    setTurns([...base, { role: "assistant", content: "" }]);
    setInput("");
    setStreaming(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const append = (t: string) => setTurns((prev) => {
      const n = [...prev];
      n[n.length - 1] = { role: "assistant", content: n[n.length - 1].content + t };
      return n;
    });
    /**
     * 正文分段边界：门禁打回 = 整篇重写、旧稿作废（不清就是同一段连出两遍）；
     * 其余只是"这段还没说完"，断个段就行。判据见 answerResetDiscards。
     */
    const boundary = (reason?: string) => setTurns((prev) => {
      const n = [...prev];
      const cur = n[n.length - 1].content;
      n[n.length - 1] = {
        role: "assistant",
        content: answerResetDiscards(reason) ? "" : (cur ? cur + "\n\n" : cur),
      };
      return n;
    });

    let got = false;
    let newSession = "";
    try {
      await ivyeaAgentChatStream({
        message: text,
        session_id: currentId,
        system: SYSTEM_PROMPT,
        use_tools: false,          // 这一页的定位就是"不带工具"
        inject_retrieval: false,   // 也不塞知识检索，要检索请用知识库那一页
        plan_mode: true,
        persist: true,
        source: "assistant",
      }, {
        onStart: (d) => { if (d?.session_id) newSession = String(d.session_id); },
        onToken: (t) => { got = true; append(t); },
        onAnswerReset: (d) => boundary(d?.reason),
        onError: (d) => { if (!got) throw new Error(d?.detail || "请求失败"); setErr(String(d?.detail || "")); },
      }, { signal: ctrl.signal });
      if (newSession && newSession !== currentId) {
        setCurrentId(newSession);
        navigate(`/assistant?session=${encodeURIComponent(newSession)}`, { replace: true });
      }
      notifyConsoleSessionsChanged();
      void refreshSessions();
    } catch (e: any) {
      if (e?.name === "AbortError") { setStreaming(false); return; }
      if (got) { setErr(e?.message || "请求失败"); setStreaming(false); return; }
      // 一个字都没出来 → 多半是 agent 没起。退回老通道，别让这一页跟着躺下。
      setNote("IvyeaAgent 未就绪，本轮走了备用通道（这一轮不会存进会话库）");
      try {
        await sendLegacy(base, ctrl.signal);
      } catch (e2: any) {
        if (e2?.name !== "AbortError") setErr(e2?.message || "请求失败");
      }
    } finally {
      setStreaming(false);
    }
  };

  const stop = () => { abortRef.current?.abort(); setStreaming(false); };

  const startNew = () => {
    if (streaming) return;
    setCurrentId("");
    setTurns([]);
    setErr(""); setNote("");
    setHistoryOpen(false);
    navigate("/assistant", { replace: true });
    try { localStorage.removeItem(STORAGE); } catch { /* 老缓存清掉，免得下次误读 */ }
  };

  const loadSession = (s: ConsoleSessionRow) => {
    if (streaming) return;
    setHistoryOpen(false);
    navigate(`/assistant?session=${encodeURIComponent(s.id)}`);
  };

  const deleteSession = async (id: string, e: MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm("删除这条对话？内容会一并删除，且无法恢复。")) return;
    setSessions((prev) => prev.filter((s) => s.id !== id));
    try {
      await consoleSessionDelete(id);
      notifyConsoleSessionsChanged();
    } catch {
      void refreshSessions();
    }
    if (id === currentId) startNew();
  };

  return (
    <div className="market-page asst-page">
      {/* Bottom sheet backdrop */}
      {historyOpen && (
        <div
          style={{ position: "fixed", inset: 0, zIndex: 897, background: "rgba(0,0,0,.5)" }}
          onClick={() => setHistoryOpen(false)}
        />
      )}

      {/* Bottom sheet — always rendered so transition plays on close */}
      <div style={{
        position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 898,
        maxHeight: "62vh", background: "var(--bg1)",
        borderRadius: "16px 16px 0 0",
        display: "flex", flexDirection: "column",
        boxShadow: "0 -4px 32px rgba(0,0,0,.4)",
        transform: historyOpen ? "translateY(0)" : "translateY(110%)",
        transition: "transform .25s cubic-bezier(.4,0,.2,1)",
      }}>
        {/* Drag handle */}
        <div style={{ display: "flex", justifyContent: "center", padding: "10px 0 4px", flexShrink: 0 }}>
          <div style={{ width: 36, height: 4, borderRadius: 2, background: "var(--b2)" }} />
        </div>
        {/* Sheet header */}
        <div style={{ display: "flex", alignItems: "center", padding: "2px 16px 10px", flexShrink: 0, borderBottom: "1px solid var(--b)" }}>
          <span style={{ fontSize: "var(--fs-14)", fontWeight: 600, color: "var(--t)", flex: 1 }}>历史对话</span>
          <button className="tbtn" onClick={startNew} disabled={streaming} style={{ marginRight: 8 }}>＋ 新对话</button>
          <button
            onClick={() => setHistoryOpen(false)}
            className="asst-icon-btn"
            style={{ background: "none", border: "none", cursor: "pointer", color: "var(--t3)", fontSize: 18, padding: "0 2px", lineHeight: 1 }}
          >✕</button>
        </div>
        {/* Session list */}
        <div style={{ overflowY: "auto", flex: 1 }}>
          {sessions.length === 0
            ? <div style={{ padding: "28px 16px", fontSize: "var(--fs-13)", color: "var(--t3)", textAlign: "center" }}>暂无历史对话</div>
            : sessions.map(s => (
              <div
                key={s.id}
                onClick={() => loadSession(s)}
                style={{
                  padding: "12px 16px", cursor: "pointer", borderBottom: "1px solid var(--b)",
                  background: s.id === currentId ? "color-mix(in srgb, var(--acc) 10%, transparent)" : undefined,
                  display: "flex", alignItems: "center", gap: 10, transition: "background .12s",
                }}
                onMouseEnter={e => { if (s.id !== currentId) (e.currentTarget as HTMLDivElement).style.background = "var(--bg3)"; }}
                onMouseLeave={e => { if (s.id !== currentId) (e.currentTarget as HTMLDivElement).style.background = ""; }}
              >
                <div style={{
                  width: 32, height: 32, borderRadius: 8, flexShrink: 0,
                  background: s.id === currentId ? "color-mix(in srgb, var(--acc) 20%, transparent)" : "var(--bg3)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: "var(--fs-14)", color: s.id === currentId ? "var(--acc)" : "var(--t3)",
                }}>✦</div>
                <div style={{ flex: 1, overflow: "hidden" }}>
                  <div style={{
                    fontSize: "var(--fs-13)", fontWeight: s.id === currentId ? 600 : 400,
                    color: s.id === currentId ? "var(--acc)" : "var(--t)",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>{s.title || s.preview || s.id}</div>
                  <div style={{ fontSize: "var(--fs-11)", color: "var(--t3)", marginTop: 2 }}>
                    {s.updated ? new Date(s.updated * 1000).toLocaleDateString("zh-CN") : "—"} · {s.turns}轮对话
                  </div>
                </div>
                <button
                  onClick={(e) => deleteSession(s.id, e)}
                  className="asst-icon-btn"
                  style={{ background: "none", border: "none", cursor: "pointer", color: "var(--t3)", fontSize: "var(--fs-16)", padding: "4px 6px", lineHeight: 1, flexShrink: 0, borderRadius: 4 }}
                  title="删除"
                >✕</button>
              </div>
            ))
          }
        </div>
      </div>

      {/* Header */}
      <div className="market-header">
        <span className="market-title"><span className="market-title-icon">✦</span> AI 写作 / 问答</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          <button className="tbtn" onClick={() => setHistoryOpen(o => !o)}>
            ≡ 历史{sessions.length > 0 ? ` (${sessions.length})` : ""}
          </button>
          <button className="tbtn" onClick={startNew} disabled={streaming || turns.length === 0}>＋ 新对话</button>
        </div>
      </div>

      {/* Chat body + input (full width, flex column) */}
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: 12, overflow: "hidden" }}>
        <div ref={bodyRef} className="asst-body">
          {turns.length === 0 && (
            <div className="market-empty">
              <div className="market-empty-icon">✦</div>
              <div className="market-empty-title">问我任何问题，或让我帮你写文档/文案</div>
              <div className="market-empty-hint">纯文本 AI，不调用任何工具 · 对话会存进会话库，左栏可随时翻回</div>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className={"asst-msg " + t.role}>
              <div className="asst-role">{t.role === "user" ? "我" : "AI"}</div>
              <div className="asst-content">
                {t.role === "assistant"
                  ? (t.content ? <MarkdownReport text={t.content} /> : <span className="cursor-blink">▋</span>)
                  : <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.7 }}>{t.content}</div>}
              </div>
            </div>
          ))}
        </div>

        {note && <div className="market-note" style={{ flexShrink: 0 }}>{note}</div>}
        {err && <div className="market-error" style={{ flexShrink: 0 }}>{err}</div>}

        <div className="market-input-row">
          <textarea
            className="market-query-input"
            style={{ resize: "none", height: 44, paddingTop: 10 }}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
            placeholder="输入问题或写作要求，Enter 发送（Shift+Enter 换行）"
            disabled={streaming}
          />
          {streaming
            ? <button className="market-btn market-btn-stop" onClick={stop}>停止</button>
            : <button className="market-btn market-btn-submit" onClick={send} disabled={!input.trim()}>发送</button>}
        </div>
      </div>
    </div>
  );
}
