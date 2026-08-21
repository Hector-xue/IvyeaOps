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
import { imageRef, streamChat, type ChatMsg } from "../../api/assistant";
import { restoreSession } from "../../lib/sessionRestore";
import { ToastProvider, useToast } from "../../components/toast";
import { CONSOLE_NEW_EVENT, sceneChips } from "../../lib/navRegistry";
import Icon from "../../components/Icon";
import {
  mergeStep,
  noteStep,
  primeOpsToolLabels,
  stepFromEvent,
  type ConsoleStep,
} from "../../lib/stepLabels";
import { useStickToBottom } from "../../lib/useStickToBottom";
import { aggregateStats, mergeStats, type ServerStats, type TurnMetrics } from "../../lib/turnStats";
import ActivityFeed, { type MatchedSkill, type Thought } from "../../components/console/ActivityFeed";
import StatsBar from "../../components/console/StatsBar";
import ContextMeter from "../../components/console/ContextMeter";
import DockMeta from "../../components/console/DockMeta";
import ApprovalCard from "../../components/console/ApprovalCard";
import Composer, { approvalPayload, type ApprovalMode, type ComposerRef, type ComposerValue } from "../../components/console/Composer";
import ArtifactRail, { type RailApproval, type RailTodo } from "../../components/console/ArtifactRail";
import FollowUps from "../../components/console/FollowUps";
import AnswerActions from "../../components/console/AnswerActions";
import LiveDock from "../../components/console/LiveDock";
import {
  CONSOLE_PRESETS_CHANGED,
  consolePresets,
  consoleWorkspaceCreate,
  consoleSessionApprovals,
  consoleSessionImport,
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
  type IvyeaChatAttachment,
  type ConsolePreset,
  type IvyeaContextUsage,
  type IvyeaFileChange,
  type IvyeaPermissionRequest,
  type IvyeaSkillInfo,
} from "../../api/ivyeaAgent";
import { splitModelId } from "../../components/console/ModelPicker";
import { getSettings, patchSettings } from "../../api/settings";
import { errText } from "../../lib/errText";
import { openSettings } from "../../components/SettingsDialog";

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

/** 兜底通道（agent 掉线时）的人设。agent 在时人设由 serve 那边给。 */
const FALLBACK_SYSTEM =
  "你是亚马逊运营助手，用中文清晰作答；需要时用 Markdown（表格/列表/标题）写出可直接复制的文档。";

/**
 * `have >= want` 吗（纯数字点分版本）。取不到版本一律当成"老的"，宁可走兼容分支。
 */
function atLeast(have: string, want: string): boolean {
  const a = have.replace(/^v/, "").split(".").map((x) => parseInt(x, 10));
  if (!a.length || Number.isNaN(a[0])) return false;
  const b = want.split(".").map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const p = a[i] || 0, q = b[i] || 0;
    if (p !== q) return p > q;
  }
  return true;
}

/** 兜底那条注记的序号 —— 只要在同一轮里唯一即可，跟正常轮次的 noteSeq 互不相干。 */
let noteSeqFallback = 0;

/** 老 AI 问答页遗留在浏览器里的历史，任务台接手搬家（那页已并入任务台）。 */
const LEGACY_ASSISTANT_KEY = "ivyea-ops-assistant-sessions";
const LEGACY_IMPORTED_KEY = "ivyea-ops-assistant-imported-v1";

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
  /** 这一轮是从存档里恢复出来的，不是这次页面跑的 —— 它身上没有计时/用量，
   *  统计条据此避免把它算两遍（落盘那份已经把它算进去了）。 */
  restored?: boolean;
  /**
   * 这一轮有几个写操作被「只读」档挡下了（agent ≥ v1.16.2 回报）。
   *
   * 它必须在界面上说出来：只读档下写操作是**直接拒绝**的，不会产生任何待审批项。
   * 用户看到的是模型转述的一句"被拦截"，然后跑去「待审批」页空等 —— 真实反馈是
   * "经常跑一半说被拦截，待审批那一页也从来没看到任何审批项"。
   */
  readonlyBlocked?: number;
  failed?: boolean;
  /**
   * 模型思考流的最近一段（agent ≥ v1.10.3 且本轮要了 stream_reasoning）。
   * 只喂活动行，不进气泡 —— 思考不是回答。只留尾部若干字符，
   * 一轮思考几万字全存进 state 会让每次 patch 都拷一遍大字符串。
   */
  reasoning?: string;
  /**
   * 思考按**批**成段：两次工具调用之间的所有思考合成一段人话。
   *
   * 不按句切 —— 模型的思考流是连续的，按句切会碎成几百条，铺出来是一面字墙。
   * 一批就是一个自然的单元："想完了，去做这几件事"，正好对上后面那一行工具摘要。
   * seq 记的是冲刷时已经有多少步，靠它和步骤穿插排序（时钟对不齐，见 ActivityFeed）。
   */
  thoughts?: Thought[];
  /** 这一轮的计时与用量，喂给底部统计条。 */
  metrics?: TurnMetrics;
  /**
   * 这一轮用户发出去的图（本次页面里是 data URL，从存档恢复时是 `ivyea-ref://`
   * 换来的地址）。**必须在气泡里显示**：图不进模型，会话记录此前完全看不出用户
   * 发过图 —— 用户原话："会话记录里面也没有展示我发送的图片"。
   */
  images?: string[];
};

