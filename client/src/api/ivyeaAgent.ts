import { api } from "./client";

export type IvyeaAgentStatus = {
  ok: boolean;
  available: boolean;
  base_url: string;
  token_configured: boolean;
  health?: any;
  error?: string;
};

export type IvyeaChatResult = {
  ok: boolean;
  session_id?: string;
  text?: string;
  events?: { type: string; text?: string }[];
  error?: string;
  detail?: string;
  model?: any;
};

export type IvyeaChatSession = {
  id: string;
  updated?: number;
  turns?: number;
  preview?: string;
};

export type IvyeaChatSessionDetail = {
  id: string;
  created?: number;
  updated?: number;
  model?: string;
  usage?: any;
  /**
   * 本页的消息。assistant 行带 `tool_calls`（id + 工具名）、tool 行带 `tool_call_id` ——
   * 它们是把落盘的执行步骤挂回对应轮次的锚点（agent ≥ v1.10.3）。
   */
  messages: {
    role: string;
    content: string;
    tool_calls?: { id: string; name: string }[];
    tool_call_id?: string;
  }[];
  /** 本页涉及的执行步骤（agent ≥ v1.10.3；老版本没有这个字段）。 */
  steps?: IvyeaStepEvent[];
  /** 本页涉及的技能命中，anchor 是该轮第一个 call_id。 */
  skill_matches?: { anchor: string; skills: MatchedSkillRow[] }[];
  /** 按轮分页的游标。老 agent 不回这个字段 —— 那就当成"只有这一页"。 */
  turns?: { total: number; from: number; to: number; has_more: boolean };
};

export type MatchedSkillRow = { id: string; title: string; domain?: string; score?: number };

export type RetrievalStatus = {
  ok: boolean;
  index: {
    enabled?: boolean;
    backend?: string;
    chunks?: number;
    knowledge_cards?: number;
    memory_chunks?: number;
    needs_rebuild?: boolean;
    [key: string]: any;
  };
};

export type RetrievalEmbeddings = {
  ok: boolean;
  embeddings: {
    configured_backend?: string;
    active_backend?: string;
    semantic_enabled?: boolean;
    vector_kind?: string;
    model?: string;
    model_path?: string;
    package_available?: boolean;
    offline_model_available?: boolean;
    fallback_reason?: string;
    [key: string]: any;
  };
};

export type KnowledgeUpload = {
  id: string;
  filename: string;
  title: string;
  raw_path: string;
  extracted_path: string;
  size: number;
  created_at: string;
  source_url?: string;
  source_type?: string;
  tags?: string[];
  card_id?: string;
  warnings?: string[];
  text_chars?: number;
  import_status?: string;
};

export type KnowledgeDraft = {
  ok: boolean;
  action: string;
  card_id: string;
  title: string;
  source_type: string;
  source_url?: string;
  diff?: string;
  warnings?: string[];
  review_required?: boolean;
  old_hash?: string;
  new_hash?: string;
};

export type KnowledgeCard = {
  id: string;
  title: string;
  path?: string;
  tags?: string[];
  source_type?: string;
  source_url?: string;
  body_hash?: string;
};

export type KnowledgeDirectoryImport = {
  ok: boolean;
  import: {
    ok: boolean;
    root: string;
    namespace: string;
    confirm: boolean;
    scanned_files: number;
    candidates: Array<{
      source_path: string;
      target_path: string;
      action: string;
      card_id: string;
      title: string;
      size: number;
      text_chars?: number;
      warnings?: string[];
    }>;
    summary: {
      candidate_files: number;
      skipped_files: number;
      create: number;
      update: number;
      noop: number;
      imported: number;
      unchanged: number;
      limit_reached?: boolean;
    };
    indexes?: any;
  };
};

export type KnowledgeReviewStatus = "pending" | "approved" | "rejected" | "superseded";

export type KnowledgeChange = {
  event_id: string;
  id: string;
  title?: string;
  url?: string;
  checked_at?: string;
  content_hash?: string;
  diff?: string;
  authority_tier?: string;
  evidence_class?: string;
  category?: string;
  topics?: string[];
  marketplaces?: string[];
  locales?: string[];
  review_status: KnowledgeReviewStatus;
  reviewed_at?: string;
  reviewer?: string;
  reviewer_source?: string;
  review_identity_verified?: boolean;
  review_note?: string;
  published?: boolean;
  published_at?: string;
  published_card_id?: string;
  ready_for_import_draft?: boolean;
};

export type KnowledgeCoverageRequirement = {
  domain: string;
  marketplace: string;
  status: "strong" | "review_due" | "governed" | "synthesis_only" | "gap" | string;
  covered: boolean;
  primary_current: boolean;
  card_ids: string[];
  source_urls: string[];
};

export type KnowledgeGovernance = {
  ok: boolean;
  healthy: boolean;
  summary: {
    pending_reviews: number;
    approved_not_published: number;
    published_changes?: number;
    coverage_gaps: number;
    stale_cards: number;
    monitor_errors: number;
    monitor_overdue: number;
    conflicts: number;
    unverified_approved?: number;
  };
  reviews: { summary: Record<string, number>; changes: KnowledgeChange[] };
  coverage: {
    summary: Record<string, number>;
    requirements: KnowledgeCoverageRequirement[];
    policy?: string;
  };
  freshness: {
    summary: {
      cards: number;
      card_freshness: Record<string, number>;
      monitor_sources: number;
      monitor_status: Record<string, number>;
    };
    cards_requiring_review: any[];
    sources: any[];
  };
  conflicts: any[];
};

