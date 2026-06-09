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
  // AI 问答（直连大模型，不走智能体）
  assistant_provider: string;
  assistant_model: string;
  assistant_api_key: string;
  assistant_base_url: string;
  // AI 生图
  image_model: string;
  image_api_key: string;
  image_base_url: string;
  // GBrain 语义检索 embedding
  gbrain_embed_provider: string;
  gbrain_embed_model: string;
  gbrain_embed_api_key: string;
  // AI synthesis (Apimart for images)
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
  gbrain_bin: string;
  brain_root: string;
  openai_api_key: string;
  // Feishu alerts
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

export interface HealthResp {
  version: RunnerStatus;
  apimart: RunnerStatus;
  sorftime: RunnerStatus;
  imgflow: RunnerStatus;
  gbrain_bin: RunnerStatus;
  ollama: RunnerStatus;
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
  const { data } = await api.post<TestResult>("/settings/test", { key, value }, { timeout: 15000 });
  return data;
}

export async function autodetectSettings(): Promise<AutodetectResp> {
  const { data } = await api.post<AutodetectResp>("/settings/autodetect", {}, { timeout: 10000 });
  return data;
}
