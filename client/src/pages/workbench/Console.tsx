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
import { imageRef, streamChat, type ChatMsg } from "../../api/assistant";
import { restoreSession, type RestoredDoc } from "../../lib/sessionRestore";
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
import { createTurnStream } from "../../lib/turnStream";
import { aggregateStats, clockText, fmtDuration, mergeStats, type ServerStats, type TurnMetrics } from "../../lib/turnStats";
import { type MatchedSkill, type Thought } from "../../components/console/ActivityFeed";
import TurnBody from "../../components/console/TurnBody";
import StatsBar from "../../components/console/StatsBar";
import ContextMeter from "../../components/console/ContextMeter";
import DockMeta from "../../components/console/DockMeta";
import ApprovalCard, { ApprovalReceipt, groupApprovals } from "../../components/console/ApprovalCard";
import QuestionCard from "../../components/console/QuestionCard";
import Composer, { approvalPayload, type ApprovalMode, type ComposerDoc, type ComposerRef, type ComposerValue } from "../../components/console/Composer";
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
  ivyeaAgentSessionLive,
  ivyeaAwaitSessionAnswer,
  ivyeaChatCancel,
  ivyeaChatInject,
  ivyeaChatPermission,
  ivyeaChatQuestion,
  ivyeaChatSession,
  ivyeaKnowledgeFile,
  ivyeaKnowledgeFiles,
  ivyeaKnowledgeUpload,
  ivyeaSessionFile,
  notifyConsoleSessionsChanged,
  ivyeaOpsTools,
  ivyeaSkills,
  visionDescribe,
  type IvyeaChatAttachment,
  type ConsolePreset,
  type IvyeaContextUsage,
  type IvyeaFileChange,
  type IvyeaAutoDecision,
  type IvyeaPermissionRequest,
  type IvyeaQuestionRequest,
  type IvyeaSkillInfo,
} from "../../api/ivyeaAgent";
import { splitModelId } from "../../components/console/ModelPicker";
import { getSettings, patchSettings } from "../../api/settings";
import { errText } from "../../lib/errText";
import { openSettings } from "../../components/SettingsDialog";
import { openLightbox } from "../../lib/lightbox";

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
/**
 * 还没发出去的追加指令，按会话存一份。
 *
 * 它此前只活在内存里：轮次跑着的时候排了两句话，手一抖关了标签页 —— 那两句就没了，
 * 而用户以为自己已经说过了。这是"说出去的话必须有着落"这条承诺的最后一段缺口
 * （agent 侧的收件箱只在活轮期间有效，也不该替浏览器记这个）。
 */
const QUEUE_KEY = "ivyea-ops.console.queue";

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
  /**
   * 正文的分段：每段是"两次工具调用之间说的那段话"，seq = 说完时已有多少步。
   *
   * 有了它，一轮才能按发生顺序铺成「说一段 → 做几件事 → 再说一段」，而不是
   * 所有工具堆一坨、所有话拼一坨（用户原话："一大堆叠在一起"）。
   * 从存档恢复的轮次没有它 —— 那种情况下每条 assistant 消息本来就是独立一轮。
   */
  segments?: { seq: number; text: string }[];
  skills?: MatchedSkill[];
  /** 这一轮开口前自动召回了哪几条长期记忆（agent 回报的 memory_recall 事件）。
   *  界面必须自己说出来 —— 模型经常不提，用户会以为记忆压根没生效。 */
  memoryRecall?: string[];
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
  /** 这一轮带的会话附件（只这轮用、没进知识库）。只存名字和原件地址，不存正文。 */
  docs?: RestoredDoc[];
  /**
   * 这一格发生的时刻（毫秒）。user = 说出这句话的时刻，assistant = 这一轮收尾的时刻。
   * 界面上就是气泡旁的「09:46:12」和回答末尾的「结束于 09:49」。
   *
   * 本次页面跑的轮次由前端填；从存档恢复的轮次由 agent 的 turn_times 带回来
   * （客户端自己掐的表在刷新/换机器之后一个数都没有）。
   */
  at?: number;
  endedAt?: number;
  /** 本轮弹出的选项卡（模型拿不准，把岔路摆出来让人点）。 */
  questions?: { req: IvyeaQuestionRequest; answers?: Record<string, string>; auto?: boolean }[];
  /** 本轮里替用户定的那些选择（没人在超时前选，按推荐项走了）。 */
  autoDecisions?: IvyeaAutoDecision[];
  /** 用户在这一轮跑着的时候补的话（已插进当前上下文）。 */
  injected?: { id: string; text: string; ts?: number }[];
};