export type KnowledgeQuality = {
  ok: boolean;
  quality: {
    ok: boolean;
    summary: {
      cases: number;
      passed: number;
      failed: number;
      pass_rate: number;
      domains: Record<string, { cases: number; passed: number }>;
    };
    results: Array<{
      id: string;
      domain: string;
      ok: boolean;
      query: string;
      ids: string[];
      matched_ranks: Record<string, number | null>;
      risk: string;
      checks: Record<string, boolean>;
    }>;
  };
};

export type KnowledgeChangePacket = {
  event: KnowledgeChange;
  snapshot_excerpt: string;
  snapshot_chars: number;
  snapshot_truncated: boolean;
  candidates: Array<KnowledgeCard & { category?: string; score: number; exact_source: boolean }>;
  target: (KnowledgeCard & { body: string; license?: string }) | null;
  selection_required: boolean;
  publication_boundary: string;
};

export type KnowledgeEvidencePayload = {
  authorized: boolean;
  rights_confirmed: boolean;
  kind: string;
  marketplace: string;
  title?: string;
  source_url?: string;
  content?: string;
  exact_message?: string;
  account_id?: string;
  case_id?: string;
  notification_id?: string;
  order_id?: string;
  claim_id?: string;
  settlement_id?: string;
  transaction_id?: string;
  asin?: string;
  sku?: string;
  product_type?: string;
  error_code?: string;
  account_status?: string;
  policy?: string;
  program?: string;
  report_type?: string;
  record_type?: string;
  currency?: string;
  registration_stage?: string;
  document_request?: string;
  confirm?: boolean;
  rebuild?: boolean;
};

export async function ivyeaAgentStatus() {
  const { data } = await api.get<IvyeaAgentStatus>("/ivyea-agent/status");
  return data;
}

export async function ivyeaAgentChat(payload: {
  message: string;
  session_id?: string;
  ops_context?: Record<string, any>;
  max_steps?: number;
  plan_mode?: boolean;
  persist?: boolean;
  inject_retrieval?: boolean;
  /** false = 纯文本轮次，不给模型任何工具（跟进建议这类小活用它，便宜且快）。 */
  use_tools?: boolean;
  skill?: string;
  system?: string;
}) {
  // 复杂任务一轮可跑 10 分钟以上；180s 会掐断仍在健康生成的轮次。
  const { data } = await api.post<IvyeaChatResult>("/ivyea-agent/chat", payload, { timeout: 600000 });
  return data;
}

// 流式中断后的恢复：serve 端的轮次独立于浏览器连接继续执行，收尾时把完整会话
// 落盘。这里轮询会话详情，等 sentAt 之后落盘的 assistant 回复出现——绝不重发
// 消息（重发会把同一个 8 分钟的 agentic 轮次再跑一遍）。
export async function ivyeaAwaitSessionAnswer(
  sessionId: string,
  sentAtEpochSeconds: number,
  opts?: { deadlineMs?: number; intervalMs?: number },
): Promise<string | null> {
  const deadline = Date.now() + (opts?.deadlineMs ?? 12 * 60 * 1000);
  const interval = opts?.intervalMs ?? 5000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, interval));
    try {
      // 只要最后一条回答，别每 5 秒把整页历史拖回来（最长要轮询 12 分钟）。
      const data = await ivyeaChatSession(sessionId, { turns: 1 });
      const session = data?.session;
      if (!session) continue;
      if ((session.updated ?? 0) < sentAtEpochSeconds) continue; // 还没落盘
      const answers = (session.messages || []).filter(
        (m) => m.role === "assistant" && m.content && m.content.trim() && m.content.trim() !== "None",
      );
      if (answers.length) return answers[answers.length - 1].content.trim();
    } catch {
      // 后端重启/瞬时网络失败：继续等下一轮
    }
  }
  return null;
}

/** 一次 agent 轮次的入参。字段与 agent serve 的 /v1/chat/stream 一一对应。 */
/**
 * 上下文占用快照（agent 的 context 事件 / final.context）。
 *
 * used 是**估算**（estimated=true）：服务商回报的 prompt_tokens 只说得清"上一次
 * 调用花了多少"，而进度条要在这一轮发出去之前就说得出话。分三档是因为满了之后
 * 该动的地方完全不同：系统提示词大 = 人设太长，工具大 = 挂了全量工具，
 * 对话消息大 = 该压缩历史了。
 */
export type IvyeaContextUsage = {
  used: number;
  window: number;
  percent: number;
  breakdown: { system: number; tools: number; messages: number };
  estimated?: boolean;
  model?: string;
};

