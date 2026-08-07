/**
 * 任务台 —— 全站的「一个任务入口」。
 *
 * 它把散在 25 个板块里的能力收到一句话后面：说需求 → Agent 匹配技能 → 调板块
 * 能力/MCP 拿真实数据 → 出结论 → 需要动线上数据时停下来问你。板块本身一个没动，
 * 只是从"用户要自己找的入口"变成"Agent 可以调用的能力"。
 *
 * 流式契约见 api/ivyeaAgent.ts：
 *   start / token / final / error          —— 一直都有
 *   step / skill_match / permission_request —— agent serve ≥ v1.9 才有；
 *                                              没有时自动退回自由文本叙述，不白屏。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../App";
import { MarkdownReport } from "../../lib/reportFormat";
import { ToastProvider, useToast } from "../../components/toast";
import { CONSOLE_NEW_EVENT, sceneChips } from "../../lib/navRegistry";
import {
  formatMs,
  mergeStep,
  noteStep,
  primeOpsToolLabels,
  stepFromEvent,
  type ConsoleStep,
} from "../../lib/stepLabels";
import StepTimeline, { type MatchedSkill } from "../../components/console/StepTimeline";
import ApprovalCard from "../../components/console/ApprovalCard";
import Composer, { approvalPayload, type ApprovalMode, type ComposerValue } from "../../components/console/Composer";
import ArtifactRail, { type RailApproval, type RailTodo } from "../../components/console/ArtifactRail";
import FollowUps from "../../components/console/FollowUps";
import {
  ivyeaAgentChat,
  ivyeaAgentStatus,
  ivyeaAgentChatStream,
  ivyeaAwaitSessionAnswer,
  ivyeaChatPermission,
  ivyeaKnowledgeUpload,
  ivyeaOpsTools,
  ivyeaSkills,
  type IvyeaPermissionRequest,
  type IvyeaSkillInfo,
} from "../../api/ivyeaAgent";

const PREFS_KEY = "ivyea-ops.console.prefs";

type Turn = {
  id: string;
  role: "user" | "assistant";
  text: string;
  /** assistant 专用 —— 这一轮的执行过程。 */
  steps?: ConsoleStep[];
  skills?: MatchedSkill[];
  approvals?: { req: IvyeaPermissionRequest; decision?: string }[];
  elapsedMs?: number;
  running?: boolean;
  failed?: boolean;
};

type Prefs = { workspace: string; approval: ApprovalMode; skill: string };

function loadPrefs(): Prefs {
  const fallback: Prefs = { workspace: "默认工作区", approval: "readonly", skill: "" };
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return fallback;
    return { ...fallback, ...JSON.parse(raw) };
  } catch {
    return fallback;
  }
}

function uid() {
  return Math.random().toString(36).slice(2, 10);
}