/** 思考流只保留尾部这么多字符 —— 活动行只显示最后一句，多存无用。 */
/**
 * 还没成段的那一段思考，**只留开头**这么多字。
 *
 * 原来留的是尾部（滑动窗口）。那一行因此永远在变：字一多，开头就开始往前漂，
 * 整行文字不停左移 —— 上下不跳了，一行之内照样在抖。留开头则前缀恒定，
 * 后面的字进省略号，视觉上是静止的。
 */
const REASONING_HEAD = 400;
/** 一轮里最多留多少段思考。再多界面上也翻不完，而 state 每次 patch 都要拷一遍。 */
const THOUGHTS_MAX = 60;

type Prefs = {
  workspace: string; approval: ApprovalMode; skill: string;
  /** 本会话选中的主脑模型（`provider:model`）；"" = 跟随 agent 的全局主脑。 */
  model: string;
  /** 跟进建议每轮额外跑一次模型调用，给个开关。默认开。 */
  followUps: boolean;
  /** 套用中的预设名与人设 —— 和工作区/档位一样，刷新后应该还在。 */
  preset: string;
  system: string;
};

function loadPrefs(): Prefs {
  const fallback: Prefs = {
    workspace: "默认工作区", approval: "readonly", skill: "", model: "",
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
  /**
   * 本会话选中的主脑（`provider:model`），逐轮下发。"" = 跟随 agent 的全局主脑。
   *
   * 刻意**不改全局**：agent 的模型是全局设置，真按全局切会把 ops 的其他用户和
   * 正在跑的定时任务一起换掉。想改全局走模型面板里的「设为默认」。
   */
  const [modelPick, setModelPick] = useState(prefs.current.model || "");
  /** agent 认不认 payload.model（≥ v1.15.4）。老版本会忽略它 —— 那就别给假开关。 */
  const [modelSwitchable, setModelSwitchable] = useState(false);
  const [readOnly, setReadOnly] = useState(true);
  const [usage, setUsage] = useState<any>(null);
  // 本会话的上下文占用。agent 每轮发两次（开跑前 + 收尾），老 agent 一次都不发 ——
  // 那就整块不显示，绝不自己编一个百分比。
  const [ctxUsage, setCtxUsage] = useState<IvyeaContextUsage | null>(null);
  /** 这条会话此前累计的账（服务端落盘）。切会话必须跟着换，新建必须清空 ——
   *  留着上一条的数是最糟的一种错：它看起来有效，其实说的是别人。 */
  const [serverStats, setServerStats] = useState<ServerStats | null>(null);
  const [todos, setTodos] = useState<RailTodo[]>([]);
  const [railApprovals, setRailApprovals] = useState<RailApproval[]>([]);
  // 本会话 Agent 改过的文件。同一路径被改多次时**保留每一次** —— 折叠成一条会让
  // "先写后改"的过程消失，而那恰恰是用户想复盘的东西。
  const [fileChanges, setFileChanges] = useState<IvyeaFileChange[]>([]);
  const [followUps, setFollowUps] = useState<string[]>([]);
  const [followLoading, setFollowLoading] = useState(false);
  const [followEnabled, setFollowEnabled] = useState(prefs.current.followUps !== false);
  const [attaching, setAttaching] = useState(false);
  // 历史会话按轮分页：`from` 是本页最早的轮号，取更早一页时当游标传回去。
  const [earlier, setEarlier] = useState<{ hasMore: boolean; from: number; loading: boolean }>(
    { hasMore: false, from: 0, loading: false });

  const [skills, setSkills] = useState<IvyeaSkillInfo[]>([]);
  const [presets, setPresets] = useState<ConsolePreset[]>([]);
  const [workspaces, setWorkspaces] = useState<string[]>(["默认工作区"]);
  const [references, setReferences] = useState<ComposerRef[]>([]);
  const [picked, setPicked] = useState<ComposerRef[]>([]);
  const [images, setImages] = useState<string[]>([]);
  /**
   * 这台机器上的 agent 认不认识 `attachments`（≥ v1.15.3）。
   *
   * 认识：附图的文字版进 user 消息，跟着历史走；不认识（或问不到版本）：退回老路子
   * 塞 system —— 那样只有贴图那一轮有效，但总好过整段丢掉。
   */
  const [agentTakesAttachments, setAgentTakesAttachments] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const started = turns.length > 0;
  // 底部统计条的数。执行中每 250ms 会随 elapsedMs 重算一次 —— 只是遍历轮次求和，
  // 比一次 markdown 重解析便宜好几个数量级。
  /**
   * 统计条的数 = **服务端落盘的整会话累计** + **本次页面自己跑的那些轮**。
   *
   * 两边不能重叠：恢复出来的轮次身上没有计时/用量（那些数只在当时那个浏览器里
   * 存在过），把它们混进本地聚合只会让轮数和步数翻倍。所以本地只聚合 live 的轮，
   * 已落盘的那些由 serverStats 代表。老 agent 没有 stats 时行为与改动前一致。
   */
  const sessionStats = useMemo(() => {
    // 这条会话有没有落盘的累计账？**这次改动之前存下的会话一条都没有**，老 agent
    // 也不回报。那时候必须退回改动前的算法（把恢复出来的轮也算上），否则打开一条
    // 旧会话，统计条会从"几轮几步"直接变成空白 —— 为了新增一项而弄丢已有的那项，
    // 是这次改动最容易犯的错。
    const hasServer = !!serverStats && (serverStats.turns || 0) > 0;
    const live = aggregateStats(
      turns.filter((t) => t.role === "assistant" && (!hasServer || !t.restored)));
    return hasServer ? mergeStats(serverStats, live) : live;
  }, [turns, serverStats]);
  /** 正在跑的那一轮 —— 状态坞的数据源。流式期间它永远是最后一条。 */
  const liveTurn = useMemo(
    () => [...turns].reverse().find((t) => t.role === "assistant" && t.running),
    [turns],
  );

  // ── 偏好持久化 ───────────────────────────────────────────────────────────
  useEffect(() => {
    // followUps 必须带上。这个 effect 是**整体覆盖**写回，漏一个字段就等于
    // 每次改工作区/档位都顺手把那个开关重置回默认。
    const next: Prefs = {
      workspace: composer.workspace,
      approval: composer.approval,
      skill: composer.skill,
      model: modelPick,
      followUps: followEnabled,
      preset: composer.preset || "",
      system: composer.system || "",
    };
    prefs.current = next;
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* ignore */ }
  }, [composer.workspace, composer.approval, composer.skill, composer.preset, composer.system,
      modelPick, followEnabled]);

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
    // 当前主脑模型 + agent 版本。agent ≥ v1.15.4 认 payload.model，所以那枚 chip
    // 现在是**真的选择器**（逐轮下发，只影响这条会话）；更老的版本会忽略这个字段，
    // 那时它退回信息位 + 去系统配置的入口。
    ivyeaAgentStatus()
      .then((d) => {
        if (!alive) return;
        const m = (d?.health as any)?.model || {};
        setModel(String(m.model || m.label || ""));
        const ver = String((d?.health as any)?.version || "");
        setAgentTakesAttachments(atLeast(ver, "1.15.3"));
        // 老 agent 收到 model 字段会直接忽略：那时给下拉框就是个假开关（选了别的
        // 模型、跑的还是老模型，还没有任何提示）。所以按版本决定给不给。
        setModelSwitchable(atLeast(ver, "1.15.4"));
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

  // 老 AI 问答页留在 localStorage 里的历史，搬一次家进会话库。
  //
  // 这段原来长在 AI 问答页上，那页收进任务台后必须跟着搬 —— 否则谁的浏览器没在
  // 那页删掉之前打开过它，那份记录就永远躺在 localStorage 里进不来了。
  // **按 id 幂等**（服务端按 id 覆盖写），localStorage 标记只是省一次请求。
  useEffect(() => {
    if (localStorage.getItem(LEGACY_IMPORTED_KEY)) return;
    let legacy: any[] = [];
    try {
      const raw = localStorage.getItem(LEGACY_ASSISTANT_KEY);
      const v = raw ? JSON.parse(raw) : [];
      legacy = (Array.isArray(v) ? v : []).filter((x) => x?.id && x.turns?.length);
    } catch { return; }
    if (!legacy.length) { localStorage.setItem(LEGACY_IMPORTED_KEY, "0"); return; }
    void (async () => {
      try {
        const r = await consoleSessionImport("assistant", legacy.map((x) => ({
          id: String(x.id).replace(/[^A-Za-z0-9_-]/g, ""),
          created: Math.floor((x.updatedAt || Date.now()) / 1000),
          messages: (x.turns || [])
            .filter((t: any) => t?.content?.trim())
            .map((t: any) => ({ role: t.role, content: t.content })),
        })).filter((x) => x.id && x.messages.length));
        localStorage.setItem(LEGACY_IMPORTED_KEY, String(r.count));
        if (r.count) notifyConsoleSessionsChanged();
      } catch {
        // 不标记，下次进任务台再试
      }
    })();
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
    setCtxUsage(null);
    setServerStats(null);
    setBusy(false);
    setEarlier({ hasMore: false, from: 0, loading: false });
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
    // 切会话先把上一条的数**清掉**再拉新的。留着不清是最糟的一种错：进度条和用量
    // 看起来有效，其实说的是刚才那条会话 —— 用户不会怀疑一个显示着数字的控件。
    setCtxUsage(null);
    setUsage(null);
    setServerStats(null);
    ivyeaChatSession(urlSession)
      .then((d) => {
        if (!alive) return;
        // 消息 + 落盘的执行步骤重新缝成轮次（缝合靠 call_id，见 lib/sessionRestore）。
        const page = restoreSession(d?.session);
        setTurns(page.turns.map((t) => ({ ...t, id: uid(), restored: true })));
        setEarlier({ hasMore: page.hasMore, from: page.from, loading: false });
        setSessionId(urlSession);
        // 这条会话此前累计的用时/用量。老 agent 不回报 → null → 统计条只显示几轮几步。
        setServerStats(page.stats || null);
        // 历史会话的上下文占用由详情接口带回来（老 agent 没有这个字段 → 保持不显示）。
        setCtxUsage((d?.session?.context as IvyeaContextUsage | undefined) || null);
        if (d?.session?.usage) setUsage(d.session.usage);
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

  // ── 加载更早的对话 ───────────────────────────────────────────────────────
  //
  // 长会话按轮分页取回来。翻页时**必须补偿滚动位置**：往顶上插内容会把当前视野
  // 整个推下去，用户刚读到的那一段就跑没了。补偿走 scrollTo 而不是直接写
  // scrollTop —— 直接写会被当成"用户翻上去了"，把跟随关掉。
  const loadEarlier = useCallback(async () => {
    if (!sessionId || earlier.loading || !earlier.hasMore) return;
    setEarlier((e) => ({ ...e, loading: true }));
    const el = bodyRef.current;
    const before = el ? el.scrollHeight - el.scrollTop : 0;
    try {
      const d = await ivyeaChatSession(sessionId, { before: earlier.from });
      const page = restoreSession(d?.session);
      setTurns((prev) => [...page.turns.map((t) => ({ ...t, id: uid(), restored: true })), ...prev]);
      setEarlier({ hasMore: page.hasMore, from: page.from, loading: false });
      requestAnimationFrame(() => {
        const node = bodyRef.current;
        if (node) scrollTo(node.scrollHeight - before);
      });
    } catch (e) {
      setEarlier((prev) => ({ ...prev, loading: false }));
      notify("error", errText(e, "更早的对话没能取回来"));
    }
  }, [sessionId, earlier.hasMore, earlier.from, earlier.loading, scrollTo, notify]);

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

  /**
   * agent 掉线时的兜底：走 `/api/assistant/chat` 的多 provider 链纯聊一轮。
   *
   * 能做什么要说清楚：**没有工具、没有知识检索、不进会话库**。它的意义只有一个 ——
   * agent 没起来的时候，别让整个任务台跟着躺下，写文案问概念这类事照样能干。
   * 返回 true = 兜底真的出了字。
   */
  const fallbackChat = useCallback(async (
    prior: Turn[], text: string, aiId: string, signal: AbortSignal,
  ): Promise<boolean> => {
    const msgs: ChatMsg[] = [
      { role: "system", content: FALLBACK_SYSTEM },
      ...prior
        .filter((t) => t.text.trim() && !t.failed)
        .slice(-10)                       // 兜底通道没有会话库，带太多只是白烧 token
        .map((t) => ({ role: t.role, content: t.text } as ChatMsg)),
      { role: "user", content: text },
    ];
    let got = "";
    try {
      patchTurn(aiId, {
        failed: false,
        steps: [noteStep("IvyeaAgent 未就绪，这一轮走备用通道（无工具、不入会话库）", noteSeqFallback++)],
      });
      await streamChat(msgs, (ev) => {
        if (ev.type === "token") {
          got += ev.text;
          patchTurn(aiId, { text: got });
        }
      }, signal);
    } catch {
      return false;                       // 兜底也不通：交回上层按原来的错误报
    }
    return got.trim().length > 0;
  }, [patchTurn]);

  // ── 发一轮 ───────────────────────────────────────────────────────────────
  const send = useCallback(async (raw?: string) => {
    const text = (raw ?? composer.text).trim();
    if (!text || busy) return;

    setFollowUps([]);
    setComposer((c) => ({ ...c, text: "" }));
    // 图和 @ 引用都是**这一轮**的东西，和文字一起清空。
    // 原来清在这个函数的末尾，于是：一轮动辄几分钟，这几分钟里图还挂在输入框上
    // （用户原话："文字出去了，但是图还显示在输入框里面"）；而中止/断链那两个
    // 分支中途 return，压根走不到那行清理，图就一直粘在下一轮上。
    // 后面用的是这两个局部快照，清 state 不影响这一轮要发的内容。
    const sentImages = images;
    const sentPicked = picked;
    setImages([]);
    setPicked([]);
    const userTurn: Turn = { id: uid(), role: "user", text, images: sentImages };
    const aiId = uid();
    const aiTurn: Turn = {
      id: aiId, role: "assistant", text: "", steps: [], skills: [], approvals: [], running: true,
      metrics: { startedAt: Date.now() },
    };
    // 顺手把这一轮之前的上下文抓下来 —— agent 掉线时兜底通道要靠它把对话接上。
    // 从更新函数里取而不是读 turns：send 的依赖里没有 turns，闭包读到的是旧值。
    let priorTurns: Turn[] = [];
    setTurns((prev) => { priorTurns = prev; return [...prev, userTurn, aiTurn]; });
    setBusy(true);
    // **把刚发出的问题顶到视野上方**，答案在它下面生长 —— 这是主流对话产品的
    // 做法，也是"我发的问题看不到了"的正解：原先直接钉在最底部，长回答一出来
    // 就把问题和执行过程顶出屏幕，而那时候滚动又被强制拽回底部，翻都翻不上去。
    pendingTopRef.current = userTurn.id;

    // @ 引用：把选中条目的正文取出来随本轮带下去。取不到的跳过并说明，
    // 不要让用户以为引用了、实际什么都没带。
    let refSystem = "";
    if (sentPicked.length) {
      const parts: string[] = [];
      const failed: string[] = [];
      for (const r of sentPicked) {
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

    // 图片有两条完全不同的用途，分开处理，不要互相拖累：
    //
    //   看图 —— ops 侧视觉旁路读成文字再带下去（主脑没有视觉，图直接发过去会被
    //           agent 拒）。只有 data URL 走得通：/vision/describe 明确只收
    //           data:image/。
    //   作图 —— 图**不进模型**。先在 ops 这边换成 ivyea-ref:// 短句柄，只把句柄
    //           告诉 agent，它拿句柄调 image_generate 就是图生图。让 base64 穿过
    //           工具参数是不可能的：光是抄一遍就能撑爆上下文，抄错一位图还废了。
    //           远程地址（比如上一轮出的图）本来就能直接当原图，原样带过去。
    let visionSystem = "";
    let imageRefSystem = "";
    const attachments: IvyeaChatAttachment[] = [];
    if (sentImages.length) {
      const local = sentImages.filter((u) => u.startsWith("data:"));
      const remote = sentImages.filter((u) => !u.startsWith("data:"));
      // **一张一张读**，不是一次把几张图丢过去拿回一整段文字：那样句柄和描述对不上
      // 号，模型下一轮说"第 2 张里的表格"就会张冠李戴。
      const failed: any[] = [];
      const read = await Promise.all(local.map(async (u) => {
        const [desc, handle] = await Promise.all([
          visionDescribe([u])
            .then((d) => ({ text: String(d?.text || "").trim(), by: String(d?.provider || "") }))
            .catch((e: any) => { failed.push(e); return { text: "", by: "" }; }),
          // 拿不到句柄就当没有 —— 把 undefined 拼进提示词，模型会拿着
          // "第 1 张：undefined" 去调作图，然后报一个谁也看不懂的错。
          imageRef(u).then(({ ref }) => (typeof ref === "string" ? ref.trim() : "")).catch(() => ""),
        ]);
        return { ...desc, ref: handle };
      }));
      if (failed.length) {
        const why = errText(failed[0], "图片没能读出来，这一轮按纯文字继续。可在「系统配置 → AI 服务」配一个视觉模型。");
        notify("error", failed.length > 1 ? `${failed.length} 张图没读出来：${why}` : why);
      }
      for (const r of read) {
        if (r.text) attachments.push({ kind: "image", ref: r.ref, by: r.by, text: r.text });
      }
      // 老 agent（< v1.15.3）不认识 attachments —— 那就还按老路子把这段文字塞进
      // system。**新 agent 上不要重复塞**：同一段描述在一轮里出现两遍纯属浪费上下文。
      if (!agentTakesAttachments && attachments.length) {
        visionSystem = "[用户附图 —— 由视觉模型读出的内容]\n" +
          attachments.map((a, i) => `第 ${i + 1} 张${a.by ? `（${a.by}）` : ""}：\n${a.text}`).join("\n\n");
      }
      const handles = [...read.map((r) => r.ref).filter(Boolean), ...remote];
      if (handles.length) {
        imageRefSystem =
          "[用户附图的原图句柄]\n" +
          handles.map((h, idx) => `第 ${idx + 1} 张：${h}`).join("\n") +
          "\n要以这些图为原图作图/改图时，把对应句柄原样填进 image_generate 的 image_urls。";
      }
    }

    const startedAt = Date.now();
    // 计时只用局部变量，收尾时一次性写进 turn。**绝不能每个 token 都 setState** ——
    // 这条流一秒钟能来上百个 token，那样等于把刚做完的"按帧批量落地"又拆回去。
    let firstTokenAt = 0;
    let lastTokenAt = 0;
    let turnUsage: any = null;
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
    let pendingThink = "";
    let flushRaf = 0;
    const flushTokens = () => {
      flushRaf = 0;
      const add = pending;
      const think = pendingThink;
      pending = "";
      pendingThink = "";
      if (add) patchTurn(aiId, (t) => ({ text: t.text + add }));
      // 思考流走同一帧：它比正文更碎（模型逐字想），一条一次 setState 会把
      // 活动行刷成每秒几十次重绘。只留尾部，活动行只看最后一句。
      if (think) {
        // reasoning 装的是**还没成段的那一段**：边想边显示，想完（去调工具了）就被
        // flushThought 收成一段落进 thoughts。只留开头，理由见 REASONING_HEAD。
        patchTurn(aiId, (t) => ({
          reasoning: ((t.reasoning || "") + think).slice(0, REASONING_HEAD),
        }));
      }
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
    /**
     * 把"到此为止想的这一段"收成叙述里的一条。
     *
     * 触发点是**它开始动手**（下一个 step 到达）或这一轮收尾 —— 那才是一段思考
     * 真正结束的时刻。按句号切会把一段完整的推理碎成几十条，铺在页面上是字墙。
     */
    const flushThought = () => {
      finishFlush();                       // 先把还在缓冲里的思考落地，别丢半句
      patchTurn(aiId, (t) => {
        const text = (t.reasoning || "").trim();
        if (!text) return {};
        const rows = [...(t.thoughts || []), { seq: (t.steps || []).length, text }];
        // 一轮里思考段数有上限：几百段时界面自己会折叠，但 state 也不该无限长。
        return { thoughts: rows.slice(-THOUGHTS_MAX), reasoning: "" };
      });
    };

    try {
      await ivyeaAgentChatStream(
        {
          message: text,
          session_id: sessionId || undefined,
          workspace: composer.workspace && composer.workspace !== "默认工作区" ? composer.workspace : undefined,
          skill: composer.skill || undefined,
          auto_skill: !composer.skill,
          // 本轮主脑。老 agent 不认识这个字段会直接忽略，且没选模型时 ops 后端
          // 会整个剔除它 —— 两个方向都安全。
          model: modelSwitchable && modelPick ? modelPick : undefined,
          plan_mode,
          approval,
          // 人设排最前：它定义"以什么身份、什么判断标准作答"，逻辑上先于本轮材料。
          system: [
            composer.system ? "[角色设定 —— 按这个身份和判断标准作答]\n" + composer.system : "",
            visionSystem,
            imageRefSystem,
            refSystem,
          ].filter(Boolean).join("\n\n") || undefined,
          // 附图走 attachments，agent 会把它并进**这一轮的 user 消息**（跟着历史和
          // 存档走）；塞在 system 里的话，system 每轮重建、落盘时被本轮那份覆盖，
          // 下一轮模型手里一个字都没有，只能否认自己看过图。
          attachments: attachments.length ? attachments : undefined,
          persist: true,
          inject_retrieval: true,
          // 要模型的思考流：活动行上"它在想什么"比"它在调哪个工具"更贴近现在发生了什么。
          // 老 agent 不认识这个字段会直接忽略，老前端根本不会发它 —— 两个方向都安全。
          stream_reasoning: true,
          ops_context: { board: "console", pathname: "/console" },
        },
        {
          onFileChange: (d) => setFileChanges((prev) => [...prev, d]),
          onStart: (d) => {
            if (d?.session_id) {
              // 新会话要**立刻**进左栏。原来只在整轮跑完时广播一次，而一轮动辄几分钟——
              // 这段时间里左栏看不见这条会话，用户只能刷新整页才看到它。
              // 带上 id：左栏取回来发现还没有这条（agent 侧刚落库，有一拍延迟）会自己再取一次。
              if (d.session_id !== liveSid) notifyConsoleSessionsChanged(d.session_id);
              liveSid = d.session_id;
              setSessionId(d.session_id);
              // 新会话的 id 此前只存进内存 state，没进网址。切换页面再切回时组件重建，
              // 找不到该恢复哪条会话，界面回空白 —— 用户会以为指令没发出去，然后把
              // 同一句话再打一遍（真实投诉就是这么来的）。把 id 同步写进 URL，让
              // 「新建会话」和「点历史会话」走同一套 ?session= 恢复逻辑。
              //
              // replace 不污染后退历史；读的是 window.location 而不是渲染闭包里的
              // urlSession —— 这个回调活在流里，闭包是发消息那一刻的旧值。地址栏已经
              // 指向这条会话时（在历史会话里继续问）就不写，省掉每轮一次白折腾。
              // 恢复 effect 那边 urlSession===sessionId 会直接 return，不会重复拉取、
              // 也不会 abort 掉正在跑的这一轮（e2e/session-url.mjs 钉住了这一条）。
              if (new URLSearchParams(window.location.search).get("session") !== d.session_id) {
                navigate(`/console?session=${encodeURIComponent(d.session_id)}`, { replace: true });
              }
            }
            if (d?.model) setModel(typeof d.model === "string" ? d.model : d.model?.model || "");
            if (typeof d?.read_only === "boolean") setReadOnly(d.read_only);
          },
          onContext: (d) => setCtxUsage(d),
          onSkillMatch: (d) => {
            patchTurn(aiId, { skills: (d?.skills || []) as MatchedSkill[] });
          },
          onStep: (ev) => {
            // 先把"想到现在为止的这一段"收成一条，再记这一步 —— 顺序就是叙述的顺序：
            // 想 → 做 → 想 → 做。收在这里而不是收在思考流里，是因为"这一批想完了"
            // 的唯一可靠信号就是它开始动手了。
            flushThought();
            patchTurn(aiId, (t) => ({ steps: mergeStep(t.steps || [], stepFromEvent(ev)) }));
          },
          // 计划**当场**落地：原来只在 onFinal 收一次，于是"接下来要干什么"要等这一轮
          // 跑完才看得到 —— 而那正是最不需要它的时刻。（老 agent 不发这条事件，
          // 那就还是只有收尾那一份，界面上不显示下一步，不编。）
          onTodos: (d) => {
            if (Array.isArray(d?.todos)) setTodos(d.todos as RailTodo[]);
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
            const now = Date.now();
            if (!firstTokenAt) firstTokenAt = now;
            lastTokenAt = now;
            if (!flushRaf) flushRaf = window.requestAnimationFrame(flushTokens);
          },
          // 模型的思考流。没有会思考的模型时这条永远不来 —— 活动行退回显示工具步骤，
          // 不伪造一句"正在思考"。
          onReasoning: (d) => {
            const t = String(d?.text || "");
            if (!t) return;
            pendingThink += t;
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
            // final 到达时，手里常常还攥着没落地的一帧 token —— 收尾那几个字和 final
            // 多半在**同一个网络分片**里到，rAF 还没来得及跑。所以这里分两种走法：
            //   · final 自带规范文本（引证门通过后的终稿）→ 草稿作废，整体替换；
            //   · final 不带文本（老 agent、兜底通道）→ 必须把那一帧**补落地**。
            // 原来两种情况都 cancelFlush()，而 cancelFlush 会把 pending 清空 ——
            // 于是不带文本的那一档，回答的最后一句就凭空少了。
            if (d?.text) cancelFlush();
            else finishFlush();
            if (d?.session_id) setSessionId(d.session_id);
            if (typeof d?.readonly_blocked === "number" && d.readonly_blocked > 0) {
              patchTurn(aiId, { readonlyBlocked: d.readonly_blocked });
            }
            if (Array.isArray(d?.todos)) setTodos(d.todos);
            if (d?.usage) { setUsage(d.usage); turnUsage = d.usage; }
            // 收尾这一份算的是"本轮结束后"的位置 —— 下一轮就是从这里起步的。
            if (d?.context) setCtxUsage(d.context as IvyeaContextUsage);
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
        // 连会话都没建起来、一个字也没出来 —— 多半是 agent 没起。退回
        // /api/assistant/chat 的多 provider 兜底链，纯聊这一档至少还能用。
        // （这条退路原来挂在 AI 问答那一页上，那页收进任务台后搬到了这里。）
        const served = await fallbackChat(priorTurns, text, aiId, ctrl.signal);
        if (!served) {
          patchTurn(aiId, { failed: true, running: false, text: String(e?.message || "请求失败") });
        }
      }
    } finally {
      finishFlush();
      flushThought();          // 最后一批思考后面没有 step 了，收尾时收进去
      window.clearInterval(tick);
      // 这一轮完了通知左栏再取一次：预览、时间要更新，而标题是服务端跑完这一轮才
      // 由模型起的（SessionRail 收到这条会延后再补取一次，正好接住它）。
      if (liveSid) notifyConsoleSessionsChanged(liveSid);
      patchTurn(aiId, (t) => ({
        running: false,
        elapsedMs: Date.now() - startedAt,
        // 断链/取消的轮次也把测到的部分留下 —— 半截数据仍然能说明"卡在哪"，
        // 整轮丢弃反而让统计条在最需要解释的那一轮上变成空白。
        metrics: {
          ...(t.metrics || { startedAt }),
          firstTokenAt: firstTokenAt || undefined,
          lastTokenAt: lastTokenAt || undefined,
          usage: turnUsage || t.metrics?.usage,
        },
      }));
      setBusy(false);
      abortRef.current = null;
    }

    notifyConsoleSessionsChanged();     // 新会话进左栏 / 已有会话更新时间
    if (finalText.trim()) void loadFollowUps(text, finalText);
    // images / picked 必须在依赖里：send 里读了它们。漏掉的话这个回调会闭包住
    // 旧值 —— 贴完图不打字直接发，图就丢了（之前靠"总会先打字"侥幸没暴露）。
  }, [composer, busy, sessionId, images, picked, agentTakesAttachments,
      patchTurn, notify, loadFollowUps]);

  /**
   * 点正文里的图 → 收进输入框，作为下一轮的原图。
   *
   * 用事件委托而不是给 MarkdownReport 传回调：那个渲染器是七八个板块共用的纯展示
   * 组件，不该认识任务台。它只在图上留了 `data-md-img`，谁想用谁自己接。
   */
  const pickAnswerImage = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const src = (e.target as HTMLElement)?.dataset?.mdImg;
    if (!src) return;
    setImages((prev) => {
      if (prev.includes(src)) return prev;
      if (prev.length >= 4) {
        notify("warn", "最多带 4 张图，先去掉一张再选。");
        return prev;
      }
      notify("success", "已把这张图放进输入框，说一下要怎么改。");
      return [...prev, src];
    });
  }, [notify]);

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

  /**
   * 把选中的模型写成**全局默认**（写 ops 的系统配置，再由后端下推给 agent）。
   *
   * 换 provider 时必须把 ops 存的那把 key 一起清空 —— sync_model_settings 会把
   * `ivyea_agent_api_key` 推成**新 provider** 对应的环境变量：拿 A 家的 key 覆盖
   * B 家的，B 家原本配好的 key 就这么没了，而且毫无征兆。清空只是"不再下推"，
   * agent 自己 .env 里各家已有的 key 一个都不动。
   */
  const setModelAsDefault = useCallback(async (id: string) => {
    const { provider, model: picked } = splitModelId(id);
    if (!provider || !picked) return;
    try {
      const cur = await getSettings();
      const changingProvider = String(cur?.settings?.ivyea_agent_provider || "") !== provider;
      await patchSettings({
        ivyea_agent_provider: provider,
        ivyea_agent_model: picked,
        ...(changingProvider ? { ivyea_agent_api_key: "", ivyea_agent_base_url: "" } : {}),
      });
      // 已经是默认了，就不该再逐轮覆盖 —— 留着的话用户以后改了默认却不生效。
      setModelPick("");
      setModel(picked);
      notify("success", `已把 ${picked} 设为默认主脑（对所有用户和定时任务生效）`);
    } catch (e: any) {
      notify("error", errText(e, "设为默认失败"));
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
      modelValue={modelPick}
      onModelChange={setModelPick}
      modelSwitchable={modelSwitchable}
      onModelSettings={() => openSettings()}
      onModelDefault={setModelAsDefault}
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
          /* Hero 版式对标 DeepSeek Harness：**只有 logo + 一行标题**。
             原来那两行副标题（"说一句需求就行 —— Ivyea 会挑技能、调板块能力…"）
             是写给第一次来的人看的，但首页是每天都要经过的地方 —— 一句每天都
             要读一遍、读完什么也不用做的话，就是噪音。它的内容已经在使用手册里。 */
          <div className="cc-hero">
            <div className="cc-hero-brand">
              <img src="/ivyea-logo.png" alt="Ivyea" className="cc-hero-logo" />
              <h1 className="cc-hero-title">意念所至，行动随行</h1>
            </div>
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
                    <Icon name={s.icon} size={15} />{s.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          /* ── 会话态 ────────────────────────────────────────────────── */
          <>
            {/*
              * `scrolled` = 还没滚到底。底部那层渐隐（静谧/琉璃皮肤的
              * .cc-thread-wrap::after）只在这时候才该出现 —— 它的意思是"下面还有，
              * 正滚过去"。已经到底了还罩着，就变成把**最后那点内容**（回答的末行、
              * 「复制/重新生成」那一行）蒙上一层灰，看起来像渲染糊了。
              * 用户原话：复制按钮好像有点模糊。
              */}
            <div className={"cc-thread-wrap" + (atBottom ? "" : " scrolled")}>
              <div className="cc-thread scroll-thin" ref={bodyRef}>
                {/*
                  * 内层这一圈是为了**把内容顶到底部**（margin-top:auto）。
                  * 对话短的时候，内容原本贴在页面最上面，中间空出一大片 —— 而正在跑
                  * 的执行叙述恰恰长在最下面，于是用户盯着的输入框上方是一片空白，
                  * 要抬头去屏幕顶上找状态。内容不满一屏时贴底，超过一屏时 auto 失效、
                  * 照常滚动，两种情况都对。
                  */}
                <div className="cc-thread-inner">
                {earlier.hasMore && (
                  <div className="cc-earlier">
                    <button type="button" onClick={() => void loadEarlier()} disabled={earlier.loading}>
                      {earlier.loading ? "正在取…" : `加载更早的对话（还有 ${earlier.from} 轮）`}
                    </button>
                  </div>
                )}
                {turns.map((t, ti) =>
                  t.role === "user" ? (
                    <div className="cc-user" key={t.id} data-turn={t.id}>
                      <div className="cc-user-col">
                        {/* 我发的图。它在气泡**上面**：先看到发了什么图，再看到问了什么，
                            和输入框里的排布一致。历史会话里取的是原图句柄，图被清理掉
                            （只留最近 200 张）时不留一块碎图，直接把这一格摘掉。 */}
                        {!!t.images?.length && (
                          <div className="cc-user-imgs">
                            {t.images.map((src, i) => (
                              <img key={i} src={src} alt="附图" loading="lazy"
                                   onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
                            ))}
                          </div>
                        )}
                        {!!t.text && <div className="cc-bubble">{t.text}</div>}
                      </div>
                    </div>
                  ) : (
                    <div className="cc-ai wb-enter" key={t.id}>
                      <ActivityFeed
                        steps={t.steps || []}
                        thoughts={t.thoughts || []}
                        skills={t.skills || []}
                        elapsedMs={t.elapsedMs}
                        running={t.running}
                        liveThought={t.reasoning}
                      />
                      {t.text && (
                        <div
                          className={"cc-answer" + (t.failed ? " cc-answer-error" : "")}
                          onClick={pickAnswerImage}
                        >
                          {t.failed ? t.text : <MarkdownReport text={t.text} />}
                        </div>
                      )}
                      {/*
                        * 正文和输入框之间的收尾。跑的过程中不出现 —— 正在写的一段
                        * 话底下挂一排"复制/重新生成"，等于请用户复制一份还没写完的
                        * 东西。跑完了才给。
                        */}
                      {t.text && !t.running && !t.failed && (
                        <AnswerActions
                          text={t.text}
                          onRegenerate={
                            // 往回找**最近的**那条提问，不能只看前面一格：一次提问
                            // 常常对应好几个 assistant 轮次（agent 边做边说，中间
                            // 插着执行过程），那时候前一格是 assistant，按"前一格"
                            // 判断的话最后一轮就没有这个按钮了 —— 用户截图里只剩
                            // 一个"复制"就是这么来的。
                            // 一条都找不到（恢复出来的半截会话只存了回答）就不给这
                            // 个按钮，别放一个点了没反应的开关。
                            (() => {
                              if (busy) return undefined;
                              for (let i = ti - 1; i >= 0; i--) {
                                if (turns[i].role === "user") {
                                  const q = turns[i].text;
                                  return () => void send(q);
                                }
                              }
                              return undefined;
                            })()
                          }
                        />
                      )}
                      {/*
                        * 这里原来还有一行「⟳ 正在处理 · 12.3s」。它和上面那条活动行
                        * **说的是同一件事**：活动行左边在转、右边的 tail 就是同一个
                        * 12.3s，两行叠在一起只是把同一个状态说了两遍，而且两行的左
                        * 缩进还对不齐（活动行有 11px 内边距，这行没有）。运行态的
                        * 唯一指示就是活动行。
                        */}
                      {/*
                        * 只读档挡下了写操作 —— 这不是出错，也不会有待审批项。
                        * 说清楚是哪一档挡的、去哪儿换，比让用户对着"被拦截"三个字
                        * 猜半天强。按钮直接把档位换掉，换完他自己重发。
                        */}
                      {!!t.readonlyBlocked && (
                        <div className="cc-blocked">
                          <span className="cc-blocked-mark">⊘</span>
                          <span>
                            这一轮有 {t.readonlyBlocked} 个写操作被「只读」档挡下了。
                            只读档只分析、不改动，**也不会产生待审批项** —— 待审批页看不到东西是正常的。
                            要真执行，把档位换成「审批放行」（每次写入问你一下）再发一遍。
                          </span>
                          <button type="button" className="cc-blocked-btn"
                                  onClick={() => patch({ approval: "ask" })}>
                            换成「审批放行」
                          </button>
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
              </div>
              {/* 脱离底部才出现 —— 跟随已经关掉了，得给一条回去的路。 */}
              {!atBottom && (
                <button type="button" className="cc-tobottom" onClick={scrollToBottom}
                        title="回到最新内容">
                  ↓ 回到底部
                </button>
              )}
            </div>
            <div className="cc-dock">
              {/* 跑起来之后，"现在在干什么 / 接下来干什么"钉在输入框上方，不随对话滚走。
                  长任务里对话区早就滚出去几屏了，把状态留在那上面等于没有状态。 */}
              <LiveDock
                running={busy && !atBottom}
                steps={liveTurn?.steps || []}
                reasoning={liveTurn?.reasoning || ""}
                elapsedMs={liveTurn?.elapsedMs}
                todos={todos}
                onStop={stop}
              />
              {composerNode(true)}
              {/* 进度条和统计条同一行：它们回答的是同一类问题（这轮花了多少），
                  分两行钉在输入框底下会把对话区又压掉一截。DockMeta 保证这一行
                  **永远不换行**（装不下就缩字号）—— 折行会把输入框整块往上顶。 */}
              <DockMeta>
                <ContextMeter usage={ctxUsage} />
                <StatsBar stats={sessionStats} />
              </DockMeta>
            </div>
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