export type IvyeaChatPayload = {
  message: string;
  session_id?: string;
  ops_context?: Record<string, any>;
  max_steps?: number;
  plan_mode?: boolean;
  persist?: boolean;
  inject_retrieval?: boolean;
  /** 显式指定本轮必须遵循的 skill id。 */
  skill?: string;
  /** 让 serve 按用户问题自动匹配 skill 并回发 skill_match 事件。 */
  auto_skill?: boolean;
  /**
   * 审批三档（线上语义，界面档位见 lib/approvalModes）：
   * "none"=只读（默认）；"remote"=写操作弹前端审批卡；"auto"=完全放行、不再弹卡。
   * **必须和 plan_mode 成对**：agent 那边 execute = 放开 && !plan_mode。
   */
  approval?: "none" | "remote" | "auto";
  /** 工作区（沙箱目录 / 上下文分组）。 */
  workspace?: string;
  turn_id?: string;
  /** false = 纯文本轮次，不给模型任何工具。 */
  use_tools?: boolean;
  /** 追加到本轮系统提示的额外上下文（@ 引用的资料就走这里）。 */
  system?: string;
  /**
   * 要模型的思考流（agent ≥ v1.10.3）。默认不要 —— agent 侧同样默认关，
   * 因为老前端会把未知事件当自由文本渲染。老 agent 收到这个多余字段直接忽略。
   */
  stream_reasoning?: boolean;
  /** 会话开在哪个板块。ops 自用（左栏来源标记），不会下发给 agent。 */
  source?: ConsoleSource;
};

/**
 * 一次文件改动。step 事件里虽然有 `path`，但它只说明"调用了 write_file"，
 * 说不出**改了什么** —— diff 那一格靠这个事件。
 */
export type IvyeaFileChange = {
  path: string;
  action: "create" | "overwrite" | "edit" | string;
  /**
   * "file" = 整文件前后对比（write_file）；
   * "fragment" = 只有被替换的那一段（edit_file），**行号是片段内的相对行号**，
   * 别拿去对文件行号。
   */
  scope: "file" | "fragment" | string;
  diff: string;
  /** 服务端截断过（超大文件）。 */
  truncated?: boolean;
  session_id?: string;
  turn_id?: string;
};

/**
 * 结构化步骤事件（agent serve ≥ v1.9 才会发；旧版本只有自由文本 event）。
 * 契约见 ivyea_agent/stream_json.py:step_event。
 * - phase "plan" = todo_write/progress_update 这类规划汇报调用，UI 折起来
 * - status "blocked" = 被前置护栏拦下的流程纠偏，不是工具出错
 */
export type IvyeaStepEvent = {
  type: "step";
  id: string;
  seq: number;
  phase: "tool" | "mcp" | "board" | "subagent" | "knowledge" | "plan";
  name: string;
  tool?: string;
  server?: string;
  args?: Record<string, any>;
  status: "running" | "ok" | "error" | "blocked";
  ms?: number | null;
  session_id?: string;
  turn_id?: string;
};

export type IvyeaSkillMatch = {
  skills: { id: string; title: string; domain?: string; score?: number }[];
};

/** 写操作审批请求 —— 对应 agent 侧 permission.request_intent 的那张确认卡。 */
export type IvyeaPermissionRequest = {
  request_id: string;
  session_id?: string;
  op_type: string;
  title: string;
  preview: string;
  options: { key: string; label: string }[];
  destructive?: boolean;
  expires_at?: number;
};

/**
 * 这条 `answer_reset` 该不该把已经流出来的正文**丢掉**。
 *
 * 分两类，因为它们的性质完全不同：
 *   - `gate:*`（引用校验/自验证/阶段汇报没过）：模型被明确要求**整篇重写**，
 *     旧的那一稿作废。不丢就是"同一张表连出三遍"。
 *   - `tool_call`（这一段之后模型又去调工具了）：这段话**没有被作废**，只是还没
 *     说完。实测过一轮：模型在早轮写完了整篇答案、又去调了几个工具、最后只补
 *     一句"上面已经给了完整回答，这里补一句收尾" —— 这时候丢掉早轮那段，用户
 *     就只剩一句没头没尾的收尾。所以这类只断段，不丢字。
 */
export function answerResetDiscards(reason?: string): boolean {
  return String(reason || "").startsWith("gate:");
}

