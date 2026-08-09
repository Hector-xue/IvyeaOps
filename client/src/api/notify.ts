import { api } from "./client";

export interface NotifyConfig {
  /** 事件 key → 中文说明。由后端给，前端不再抄一份。 */
  events: Record<string, string>;
  default_events: string[];
  enabled_events: string[];
  webhook_set: boolean;
  /** 后端从 URL 认出来的渠道：feishu / dingtalk / wecom / slack / generic。 */
  channel: string;
}

export interface BudgetStatus {
  month: string;
  limit_usd: number;
  spend_usd: number;
  ratio: number;
  /** ok | warn（≥80%）| exceeded（≥100%，自动任务已暂停） */
  level: "ok" | "warn" | "exceeded";
  exceeded: boolean;
  enabled: boolean;
  /** false = 缓存里还没算出来过，界面显示占位符而不是 $0 */
  known: boolean;
  /** 这个数是多久以前算的（秒）。界面要如实标出新鲜度。 */
  age_seconds: number;
  notified?: boolean;
  already_notified?: boolean;
}

export async function getNotifyConfig(): Promise<NotifyConfig> {
  return (await api.get("/notify/config")).data;
}

export async function testNotify(url = ""): Promise<{ ok: boolean; detail: string }> {
  return (await api.post("/notify/test", { url })).data;
}

/** 设置页用：**现算**，可能要几秒。 */
export async function getBudget(): Promise<BudgetStatus> {
  return (await api.get("/notify/budget", { timeout: 60000 })).data;
}

/** 顶栏用：只读缓存，永远很快。 */
export async function getBudgetSummary(): Promise<BudgetStatus> {
  return (await api.get("/notify/budget/summary")).data;
}
