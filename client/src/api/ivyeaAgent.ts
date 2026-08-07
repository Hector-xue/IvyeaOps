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
  messages: { role: string; content: string }[];
};

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
      const data = await ivyeaChatSession(sessionId);
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
  /** "none"=沿用只读语义（默认）；"remote"=写操作走前端审批卡。 */
  approval?: "none" | "remote";
  /** 工作区（沙箱目录 / 上下文分组）。 */
  workspace?: string;
  turn_id?: string;
  /** false = 纯文本轮次，不给模型任何工具。 */
  use_tools?: boolean;
  /** 追加到本轮系统提示的额外上下文（@ 引用的资料就走这里）。 */
  system?: string;
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
    /** 审批超时被自动拒绝。 */
    onPermissionTimeout?: (data: { request_id: string }) => void;
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
    else if (event === "step") handlers.onStep?.(data);
    else if (event === "skill_match") handlers.onSkillMatch?.(data);
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

export async function ivyeaChatSession(sessionId: string) {
  const { data } = await api.get<{ ok: boolean; session: IvyeaChatSessionDetail }>(
    `/ivyea-agent/chat/sessions/${encodeURIComponent(sessionId)}`,
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
  /** false = agent 那边有正文但 ops 没登记归属（悬浮球/CLI 开的，仅管理员可见）。 */
  indexed: boolean;
};

export type ConsoleWorkspace = { name: string; path: string; builtin: boolean };

export async function consoleSessions(workspace = "", limit = 60) {
  const { data } = await api.get<{
    ok: boolean; sessions: ConsoleSessionRow[]; workspaces: ConsoleWorkspace[];
    /** false = agent 读不到，列表是空的但不代表会话没了。 */
    agent_available?: boolean;
  }>("/ivyea-agent/console/sessions", { params: { workspace, limit } });
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

export function notifyConsoleSessionsChanged() {
  window.dispatchEvent(new CustomEvent(CONSOLE_SESSIONS_CHANGED));
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