export async function ivyeaAgentChatStream(
  payload: IvyeaChatPayload,
  handlers: {
    onStart?: (data: any) => void;
    onToken?: (text: string) => void;
    onFinal?: (data: any) => void;
    onEvent?: (data: any) => void;
    onError?: (data: any) => void;
    /** 结构化步骤（工具/MCP/板块能力调用的开始与收尾）。 */
    onStep?: (data: IvyeaStepEvent) => void;
    /** 本轮命中的 skill。 */
    onSkillMatch?: (data: IvyeaSkillMatch) => void;
    /** 需要人工确认的写操作。 */
    onPermission?: (data: IvyeaPermissionRequest) => void;
    /** Agent 改过一个文件（带 diff）。 */
    onFileChange?: (data: IvyeaFileChange) => void;
    /** 审批超时被自动拒绝。 */
    onPermissionTimeout?: (data: { request_id: string }) => void;
    /**
     * 前面已经流出来的正文**作废**了，从下一个 token 起是新一稿（agent ≥ v1.10.2）。
     * 一轮里模型会把正文吐好几遍——工具前的开场白、门禁打回后的整篇重写——
     * 不接这条就会把三份草稿首尾拼在同一个气泡里（"同一张表连出三遍"）。
     * reason: tool_call | gate:verify | gate:progress | gate:citation。
     */
    onAnswerReset?: (data: { reason?: string }) => void;
    /**
     * 本轮的上下文占用（agent ≥ v1.16）：进度条画的就是它。
     * 老 agent 不发这条 —— 进度条整块不出现，而不是画一个编出来的百分比。
     */
    onContext?: (data: IvyeaContextUsage) => void;
    /**
     * 模型的思考流（agent ≥ v1.10.3，且 payload 里带 stream_reasoning）。
     * 只有会思考的模型（deepseek-reasoner / claude / codex / gemini）才有；
     * 主脑不吐思考时这条永远不来，活动行退回显示工具步骤。
     */
    onReasoning?: (data: { text?: string }) => void;
  },
  opts?: { signal?: AbortSignal },
) {
  const res = await fetch("/api/ivyea-agent/chat/stream", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: opts?.signal,
  });
  if (!res.ok || !res.body) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body.detail || body.error || "";
    } catch {
      detail = await res.text().catch(() => "");
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const emit = (block: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const raw of block.split(/\r?\n/)) {
      if (raw.startsWith("event:")) event = raw.slice(6).trim();
      else if (raw.startsWith("data:")) dataLines.push(raw.slice(5).trimStart());
    }
    if (dataLines.length === 0) return;
    let data: any = dataLines.join("\n");
    try { data = JSON.parse(data); } catch { /* keep raw string */ }
    if (event === "start") handlers.onStart?.(data);
    else if (event === "token") handlers.onToken?.(typeof data === "string" ? data : data.text || "");
    else if (event === "final") handlers.onFinal?.(data);
    else if (event === "error") handlers.onError?.(data);
    // 结构化事件（agent serve ≥ v1.9）。老版本不发这些，走下面的自由文本兜底，
    // 所以升级前后前端都不会白屏。
    // answer_reset 必须显式分流：落进下面的 onEvent 就会被当成自由文本叙述，
    // 而它没有 text 字段，等于这条边界被静默丢掉，重复照旧。
    else if (event === "answer_reset") handlers.onAnswerReset?.(typeof data === "string" ? {} : data || {});
    // 思考流同样必须显式分流：落进 onEvent 就会被当成"老 agent 的自由文本叙述"，
    // 一段思考几百个碎片，注记那条路只留最近 12 行，等于把真正的执行叙述挤没了。
    else if (event === "reasoning") {
      handlers.onReasoning?.(typeof data === "string" ? { text: data } : data || {});
    }
    else if (event === "context") handlers.onContext?.(data);
    else if (event === "step") handlers.onStep?.(data);
    else if (event === "skill_match") handlers.onSkillMatch?.(data);
    else if (event === "file_change") handlers.onFileChange?.(data);
    else if (event === "permission_request") handlers.onPermission?.(data);
    else if (event === "permission_timeout") handlers.onPermissionTimeout?.(data);
    else handlers.onEvent?.(data);
  };
  while (true) {
    const { value, done } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: !done });
      let idx = buffer.indexOf("\n\n");
      while (idx >= 0) {
        emit(buffer.slice(0, idx));
        buffer = buffer.slice(idx + 2);
        idx = buffer.indexOf("\n\n");
      }
    }
    if (done) break;
  }
  if (buffer.trim()) emit(buffer);
}

export async function ivyeaChatSessions(limit = 30) {
  const { data } = await api.get<{ ok: boolean; sessions: IvyeaChatSession[] }>("/ivyea-agent/chat/sessions", {
    params: { limit },
  });
  return data;
}

/**
 * 历史会话详情，**按轮**分页。
 *
 * turns = 这一页要几轮；before = 从第几轮往前取（翻更早的对话时传上一页的 from）。
 * 别改回按条数取：一次提问能产生几十条消息，按条切会把用户自己发的那句话挤出窗口 ——
 * 这正是"刷新之后我发的指令不见了"的成因。
 */
export async function ivyeaChatSession(
  sessionId: string, opts?: { turns?: number; before?: number },
) {
  const { data } = await api.get<{ ok: boolean; session: IvyeaChatSessionDetail }>(
    `/ivyea-agent/chat/sessions/${encodeURIComponent(sessionId)}`,
    { params: { turns: opts?.turns, before: opts?.before } },
  );
  return data;
}

export async function ivyeaChatSessionDelete(sessionId: string) {
  const { data } = await api.delete<{ ok: boolean; deleted: string }>(
    `/ivyea-agent/chat/sessions/${encodeURIComponent(sessionId)}`,
  );
  return data;
}

/**
 * 板块能力（ops-bridge 工具）目录。每条自带中文 title / module / destructive /
 * long_running —— 任务台的步骤芯片文案和「需要确认」判定都直接吃这份元数据，
 * 不再另建映射表。
 */
export type OpsToolInfo = {
  name: string;
  module: string;
  title: string;
  description: string;
  parameters?: any;
  destructive?: boolean;
  long_running?: boolean;
};

export async function ivyeaOpsTools(params?: { module?: string; query?: string }) {
  const { data } = await api.get<{ ok: boolean; tools: OpsToolInfo[]; modules: string[] }>(
    "/ivyea-agent/ops-tools",
    { params },
  );
  return data;
}

