// Listing 工作台 API 层：普通请求 + 后台 job（SSE 进度、轮询兜底）。
import axios from "axios";
import type { Job, ProjectDetail, ProjectSummary, RefImages } from "./types";
import { errText } from "../../../lib/errText";

const api = axios.create({ baseURL: "/api/listing", withCredentials: true });

export function messageOf(error: unknown): string {
  const err = error as { response?: { data?: { detail?: string } }; message?: string };
  return errText(err, String(error));
}

// ─── 项目 ─────────────────────────────────────────────────────────────────────

export const listProjects = async (): Promise<ProjectSummary[]> =>
  (await api.get("/projects")).data;

export const createProject = async (asin: string, marketplace: string) =>
  (await api.post("/projects", { asin, marketplace })).data as { id: string };

export const getProject = async (id: string): Promise<ProjectDetail> =>
  (await api.get(`/projects/${id}`)).data;

export const deleteProject = async (id: string) => api.delete(`/projects/${id}`);

export const saveProductInfo = async (id: string, info: Record<string, string>) =>
  (await api.post(`/projects/${id}/product-info`, info)).data;

export const getReferenceImages = async (id: string): Promise<RefImages> =>
  (await api.get(`/projects/${id}/reference-images`)).data;

export const uploadImage = async (id: string, file: File) => {
  const fd = new FormData();
  fd.append("file", file);
  return (await api.post(`/projects/${id}/upload-image`, fd, {
    headers: { "Content-Type": "multipart/form-data" },
  })).data;
};

export const deleteUploadedImage = async (id: string, filename: string) =>
  (await api.delete(`/projects/${id}/uploaded-image/${encodeURIComponent(filename)}`)).data;

export const imgflowStart = async () => (await api.post("/imgflow/start")).data;

export const saveCreativeSet = async (id: string, deliverable: string, plan: unknown) =>
  (await api.post(`/projects/${id}/creative-set`, { deliverable, plan }, { timeout: 120000 })).data;

// ─── 后台任务 ─────────────────────────────────────────────────────────────────

export const getJob = async (jobId: string): Promise<Job> =>
  (await api.get(`/jobs/${jobId}`)).data;

export const getProjectJobs = async (id: string): Promise<{ jobs: Job[]; active: Job[] }> =>
  (await api.get(`/projects/${id}/jobs`)).data;

export const startScrape = async (id: string): Promise<Job> =>
  (await api.post(`/projects/${id}/scrape`)).data;

export const startAnalyze = async (id: string): Promise<Job> =>
  (await api.post(`/projects/${id}/ai-analyze`)).data;

export const startCopy = async (id: string, extraNotes = "", competitorAsins: string[] = []): Promise<Job> =>
  (await api.post(`/projects/${id}/copy`, { extra_notes: extraNotes, competitor_asins: competitorAsins })).data;

export const startPlan = async (id: string, params: {
  target_count: number; color_scheme?: string; deliverable: string;
  visual_tone: string; language: string; brief: string;
}): Promise<Job> => (await api.post(`/projects/${id}/plan-image-set`, params)).data;

export const startRenderImage = async (id: string, params: {
  deliverable: string; index: number; prompt_override?: string;
}): Promise<Job> => (await api.post(`/projects/${id}/render-image`, params)).data;

export const startRenderSet = async (id: string, params: {
  deliverable: string; only_missing?: boolean;
}): Promise<Job> => (await api.post(`/projects/${id}/render-set`, params)).data;

export const startReviewSet = async (id: string, deliverable: string): Promise<Job> =>
  (await api.post(`/projects/${id}/review-image-set`, { deliverable })).data;

/**
 * 订阅一个 job 的进度：SSE 优先，断线自动退回 2s 轮询。
 * onUpdate 在每次进度和终态时都会被调用；返回取消函数。
 */
export function watchJob(jobId: string, onUpdate: (job: Job) => void): () => void {
  let closed = false;
  let source: EventSource | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;

  const finish = () => {
    closed = true;
    source?.close();
    if (pollTimer) clearTimeout(pollTimer);
  };

  const handle = (job: Job) => {
    if (closed) return;
    onUpdate(job);
    if (job.status !== "running") finish();
  };

  const poll = async () => {
    if (closed) return;
    try {
      handle(await getJob(jobId));
    } catch {
      /* 网络抖动继续等 */
    }
    if (!closed) pollTimer = setTimeout(poll, 2000);
  };

  try {
    source = new EventSource(`/api/listing/jobs/${jobId}/events`, { withCredentials: true });
    source.onmessage = (event) => {
      try {
        handle(JSON.parse(event.data) as Job);
      } catch {
        /* 跳过坏帧 */
      }
    };
    source.onerror = () => {
      // SSE 断开（代理缓冲/网络切换）→ 轮询兜底
      source?.close();
      source = null;
      if (!closed && !pollTimer) poll();
    };
  } catch {
    poll();
  }
  return finish;
}
