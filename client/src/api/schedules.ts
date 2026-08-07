import { api } from "./client";

/** 一条定时任务：到点让 Agent 自己跑一轮。 */
export type ScheduleTask = {
  id: string;
  name: string;
  cron: string;
  prompt: string;
  skill: string;
  workspace: string;
  enabled: boolean;
  principal: string;
  created: number;
  updated: number;
  last_run: number;
  next_run: number;
  /** 服务端算好的"下次什么时候跑"，直接展示。 */
  next_text?: string;
};

export type ScheduleRun = {
  id: string;
  task_id: string;
  trigger: string;
  status: "running" | "done" | "error" | string;
  started: number;
  finished: number;
  session_id: string;
  output: string;
  error: string;
};

export async function listSchedules() {
  const { data } = await api.get<{ ok: boolean; tasks: ScheduleTask[] }>("/schedules");
  return data.tasks || [];
}

export async function createSchedule(body: {
  name: string; cron: string; prompt: string;
  skill?: string; workspace?: string; enabled?: boolean;
}) {
  const { data } = await api.post<{ ok: boolean; task: ScheduleTask }>("/schedules", body);
  return data.task;
}

export async function patchSchedule(id: string, patch: Partial<{
  name: string; cron: string; prompt: string; skill: string; workspace: string; enabled: boolean;
}>) {
  const { data } = await api.patch<{ ok: boolean; task: ScheduleTask }>(`/schedules/${id}`, patch);
  return data.task;
}

export async function deleteSchedule(id: string) {
  const { data } = await api.delete<{ ok: boolean }>(`/schedules/${id}`);
  return data;
}

export async function scheduleRuns(id: string, limit = 20) {
  const { data } = await api.get<{ ok: boolean; runs: ScheduleRun[] }>(
    `/schedules/${id}/runs`, { params: { limit } });
  return data.runs || [];
}

export async function runScheduleNow(id: string) {
  // 手动触发和自动跑走同一条路，可能要几分钟。
  const { data } = await api.post<{ ok: boolean; run: ScheduleRun }>(
    `/schedules/${id}/run`, {}, { timeout: 600000 });
  return data.run;
}

/** 保存前预览：这个 cron 接下来几次到底什么时候跑。 */
export async function previewCron(expr: string) {
  const { data } = await api.post<{ ok: boolean; next?: string[]; error?: string }>(
    "/schedules/preview-cron", null, { params: { expr } });
  return data;
}

/** 常用节奏预设 —— 大多数人不想手写 cron。 */
export const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: "每天 09:00", cron: "0 9 * * *" },
  { label: "每个工作日 09:00", cron: "0 9 * * 1-5" },
  { label: "每周一 09:00", cron: "0 9 * * 1" },
  { label: "每月 1 号 09:00", cron: "0 9 1 * *" },
  { label: "每 4 小时", cron: "0 */4 * * *" },
];