/**
 * 主脑 provider。
 * ⚠️ `models` 是**字符串数组**（["gpt-4.1", "gpt-4o", …]），不是对象数组 ——
 * 按对象取 m.id/m.name 会全取到 undefined，模型列表会静默变空。
 */
export type IvyeaProvider = {
  id: string;
  label?: string;
  models?: string[];
  default_model?: string;
  key_status?: string;
  model_count?: number;
  [k: string]: any;
};

/** 兼容字符串/对象两种形状，取出模型 id。 */
export function providerModelId(m: unknown): string {
  if (typeof m === "string") return m;
  if (m && typeof m === "object") {
    const o = m as Record<string, any>;
    return String(o.id || o.name || o.model || "");
  }
  return "";
}

export async function ivyeaModelProviders() {
  const { data } = await api.get<{ ok: boolean; providers?: IvyeaProvider[]; active?: any }>(
    "/ivyea-agent/model/providers",
  );
  return data;
}

/** agent 侧技能库（内置 + ~/.ivyea/skills）。 */
export type IvyeaSkillInfo = {
  id: string;
  title: string;
  domain?: string;
  version?: string;
  description?: string;
  triggers?: string[];
  score?: number;
};

export async function ivyeaSkills(query = "") {
  const path = query.trim()
    ? `/ivyea-agent/skills/search?q=${encodeURIComponent(query.trim())}`
    : "/ivyea-agent/skills";
  const { data } = await api.get<{ ok: boolean; skills: IvyeaSkillInfo[] }>(path);
  return data;
}

// ── 任务台会话与工作区 ──────────────────────────────────────────────────────
export type ConsoleSessionRow = {
  id: string;
  title: string;
  preview: string;
  turns: number;
  updated: number;
  workspace: string;
  owner: string;
  /** 会话开在哪个板块：任务台 / AI 问答 / 知识库。空 = 未登记的历史会话。 */
  source?: ConsoleSource | "";
  /** false = agent 那边有正文但 ops 没登记归属（悬浮球/CLI 开的，仅管理员可见）。 */
  indexed: boolean;
};

/** 三个板块共用 agent 的会话库，靠这个字段区分来源。 */
export type ConsoleSource = "console" | "assistant" | "brain";

export const SOURCE_LABEL: Record<ConsoleSource, string> = {
  console: "任务台",
  assistant: "AI 问答",
  brain: "知识库",
};

/**
 * 各来源的归属页面 —— 左栏点一条会话回到它本来的板块。
 *
 * assistant 指向任务台：AI 问答那一页已经并进任务台，但**来源标记要留着** ——
 * 历史会话是按来源筛选的，把 assistant 从类型里删掉等于让那些老对话在左栏里
 * 筛不出来。会话本身一条没动，只是都在任务台里打开。
 */
export const SOURCE_PATH: Record<ConsoleSource, string> = {
  console: "/console",
  assistant: "/console",
  brain: "/brain",
};

export type ConsoleWorkspace = { name: string; path: string; builtin: boolean };

export async function consoleSessions(
  workspace = "", limit = 60, source = "", q = "", offset = 0,
) {
  const { data } = await api.get<{
    ok: boolean; sessions: ConsoleSessionRow[]; workspaces: ConsoleWorkspace[];
    /** false = agent 读不到，列表是空的但不代表会话没了。 */
    agent_available?: boolean;
    /** 过滤后的总条数（不是本页条数）。 */
    total?: number;
    offset?: number;
    /** 服务端算好的，前端别自己推 —— 推错就是"加载更多"点了没反应。 */
    has_more?: boolean;
  }>("/ivyea-agent/console/sessions", { params: { workspace, limit, source, q, offset } });
  return data;
}

export async function consoleSessionPatch(
  sessionId: string, patch: { title?: string; workspace?: string },
) {
  const { data } = await api.patch<{ ok: boolean }>(
    `/ivyea-agent/console/sessions/${encodeURIComponent(sessionId)}`, patch);
  return data;
}

export async function consoleSessionDelete(sessionId: string) {
  const { data } = await api.delete<{ ok: boolean }>(
    `/ivyea-agent/console/sessions/${encodeURIComponent(sessionId)}`);
  return data;
}

/** path 可选，且**仅管理员**能绑目录 —— 绑了它就是 Agent 文件工具的工作目录。 */
/** 工作区列表。后端 GET /console/workspaces 一直都在，只是前端此前都从会话列表里顺带取。 */
export async function consoleWorkspaces() {
  const { data } = await api.get<{ ok: boolean; workspaces: ConsoleWorkspace[] }>(
    "/ivyea-agent/console/workspaces");
  return data.workspaces || [];
}

export async function consoleWorkspaceCreate(name: string, path = "") {
  const { data } = await api.post<{ ok: boolean; workspace: ConsoleWorkspace }>(
    "/ivyea-agent/console/workspaces", { name, path });
  return data;
}

export async function consoleWorkspaceDelete(name: string) {
  const { data } = await api.delete<{ ok: boolean; sessions_moved: number }>(
    `/ivyea-agent/console/workspaces/${encodeURIComponent(name)}`);
  return data;
}

/** 左栏需要刷新会话列表时广播它（发完一轮、改名、删除…）。 */
export const CONSOLE_SESSIONS_CHANGED = "ivyea-ops:console-sessions-changed";

