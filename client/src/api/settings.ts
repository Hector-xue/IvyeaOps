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
