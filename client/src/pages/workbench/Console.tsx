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
import { stripInjected } from "../../lib/stripInjected";
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
import { useStickToBottom } from "../../lib/useStickToBottom";
import StepTimeline, { type MatchedSkill } from "../../components/console/StepTimeline";
import ApprovalCard from "../../components/console/ApprovalCard";
import Composer, { approvalPayload, type ApprovalMode, type ComposerRef, type ComposerValue } from "../../components/console/Composer";
import ArtifactRail, { type RailApproval, type RailTodo } from "../../components/console/ArtifactRail";
import FollowUps from "../../components/console/FollowUps";
import {
  CONSOLE_PRESETS_CHANGED,
  consolePresets,
  consoleWorkspaceCreate,
  consoleSessionApprovals,
  consoleSessions,
  answerResetDiscards,
  ivyeaAgentChat,
  ivyeaAgentStatus,
  ivyeaAgentChatStream,
  ivyeaAwaitSessionAnswer,
  ivyeaChatPermission,
  ivyeaChatSession,
  ivyeaKnowledgeFile,
  ivyeaKnowledgeFiles,
  ivyeaKnowledgeUpload,
  notifyConsoleSessionsChanged,
  ivyeaOpsTools,
  ivyeaSkills,
  visionDescribe,
  type ConsolePreset,
  type IvyeaFileChange,
  type IvyeaPermissionRequest,
  type IvyeaSkillInfo,
} from "../../api/ivyeaAgent";
import { errText } from "../../lib/errText";

/** 老版本 agent 的自由文本叙述最多保留最近几行 —— 长任务的叙述能有几十条。 */
const MAX_NOTES = 12;

/**
 * 正文被门禁打回重写时，在时间线上留一行 —— 气泡里那一稿被清掉了，
 * 不说一声的话用户只会看到字突然消失。
 */
const GATE_NOTE: Record<string, string> = {
  "gate:citation": "知识引用未通过校验，正在重写这段回答",
  "gate:verify": "完成前自验证未通过，正在重写这段回答",
  "gate:progress": "阶段汇报尚未闭环，正在重写这段回答",
};

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

type Prefs = {
  workspace: string; approval: ApprovalMode; skill: string;
  /** 跟进建议每轮额外跑一次模型调用，给个开关。默认开。 */
  followUps: boolean;
  /** 套用中的预设名与人设 —— 和工作区/档位一样，刷新后应该还在。 */
  preset: string;
  system: string;
};