/**
 * @param expectId 期望这一次刷新之后能在列表里看到的会话 id。会话是 agent 侧落库的，
 *   「开始」事件到得比落库早一拍 —— 左栏取回来没看见它，就按这个 id 再补取一次，
 *   而不是让用户去刷新整页。
 */
export function notifyConsoleSessionsChanged(expectId?: string) {
  window.dispatchEvent(new CustomEvent(CONSOLE_SESSIONS_CHANGED, { detail: { expectId } }));
}

/**
 * 把图片读成文字（ops 侧视觉旁路）。
 * agent serve 在主脑没有视觉时会直接抛错，而本机主脑就没有视觉 —— 所以图片不能直接
 * 丢给 agent，得先在 ops 这边用配好的视觉链读成文字，再作为文本带进那一轮。
 */
export async function visionDescribe(images: string[], prompt = "") {
  const { data } = await api.post<{ ok: boolean; provider: string; text: string }>(
    "/ivyea-agent/vision/describe", { images, prompt }, { timeout: 180000 });
  return data;
}

/** agent 的 MCP 注册表（~/.ivyea/mcp.json）—— 决定 Agent 能连哪些数据源。 */
export type AgentMcpServer = {
  name: string;
  transport: string;
  trusted: boolean;
  /** true = 由「系统配置 → 数据源」的密钥自动同步，删了下次保存设置又会回来。 */
  managed: boolean;
  has_data_source: boolean;
  spec: Record<string, any>;
};

export async function ivyeaMcpServers() {
  const { data } = await api.get<{
    ok: boolean;
    servers: AgentMcpServer[];
    claude_servers: { name: string; transport: string; spec: Record<string, any> }[];
    managed: string[];
  }>("/ivyea-agent/mcp/servers");
  return data;
}

export async function ivyeaMcpUpsert(payload: {
  name: string;
  transport: "http" | "sse" | "stdio";
  url?: string;
  command?: string;
  args?: string[];
  headers?: Record<string, string>;
  env?: Record<string, string>;
  trusted?: boolean;
}) {
  const { data } = await api.post<{ ok: boolean; name: string }>("/ivyea-agent/mcp/servers", payload);
  return data;
}

export async function ivyeaMcpDelete(name: string) {
  const { data } = await api.delete<{ ok: boolean; removed: string }>(
    `/ivyea-agent/mcp/servers/${encodeURIComponent(name)}`,
  );
  return data;
}

/**
 * 回送一次写操作审批决策，解开 agent 侧阻塞的那一步。
 * choice 与 permission_request 事件里的 options[].key 对应
 * （approve / session / deny / abort，部分场景还有 edit）。
 */
export async function ivyeaChatPermission(params: {
  request_id: string;
  session_id?: string;
  choice: string;
  edits?: Record<string, any>;
}) {
  const { data } = await api.post<{ ok: boolean; error?: string; detail?: string }>(
    "/ivyea-agent/chat/permission",
    params,
    { timeout: 20000 },
  );
  return data;
}

/** 一条待审批（跨会话列表用，字段来自 console_approvals 表）。 */
export type PendingApproval = {
  request_id: string;
  session_id: string;
  title: string;
  op_type: string;
  requested_at: number;
};

/** 我名下所有还没决定的审批，跨会话 —— 手机上审批的入口。 */
export async function ivyeaPendingApprovals() {
  const { data } = await api.get<{ ok: boolean; approvals: PendingApproval[] }>(
    "/ivyea-agent/console/approvals/pending");
  return data.approvals || [];
}

export async function ivyeaServiceStart() {
  const { data } = await api.post<{ ok: boolean }>("/ivyea-agent/service/start", {}, { timeout: 25000 });
  return data;
}

export async function ivyeaRetrievalStatus() {
  const { data } = await api.get<RetrievalStatus>("/ivyea-agent/retrieval/status");
  return data;
}

export async function ivyeaRetrievalEmbeddings() {
  const { data } = await api.get<RetrievalEmbeddings>("/ivyea-agent/retrieval/embeddings");
  return data;
}

export async function ivyeaRetrievalSync() {
  const { data } = await api.post<any>("/ivyea-agent/retrieval/sync", {}, { timeout: 180000 });
  return data;
}

export async function ivyeaKnowledgeFiles(limit = 500) {
  const { data } = await api.get<{
    ok: boolean;
    uploads: { path: string; name: string; size: number; kind: string; mtime: number }[];
    cards: KnowledgeCard[];
    history: KnowledgeUpload[];
  }>("/ivyea-agent/knowledge/files", { params: { limit } });
  return data;
}

/**
 * 读一份知识库文件/卡片的正文 —— composer 的 @ 引用靠它把内容真的带进本轮。
 * ⚠️ 正文在 `file.content` 里，不是顶层 `content`（照顶层取会静默拿到空字符串，
 * 结果就是"引用了但什么都没带进去"，实测踩过）。
 */
export async function ivyeaKnowledgeFile(path: string) {
  const { data } = await api.get<{
    ok: boolean;
    file?: { path: string; name: string; size: number; mtime: number; content: string };
  }>("/ivyea-agent/knowledge/file", { params: { path } });
  return { ok: data.ok, content: String(data.file?.content || ""), name: data.file?.name || "" };
}

