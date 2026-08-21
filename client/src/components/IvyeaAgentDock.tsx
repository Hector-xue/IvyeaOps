import { type CSSProperties, type PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { stripInjected } from "../lib/stripInjected";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import {
  ArrowLeft,
  Bot,
  Check,
  Database,
  FileText,
  History,
  Loader2,
  MessageCircle,
  Plus,
  Power,
  RefreshCw,
  Search,
  Send,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  answerResetDiscards,
  ivyeaAgentChat,
  ivyeaAgentChatStream,
  ivyeaAgentStatus,
  ivyeaAwaitSessionAnswer,
  ivyeaChatSession,
  ivyeaChatSessionDelete,
  ivyeaChatSessions,
  ivyeaServiceStart,
  ivyeaKnowledgeApplyText,
  ivyeaKnowledgeFiles,
  ivyeaKnowledgeImportDirectory,
  ivyeaKnowledgeSearch,
  ivyeaKnowledgeUpload,
  ivyeaKnowledgeWatchlist,
  ivyeaRetrievalEmbeddings,
  ivyeaRetrievalStatus,
  ivyeaRetrievalSync,
  type IvyeaAgentStatus,
  type IvyeaChatSession,
  type KnowledgeDirectoryImport,
  type KnowledgeCard,
  type KnowledgeUpload,
  type RetrievalEmbeddings,
  type RetrievalStatus,
} from "../api/ivyeaAgent";
import "../styles/ivyea-agent-dock.css";
import { errText } from "../lib/errText";
import { useStickToBottom } from "../lib/useStickToBottom";

// 只剩对话。悬浮球的独有价值是**不离开当前页面就能问一句**（在 Listing 干活时查条
// 规则，切走再切回来是有成本的）；而它原来的「知识库」「状态」两个 tab 分别重复了
// 知识库工作台和系统配置 —— 同一件事有两个入口，两边还会各自演化。
type Tab = "chat";
type HistoryView = "chat" | "list" | "detail";
type ChatMessage = { role: "user" | "assistant" | "system"; text: string };
type FabPosition = { x: number; y: number };

const FAB_SIZE = 52;
const FAB_MARGIN = 12;

function fmtSize(n = 0) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function apiErrorMessage(e: any, fallback: string) {
  if (e?.response?.status === 404) {
    return "IvyeaAgent 接口未加载，请重启 IvyeaOps 后端后再试。";
  }
  if (e?.code === "ECONNABORTED" || String(e?.message || "").toLowerCase().includes("timeout")) {
    return "请求等待超时，模型可能仍在生成或网络已中断，请稍后重试。";
  }
  return errText(e, fallback);
}

function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.ceil((seconds % 3600) / 60);
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`;
  return `${minutes} 分钟`;
}

function usageLimitSeconds(text: string) {
  const match = text.match(/"resets_in_seconds"\s*:\s*(\d+)/);
  return match ? Number(match[1]) : 0;
}

function isUsageLimitError(text: string) {
  return /usage_limit_reached|usage limit has been reached|HTTP 429/i.test(text);
}

function agentErrorMessage(detail: string) {
  const raw = detail || "模型暂不可用";
  if (isUsageLimitError(raw)) {
    const wait = formatDuration(usageLimitSeconds(raw));
    return `当前 OpenAI Codex OAuth 账号额度已用完${wait ? `，约 ${wait} 后重置` : ""}。可以先切换其它模型，或使用知识库搜索、文本入库等本地能力。`;
  }
  return raw.replace(/^Codex Responses stream HTTP \d+:\s*/i, "").trim() || "模型暂不可用";
}

function isKnowledgeQuestion(text: string) {
  return /知识库|知识|embedding|向量|索引|检索|gbrain/i.test(text);
}

function cleanSessionContent(role: string, content: string) {
  // 用全站共用的那份剥离清单（lib/stripInjected）。这里原来自己写了半份，只认
  // "[Ivyea 本地知识检索]" —— 技能说明书、任务范围锁定、附图的文字版都会原样摆
  // 进用户那侧的气泡。各写一份必然漂，agent 侧 transcript.py 顶上那段注释说的
  // 就是这件事。
  if (role === "user") return stripInjected(content);
  return content.trim();
}

function formatSessionTime(ts?: number) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function sessionTitle(item?: IvyeaChatSession | null) {
  const title = (item?.preview || item?.id || "未命名会话").replace(/\s+/g, " ").trim();
  return title.length > 56 ? `${title.slice(0, 56)}...` : title;
}

function clampFabPosition(x: number, y: number): FabPosition {
  if (typeof window === "undefined") return { x, y };
  const maxX = Math.max(FAB_MARGIN, window.innerWidth - FAB_SIZE - FAB_MARGIN);
  const maxY = Math.max(FAB_MARGIN, window.innerHeight - FAB_SIZE - FAB_MARGIN);
  return {
    x: Math.min(Math.max(FAB_MARGIN, x), maxX),
    y: Math.min(Math.max(FAB_MARGIN, y), maxY),
  };
}

export default function IvyeaAgentDock() {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [fabPos, setFabPos] = useState<FabPosition | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      const raw = window.localStorage.getItem("ivyea-agent-fab-pos");
      if (!raw) return null;
      const parsed = JSON.parse(raw) as Partial<FabPosition>;
      if (!Number.isFinite(parsed.x) || !Number.isFinite(parsed.y)) return null;
      return clampFabPosition(Number(parsed.x), Number(parsed.y));
    } catch {
      return null;
    }
  });
  const [tab, setTab] = useState<Tab>("chat");
  const [status, setStatus] = useState<IvyeaAgentStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [sessions, setSessions] = useState<IvyeaChatSession[]>([]);
  const [historyView, setHistoryView] = useState<HistoryView>("chat");
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [sending, setSending] = useState(false);
  const [liveStatus, setLiveStatus] = useState("");
  const [error, setError] = useState("");
  const [filesLoading, setFilesLoading] = useState(false);
  const [cards, setCards] = useState<KnowledgeCard[]>([]);
  const [uploads, setUploads] = useState<KnowledgeUpload[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any[]>([]);
  const [searching, setSearching] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadTags, setUploadTags] = useState("");
  const [uploadSourceType, setUploadSourceType] = useState("user");
  const [uploading, setUploading] = useState(false);
  const [watchlistCount, setWatchlistCount] = useState<number | null>(null);
  const [legacyImport, setLegacyImport] = useState<KnowledgeDirectoryImport["import"] | null>(null);
  const [legacyLoading, setLegacyLoading] = useState(false);
  const [retrievalStatus, setRetrievalStatus] = useState<RetrievalStatus["index"] | null>(null);
  const [embeddings, setEmbeddings] = useState<RetrievalEmbeddings["embeddings"] | null>(null);
  const [syncingRetrieval, setSyncingRetrieval] = useState(false);
  const [textTitle, setTextTitle] = useState("");
  const [textBody, setTextBody] = useState("");
  const [textTags, setTextTags] = useState("");
  const [savingText, setSavingText] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerFileRef = useRef<HTMLInputElement>(null);
  const [attaching, setAttaching] = useState(false);
  // 对话区「添加文件」：把选中的文件上传到知识库（confirm+rebuild），并在输入框提示，
  // 之后就能直接问 Agent 该文件内容（复用现有 knowledge/upload）。
  const attachFileToChat = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setAttaching(true);
    try {
      await ivyeaKnowledgeUpload({ file: f, title: f.name, sourceType: "user", tags: "", confirm: true, rebuild: true });
      setInput((prev) => (prev ? prev + "\n" : "") + `（已添加文件「${f.name}」到知识库，你可以问我它的内容）`);
      await loadKnowledge();
    } catch (err: any) {
      setError(apiErrorMessage(err, "添加文件失败"));
    } finally {
      setAttaching(false);
      if (composerFileRef.current) composerFileRef.current.value = "";
    }
  };
  const fabDragRef = useRef({
    dragging: false,
    moved: false,
    suppressClick: false,
    startX: 0,
    startY: 0,
    originX: 0,
    originY: 0,
  });

  const online = !!status?.available;
  const statusTone = status?.available ? "ok" : status?.ok === false ? "bad" : "idle";
  const currentModel = status?.health?.model?.label || status?.health?.model?.model || "未连接";
  const activeSession = sessions.find((item) => item.id === sessionId);
  const activeSessionName = activeSession ? sessionTitle(activeSession) : "当前会话";
  const fabStyle: CSSProperties | undefined = fabPos
    ? { left: fabPos.x, top: fabPos.y, right: "auto", bottom: "auto" }
    : undefined;
  const opsContext = useMemo(() => ({
    pathname: location.pathname,
    search: location.search,
    board: location.pathname.replace(/^\/+/, "") || "home",
  }), [location.pathname, location.search]);

  const loadStatus = async () => {
    setLoadingStatus(true);
    try {
      const s = await ivyeaAgentStatus();
      setStatus(s);
    } catch (e: any) {
      setStatus({ ok: false, available: false, base_url: "", token_configured: false, error: apiErrorMessage(e, "状态加载失败") });
    } finally {
      setLoadingStatus(false);
    }
  };

  const loadKnowledge = async () => {
    setFilesLoading(true);
    try {
      const [f, w, legacy, retrieval, embedding] = await Promise.all([
        ivyeaKnowledgeFiles(),
        ivyeaKnowledgeWatchlist(),
        ivyeaKnowledgeImportDirectory({ confirm: false, maxFiles: 1000 }),
        ivyeaRetrievalStatus(),
        ivyeaRetrievalEmbeddings(),
      ]);
      setCards(f.cards || []);
      setUploads(f.history || []);
      setWatchlistCount(w.sources?.length || 0);
      setLegacyImport(legacy.import);
      setRetrievalStatus(retrieval.index || null);
      setEmbeddings(embedding.embeddings || null);
    } catch (e: any) {
      setError(apiErrorMessage(e, "知识库加载失败"));
    } finally {
      setFilesLoading(false);
    }
  };

  useEffect(() => {
    loadStatus();
  }, []);

  useEffect(() => {
    if (!open) return;
    loadStatus();
    ivyeaChatSessions(30).then((data) => setSessions(data.sessions || [])).catch(() => {});
  }, [open, tab]);

  // 跟随滚动按用户意图判（wheel/touch/键），不按滚动位置判 —— 位置判据在流式
  // 输出下必输，用户往上翻的那一下会被下一个 token 抢先拍回底部。
  useStickToBottom(messagesRef, [messages, sending]);

  useEffect(() => {
    const onResize = () => setFabPos((prev) => {
      if (!prev) return prev;
      const next = clampFabPosition(prev.x, prev.y);
      window.localStorage.setItem("ivyea-agent-fab-pos", JSON.stringify(next));
      return next;
    });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const onFabPointerDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    fabDragRef.current = {
      dragging: true,
      moved: false,
      suppressClick: false,
      startX: e.clientX,
      startY: e.clientY,
      originX: fabPos?.x ?? rect.left,
      originY: fabPos?.y ?? rect.top,
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
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // Pointer capture may already be released by the browser.
    }
    if (drag.moved) {
      const rect = e.currentTarget.getBoundingClientRect();
      const next = clampFabPosition(rect.left, rect.top);
      setFabPos(next);
      window.localStorage.setItem("ivyea-agent-fab-pos", JSON.stringify(next));
    }
  };

  const onFabClick = () => {
    if (fabDragRef.current.suppressClick) {
      fabDragRef.current.suppressClick = false;
      return;
    }
    setOpen((v) => !v);
  };

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setError("");
    setSending(true);
    setMessages((prev) => [...prev, { role: "user", text }, { role: "assistant", text: "" }]);
    const appendAssistant = (chunk: string, replace = false) => {
      if (!chunk) return;
      setMessages((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i -= 1) {
          if (next[i].role === "assistant") {
            next[i] = { ...next[i], text: replace ? chunk : `${next[i].text}${chunk}` };
            return next;
          }
        }
        return [...next, { role: "assistant", text: chunk }];
      });
    };
    /**
     * 正文分段边界：门禁打回 = 整篇重写、旧稿作废；其余只是"这段还没说完"，
     * 断个段就行（判据见 answerResetDiscards）。
     */
    const answerBoundary = (reason?: string) => {
      const drop = answerResetDiscards(reason);
      setMessages((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i -= 1) {
          if (next[i].role === "assistant") {
            const cur = next[i].text;
            next[i] = { ...next[i], text: drop ? "" : (cur ? `${cur}\n\n` : cur) };
            return next;
          }
        }
        return next;
      });
    };
    const replaceAssistantWithSystem = (message: string) => {
      setMessages((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i -= 1) {
          if (next[i].role === "assistant") {
            next[i] = { role: "system", text: message };
            return next;
          }
        }
        return [...next, { role: "system", text: message }];
      });
    };
    const replaceAssistant = (message: string) => {
      setMessages((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i -= 1) {
          if (next[i].role === "assistant") {
            next[i] = { role: "assistant", text: message };
            return next;
          }
        }
        return [...next, { role: "assistant", text: message }];
      });
    };
    // max_steps 不再从前端限定：交给 agent serve 的 config 默认（200），
    // 复杂运营任务不会再动辄撞"工具步数上限"。
    let liveSid = sessionId || "";
    const sentAt = Math.floor(Date.now() / 1000);
    try {
      let finalText = "";
      await ivyeaAgentChatStream(
        {
          message: text,
          session_id: sessionId || undefined,
          ops_context: opsContext,
          plan_mode: true,
          persist: true,
          inject_retrieval: true,
        },
        {
          onStart: (data) => {
            if (data.session_id) {
              liveSid = data.session_id;
              setSessionId(data.session_id);
            }
          },
          onToken: (chunk) => {
            if (!finalText) setLiveStatus("");
            finalText += chunk;
            appendAssistant(chunk);
          },
          onAnswerReset: (d) => {
            if (answerResetDiscards(d?.reason)) finalText = "";
            answerBoundary(d?.reason);
          },
          onEvent: (data) => {
            // 工具叙事（正在调研/检索/生成…）：流式正文没来之前展示在"处理中"位置，
            // 长工具链不再是一动不动的转圈。
            const line = String(data?.text || "").trim().split("\n").pop() || "";
            if (line && !finalText) setLiveStatus(line.slice(0, 120));
          },
          onFinal: (data) => {
            if (data.session_id) setSessionId(data.session_id);
            const textOut = String(data.text || "");
            // final.text 是引证门通过后的规范文本，始终以它为准整体替换——
            // 流式期间的中间草稿（引证重写前）不留脏文本。
            if (textOut) appendAssistant(textOut, true);
          },
          onError: (data) => {
            // 抛给统一的 catch 恢复路径。区分两类错误：serve 显式报错（model_error
            // 等）= 轮次已死，不能傻等；bridge_error/传输断链 = serve 可能仍在跑。
            const err: any = new Error(String(data.detail || data.error || "模型暂不可用"));
            err.explicit = data?.error !== "bridge_error";
            throw err;
          },
        },
      );
    } catch (e: any) {
      const raw = String(e?.message || "");
      if (isUsageLimitError(raw) && isKnowledgeQuestion(text)) {
        replaceAssistant(await localKnowledgeAnswer(text));
      } else if (e?.explicit) {
        // serve 显式宣告轮次失败（模型报错/额度不足等）：直接展示，绝不轮询等待。
        replaceAssistantWithSystem(agentErrorMessage(raw));
      } else if (liveSid) {
        // 传输断链，但 serve 端的轮次独立继续执行并会把结果落盘——
        // 不重发（重发会把同一轮再跑一遍），改为等待落盘的回答。
        replaceAssistant("连接中断，但模型仍在后台生成，正在等待结果…");
        const answer = await ivyeaAwaitSessionAnswer(liveSid, sentAt);
        if (answer) {
          replaceAssistant(answer);
        } else {
          replaceAssistantWithSystem("这轮生成时间较长，后台仍在处理；完成后可在「历史会话」里查看这条回答。");
        }
      } else {
        // 流从未建立（后端 503/旧版本无流式接口）：走一次阻塞式请求兜底。
        try {
          const res = await ivyeaAgentChat({
            message: text,
            session_id: sessionId || undefined,
            ops_context: opsContext,
            plan_mode: true,
            persist: true,
            inject_retrieval: true,
          });
          if (res.session_id) setSessionId(res.session_id);
          if (!res.ok) {
            const detail = res.detail || res.error || "模型暂不可用";
            if (isUsageLimitError(detail) && isKnowledgeQuestion(text)) {
              replaceAssistant(await localKnowledgeAnswer(text));
            } else {
              replaceAssistantWithSystem(agentErrorMessage(detail));
            }
          } else {
            appendAssistant(res.text || "已完成。", true);
          }
        } catch (fallbackError: any) {
          replaceAssistantWithSystem(apiErrorMessage(fallbackError, apiErrorMessage(e, "发送失败")));
        }
      }
    } finally {
      setSending(false);
      setLiveStatus("");
      ivyeaChatSessions(30).then((data) => setSessions(data.sessions || [])).catch(() => {});
    }
  };

  const localKnowledgeAnswer = async (question: string) => {
    try {
      const [f, retrieval, embedding, search] = await Promise.all([
        ivyeaKnowledgeFiles(),
        ivyeaRetrievalStatus(),
        ivyeaRetrievalEmbeddings(),
        ivyeaKnowledgeSearch(question, 20),
      ]);
      const allCards = f.cards || [];
      const adPattern = /广告|amazon.?ads|sponsored|campaign|acos|bid|budget|keyword|placement|targeting|search.?term/i;
      const adCards = allCards.filter((card) => adPattern.test([
        card.id,
        card.title,
        card.path,
        card.source_type,
        ...(card.tags || []),
      ].join(" ")));
      setCards(allCards);
      setUploads(f.history || []);
      setRetrievalStatus(retrieval.index || null);
      setEmbeddings(embedding.embeddings || null);
      setResults(search.results || []);
      const hitCount = search.results?.length || 0;
      return [
        "Codex 当前额度已用完，这个问题我先用本地知识库直接回答：",
        "",
        `- 知识卡总数：${allCards.length}`,
        `- 广告相关知识卡：约 ${adCards.length}`,
        `- 本次问题检索命中：${hitCount}`,
        `- 检索索引：${retrieval.index?.chunks ?? "-"} chunks，${retrieval.index?.needs_rebuild ? "需要同步" : "已同步"}`,
        `- Embedding：${embedding.embeddings?.semantic_enabled ? "dense" : "local sparse"}（${embedding.embeddings?.active_backend || "-"}）`,
        "",
        "需要完整内容时，可以切到「知识库」页搜索，或等 Codex 额度重置后让 Agent 做总结分析。",
      ].join("\n");
    } catch (e: any) {
      return agentErrorMessage(apiErrorMessage(e, "Codex 额度已用完，本地知识库兜底查询也失败了。"));
    }
  };

  const loadSessions = async () => {
    setLoadingHistory(true);
    setError("");
    try {
      const data = await ivyeaChatSessions(30);
      setSessions(data.sessions || []);
      setHistoryView("list");
    } catch (e: any) {
      setError(apiErrorMessage(e, "历史记录加载失败"));
    } finally {
      setLoadingHistory(false);
    }
  };

  const openSession = async (sid: string) => {
    setLoadingHistory(true);
    setError("");
    try {
      const data = await ivyeaChatSession(sid);
      setSessionId(data.session.id);
      setMessages((data.session.messages || [])
        .filter((msg) => msg.role === "user" || msg.role === "assistant")
        .map((msg) => ({
          role: msg.role as "user" | "assistant",
          text: cleanSessionContent(msg.role, msg.content),
        }))
        .filter((msg) => msg.text));
      setHistoryView("detail");
      setTab("chat");
    } catch (e: any) {
      setError(apiErrorMessage(e, "打开历史会话失败"));
    } finally {
      setLoadingHistory(false);
    }
  };

  const newSession = () => {
    setSessionId("");
    setMessages([]);
    setHistoryView("chat");
    setTab("chat");
  };

  // 继续这条历史会话：保留已加载的 sessionId + messages，切回对话视图即可接着聊。
  const continueSession = () => {
    setHistoryView("chat");
    setTab("chat");
  };

  const deleteSession = async (sid: string) => {
    if (!window.confirm("删除这条历史会话？不可恢复。")) return;
    try {
      await ivyeaChatSessionDelete(sid);
      setSessions((prev) => prev.filter((s) => s.id !== sid));
      if (sid === sessionId) newSession();
    } catch (e: any) {
      setError(apiErrorMessage(e, "删除会话失败"));
    }
  };

  const startService = async () => {
    setLoadingStatus(true);
    setError("");
    try {
      await ivyeaServiceStart();
      await new Promise((r) => setTimeout(r, 1500));  // give serve a moment to bind
      await loadStatus();
    } catch (e: any) {
      setError(apiErrorMessage(e, "启动本地服务失败"));
    } finally {
      setLoadingStatus(false);
    }
  };

  const runSearch = async () => {
    const q = query.trim();
    if (!q || searching) return;
    setSearching(true);
    setError("");
    try {
      const data = await ivyeaKnowledgeSearch(q, 8);
      setResults(data.results || []);
    } catch (e: any) {
      setError(apiErrorMessage(e, "搜索失败"));
    } finally {
      setSearching(false);
    }
  };

  const uploadFile = async () => {
    if (!file || uploading) return;
    setUploading(true);
    setError("");
    try {
      await ivyeaKnowledgeUpload({
        file,
        title: uploadTitle || file.name,
        sourceType: uploadSourceType,
        tags: uploadTags,
        confirm: true,
        rebuild: true,
      });
      setFile(null);
      setUploadTitle("");
      setUploadTags("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      await loadKnowledge();
    } catch (e: any) {
      setError(apiErrorMessage(e, "保存到知识库失败"));
    } finally {
      setUploading(false);
    }
  };

  const scanLegacyGbrain = async (confirm: boolean) => {
    setLegacyLoading(true);
    setError("");
    try {
      const data = await ivyeaKnowledgeImportDirectory({ confirm, rebuild: true, maxFiles: 1000 });
      setLegacyImport(data.import);
      if (confirm) {
        const f = await ivyeaKnowledgeFiles();
        setCards(f.cards || []);
        setUploads(f.history || []);
      }
    } catch (e: any) {
      setError(apiErrorMessage(e, confirm ? "迁移旧知识库失败" : "扫描旧知识库失败"));
    } finally {
      setLegacyLoading(false);
    }
  };

  const syncRetrieval = async () => {
    if (syncingRetrieval) return;
    setSyncingRetrieval(true);
    setError("");
    try {
      const data = await ivyeaRetrievalSync();
      setRetrievalStatus(data.index || data);
      const embedding = await ivyeaRetrievalEmbeddings();
      setEmbeddings(embedding.embeddings || null);
    } catch (e: any) {
      setError(apiErrorMessage(e, "索引同步失败"));
    } finally {
      setSyncingRetrieval(false);
    }
  };

  const saveTextKnowledge = async () => {
    const body = textBody.trim();
    if (!body || savingText) return;
    setSavingText(true);
    setError("");
    try {
      const data = await ivyeaKnowledgeApplyText({
        title: textTitle.trim() || body.slice(0, 32),
        body,
        tags: textTags,
        sourceType: "user",
        rebuild: true,
      });
      if (!data.ok) {
        setError(data.result?.error || "保存文本知识失败");
        return;
      }
      setTextTitle("");
      setTextBody("");
      setTextTags("");
      await loadKnowledge();
    } catch (e: any) {
      setError(apiErrorMessage(e, "保存文本知识失败"));
    } finally {
      setSavingText(false);
    }
  };

  const askWithKnowledge = () => {
    const q = query.trim();
    if (!q) return;
    setInput(`请结合 IvyeaAgent 本地知识库回答：${q}`);
    setTab("chat");
  };

  const visibleUploads = useMemo(() => uploads.slice(0, 8), [uploads]);
  const visibleCards = useMemo(() => cards.slice(0, 8), [cards]);

  // 挂到 document.body（脱离 #root 的 zoom）：FAB/面板是 position:fixed 悬浮层，放在 zoom 里会
  // 导致 fixed 相对被缩放的 #root 定位、且拖拽坐标错乱（拉不到真边缘）。挂 body 后回到纯视口
  // 坐标系，拖拽与 zoom 前一致；主题/字体变量在 documentElement 上，body 子树照样继承。
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

      {open && (
        <section className="ivyea-agent-panel" aria-label="Ivyea Agent">
          <header className="ivyea-agent-head">
            <div className="ivyea-agent-brand">
              <span className="ivyea-agent-mark"><Bot size={17} /></span>
              <div>
                <div className="ivyea-agent-title">Ivyea Agent</div>
                <div className="ivyea-agent-sub">{online ? currentModel : status?.error || "本地服务未连接"}</div>
              </div>
            </div>
            <div className="ivyea-agent-head-actions">
              {tab === "chat" && (
                <>
                  <button className="ivyea-agent-icon-btn" onClick={newSession} title="新会话">
                    <Plus size={16} />
                  </button>
                  <button className="ivyea-agent-icon-btn" onClick={loadSessions} disabled={loadingHistory} title="历史会话">
                    {loadingHistory ? <Loader2 size={15} className="spin" /> : <History size={15} />}
                  </button>
                </>
              )}
              <button className="ivyea-agent-icon-btn" onClick={loadStatus} disabled={loadingStatus} title="刷新">
                <RefreshCw size={15} className={loadingStatus ? "spin" : ""} />
              </button>
              <button className="ivyea-agent-icon-btn" onClick={() => setOpen(false)} title="关闭">
                <X size={16} />
              </button>
            </div>
          </header>

          {error && <div className="ivyea-agent-error">{error}</div>}

          {tab === "chat" && (
            <div className="ivyea-agent-chat">
              {historyView !== "chat" ? (
                <div className="ivyea-agent-history-view">
                  <div className="ivyea-agent-history-head">
                    {historyView === "detail" ? (
                      <button className="ivyea-agent-mini-btn" onClick={() => setHistoryView("list")}>
                        <ArrowLeft size={13} />返回历史
                      </button>
                    ) : (
                      <span>历史会话</span>
                    )}
                    {historyView === "detail" && <span className="ivyea-agent-history-title">{activeSessionName}</span>}
                    {historyView === "detail" && (
                      <button className="ivyea-agent-mini-btn" onClick={continueSession} style={{ marginLeft: "auto" }}>
                        <Send size={12} />继续对话
                      </button>
                    )}
                  </div>
                  {historyView === "list" ? (
                    <div className="ivyea-agent-history-list">
                      {sessions.length === 0 ? (
                        <div className="ivyea-agent-history-empty">暂无历史会话</div>
                      ) : sessions.map((item) => (
                        <div key={item.id} className="ivyea-agent-history-row">
                          <button
                            className="ivyea-agent-history-item"
                            onClick={() => openSession(item.id)}
                            title={`${sessionTitle(item)}${formatSessionTime(item.updated) ? ` · ${formatSessionTime(item.updated)}` : ""}`}
                          >
                            <span>{sessionTitle(item)}</span>
                            <em>{formatSessionTime(item.updated) || "最近"} · {item.turns || 0} 轮</em>
                          </button>
                          <button
                            className="ivyea-agent-history-del"
                            onClick={() => deleteSession(item.id)}
                            title="删除会话"
                            aria-label="删除会话"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="ivyea-agent-messages ivyea-agent-history-messages" ref={messagesRef}>
                      {messages.length === 0 ? (
                        <div className="ivyea-agent-empty">这个会话暂无内容</div>
                      ) : messages.map((m, idx) => (
                        <div key={idx} className={`ivyea-agent-msg ${m.role}`}>
                          <div>{m.text}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <>
                  <div className="ivyea-agent-messages" ref={messagesRef}>
                    {messages.length === 0 && (
                      <div className="ivyea-agent-empty">
                        <Bot size={22} />
                        <span>亚马逊运营、Listing、广告、知识库和代码任务都可以问。</span>
                      </div>
                    )}
                    {messages.map((m, idx) => {
                      // The assistant bubble is pushed empty at send time and filled by
                      // streaming tokens. Before the first token it shows an inline
                      // "处理中" spinner (one box, not two); a stray empty bubble left
                      // after sending finishes is hidden.
                      const pendingAssistant = m.role === "assistant" && !m.text;
                      if (pendingAssistant && !sending) return null;
                      return (
                        <div key={idx} className={`ivyea-agent-msg ${m.role}`}>
                          {pendingAssistant
                            ? <span className="ivyea-agent-typing"><Loader2 size={14} className="spin" /> {liveStatus || "处理中..."}</span>
                            : <div>{m.text}</div>}
                        </div>
                      );
                    })}
                  </div>
                  <div className="ivyea-agent-composer">
                    <input ref={composerFileRef} type="file" style={{ display: "none" }} onChange={attachFileToChat} />
                    <button title="添加文件（上传到知识库，可直接问它的内容）"
                            onClick={() => composerFileRef.current?.click()} disabled={attaching || sending}>
                      <Upload size={16} />
                    </button>
                    <textarea
                      rows={1}
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          sendMessage();
                        }
                      }}
                      placeholder="问 Ivyea Agent..."
                    />
                    <button onClick={sendMessage} disabled={!input.trim() || sending}><Send size={16} /></button>
                  </div>
                </>
              )}
            </div>
          )}

</section>
      )}
    </>,
    document.body,
  );
}