function ConsoleInner() {
  const notify = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const { role, permissions } = useAuth();
  const visibility = useMemo(() => ({ isAdmin: role === "admin", permissions }), [role, permissions]);
  const scenes = useMemo(() => sceneChips(visibility), [visibility]);

  const prefs = useRef<Prefs>(loadPrefs());
  const [composer, setComposer] = useState<ComposerValue>({ text: "", ...prefs.current });
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const [model, setModel] = useState("");
  const [readOnly, setReadOnly] = useState(true);
  const [usage, setUsage] = useState<any>(null);
  const [todos, setTodos] = useState<RailTodo[]>([]);
  const [railApprovals, setRailApprovals] = useState<RailApproval[]>([]);
  const [followUps, setFollowUps] = useState<string[]>([]);
  const [followLoading, setFollowLoading] = useState(false);
  const [attaching, setAttaching] = useState(false);

  const [skills, setSkills] = useState<IvyeaSkillInfo[]>([]);

  const abortRef = useRef<AbortController | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const started = turns.length > 0;

  // ── 偏好持久化 ───────────────────────────────────────────────────────────
  useEffect(() => {
    const next: Prefs = {
      workspace: composer.workspace,
      approval: composer.approval,
      skill: composer.skill,
    };
    prefs.current = next;
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* ignore */ }
  }, [composer.workspace, composer.approval, composer.skill]);

  // ── 能力目录：板块工具的中文 title 是步骤芯片的文案来源 ────────────────────
  useEffect(() => {
    let alive = true;
    ivyeaOpsTools()
      .then((d) => { if (alive && d?.tools) primeOpsToolLabels(d.tools); })
      .catch(() => void 0);   // 目录拿不到只是芯片显示英文名，不影响对话
    ivyeaSkills()
      .then((d) => { if (alive && Array.isArray(d?.skills)) setSkills(d.skills); })
      .catch(() => void 0);   // /skills 代理是后续阶段的事，这里 404 属正常
    // 当前主脑模型：只用来显示。agent 的模型是全局配置，不支持按轮次覆盖，
    // 所以 composer 上那枚 chip 是信息位 + 去系统配置的入口，不是选择器。
    ivyeaAgentStatus()
      .then((d) => {
        if (!alive) return;
        const m = (d?.health as any)?.model || {};
        setModel(String(m.model || m.label || ""));
      })
      .catch(() => void 0);
    return () => { alive = false; };
  }, []);

  // ── 滚动到底 ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, followUps]);

  // ── 侧边栏「新建任务」────────────────────────────────────────────────────
  const resetSession = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setTurns([]);
    setSessionId("");
    setTodos([]);
    setRailApprovals([]);
    setFollowUps([]);
    setUsage(null);
    setBusy(false);
    setComposer((c) => ({ ...c, text: "" }));
  }, []);

  useEffect(() => {
    const handler = () => resetSession();
    window.addEventListener(CONSOLE_NEW_EVENT, handler);
    return () => window.removeEventListener(CONSOLE_NEW_EVENT, handler);
  }, [resetSession]);

  // 从别的板块带过来的预填：?q= 提示词、?skill= 预选技能（能力市场「用这个技能」
  // 走的就是它）。用完即从地址栏抹掉，免得刷新时又套一遍。
  useEffect(() => {
    const sp = new URLSearchParams(location.search);
    const q = sp.get("q");
    const skill = sp.get("skill");
    if (!q && !skill) return;
    setComposer((c) => ({ ...c, ...(q ? { text: q } : {}), ...(skill ? { skill } : {}) }));
    navigate("/console", { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 单条 assistant 轮次的原地更新 ────────────────────────────────────────
  const patchTurn = useCallback((id: string, patch: Partial<Turn> | ((t: Turn) => Partial<Turn>)) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)));
  }, []);

  // ── 审批决策 ─────────────────────────────────────────────────────────────
  const decide = useCallback(async (turnId: string, req: IvyeaPermissionRequest, choice: string) => {
    // 先乐观落地，避免用户以为没点上；失败再回退成未决。
    patchTurn(turnId, (t) => ({
      approvals: (t.approvals || []).map((a) => (a.req.request_id === req.request_id ? { ...a, decision: choice } : a)),
    }));
    try {
      await ivyeaChatPermission({ request_id: req.request_id, session_id: req.session_id || sessionId, choice });
      setRailApprovals((prev) => [...prev, { title: req.title || req.op_type, decision: choice, at: Date.now() }]);
    } catch (e: any) {
      patchTurn(turnId, (t) => ({
        approvals: (t.approvals || []).map((a) => (a.req.request_id === req.request_id ? { ...a, decision: undefined } : a)),
      }));
      notify("error", e?.response?.data?.detail || e?.message || "提交审批失败，请重试");
    }
  }, [patchTurn, sessionId, notify]);

  // ── 跟进建议：一次无工具的廉价文本轮次，失败静默 ─────────────────────────
  const loadFollowUps = useCallback(async (question: string, answer: string) => {
    if (!answer.trim()) return;
    setFollowLoading(true);
    try {
      const res = await ivyeaAgentChat({
        message:
          "下面是一轮亚马逊运营对话。请只输出 3 条用户接下来最可能想问的问题，" +
          "每行一条、不要编号、不要解释，每条不超过 20 个字。\n\n" +
          `【用户问】${question.slice(0, 500)}\n【回答摘要】${answer.slice(0, 1500)}`,
        use_tools: false,
        persist: false,
        inject_retrieval: false,
        plan_mode: true,
      });
      const lines = String(res?.text || "")
        .split("\n")
        .map((s) => s.replace(/^[\s\-*•\d.、)]+/, "").trim())
        .filter((s) => s.length >= 4 && s.length <= 40)
        .slice(0, 3);
      setFollowUps(lines);
    } catch {
      setFollowUps([]);   // 跟进建议是锦上添花，出错绝不打扰用户
    } finally {
      setFollowLoading(false);
    }
  }, []);

  // ── 发一轮 ───────────────────────────────────────────────────────────────
  const send = useCallback(async (raw?: string) => {
    const text = (raw ?? composer.text).trim();
    if (!text || busy) return;

    setFollowUps([]);
    setComposer((c) => ({ ...c, text: "" }));
    const userTurn: Turn = { id: uid(), role: "user", text };
    const aiId = uid();
    const aiTurn: Turn = { id: aiId, role: "assistant", text: "", steps: [], skills: [], approvals: [], running: true };
    setTurns((prev) => [...prev, userTurn, aiTurn]);
    setBusy(true);

    const startedAt = Date.now();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let liveSid = sessionId;
    let noteSeq = 0;
    let finalText = "";
    const sentAt = Math.floor(Date.now() / 1000);
    const { plan_mode, approval } = approvalPayload(composer.approval);

    const tick = window.setInterval(
      () => patchTurn(aiId, { elapsedMs: Date.now() - startedAt }),
      250,
    );

    try {
      await ivyeaAgentChatStream(
        {
          message: text,
          session_id: sessionId || undefined,
          workspace: composer.workspace && composer.workspace !== "默认工作区" ? composer.workspace : undefined,
          skill: composer.skill || undefined,
          auto_skill: !composer.skill,
          plan_mode,
          approval,
          persist: true,
          inject_retrieval: true,
          ops_context: { board: "console", pathname: "/console" },
        },
        {
          onStart: (d) => {
            if (d?.session_id) { liveSid = d.session_id; setSessionId(d.session_id); }
            if (d?.model) setModel(typeof d.model === "string" ? d.model : d.model?.model || "");
            if (typeof d?.read_only === "boolean") setReadOnly(d.read_only);
          },
          onSkillMatch: (d) => {
            patchTurn(aiId, { skills: (d?.skills || []) as MatchedSkill[] });
          },
          onStep: (ev) => {
            patchTurn(aiId, (t) => ({ steps: mergeStep(t.steps || [], stepFromEvent(ev)) }));
          },
          onPermission: (req) => {
            patchTurn(aiId, (t) => ({ approvals: [...(t.approvals || []), { req }] }));
          },
          onPermissionTimeout: (d) => {
            patchTurn(aiId, (t) => ({
              approvals: (t.approvals || []).map((a) =>
                a.req.request_id === d.request_id ? { ...a, decision: "deny" } : a),
            }));
            notify("warn", "审批等待超时，这一步已自动取消。");
          },
          onEvent: (d) => {
            // 老版本 agent 只有自由文本叙述 —— 不猜工具名，原样留一行注记。
            const line = String(d?.text || "").trim().split("\n").filter(Boolean).pop() || "";
            if (!line) return;
            patchTurn(aiId, (t) => ({ steps: [...(t.steps || []), noteStep(line, noteSeq++)] }));
          },
          onToken: (chunk) => {
            finalText += chunk;
            patchTurn(aiId, (t) => ({ text: t.text + chunk }));
          },
          onFinal: (d) => {
            if (d?.session_id) setSessionId(d.session_id);
            if (Array.isArray(d?.todos)) setTodos(d.todos);
            if (d?.usage) setUsage(d.usage);
            // final.text 是引证门通过后的规范文本，整体替换 —— 流式期间的中间草稿
            // （引证重写前）不留脏文本。
            if (d?.text) { finalText = String(d.text); patchTurn(aiId, { text: finalText }); }
          },
          onError: (d) => {
            const err: any = new Error(String(d?.detail || d?.error || "模型暂不可用"));
            err.explicit = d?.error !== "bridge_error";
            throw err;
          },
        },
        { signal: ctrl.signal },
      );
    } catch (e: any) {
      if (ctrl.signal.aborted) {
        patchTurn(aiId, (t) => ({ running: false, text: t.text || "（已停止）" }));
        window.clearInterval(tick);
        setBusy(false);
        abortRef.current = null;
        return;
      }
      if (e?.explicit) {
        // serve 显式宣告轮次失败（模型报错/额度不足）：直接展示，绝不傻等。
        patchTurn(aiId, { failed: true, running: false, text: String(e?.message || "模型暂不可用") });
      } else if (liveSid) {
        // 传输断链，但 serve 端的轮次独立继续执行并会落盘 —— 不重发（重发会把同
        // 一个多分钟的 agentic 轮次再跑一遍），改为等待落盘的回答。
        patchTurn(aiId, { text: "连接中断，但模型仍在后台继续，正在等待结果…" });
        const answer = await ivyeaAwaitSessionAnswer(liveSid, sentAt);
        if (answer) { finalText = answer; patchTurn(aiId, { text: answer }); }
        else patchTurn(aiId, { failed: true, text: "这轮时间较长，后台仍在处理；完成后可在会话历史里查看。" });
      } else {
        patchTurn(aiId, { failed: true, running: false, text: String(e?.message || "请求失败") });
      }
    } finally {
      window.clearInterval(tick);
      patchTurn(aiId, { running: false, elapsedMs: Date.now() - startedAt });
      setBusy(false);
      abortRef.current = null;
    }

    if (finalText.trim()) void loadFollowUps(text, finalText);
  }, [composer, busy, sessionId, patchTurn, notify, loadFollowUps]);

  const stop = () => {
    abortRef.current?.abort();
  };

  const attach = useCallback(async (file: File) => {
    setAttaching(true);
    try {
      await ivyeaKnowledgeUpload({ file, title: file.name, sourceType: "user", tags: "", confirm: true, rebuild: true });
      setComposer((c) => ({
        ...c,
        text: (c.text ? c.text + "\n" : "") + `（已把「${file.name}」加进知识库，可以直接问它的内容）`,
      }));
      notify("success", `已添加「${file.name}」`);
    } catch (e: any) {
      notify("error", e?.response?.data?.detail || e?.message || "添加文件失败");
    } finally {
      setAttaching(false);
    }
  }, [notify]);

  const patch = (p: Partial<ComposerValue>) => setComposer((c) => ({ ...c, ...p }));

  const composerNode = (compact: boolean) => (
    <Composer
      value={composer}
      onChange={patch}
      onSubmit={() => void send()}
      onStop={stop}
      onAttach={attach}
      busy={busy}
      attaching={attaching}
      skills={skills}
      workspaces={[composer.workspace || "默认工作区"]}
      modelLabel={model}
      onModelClick={() => navigate("/hub-settings")}
      autoFocus={!compact}
      compact={compact}
    />
  );

  return (
    <div className="cc-page">
      <div className="cc-main">
        {!started ? (
          /* ── Hero 态 ───────────────────────────────────────────────── */
          <div className="cc-hero">
            <h1 className="cc-hero-title">今天要做点什么？</h1>
            <p className="cc-hero-sub">
              说一句需求就行 —— Ivyea 会挑技能、调板块能力拿真实数据，需要动线上数据时会先问你。
            </p>
            <div className="cc-hero-composer">{composerNode(false)}</div>
            {scenes.length > 0 && (
              <div className="cc-scenes">
                {scenes.map((s) => (
                  <button
                    key={s.label}
                    type="button"
                    className="cc-scene"
                    onClick={() => { patch({ text: s.prompt }); }}
                    title={s.prompt}
                  >
                    <i>{s.icon}</i>{s.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          /* ── 会话态 ────────────────────────────────────────────────── */
          <>
            <div className="cc-thread scroll-thin" ref={bodyRef}>
              {turns.map((t) =>
                t.role === "user" ? (
                  <div className="cc-user" key={t.id}><div className="cc-bubble">{t.text}</div></div>
                ) : (
                  <div className="cc-ai wb-enter" key={t.id}>
                    <StepTimeline
                      steps={t.steps || []}
                      skills={t.skills || []}
                      elapsedMs={t.elapsedMs}
                      running={t.running}
                    />
                    {t.text && (
                      <div className={"cc-answer" + (t.failed ? " cc-answer-error" : "")}>
                        {t.failed ? t.text : <MarkdownReport text={t.text} />}
                      </div>
                    )}
                    {t.running && !t.text && (
                      <div className="cc-thinking">
                        <span className="spin" /> 正在处理{t.elapsedMs ? ` · ${formatMs(t.elapsedMs)}` : ""}
                      </div>
                    )}
                    {(t.approvals || []).map(({ req, decision }) => (
                      <ApprovalCard
                        key={req.request_id}
                        request={req}
                        decided={decision}
                        onDecide={(choice) => void decide(t.id, req, choice)}
                      />
                    ))}
                  </div>
                ),
              )}
              {!busy && (
                <FollowUps items={followUps} loading={followLoading} onPick={(q) => void send(q)} />
              )}
            </div>
            <div className="cc-dock">{composerNode(true)}</div>
          </>
        )}
      </div>

      <ArtifactRail
        todos={todos}
        approvals={railApprovals}
        sessionId={sessionId}
        model={model}
        readOnly={readOnly}
        usage={usage}
      />
    </div>
  );
}

export default function Console() {
  return (
    <ToastProvider>
      <ConsoleInner />
    </ToastProvider>
  );
}