export async function ivyeaKnowledgeSearch(q: string, limit = 8) {
  const { data } = await api.get<{ ok: boolean; results: any[] }>("/ivyea-agent/knowledge/search", {
    params: { q, limit },
  });
  return data;
}

export async function ivyeaKnowledgeWatchlist() {
  const { data } = await api.get<{ ok: boolean; summary: any; sources: any[] }>("/ivyea-agent/knowledge/watchlist");
  return data;
}

export async function ivyeaKnowledgeGovernance() {
  const { data } = await api.get<KnowledgeGovernance>("/ivyea-agent/knowledge/governance");
  return data;
}

export async function ivyeaKnowledgeCoverage() {
  const { data } = await api.get<{ ok: boolean; coverage: KnowledgeGovernance["coverage"] }>(
    "/ivyea-agent/knowledge/coverage",
  );
  return data;
}

export async function ivyeaKnowledgeFreshness() {
  const { data } = await api.get<{ ok: boolean; freshness: KnowledgeGovernance["freshness"] }>(
    "/ivyea-agent/knowledge/freshness",
  );
  return data;
}

export async function ivyeaKnowledgeQuality() {
  // 质量评测是全量跑一遍用例，可超过 axios 默认 30s
  const { data } = await api.get<KnowledgeQuality>("/ivyea-agent/knowledge/quality", { validateStatus: () => true, timeout: 120000 });
  return data;
}

export async function ivyeaKnowledgeChanges(status = "", limit = 100) {
  const { data } = await api.get<{
    ok: boolean;
    summary: Record<string, number>;
    changes: KnowledgeChange[];
    review_required: boolean;
  }>("/ivyea-agent/knowledge/changes", { params: { status, limit } });
  return data;
}

export async function ivyeaKnowledgeReviews(eventId = "", limit = 100) {
  const { data } = await api.get<{ ok: boolean; summary: any; reviews: any[] }>(
    "/ivyea-agent/knowledge/reviews",
    { params: { event_id: eventId, limit } },
  );
  return data;
}

export async function ivyeaKnowledgePublications(eventId = "", limit = 100) {
  const { data } = await api.get<{ ok: boolean; summary: any; publications: any[] }>(
    "/ivyea-agent/knowledge/publications",
    { params: { event_id: eventId, limit } },
  );
  return data;
}

export async function ivyeaKnowledgeEvidence(limit = 100) {
  const { data } = await api.get<{ ok: boolean; summary: any; evidence: any[] }>(
    "/ivyea-agent/knowledge/evidence", { params: { limit } },
  );
  return data;
}

export async function ivyeaKnowledgeEvidenceDraft(payload: KnowledgeEvidencePayload) {
  const { data } = await api.post<any>("/ivyea-agent/knowledge/evidence/draft", payload);
  return data;
}

export async function ivyeaKnowledgeEvidenceApply(payload: KnowledgeEvidencePayload) {
  const { data } = await api.post<any>("/ivyea-agent/knowledge/evidence/apply", payload);
  return data;
}

export async function ivyeaKnowledgeReviewChange(params: {
  eventId: string;
  decision: Exclude<KnowledgeReviewStatus, "pending">;
  reviewer?: string;
  note?: string;
  confirm: boolean;
}) {
  const { data } = await api.post<any>("/ivyea-agent/knowledge/changes/review", {
    event_id: params.eventId,
    decision: params.decision,
    reviewer: params.reviewer || "local-operator",
    note: params.note || "",
    confirm: params.confirm,
  });
  return data;
}

export async function ivyeaKnowledgeChangePacket(eventId: string, cardId = "") {
  const { data } = await api.get<{ ok: boolean; packet: KnowledgeChangePacket }>(
    `/ivyea-agent/knowledge/changes/${encodeURIComponent(eventId)}/packet`,
    { params: { card_id: cardId } },
  );
  return data;
}

export async function ivyeaKnowledgeChangeDraft(params: {
  eventId: string;
  cardId?: string;
  newCardId?: string;
  title?: string;
  body: string;
}) {
  const { data } = await api.post<any>("/ivyea-agent/knowledge/changes/draft", {
    event_id: params.eventId,
    card_id: params.cardId || "",
    new_card_id: params.newCardId || "",
    title: params.title || "",
    body: params.body,
  });
  return data;
}

export async function ivyeaKnowledgeChangeApply(params: {
  eventId: string;
  cardId?: string;
  newCardId?: string;
  title?: string;
  body: string;
  confirm: boolean;
  rebuild?: boolean;
}) {
  const { data } = await api.post<any>("/ivyea-agent/knowledge/changes/apply", {
    event_id: params.eventId,
    card_id: params.cardId || "",
    new_card_id: params.newCardId || "",
    title: params.title || "",
    body: params.body,
    confirm: params.confirm,
    rebuild: params.rebuild !== false,
  }, { timeout: 120000 });
  return data;
}

export async function ivyeaKnowledgeSync(sourceIds: string[] = [], force = false) {
  const { data } = await api.post<any>("/ivyea-agent/knowledge/sync", {
    source_ids: sourceIds,
    force,
  }, { timeout: 120000 });
  return data;
}