/**
 * 一轮的收尾行：「结束于 09:31 · 用时 9.0秒」。两半各自可能缺（跑到一半被打断
 * 就没有 endedAt），缺哪半就少哪半，都没有就返回空串。
 *
 * 它跟着「复制 / 重新生成」那一排走，不再单占一行 —— 只有那排按钮不出现的时候
 * （回答失败、或只有执行过程没有正文）才退回独立的一行。
 */
function turnClockText(t: Turn): string {
  const ended = t.endedAt ? `结束于 ${clockText(t.endedAt)}` : "";
  const took = t.elapsedMs ? `用时 ${fmtDuration(t.elapsedMs)}` : "";
  return ended && took ? `${ended} · ${took}` : ended || took;
}

/**
 * 一条说出去、但还没被这一轮读到的追加指令。
 *
 * `agentId` 是 agent 收下时给的编号 —— 一轮结束后靠它认领"哪几条真被读到了"，
 * 剩下的补发成下一轮。
 */
type QueuedFollowUp = {
  id: string;
  text: string;
  state: "sending" | "injected" | "queued";
  agentId?: string;
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

/**
 * 嵌入模式（右下角悬浮球用）。
 *
 * 悬浮球以前是**另一份**简化实现：只有流式正文和一行工具叙述，没有审批档位、
 * 没有确认卡、没有步骤流、没有模型切换……而任务台这边一直在长。两份各自演化的
 * 结果就是"同一个 Agent，在两个入口下能力差一大截"。
 *
 * 所以不再补功能，而是**让悬浮球直接渲染这一份**。embedded 只关掉三件与"页面"
 * 绑定的事：读写地址栏、右侧产物栏、大 Hero。其余全部照旧 —— 以后任务台加什么，
 * 悬浮球自动就有。
 */
export type ConsoleEmbedProps = {
  embedded?: boolean;
  /** 嵌入模式下要打开的历史会话（外壳的历史列表选中的那条）。 */
  sessionId?: string;
  /** 会话 id 变化时回传给外壳（它要拿来高亮历史列表）。 */
  onSessionChange?: (id: string) => void;
  /** 数字一变就开新会话。嵌入模式专用 —— 见下面为什么不走全局事件。 */
  resetSignal?: number;
};

function ConsoleInner({ embedded = false, sessionId: embedSession = "",
                        onSessionChange, resetSignal = 0 }: ConsoleEmbedProps = {}) {
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
  // 轮次跑着的时候用户又说的话。空数组 = 没有待处理的追加指令。
  const [queue, setQueue] = useState<QueuedFollowUp[]>([]);
  // 队列落地：关标签页 / 刷新之后，还没发出去的那几句要还在。
  // **只恢复"还没进 agent"的那些**：已经插进某一轮的（injected）属于那一轮，
  // 恢复出来会让用户以为它还没发。
  // 正在请求 agent 停这一轮（按钮转成"正在停止…"，防连点）。
  const [stopping, setStopping] = useState(false);
  const stoppingRef = useRef(false);
  /** 这一轮是被停掉的 —— 收尾时据此跳过跟进建议。 */
  const cancelledRef = useRef(false);
  /** 已经从盘上恢复过待发队列的那个会话格子。没恢复完不许写盘（见下面两个 effect）。 */
  const restoredQueueRef = useRef<string>("");
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
  /** 这一轮要带下去的会话附件（文档）。**不入知识库**，发完就随轮次留在存档里。 */
  const [docs, setDocs] = useState<ComposerDoc[]>([]);
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
      // 嵌入模式下这一份没有自己的地址栏（外壳可能停在任意板块），一律不碰。
      if (!embedded && window.location.search) navigate("/console", { replace: true });
    };
    // 嵌入模式**不听这个全局事件**：它是侧边栏「新建任务」按下时广播的，说的是
    // "页面那一份开一轮新的"。两份都听，就会出现"在悬浮球里点新会话，页面上正跑着
    // 的那轮也被清掉"这种互相干扰。嵌入模式改用 resetSignal（点对点）。
    if (!embedded) window.addEventListener(CONSOLE_NEW_EVENT, handler);
    return () => window.removeEventListener(CONSOLE_NEW_EVENT, handler);
  }, [resetSession, navigate, embedded]);

  const resetSeenRef = useRef(resetSignal);
  useEffect(() => {
    if (!embedded || resetSignal === resetSeenRef.current) return;
    resetSeenRef.current = resetSignal;
    resetSession();
  }, [embedded, resetSignal, resetSession]);

  // 从别的板块带过来的预填：?q= 提示词、?skill= 预选技能（能力市场「用这个技能」
  // 走的就是它）。用完即从地址栏抹掉，免得刷新时又套一遍。
  useEffect(() => {
    if (embedded) return;      // 地址栏是页面那一份的，嵌入模式不掺和
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
  // 打开哪条历史会话：页面模式看地址栏（左栏高亮、刷新、分享链接都靠它），
  // 嵌入模式听外壳传进来的 prop（悬浮球有自己的历史列表）。
  const urlSession = embedded
    ? embedSession
    : (new URLSearchParams(location.search).get("session") || "");
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
        // 这条会话正跑着一轮 —— 接进去把执行过程补上。没有这一句，切走再切回来
        // 看到的就是"只剩自己发的那句话"，而后台其实一直在干活。
        if (d?.live?.running) void attachLiveTurn(urlSession, d.live.started_ms);
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
        if (embedded) onSessionChange?.("");
        else navigate("/console", { replace: true });
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

  // ── 事件流 → 界面状态（直连与"接进活轮"共用同一份，见 lib/turnStream）──────
  const makeTurnStream = useCallback((aiId: string, extra?: {
    onSessionId?: (id: string) => void;
    onFinal?: (d: any) => void;
    onCancelled?: (d: any) => void;
  }) => createTurnStream({
    patch: (patch) => patchTurn(aiId, patch as any),
    notify,
    setFileChanges,
    setTodos,
    setCtxUsage,
    setModel,
    setReadOnly,
    onSessionId: extra?.onSessionId,
    onFinal: extra?.onFinal,
    onCancelled: extra?.onCancelled,
  }), [patchTurn, notify]);

  /**
   * 接进一条**已经在跑**的轮次，把进度补进 aiId 这一格。
   *
   * 用在两处：断链之后（管子断了但轮次没断），以及打开一条正在跑的会话时。
   * 回 false = 接不上（老 agent 没有这个端点 / 这一轮已经收尾）。
   */
  const followLive = useCallback(async (
    sid: string, aiId: string, signal?: AbortSignal,
  ): Promise<boolean> => {
    const stream = makeTurnStream(aiId);
    let attached = false;
    try {
      await ivyeaAgentSessionLive(sid, {
        ...stream.handlers,
        onLiveBegin: () => { attached = true; },
      }, { signal });
    } catch {
      return attached;      // 404 = 这条会话没有活轮；其余按接不上处理
    } finally {
      stream.finish();
    }
    return attached;
  }, [makeTurnStream]);

  /**
   * 打开会话时发现它正在跑 —— 造一格 assistant 轮次接住进度。
   *
   * 这就是"切走再切回来 / 刷新 / 换台机器"能看到执行过程的那条路。此前这些情况下
   * 页面上只剩用户自己发的那句话（它在轮次开始时就落盘了），执行过程和正文要等
   * 整轮跑完再刷新一次才"一下子全出来"。
   */
  const attachLiveTurn = useCallback(async (sid: string, startedMs?: number) => {
    const aiId = uid();
    const startedAt = startedMs || Date.now();
    setTurns((prev) => [...prev, {
      id: aiId, role: "assistant", text: "", steps: [], skills: [], approvals: [],
      running: true, metrics: { startedAt },
    }]);
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const tick = window.setInterval(
      () => patchTurn(aiId, { elapsedMs: Date.now() - startedAt }), 250);
    const ok = await followLive(sid, aiId, ctrl.signal);
    window.clearInterval(tick);
    patchTurn(aiId, (t) => ({
      running: false,
      elapsedMs: Date.now() - startedAt,
      // 同上：本地 abort（停不掉时的兜底、或离开这条会话）不代表那一轮结束了。
      ...(ctrl.signal.aborted ? {} : { endedAt: Date.now() }),
      // 走到这里的 abort 是"不看了"，不是"停了" —— 真停止走 /chat/cancel，
      // 那条路上流会正常收尾。说成"已停止"会让人以为任务被掐了，然后对着还在
      // 动的会话发懵。
      ...(ctrl.signal.aborted && !t.text
        ? { text: "（已停止查看，这一轮仍在后台继续；回到这条会话可以再接上）" }
        : {}),
    }));
    setBusy(false);
    abortRef.current = null;
    if (!ok) {
      // 一格都没接到（这一轮刚好在我们接进去之前收尾了）：把空壳撤掉，
      // 别在时间线上留一格什么都没有的空回答。
      setTurns((prev) => prev.filter((t) => t.id !== aiId || t.text || (t.steps || []).length));
    }
    notifyConsoleSessionsChanged(sid);
  }, [followLive, patchTurn]);

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
    const sentDocs = docs;
    const sentPicked = picked;
    setImages([]);
    setDocs([]);          // 同理：附件也别粘在下一轮上
    setPicked([]);
    const userTurn: Turn = {
      id: uid(), role: "user", text, images: sentImages,
      // 本轮发出去的附件名留在这一格里。刷新之后是从存档里按注入段落还原的
      // （sessionRestore.attachedDocs），两条路要显示成同一个样子。
      ...(sentDocs.length ? { docs: sentDocs.map((d) => ({ name: d.name, url: d.url })) } : {}),
      at: Date.now(),
    };
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

    // 会话附件（文档）。**放在图片那个 if 之外** —— 只传文档不传图也要带下去。
    // agent 按 kind 分流成独立的一段，有自己的份数和字数上限，不跟图片挤同一个池子。
    for (const d of sentDocs) {
      if (d.text) attachments.push({ kind: "document", name: d.name, ref: d.url, text: d.text });
    }

    const startedAt = Date.now();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let liveSid = sessionId;
    /** 断链后从落盘里捞回来的回答（正常路径下是空的，正文在 stream.text()）。 */
    let recovered = "";
    const sentAt = Math.floor(Date.now() / 1000);
    const { plan_mode, approval } = approvalPayload(composer.approval);

    const tick = window.setInterval(
      () => patchTurn(aiId, { elapsedMs: Date.now() - startedAt }),
      250,
    );

    // 事件 → 界面状态只此一份（lib/turnStream）。**从别处接进这一轮时走的是同一份**
    // —— 自己发起看到的过程，和切回来看到的过程，必须是同一个东西。
    const stream = makeTurnStream(aiId, {
      onSessionId: (id) => {
        // 新会话要**立刻**进左栏。原来只在整轮跑完时广播一次，而一轮动辄几分钟 ——
        // 这段时间里左栏看不见这条会话，用户只能刷新整页才看到它。
        if (id !== liveSid) notifyConsoleSessionsChanged(id);
        liveSid = id;
        setSessionId(id);
        // 新会话的 id 此前只存进内存 state，没进网址。切换页面再切回时组件重建，
        // 找不到该恢复哪条会话，界面回空白 —— 用户会以为指令没发出去，然后把同一句
        // 话再打一遍（真实投诉就是这么来的）。把 id 同步写进 URL，让「新建会话」和
        // 「点历史会话」走同一套 ?session= 恢复逻辑。
        //
        // replace 不污染后退历史；读的是 window.location 而不是渲染闭包里的
        // urlSession —— 这个回调活在流里，闭包是发消息那一刻的旧值。
        if (embedded) {
          onSessionChange?.(id);
        } else if (new URLSearchParams(window.location.search).get("session") !== id) {
          navigate(`/console?session=${encodeURIComponent(id)}`, { replace: true });
        }
      },
      onFinal: (d) => {
        if (d?.session_id) setSessionId(d.session_id);
        if (d?.usage) setUsage(d.usage);
      },
      onCancelled: () => { cancelledRef.current = true; },
    });

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
          // 这一端有人在看，也画得出选项卡 —— 模型拿不准时才该把岔路弹过来。
          // 不带这个字段的调用方（服务端自己读流的那几处）不会收到选项卡。
          interactive: true,
          ops_context: { board: "console", pathname: "/console" },
        },
        stream.handlers,
        { signal: ctrl.signal },
      );
    } catch (e: any) {
      stream.finish();
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
        // 传输断链，但 serve 端的轮次独立继续执行 —— 不重发（重发会把同一个多分钟的
        // agentic 轮次再跑一遍），**改为接回活轮日志**：断的只是这根管子，进度一条
        // 都没丢，重新接上就能继续看它跑，而不是干等一个结果。
        // 接不上（老 agent 没有这个端点）才退回轮询落盘的回答。
        const followed = await followLive(liveSid, aiId, ctrl.signal);
        if (!followed) {
          patchTurn(aiId, { text: "连接中断，但模型仍在后台继续，正在等待结果…" });
          const answer = await ivyeaAwaitSessionAnswer(liveSid, sentAt);
          if (answer) { recovered = answer; patchTurn(aiId, { text: answer }); }
          else patchTurn(aiId, { failed: true, text: "这轮时间较长，后台仍在处理；完成后可在会话历史里查看。" });
        }
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
      stream.finish();         // 没落地的那一帧 + 最后一批思考，收尾时补进去
      window.clearInterval(tick);
      // 这一轮完了通知左栏再取一次：预览、时间要更新，而标题是服务端跑完这一轮才
      // 由模型起的（SessionRail 收到这条会延后再补取一次，正好接住它）。
      if (liveSid) notifyConsoleSessionsChanged(liveSid);
      patchTurn(aiId, (t) => ({
        running: false,
        elapsedMs: Date.now() - startedAt,
        // 收尾时刻只在这一轮**真的结束**时才记。按停止走的是 /chat/cancel，agent
        // 确认停住后这条流会正常收尾（照写）；而"停不掉、只好断流"那条兜底路径下
        // 轮次还在 agent 那边跑，那时候写一行"结束于 09:49"就是在撒谎。
        ...(ctrl.signal.aborted ? {} : { endedAt: Date.now() }),
        // 断链/取消的轮次也把测到的部分留下 —— 半截数据仍然能说明"卡在哪"，
        // 整轮丢弃反而让统计条在最需要解释的那一轮上变成空白。
        metrics: {
          ...(t.metrics || { startedAt }),
          firstTokenAt: stream.firstTokenAt() || undefined,
          lastTokenAt: stream.lastTokenAt() || undefined,
          usage: stream.usage() || t.metrics?.usage,
        },
      }));
      setBusy(false);
      abortRef.current = null;
    }

    notifyConsoleSessionsChanged();     // 新会话进左栏 / 已有会话更新时间
    const answered = recovered || stream.text();
    // 被用户停掉的轮次不跑跟进建议：那是**又一次模型调用**，而他刚说的是"别做了"。
    if (answered.trim() && !cancelledRef.current) void loadFollowUps(text, answered);
    cancelledRef.current = false;
    // images / picked 必须在依赖里：send 里读了它们。漏掉的话这个回调会闭包住
    // 旧值 —— 贴完图不打字直接发，图就丢了（之前靠"总会先打字"侥幸没暴露）。
  }, [composer, busy, sessionId, images, docs, picked, agentTakesAttachments,
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

  /**
   * 轮次跑着的时候用户又说的话。
   *
   * 两条去处，界面上分得很清（用户必须知道自己那句话什么时候起作用）：
   *   · 这条会话此刻有活轮 → agent 收下，在两个工具步之间插进**当前这一轮**，
   *     模型下一步就看得见；
   *   · 没有活轮（老 agent、或这一轮刚好收尾了）→ 排队，本轮一结束就当下一轮发出去。
   *
   * "说出去的话到底进没进去"必须有准信：`accepted` 才算送达，收到 agent 的
   * `injected` 事件（带同一个 id）才算真被读到。没被读到的，收尾时一律补发 ——
   * 宁可晚一轮，也不能无声吞掉一句用户说过的话。
   */
  const followUp = useCallback(async (raw: string) => {
    const text = raw.trim();
    if (!text) return;
    const id = uid();
    setQueue((q) => [...q, { id, text, state: "sending" }]);
    if (!sessionId) {
      setQueue((q) => q.map((it) => (it.id === id ? { ...it, state: "queued" } : it)));
      return;
    }
    try {
      const out = await ivyeaChatInject({ session_id: sessionId, text });
      setQueue((q) => q.map((it) => (it.id === id
        ? (out?.accepted
            ? { ...it, state: "injected" as const, agentId: String(out?.item?.id || "") }
            : { ...it, state: "queued" as const })
        : it)));
      if (!out?.accepted && out?.reason && out.reason !== "no_live_turn") {
        notify("warn", `这句话没能插进当前这一轮（${out.reason}），已排到下一轮。`);
      }
    } catch (e: any) {
      // 送不进去不等于这句话作废 —— 排队，本轮结束后照发。
      setQueue((q) => q.map((it) => (it.id === id ? { ...it, state: "queued" } : it)));
      notify("warn", errText(e, "追加指令没送进这一轮，已排到下一轮"));
    }
  }, [sessionId, notify]);

  /**
   * 一轮结束：把**没被这一轮读到的**追加指令当成下一轮发出去。
   *
   * 认领靠 agent 回的 id（`injected` 事件里的那个），不靠文本比对 —— 事件里的文本
   * 经过脱敏，可能和用户打的字不完全一样，比文本会漏认，漏认就是重复发一遍。
   */
  useEffect(() => {
    if (busy || !queue.length) return;
    const consumed = new Set(
      turns.flatMap((t) => (t.injected || []).map((i) => i.id).filter(Boolean)),
    );
    const leftovers = queue.filter((q) => !q.agentId || !consumed.has(q.agentId));
    setQueue([]);
    if (leftovers.length) void send(leftovers.map((q) => q.text).join("\n"));
  }, [busy, queue, turns, send]);

  // 读盘：打开/切到一条会话时，把上次没发出去的接回来。
  // 放进队列即可 —— 补发那条 effect 会在不忙的时候把它们发出去。
  useEffect(() => {
    const key = QUEUE_KEY + ":" + (sessionId || "new");
    let saved: QueuedFollowUp[] = [];
    try { saved = JSON.parse(localStorage.getItem(key) || "[]"); } catch { /* 读不出就当没有 */ }
    if (Array.isArray(saved) && saved.length) {
      setQueue((cur) => {
        const seen = new Set(cur.map((q) => q.text));
        const back = saved
          .filter((q) => q && typeof q.text === "string" && q.text.trim() && !seen.has(q.text))
          .map((q) => ({ id: uid(), text: q.text, state: "queued" as const }));
        return back.length ? [...cur, ...back] : cur;
      });
    }
    // **恢复完才允许写盘。** 顺序反过来的话，挂载时那次"队列是空的"会先把盘上
    // 那份删掉，紧接着才去读 —— 读到的自然是空的，于是关页面前排的那几句
    // 每次都恰好在恢复前一刻被自己抹掉（这条是 E2E 当场抓出来的）。
    restoredQueueRef.current = key;
  }, [sessionId]);

  // 写盘：队列一变就存（按会话分格；没有会话 id 时用一个通用格子，
  // 因为那种情况下这句话本来就是要当成"下一轮"发出去的）。
  useEffect(() => {
    const key = QUEUE_KEY + ":" + (sessionId || "new");
    if (restoredQueueRef.current !== key) return;      // 见上面那段：先恢复，后写盘
    try {
      // 已经被这一轮收下的（injected）属于那一轮，不进待发盘 —— 留着的话刷新之后
      // 会被当成"还没发"再发一遍。
      const keep = queue.filter((q) => q.state !== "injected");
      if (keep.length) localStorage.setItem(key, JSON.stringify(keep));
      else localStorage.removeItem(key);
    } catch { /* 存不下就退回内存态，不该打扰用户 */ }
  }, [queue, sessionId]);

  /** 回答一张选项卡。答不上去（超时/已被别的页签答了）就照实说，别装作点成功了。 */
  const answerQuestion = useCallback(async (
    turnId: string, req: IvyeaQuestionRequest, answers: Record<string, string>,
  ) => {
    try {
      // session_id 是**归属凭据**，不是可选的补充信息：ops 按"这条会话是不是你的"
      // 放行（那份归属落在库里，ops 重启还在）。req.session_id 是 agent 发卡时带的，
      // 优先用它 —— 页面上的 sessionId 在极少数时序下还没落定。
      await ivyeaChatQuestion({
        request_id: req.request_id,
        session_id: req.session_id || sessionId,
        answers,
      });
      patchTurn(turnId, (t) => ({
        questions: (t.questions || []).map((q) =>
          q.req.request_id === req.request_id ? { ...q, answers } : q),
      }));
    } catch (e: any) {
      notify("warn", errText(e, "这张选项卡已经失效（多半是超时后按推荐项继续了）"));
      patchTurn(turnId, (t) => ({
        questions: (t.questions || []).map((q) =>
          q.req.request_id === req.request_id ? { ...q, auto: true } : q),
      }));
    }
  }, [sessionId, notify, patchTurn]);

  /**
   * 真·停止。
   *
   * 此前这里只有一行 `abortRef.current?.abort()` —— 那不是停止，是"我不看了"：
   * 轮次在 agent 那边照跑照烧 token（用户原话："有的任务不想做了却无法终止，
   * 难道要一直烧 token 吗"）。现在先让 agent 真停下来，再收尾。
   *
   * 三条路都必须说实话：
   *   · 停住了 → 等 agent 的 `cancelled` 事件把这一轮收尾（正文和落盘都在里面）；
   *   · 这条会话本来就没在跑（刚好收尾了）→ 断流即可，别显示"已停止"；
   *   · 停不掉（老 agent 没这个端点 / agent 挂了）→ 明说后台可能还在跑，
   *     绝不能让界面显示"已停止"而它其实还在花钱。
   */
  const stop = useCallback(async () => {
    if (stoppingRef.current) return;             // 连点两下不该发两次
    const sid = sessionId;
    if (!sid) {
      abortRef.current?.abort();                 // 会话还没建起来，只能断流
      return;
    }
    stoppingRef.current = true;
    setStopping(true);
    try {
      const out = await ivyeaChatCancel({ session_id: sid });
      if (out?.cancelled) {
        // 排着的追加指令一并作废：任务都不想做了，那几句话更不该自己跑起来。
        const dropped = queue.length;
        setQueue([]);
        notify("info", dropped
          ? `已停止这一轮，${dropped} 条待发的追加指令也一并取消。已经跑出来的内容都留着。`
          : "已停止这一轮。已经跑出来的内容都留着。");
        // 不立刻 abort：`cancelled` 事件里带着这一轮的正文，而且 agent 要在那之前
        // 把执行过程和时间账落盘。给它 6 秒，超时再断流兜底。
        //
        // **只掐这一轮那个 controller**，不是"6 秒后 abort 当时的 abortRef" ——
        // 用户停完马上又发了一句的话，那个引用早换成新一轮的了，会把刚发的这轮掐掉。
        const stoppingCtrl = abortRef.current;
        window.setTimeout(() => stoppingCtrl?.abort(), 6000);
      } else {
        abortRef.current?.abort();
        notify("info", "这一轮刚好已经结束了。");
      }
    } catch (e: any) {
      abortRef.current?.abort();
      notify("warn", errText(e, "没能停掉这一轮（agent 版本过旧或没连上）：已停止查看，但后台可能还在跑"));
    } finally {
      stoppingRef.current = false;
      setStopping(false);
    }
  }, [sessionId, queue.length, notify]);

  /**
   * 选了一份文档（非图片）。**默认只带进这一轮对话，不进知识库。**
   *
   * 此前这里无条件调 ivyeaKnowledgeUpload(confirm, rebuild)：任何文件一上传就进库
   * 并重建索引，还往输入框里塞一句"已加进知识库"。用户的原话是「有些文件只是会话的
   * 时候用，并不需要纳入知识库」—— 那条路根本没有出口。
   *
   * 现在默认是"用完就没"，想长期留着是附件胶囊上的一个**显式按钮**（docToKnowledge）。
   * 也不再往输入框里代写文字了：那是用户的输入框，附件的状态该由附件栏自己表达。
   */
  const attach = useCallback(async (file: File) => {
    setAttaching(true);
    try {
      const got = await ivyeaSessionFile(file);
      setDocs((prev) => (prev.length >= 4
        ? (notify("warn", "一轮最多带 4 份附件，先去掉一份再加。"), prev)
        : [...prev, { ...got, file }]));
    } catch (e: any) {
      notify("error", errText(e, "读取文件失败"));
    } finally {
      setAttaching(false);
    }
  }, [notify]);

  /** 附件胶囊上的「收进知识库」—— 显式动作才入库、才重建索引。 */
  const docToKnowledge = useCallback(async (index: number) => {
    const d = docs[index];
    if (!d) return;
    try {
      await ivyeaKnowledgeUpload({ file: d.file, title: d.name, sourceType: "user",
                                   tags: "", confirm: true, rebuild: true });
      notify("success", `已把「${d.name}」收进知识库（这一轮照样带着它）`);
    } catch (e: any) {
      notify("error", errText(e, "收进知识库失败"));
    }
  }, [docs, notify]);

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
      onFollowUp={(text) => void followUp(text)}
      stopping={stopping}
      queue={queue}
      onQueueRemove={(id) => setQueue((q) => q.filter((it) => it.id !== id))}
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
      docs={docs}
      onDocsChange={setDocs}
      onDocToKnowledge={docToKnowledge}
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
    <div className={"cc-page" + (embedded ? " cc-embedded" : "")}>
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
            {/* 嵌入模式（悬浮球）里 Hero 只留一行小字：面板本来就只有几百像素高，
                44px 的 logo + 24px 大标题会把输入框顶到看不见的地方。 */}
            <div className="cc-hero-brand">
              {!embedded && <img src="/ivyea-logo.png" alt="Ivyea" className="cc-hero-logo" />}
              <h1 className="cc-hero-title">{embedded ? "有什么可以帮你？" : "意念所至，行动随行"}</h1>
            </div>
            <div className="cc-hero-composer">{composerNode(false)}</div>
            {!embedded && scenes.length > 0 && (
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
                              <img key={i} src={src} alt="附图" loading="lazy" title="点击查看原图"
                                   onClick={() => openLightbox(
                                     (t.images ?? []).map((u) => ({ src: u, alt: "附图" })), i)}
                                   onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
                            ))}
                          </div>
                        )}
                        {/* 我带的附件。和图一样放在气泡上面 —— 先看到带了什么，
                            再看到问了什么。只显示文件名：正文是注入给模型的几万字，
                            摆进气泡就是把整份 PDF 糊在自己脸上。 */}
                        {!!t.docs?.length && (
                          <div className="cc-user-docs">
                            {t.docs.map((d, i) => (d.url ? (
                              /* 有原件就能下回来。`download` 让浏览器存盘而不是打开 ——
                                 出口那边也钉了 attachment，这里只是把文件名带上。 */
                              <a key={i} className="cc-user-doc" href={`${d.url}?filename=${encodeURIComponent(d.name)}`}
                                 download={d.name} title={`会话附件（未入知识库），点击下载：${d.name}`}>
                                <Icon name="file" size={12} />{d.name}
                              </a>
                            ) : (
                              /* 老会话没有原件句柄 —— 显示成纯文字，而不是一个点了 404 的链接。 */
                              <span key={i} className="cc-user-doc" title={`会话附件（未入知识库）：${d.name}`}>
                                <Icon name="file" size={12} />{d.name}
                              </span>
                            )))}
                          </div>
                        )}
                        {!!t.text && <div className="cc-bubble">{t.text}</div>}
                        {/* 发出这句话的时刻。一轮动辄几十分钟，"我是什么时候让它做
                            这件事的"是回看会话时最先要找的坐标。 */}
                        {!!t.at && <div className="cc-user-time">{clockText(t.at, true)}</div>}
                      </div>
                    </div>
                  ) : (
                    <div className="cc-ai wb-enter" key={t.id}>
                      {/* 正文与执行过程按发生顺序交错 —— 说一段、做几件事、再说一段。
                          见 components/console/TurnBody 顶部那段"为什么要交错"。 */}
                      <TurnBody
                        text={t.text}
                        segments={t.segments}
                        steps={t.steps || []}
                        thoughts={t.thoughts || []}
                        skills={t.skills || []}
                        memoryRecall={t.memoryRecall || []}
                        elapsedMs={t.elapsedMs}
                        running={t.running}
                        failed={t.failed}
                        liveThought={t.reasoning}
                        onPickImage={pickAnswerImage}
                      />
                      {/*
                        * 正文和输入框之间的收尾。跑的过程中不出现 —— 正在写的一段
                        * 话底下挂一排"复制/重新生成"，等于请用户复制一份还没写完的
                        * 东西。跑完了才给。
                        */}
                      {t.text && !t.running && !t.failed && (
                        <AnswerActions
                          text={t.text}
                          meta={turnClockText(t)}
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
                      {/* 已决策的回执合并成一行（见 groupApprovals）：一轮里连批
                          十几次的话，一次一张卡会把整屏占满。 */}
                      {groupApprovals(t.approvals || []).map((g) => (
                        g.kind === "done"
                          ? <ApprovalReceipt key={g.key} decision={g.decision}
                                             label={g.label} count={g.count} />
                          : <ApprovalCard
                              key={g.key}
                              request={g.req}
                              onDecide={(choice) => void decide(t.id, g.req, choice)}
                            />
                      ))}
                      {/*
                        * 这一轮跑到一半时用户补的话。它**不是**执行步骤，所以不能只
                        * 躺在执行过程里（那一栏只显示最近几行，长任务里它很快就被折进
                        * "展开更早的 N 行"）。用户说过的话必须一直看得见。
                        */}
                      {!!t.injected?.length && (
                        <div className="cc-injected">
                          {t.injected.map((it) => (
                            <div className="cc-injected-row" key={it.id || it.text}>
                              <span className="cc-injected-mark">↳</span>
                              <span className="cc-injected-text">{it.text}</span>
                              <span className="cc-injected-meta">
                                {it.ts ? `${clockText(it.ts * 1000, true)} · ` : ""}收到追加指令，已插入本轮
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                      {/* 选项卡：模型拿不准，把岔路摆出来让人点。没人点也不挂死 ——
                          到点 agent 自己按推荐项继续。 */}
                      {(t.questions || []).map(({ req, answers, auto }) => (
                        <QuestionCard
                          key={req.request_id}
                          request={req}
                          answered={answers}
                          autoChosen={auto}
                          onAnswer={(picked) => void answerQuestion(t.id, req, picked)}
                        />
                      ))}
                      {/*
                        * 这一轮有哪几项是**替用户定的**。界面自己说 —— 模型经常不在
                        * 总结里提，而"我没选，它替我选了"恰恰是最该被看到的一件事。
                        */}
                      {!!t.autoDecisions?.length && (
                        <div className="cc-autodec">
                          <span className="cc-autodec-mark">⏱</span>
                          <div>
                            <b>这一轮有 {t.autoDecisions.length} 项是按推荐项自动定的</b>
                            （弹了选项卡但没人在超时前选）：
                            {t.autoDecisions.map((d) => (
                              <div key={d.question} className="cc-autodec-row">
                                {d.header ? `【${d.header}】` : ""}{d.question} → <b>{d.chosen}</b>
                              </div>
                            ))}
                            <div className="cc-autodec-tip">想换一个做法，直接说一句就行。</div>
                          </div>
                        </div>
                      )}
                      {/*
                        * 一轮的收尾时刻与时长。刷新之后也在（数来自 agent 的 turn_times）。
                        * **正常情况下它在上面那排「复制 / 重新生成」里**，这里只兜没有
                        * 那排按钮的两种轮次：回答失败的，和只有执行过程没有正文的。
                        */}
                      {!t.running && !(t.text && !t.failed) && (t.endedAt || t.elapsedMs) && (
                        <div className="cc-turn-clock">{turnClockText(t)}</div>
                      )}
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

      {/* 产物栏在悬浮球里放不下，也不该放：面板的价值是"不离开当前页面问一句"，
          真要看产物/待办/审批留痕，点开任务台。 */}
      {!embedded && <ArtifactRail
        answers={turns.filter((t) => t.role === "assistant" && !t.failed).map((t) => t.text)}
        todos={todos}
        fileChanges={fileChanges}
        approvals={railApprovals}
        sessionId={sessionId}
        model={model}
        readOnly={readOnly}
        usage={usage}
      />}
    </div>
  );
}

export default function Console(props: ConsoleEmbedProps = {}) {
  return (
    <ToastProvider>
      <ConsoleInner {...props} />
    </ToastProvider>
  );
}
