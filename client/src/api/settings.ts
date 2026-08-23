import { api } from "./client";

export interface HubSettings {
  // Hermes LLM — primary model (synced to ~/.hermes/config.yaml + .env)
  hermes_provider: string;
  hermes_model: string;
  hermes_api_key: string;
  hermes_base_url: string;
  // Hermes LLM — fallback model
  hermes_fallback_provider: string;
  hermes_fallback_model: string;
  hermes_fallback_api_key: string;
  hermes_fallback_base_url: string;
  // Global fallback LLM for text tasks and AI Q&A
  assistant_provider: string;
  assistant_model: string;
  assistant_api_key: string;
  assistant_base_url: string;
  assistant_vision_model: string;
  vision_provider: string;
  vision_model: string;
  vision_api_key: string;
  vision_base_url: string;
  // IvyeaAgent local service
  ivyea_agent_url: string;
  ivyea_agent_token: string;
  ivyea_agent_auto_start: boolean;
  ivyea_agent_provider: string;
  ivyea_agent_model: string;
  ivyea_agent_api_key: string;
  ivyea_agent_base_url: string;
  // Image generation. Empty image_api_key/base_url reuses Apimart below.
  image_model: string;
  image_api_key: string;
  image_base_url: string;
  // GBrain 语义检索 embedding
  // Primary image-generation gateway
  apimart_key: string;
  apimart_base: string;
  // Comma-separated text-AI fallback order for IvyeaOps internal synthesis
  text_ai_providers: string;
  // Vision provider order (openai, assistant) for 图片分析
  vision_ai_providers: string;
  // Dedicated DeepSeek key (only used when 'deepseek' is in text_ai_providers)
  deepseek_api_key: string;
  // 资讯 RSS sources, newline-separated: url | name | category
  news_feeds: string;
  // Market data
  sorftime_key: string;
  // Listing Generator
  imgflow_url: string;
  // GBrain
  brain_root: string;
  openai_api_key: string;
  // Feishu alerts
  // 通知渠道（见 server/app/services/notify）
  notify_webhook: string;
  notify_events: string;
  ai_budget_monthly_usd: number;
  alert_webhook: string;
  alert_app_id: string;
  alert_app_secret: string;
  alert_chat_id: string;
  // feishu = open.feishu.cn（国内）/ lark = open.larksuite.com（国际）
  alert_feishu_domain: string;
  // Alert thresholds
  alert_threshold: number;
  alert_sustain: number;
  alert_cooldown: number;
  // Embedded URLs
  dashboard_url: string;
  terminal_url: string;
  // External integrations
  hermes_bin: string;
  codex_bin: string;
  claude_bin: string;
  kiro_cli_bin: string;
  hermes_db: string;
  codex_db: string;
  feishu_codex_db: string;
  kiro_gateway_db: string;
  kiro_cli_db: string;
  kiro_cli_sessions_dir: string;
  claude_projects_dir: string;
  hermes_node_bin: string;
  bun_bin: string;
  // Auto bug-fix toggle (admin-only feature)
  autofix_enabled: boolean;
  /** 能力市场（门道社区）。默认关闭 —— 它会外联，而产品立场是数据不出本机。 */
  skill_market_enabled: boolean;
  skill_market_url: string;
  skill_market_pubkey: string;
  skill_market_allow_class_b: boolean;
  // SIF — 深度分析工具箱，独立 key（mcp.sif.com Bearer token）
  sif_key: string;
  // SellerSprite — separate key, auto-registers stdio MCP server in Hermes
  sellersprite_key: string;
  // Account (password_hash not exposed to frontend)
}

export interface SettingsResp {
  settings: HubSettings;
  secret_keys: string[];
}

export interface RunnerStatus {
  ok: boolean;
  detail: string;
}

/** 视觉能力不是"有/无"两态，而是三档降级链：
 *  1 主脑直读 · 2 第三方视觉模型旁路 · 3 本地 CV+OCR 量化 · 0 不可用。
 *  只看 ok 会让用户以为 T3 等于没有——T3 其实能做全部可测量的分析。 */
export interface VisionStatus extends RunnerStatus {
  tier?: 0 | 1 | 2 | 3;
  tier_label?: string;
}

export interface AiChainStatus {
  text: RunnerStatus;
  global_fallback: RunnerStatus;
  vision: VisionStatus;
  chain_order: string;
}