export async function ivyeaKnowledgeApplyText(params: {
  title: string;
  body: string;
  tags?: string;
  sourceType?: string;
  sourceUrl?: string;
  id?: string;
  rebuild?: boolean;
}) {
  const tags = (params.tags || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const { data } = await api.post<{
    ok: boolean;
    result: {
      applied?: boolean;
      action?: string;
      card?: KnowledgeCard;
      draft?: KnowledgeDraft;
      error?: string;
    };
  }>(
    "/ivyea-agent/knowledge/update/apply",
    {
      id: params.id || "",
      title: params.title,
      body: params.body,
      source_type: params.sourceType || "user",
      source_url: params.sourceUrl || "",
      tags,
      confirm: true,
      rebuild: params.rebuild !== false,
    },
    { timeout: 120000 },
  );
  return data;
}

export async function ivyeaKnowledgeUpload(params: {
  file: File;
  title?: string;
  id?: string;
  sourceUrl?: string;
  sourceType?: string;
  tags?: string;
  confirm?: boolean;
  rebuild?: boolean;
}) {
  const form = new FormData();
  form.append("file", params.file);
  form.append("title", params.title || "");
  form.append("id", params.id || "");
  form.append("source_url", params.sourceUrl || "");
  form.append("source_type", params.sourceType || "user");
  form.append("tags", params.tags || "");
  form.append("confirm", params.confirm ? "true" : "false");
  form.append("rebuild", params.rebuild === false ? "false" : "true");
  const { data } = await api.post<{
    ok: boolean;
    upload: KnowledgeUpload;
    extraction: { text_chars: number; warnings?: string[]; preview?: string };
    draft: KnowledgeDraft;
    apply?: any;
  }>("/ivyea-agent/knowledge/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000,
  });
  return data;
}

export async function ivyeaKnowledgeApplyUpload(uploadId: string, confirm = true, rebuild = true) {
  const { data } = await api.post<{
    ok: boolean;
    upload: KnowledgeUpload;
    draft: KnowledgeDraft;
    result: any;
  }>("/ivyea-agent/knowledge/uploads/apply", {
    upload_id: uploadId,
    confirm,
    rebuild,
  });
  return data;
}

export async function ivyeaKnowledgeImportDirectory(params?: {
  root?: string;
  namespace?: string;
  confirm?: boolean;
  rebuild?: boolean;
  maxFiles?: number;
}) {
  const { data } = await api.post<KnowledgeDirectoryImport>(
    "/ivyea-agent/knowledge/import-directory",
    {
      root: params?.root || "",
      namespace: params?.namespace || "gbrain",
      confirm: !!params?.confirm,
      rebuild: params?.rebuild !== false,
      max_files: params?.maxFiles || 1000,
    },
    { timeout: 180000 },
  );
  return data;
}

/** 把外部会话（旧 localStorage 历史等）搬进 agent 会话库。按 id 幂等，重复调用是覆盖。 */
export async function consoleSessionImport(
  source: "assistant" | "brain",
  sessions: { id: string; created?: number; messages: { role: "user" | "assistant"; content: string }[] }[],
) {
  const { data } = await api.post<{ ok: boolean; imported: string[]; count: number; skipped: number }>(
    "/ivyea-agent/console/sessions/import", { source, sessions });
  return data;
}

/** 智能体预设：一套"这类活按这么跑"的设置（技能 + 审批档位 + 工作区）。按用户隔离。 */
export type ConsolePreset = {
  name: string; skill: string;
  /** 线上语义的审批档位，见 lib/approvalModes。 */
  approval: "none" | "remote" | "auto";
  workspace: string;
  /** 人设/判断标准。套用时整段并进这一轮的系统提示。 */
  system: string;
  note: string; created: number;
};

export async function consolePresets() {
  const { data } = await api.get<{ ok: boolean; presets: ConsolePreset[] }>("/ivyea-agent/console/presets");
  return data.presets || [];
}

export async function consolePresetSave(p: Omit<ConsolePreset, "created">) {
  const { data } = await api.post<{ ok: boolean; preset: ConsolePreset }>("/ivyea-agent/console/presets", p);
  return data.preset;
}

export async function consolePresetDelete(name: string) {
  const { data } = await api.delete<{ ok: boolean }>(
    `/ivyea-agent/console/presets/${encodeURIComponent(name)}`);
  return data;
}

/** 预设变了 → 任务台的下拉要跟着变。和会话列表用同一套广播机制。 */
export const CONSOLE_PRESETS_CHANGED = "ivyea-ops:console-presets-changed";
export function notifyConsolePresetsChanged() {
  window.dispatchEvent(new Event(CONSOLE_PRESETS_CHANGED));
}

/** 一条会话的审批留痕（谁在什么时候批了/拒了哪一步写操作）。 */
export type ConsoleApproval = {
  request_id: string; session_id: string; principal: string;
  title: string; op_type: string;
  /** "" = 还没决定（页面中途关掉）；timeout = 超时自动拒。 */
  decision: "" | "approve" | "session" | "deny" | "abort" | "timeout" | string;
  requested_at: number; decided_at: number;
};

export async function consoleSessionApprovals(sessionId: string) {
  const { data } = await api.get<{ ok: boolean; approvals: ConsoleApproval[] }>(
    `/ivyea-agent/console/sessions/${encodeURIComponent(sessionId)}/approvals`);
  return data.approvals || [];
}