function loadPrefs(): Prefs {
  const fallback: Prefs = {
    workspace: "默认工作区", approval: "readonly", skill: "",
    followUps: true, preset: "", system: "",
  };
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
  // 本会话 Agent 改过的文件。同一路径被改多次时**保留每一次** —— 折叠成一条会让
  // "先写后改"的过程消失，而那恰恰是用户想复盘的东西。
  const [fileChanges, setFileChanges] = useState<IvyeaFileChange[]>([]);
  const [followUps, setFollowUps] = useState<string[]>([]);
  const [followLoading, setFollowLoading] = useState(false);
  const [followEnabled, setFollowEnabled] = useState(prefs.current.followUps !== false);
  const [attaching, setAttaching] = useState(false);

  const [skills, setSkills] = useState<IvyeaSkillInfo[]>([]);
  const [presets, setPresets] = useState<ConsolePreset[]>([]);
  const [workspaces, setWorkspaces] = useState<string[]>(["默认工作区"]);
  const [references, setReferences] = useState<ComposerRef[]>([]);
  const [picked, setPicked] = useState<ComposerRef[]>([]);
  const [images, setImages] = useState<string[]>([]);
  const [loadingSession, setLoadingSession] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const started = turns.length > 0;

  // ── 偏好持久化 ───────────────────────────────────────────────────────────
  useEffect(() => {
    // followUps 必须带上。这个 effect 是**整体覆盖**写回，漏一个字段就等于
    // 每次改工作区/档位都顺手把那个开关重置回默认。
    const next: Prefs = {
      workspace: composer.workspace,
      approval: composer.approval,
      skill: composer.skill,
      followUps: followEnabled,
      preset: composer.preset || "",
      system: composer.system || "",
    };
    prefs.current = next;
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* ignore */ }
  }, [composer.workspace, composer.approval, composer.skill, composer.preset, composer.system, followEnabled]);

  // ── 能力目录：板块工具的中文 title 是步骤芯片的文案来源 ────────────────────
  useEffect(() => {
    let alive = true;
    ivyeaOpsTools()
      .then((d) => { if (alive && d?.tools) primeOpsToolLabels(d.tools); })
      .catch(() => void 0);   // 目录拿不到只是芯片显示英文名，不影响对话
    ivyeaSkills()
      .then((d) => { if (alive && Array.isArray(d?.skills)) setSkills(d.skills); })
      .catch(() => void 0);   // /skills 代理是后续阶段的事，这里 404 属正常
    consolePresets()
      .then((d) => { if (alive) setPresets(d); })
      .catch(() => void 0);   // 没有预设不影响开一轮
    // 当前主脑模型：只用来显示。agent 的模型是全局配置，不支持按轮次覆盖，
    // 所以 composer 上那枚 chip 是信息位 + 去系统配置的入口，不是选择器。
    ivyeaAgentStatus()
      .then((d) => {
        if (!alive) return;
        const m = (d?.health as any)?.model || {};
        setModel(String(m.model || m.label || ""));
      })
      .catch(() => void 0);
    // @ 可引用的东西：知识卡 + 上传件。取不到就没有 @ 菜单，不影响别的。
    ivyeaKnowledgeFiles(200)
      .then((d) => {
        if (!alive) return;
        const cards = (d.cards || []).map((c) => ({
          id: c.id, title: c.title || c.id, path: c.path || "",
        })).filter((c) => c.path);
        const ups = (d.uploads || []).map((u) => ({ id: u.path, title: u.name, path: u.path }));
        setReferences([...cards, ...ups].slice(0, 300));
      })
      .catch(() => void 0);
    consoleSessions()
      .then((d) => {
        if (!alive) return;
        const names = (d.workspaces || []).map((w) => w.name);
        if (names.length) setWorkspaces(names);
      })
      .catch(() => void 0);
    return () => { alive = false; };
  }, []);

  // ── 跟随滚动 ─────────────────────────────────────────────────────────────
  //
  // 判据在 lib/useStickToBottom：**按用户意图（wheel/touch/键）判，不按滚动位置判**。
  // 位置判据在流式输出下必输 —— scroll 事件是异步派发的，每个 token 都会抢先把
  // scrollTop 拍回底部，用户往上翻的那一下永远量不到，手一松就被扯下来。
  const { atBottom, scrollToBottom, scrollTo, setFollow } =
    useStickToBottom(bodyRef, [turns, followUps]);

  // 新一轮发出后，把那条问题滚到视野顶端（只做一次）。
  const pendingTopRef = useRef<string>("");
  useEffect(() => {
    const id = pendingTopRef.current;
    if (!id) return;
    const el = bodyRef.current;
    const node = el?.querySelector(`[data-turn="${id}"]`) as HTMLElement | null;
    if (!el || !node) return;
    pendingTopRef.current = "";
    // 走 scrollTo 而不是直接写 scrollTop：直接写会被 scroll 监听当成"用户翻上去了"，
    // 跟随就此关掉，后面的回答一个字都不跟。
    scrollTo(node.offsetTop - el.offsetTop - 8);
    setFollow(true);              // 之后照常跟随，直到用户自己往上翻
  }, [turns, scrollTo, setFollow]);

  // ── 新建工作区（从输入框那个下拉里进来）──────────────────────────────────
  //
  // 放在这里而不是只留在左栏：用户想换工作区时点的是输入框那个 chip，
  // 在那儿给出路，比让他去左栏找一个「+」自然得多。
  const newWorkspace = useCallback(async () => {
    const name = window.prompt("工作区名称（例如：我的店铺资料）");
    if (!name || !name.trim()) return;
    const path = window.prompt(
      "绑定目录的绝对路径（Agent 的文件读写就发生在这个目录里）。\n" +
      "留空则不绑目录 —— 那样它只能在会话里工作，碰不到你的文件。", "") || "";
    try {
      await consoleWorkspaceCreate(name.trim(), path.trim());
      const d = await consoleSessions();
      const names = (d.workspaces || []).map((w) => w.name);
      if (names.length) setWorkspaces(names);
      patch({ workspace: name.trim() });
      notifyConsoleSessionsChanged();
      notify("success", path.trim() ? `已创建并切到「${name.trim()}」，目录：${path.trim()}`
                               : `已创建并切到「${name.trim()}」（未绑目录）`);
    } catch (e) {
      notify("error", errText(e, "工作区创建失败"));
    }
  }, [notify]);

  // ── 侧边栏「新建任务」────────────────────────────────────────────────────
  const resetSession = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setTurns([]);
    setSessionId("");
    setTodos([]);
    setRailApprovals([]);
    setFileChanges([]);
    setFollowUps([]);
    setUsage(null);
    setBusy(false);
    setComposer((c) => ({ ...c, text: "" }));
  }, []);

  useEffect(() => {
    const handler = () => {
      resetSession();
      // 地址栏还留着 ?session= 的话，下面那个 effect 会立刻把旧会话又拉回来。
      if (window.location.search) navigate("/console", { replace: true });
    };
    window.addEventListener(CONSOLE_NEW_EVENT, handler);
    return () => window.removeEventListener(CONSOLE_NEW_EVENT, handler);
  }, [resetSession, navigate]);

  // 从别的板块带过来的预填：?q= 提示词、?skill= 预选技能（能力市场「用这个技能」
  // 走的就是它）。用完即从地址栏抹掉，免得刷新时又套一遍。
  useEffect(() => {
    const sp = new URLSearchParams(location.search);
    const q = sp.get("q");
    const skill = sp.get("skill");
    if (!q && !skill) return;
    setComposer((c) => ({ ...c, ...(q ? { text: q } : {}), ...(skill ? { skill } : {}) }));
    navigate("/console" + (sp.get("session") ? `?session=${encodeURIComponent(sp.get("session")!)}` : ""),
             { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ?session= 打开左栏点选的历史会话。**不同于 ?q/?skill，它留在地址栏里** ——
  // 左栏靠它高亮当前会话，刷新/分享链接也要能回到同一条。
  const urlSession = new URLSearchParams(location.search).get("session") || "";
  useEffect(() => {
    if (!urlSession) return;
    if (urlSession === sessionId) return;      // 已经是当前会话，别重复拉
    let alive = true;
    setLoadingSession(true);
    abortRef.current?.abort();
    ivyeaChatSession(urlSession)
      .then((d) => {
        if (!alive) return;
        const msgs = d?.session?.messages || [];
        // system 轮次是每轮现算的运行时上下文，不是对话内容，别摆给用户看。
        const restored: Turn[] = msgs
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m) => ({
            id: uid(),
            role: m.role as "user" | "assistant",
            // 注入给模型的技能/知识块存在同一条消息里，气泡里不该出现
            text: stripInjected(String(m.content || "")),
          }))
          .filter((t) => t.text);
        setTurns(restored);
        setSessionId(urlSession);
        setFollowUps([]);
        setTodos([]);
        setFileChanges([]);
        // 审批留痕落在服务端，刷新/隔天回来都还在 —— 这是这套系统最该
        // 留下的一条记录，不能只活在内存里。
        setRailApprovals([]);
        void consoleSessionApprovals(urlSession)
          .then((list) => {
            if (!alive) return;
            setRailApprovals(list.map((a) => ({
              title: a.title || a.op_type || a.request_id,
              decision: a.decision || "pending",
              at: (a.decided_at || a.requested_at || 0) * 1000,
            })));
          })
          .catch(() => void 0);   // 拿不到留痕不影响会话本身
      })
      .catch((e: any) => {
        if (!alive) return;
        notify("error", e?.response?.status === 403
          ? "这条会话不属于你"
          : (errText(e, "打开会话失败")));
        navigate("/console", { replace: true });
      })
      .finally(() => { if (alive) setLoadingSession(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlSession]);

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
      const status = e?.response?.status;
      // 409 = 这条已经被处理过了（多半是另一个页签点的）。**不能退回"未决"** ——
      // 那一步确实已经执行/拒绝了，把卡片变回可点只会让人再点一次、再撞一次 409。
      if (status === 409) {
        setRailApprovals((prev) => [...prev, { title: req.title || req.op_type, decision: choice, at: Date.now() }]);
        notify("info", errText(e, "这条审批已经被处理过了"));
        return;
      }
      // 404 = 已超时失效或 ops 重启丢了登记。同样不该退回未决 —— 它永远不会成功了。
      if (status === 404) {
        patchTurn(turnId, (t) => ({
          approvals: (t.approvals || []).map((a) => (a.req.request_id === req.request_id ? { ...a, decision: "abort" } : a)),
        }));
        notify("error", "这条审批已失效（超时或服务重启），那一步没有执行。");
        return;
      }
      patchTurn(turnId, (t) => ({
        approvals: (t.approvals || []).map((a) => (a.req.request_id === req.request_id ? { ...a, decision: undefined } : a)),
      }));
      notify("error", errText(e, "提交审批失败，请重试"));
    }
  }, [patchTurn, sessionId, notify]);

  // ── 跟进建议：一次无工具的廉价文本轮次，失败静默 ─────────────────────────
  const loadFollowUps = useCallback(async (question: string, answer: string) => {
    if (!answer.trim() || !followEnabled) return;
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
    // **把刚发出的问题顶到视野上方**，答案在它下面生长 —— 这是主流对话产品的
    // 做法，也是"我发的问题看不到了"的正解：原先直接钉在最底部，长回答一出来
    // 就把问题和执行过程顶出屏幕，而那时候滚动又被强制拽回底部，翻都翻不上去。
    pendingTopRef.current = userTurn.id;

    // @ 引用：把选中条目的正文取出来随本轮带下去。取不到的跳过并说明，
    // 不要让用户以为引用了、实际什么都没带。
    let refSystem = "";
    if (picked.length) {
      const parts: string[] = [];
      const failed: string[] = [];
      for (const r of picked) {
        try {
          const d = await ivyeaKnowledgeFile(r.path);
          const body = String(d?.content || "").slice(0, 12000);
          if (body.trim()) parts.push(`### ${r.title}\n${body}`);
          else failed.push(r.title);
        } catch {
          failed.push(r.title);
        }
      }
      if (parts.length) {
        refSystem = "[用户显式引用的资料 —— 优先据此作答]\n" + parts.join("\n\n");
      }
      if (failed.length) notify("warn", `这些引用读不到，已跳过：${failed.join("、")}`);
    }

    // 图片：ops 侧视觉旁路读成文字再带下去（主脑没有视觉，图直接发过去会被 agent 拒）。
    let visionSystem = "";
    if (images.length) {
      try {
        const d = await visionDescribe(images);
        if (d?.text?.trim()) {
          visionSystem = `[用户附图 —— 由视觉模型（${d.provider || "vision"}）读出的内容]\n${d.text.trim()}`;
        }
      } catch (e: any) {
        notify("error", errText(e, "图片没能读出来，这一轮按纯文字继续。可在「系统配置 → AI 服务」配一个视觉模型。"));
      }
    }

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

    // token 按帧批量落地。一个字一次 setState 时，长报告的 markdown 每秒被重解析
    // 几十遍；合并到一帧一次，内容一模一样，但渲染成本掉一个数量级。
    let pending = "";
    let flushRaf = 0;
    const flushTokens = () => {
      flushRaf = 0;
      const add = pending;
      pending = "";
      if (add) patchTurn(aiId, (t) => ({ text: t.text + add }));
    };
    const cancelFlush = () => {
      if (flushRaf) { window.cancelAnimationFrame(flushRaf); flushRaf = 0; }
      pending = "";
    };
    // 收尾：还没落地的那一帧要补上，否则回答会缺最后几个字。
    const finishFlush = () => {
      if (flushRaf) window.cancelAnimationFrame(flushRaf);
      flushTokens();
    };

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
          // 人设排最前：它定义"以什么身份、什么判断标准作答"，逻辑上先于本轮材料。
          system: [
            composer.system ? "[角色设定 —— 按这个身份和判断标准作答]\n" + composer.system : "",
            visionSystem,
            refSystem,
          ].filter(Boolean).join("\n\n") || undefined,
          persist: true,
          inject_retrieval: true,
          ops_context: { board: "console", pathname: "/console" },
        },
        {
          onFileChange: (d) => setFileChanges((prev) => [...prev, d]),
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
            // 自由文本叙述是**老版本 agent 的兜底**（< v1.9 只发人话、没有结构化
            // 步骤）。新版两种都发，而它们说的是同一批动作 —— 实测一次带工具的
            // 提问会来 44 条 step + 46 条 event，两个都渲染就等于每个动作出现两
            // 次，且文本那份没有上限，几十行糊满整页把回答挤没了。
            //
            // 所以：**这一轮只要收到过结构化步骤，就不再渲染叙述。**
            const line = String(d?.text || "").trim().split("\n").filter(Boolean).pop() || "";
            if (!line) return;
            patchTurn(aiId, (t) => {
              const steps = t.steps || [];
              if (steps.some((x) => x.phase !== "note")) return {};   // 有结构化的，叙述丢掉
              // 真·老版本路径：注记也要有上限，否则长任务照样能刷满屏。
              const notes = steps.filter((x) => x.phase === "note");
              const next = [...steps, noteStep(line, noteSeq++)];
              return { steps: notes.length >= MAX_NOTES ? next.slice(next.length - MAX_NOTES) : next };
            });
          },
          onToken: (chunk) => {
            finalText += chunk;
            pending += chunk;
            if (!flushRaf) flushRaf = window.requestAnimationFrame(flushTokens);
          },
          // 正文的分段边界。门禁打回 = 整篇重写，旧稿作废（不清就是"同一张表连出
          // 三遍"）；去调工具 = 这段没说完，只断段不丢字。判据见 answerResetDiscards。
          onAnswerReset: (d) => {
            const reason = String(d?.reason || "");
            if (!answerResetDiscards(reason)) {
              pending += "\n\n";                 // 两段之间留个空行，别糊成一段
              if (!flushRaf) flushRaf = window.requestAnimationFrame(flushTokens);
              return;
            }
            cancelFlush();
            finalText = "";
            const note = GATE_NOTE[reason] || "正在重写这段回答";
            patchTurn(aiId, (t) => ({
              text: "",
              // 字凭空少了，必须说一声 —— 否则用户只会看到回答突然被清空。
              steps: mergeStep(t.steps || [], noteStep(note, noteSeq++)),
            }));
          },
          onFinal: (d) => {
            cancelFlush();
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
      finishFlush();
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
      finishFlush();
      window.clearInterval(tick);
      patchTurn(aiId, { running: false, elapsedMs: Date.now() - startedAt });
      setBusy(false);
      abortRef.current = null;
    }

    setPicked([]);                      // 引用是"本轮"的，发完就清
    setImages([]);
    notifyConsoleSessionsChanged();     // 新会话进左栏 / 已有会话更新时间
    if (finalText.trim()) void loadFollowUps(text, finalText);
    // images / picked 必须在依赖里：send 里读了它们。漏掉的话这个回调会闭包住
    // 旧值 —— 贴完图不打字直接发，图就丢了（之前靠"总会先打字"侥幸没暴露）。
  }, [composer, busy, sessionId, images, picked, patchTurn, notify, loadFollowUps]);

  const toggleFollowUps = (next: boolean) => {
    setFollowEnabled(next);
    if (!next) setFollowUps([]);
    try {
      const raw = localStorage.getItem(PREFS_KEY);
      const cur = raw ? JSON.parse(raw) : {};
      localStorage.setItem(PREFS_KEY, JSON.stringify({ ...cur, followUps: next }));
    } catch { /* 存不下就只影响这一次会话，不值得打扰用户 */ }
  };

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
      notify("error", errText(e, "添加文件失败"));
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
      workspaces={workspaces}
      onNewWorkspace={newWorkspace}
      references={references}
      picked={picked}
      onPickedChange={setPicked}
      scenes={scenes}
      presets={presets}
      onNewTask={resetSession}
      images={images}
      onImagesChange={setImages}
      modelLabel={model}
      onModelClick={() => navigate("/hub-settings")}
      autoFocus={!compact}
      compact={compact}
    />
  );

  return (
    <div className="cc-page">
      <div className="cc-main">
        {loadingSession ? (
          <div className="cc-hero">
            <div className="cc-thinking"><span className="spin" /> 正在打开会话…</div>
          </div>
        ) : !started ? (
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
            <div className="cc-thread-wrap">
              <div className="cc-thread scroll-thin" ref={bodyRef}>
                {turns.map((t) =>
                  t.role === "user" ? (
                    <div className="cc-user" key={t.id} data-turn={t.id}>
                      <div className="cc-bubble">{t.text}</div>
                    </div>
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
                  <FollowUps
                    items={followUps}
                    loading={followLoading}
                    enabled={followEnabled}
                    onToggle={toggleFollowUps}
                    onPick={(q) => void send(q)}
                  />
                )}
              </div>
              {/* 脱离底部才出现 —— 跟随已经关掉了，得给一条回去的路。 */}
              {!atBottom && (
                <button type="button" className="cc-tobottom" onClick={scrollToBottom}
                        title="回到最新内容">
                  ↓ 回到底部
                </button>
              )}
            </div>
            <div className="cc-dock">{composerNode(true)}</div>
          </>
        )}
      </div>

      <ArtifactRail
        answers={turns.filter((t) => t.role === "assistant" && !t.failed).map((t) => t.text)}
        todos={todos}
        fileChanges={fileChanges}
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