export interface HealthResp {
  version: RunnerStatus;
  ai_chain?: AiChainStatus;
  ivyea_agent: RunnerStatus;
  apimart: RunnerStatus;
  sorftime: RunnerStatus;
  imgflow: RunnerStatus;
  brain_root: RunnerStatus;
  openai: RunnerStatus;
  runners: {
    hermes: RunnerStatus;
    codex: RunnerStatus;
    claude: RunnerStatus;
  };
  integrations?: Record<string, RunnerStatus>;
}

export async function getSettings(): Promise<SettingsResp> {
  const { data } = await api.get<SettingsResp>("/settings");
  return data;
}

export async function patchSettings(updates: Partial<HubSettings>): Promise<SettingsResp> {
  const { data } = await api.patch<SettingsResp>("/settings", { settings: updates });
  return data;
}

/** 一个模型槽位能用哪些模型。slot: agent | assistant | vision | image。 */
export type SlotCatalogResp = {
  ok: boolean;
  error?: string;
  catalog: {
    ok: boolean;
    models: string[];
    default_model?: string;
    label?: string;
    /** live / cache / builtin / none —— builtin 和 none 都意味着"只能手输"。 */
    source?: string;
    error?: string;
  };
};

/**
 * 列出某个槽位当前那套账号支持哪些模型。
 *
 * provider/base_url/api_key 可以现给：系统配置页在**保存之前**就要能看清单，
 * 那时新填的 key 还没落库。都不给就用库里存的那份。
 */
export async function slotModelCatalog(body: {
  slot: string; provider?: string; base_url?: string; api_key?: string; refresh?: boolean;
}): Promise<SlotCatalogResp> {
  const { data } = await api.post<SlotCatalogResp>("/settings/model-catalog", body, { timeout: 25000 });
  return data;
}

export async function getHealth(): Promise<HealthResp> {
  const { data } = await api.get<HealthResp>("/settings/health", { timeout: 10000 });
  return data;
}

export interface AiCall {
  ts: string;
  provider: string;
  ok: boolean;
  chars: number;
  kind: string;
  failures: string[];
}

export async function getAiLog(): Promise<AiCall[]> {
  const { data } = await api.get<{ calls: AiCall[] }>("/settings/ai-log", { timeout: 8000 });
  return data.calls || [];
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  await api.post("/auth/change-password", { old_password: oldPassword, new_password: newPassword });
}

export interface TestResult {
  ok: boolean;
  detail: string;
}

export interface AutodetectResp {
  suggestions: Partial<Record<keyof HubSettings, string>>;
  scanned: string[];
}

export async function testSetting(key: keyof HubSettings, value?: string): Promise<TestResult> {
  // 35s > the backend probe's 20s timeout, so slow/proxied client networks don't
  // get cut off by the HTTP client before the probe itself decides.
  const { data } = await api.post<TestResult>("/settings/test", { key, value }, { timeout: 35000 });
  return data;
}

export async function autodetectSettings(): Promise<AutodetectResp> {
  const { data } = await api.post<AutodetectResp>("/settings/autodetect", {}, { timeout: 10000 });
  return data;
}

export interface SelfCheckItem { key: string; label: string; status: "ok" | "err" | "skip"; detail: string; }
export interface SelfCheckResp { results: SelfCheckItem[]; ok: number; err: number; skip: number; total: number; }

export async function selfCheckSettings(): Promise<SelfCheckResp> {
  const { data } = await api.post<SelfCheckResp>("/settings/self-check", {}, { timeout: 90000 });
  return data;
}

export interface AgentVersionResp {
  version: string; available: boolean;
  installed?: string; latest?: string; update_available?: boolean;
  latest_known?: boolean; frozen?: boolean;
}

export async function getAgentVersion(): Promise<AgentVersionResp> {
  const { data } = await api.get<AgentVersionResp>("/ivyea-agent/version", { timeout: 8000 });
  return data;
}

export interface AgentUpgradeProgress {
  phase: "idle" | "preparing" | "downloading" | "restarting" | "done" | "error";
  percent: number;
  before: string;
  after: string;
  ok: boolean | null;
  note?: string;
  error?: string;
}

export async function startAgentUpgrade(): Promise<{ started: boolean; already_running?: boolean }> {
  const { data } = await api.post("/ivyea-agent/upgrade", {}, { timeout: 10000 });
  return data;
}

export async function getAgentUpgradeProgress(): Promise<AgentUpgradeProgress> {
  const { data } = await api.get<AgentUpgradeProgress>("/ivyea-agent/upgrade/progress", { timeout: 8000 });
  return data;
}


