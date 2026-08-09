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
  exceeded: boolean;
  enabled: boolean;
  notified?: boolean;
  already_notified?: boolean;
}

export async function getNotifyConfig(): Promise<NotifyConfig> {
  return (await api.get("/notify/config")).data;
}

export async function testNotify(url = ""): Promise<{ ok: boolean; detail: string }> {
  return (await api.post("/notify/test", { url })).data;
}

export async function getBudget(): Promise<BudgetStatus> {
  return (await api.get("/notify/budget")).data;
}