// ── 飞书 / Lark 配置向导 ────────────────────────────────────────────────────
// 凭据本身存在 hub settings（上面的 alert_* 那一组），保存时由后端下推给
// IvyeaAgent。这里的接口拿的是**只有 agent 知道的那部分**：连通性、白名单、
// relay 状态、巡检任务。它们只存 agent 一份，界面直接读写，不在 ops 侧留副本。

export interface FeishuStep {
  key: string; title: string; done: boolean; detail: string; hint: string;
}
export interface FeishuChannel { ready: boolean; blockers: string[]; note: string; }
export interface FeishuPatrolJob {
  name: string; task: string; enabled: boolean; every_minutes: number;
  channel: string; notify: boolean; scope: string; sids: string[]; sid: string;
  last_run: number;
}
export interface FeishuStatus {
  ok: boolean;
  error?: string;
  hint?: string;
  app?: { app_id: string; app_id_masked: string; configured: boolean; secret_configured: boolean; domain: string; source: string };
  chat?: { chat_id: string; configured: boolean };
  webhook?: { configured: boolean; url_masked: string };
  gates?: { allowed_senders: string[]; allowed_chats: string[] };
  relay?: { state: string; running: boolean | null; detail: string; sdk?: boolean; can_install?: boolean };
  patrol?: {
    jobs: FeishuPatrolJob[]; any_enabled: boolean; pushing_to_feishu: number;
    // 各档的默认间隔与说明由 agent 给（唯一真源），前端不再写第二份默认值
    defaults: Record<string, { task: string; label: string; desc: string; every_minutes: number }>;
    timer: { state: string; running: boolean | null; detail: string; can_install?: boolean };
  };
  probe?: { ran: boolean; ok?: boolean; error?: string; chat_count?: number };
  channels?: Record<string, FeishuChannel>;
  steps?: FeishuStep[];
  last_test_at?: number;
}

export async function getFeishuStatus(probe = false): Promise<FeishuStatus> {
  const { data } = await api.get<FeishuStatus>("/settings/feishu", {
    params: probe ? { probe: 1 } : undefined,
    // probe 会真的去飞书换 token，比本地读配置慢得多
    timeout: probe ? 40000 : 15000,
  });
  return data;
}

export interface FeishuActionResp {
  ok: boolean; error?: string; note?: string;
  chats?: { chat_id: string; name: string }[];
  members?: { open_id: string; name: string }[];
  message_id?: string;
  created?: string[]; replaced?: string[];
  // install_relay / install_timer 的返回
  steps?: { cmd: string; ok: boolean; detail: string }[];
  hint?: string;
  patrol?: FeishuStatus["patrol"];
}

export async function feishuAction(body: Record<string, unknown>): Promise<FeishuActionResp> {
  const { data } = await api.post<FeishuActionResp>("/settings/feishu", body, { timeout: 40000 });
  return data;
}

// ── 亚马逊官方 API（SP-API / Ads API）────────────────────────────────────────
// 凭据只存 IvyeaAgent 一侧，这里全程只经手"要填什么"和"通没通"，不留副本。

export interface AmazonMarketplace {
  sid: string;
  marketplace_id: string;
  name: string;
  country?: string;
  region?: string;
  ads_profile_id: string;
  seller_id?: string;
}
export interface AmazonStatus {
  ok: boolean;
  error?: string;
  hint?: string;
  configured?: boolean;
  ads_configured?: boolean;
  ads_uses_own_app?: boolean;
  region?: string;
  spapi_host?: string;
  ads_host?: string;
  seller_id?: string;
  marketplaces?: AmazonMarketplace[];
  marketplace_count?: number;
  with_ads_profile?: number;
  catalog?: { marketplace_id: string; country: string; region: string }[];
}
export interface AmazonVerifyResp {
  ok: boolean;
  error?: string;
  steps?: { step: string; ok: boolean; detail: string; hint: string }[];
  profiles?: { profile_id: string; country: string; type: string; name: string; marketplace_id: string }[];
}

export async function getAmazonStatus(): Promise<AmazonStatus> {
  const { data } = await api.get<AmazonStatus>("/settings/amazon", { timeout: 15000 });
  return data;
}

export async function saveAmazonConfig(body: Record<string, unknown>): Promise<AmazonStatus> {
  const { data } = await api.post<AmazonStatus>("/settings/amazon", body, { timeout: 30000 });
  return data;
}

export async function amazonAction(action: string): Promise<AmazonVerifyResp> {
  // verify 会真的换 token + 打库存接口 + 列广告档案，比本地读配置慢得多
  const { data } = await api.post<AmazonVerifyResp>("/settings/amazon/action", { action },
    { timeout: 120000 });
  return data;
}
