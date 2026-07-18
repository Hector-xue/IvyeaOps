// News digest API client. Matches /api/news/* on the FastAPI backend.

import { api } from "./client";

export type NewsCategory = "ai_industry" | "amazon_seller";

export type NewsItem = {
  title: string;
  source: string;
  url: string;
  summary_zh: string;
  /** 推荐理由：这条为什么对卖家/AI从业者重要（aihot 式一句话点评） */
  reason_zh?: string;
  category: NewsCategory | string;
  importance: number;
  is_official: boolean;
  published_at: string | null;
  tags: string[];
};

export type NewsDay = {
  date: string;
  generated_at: string;
  items: NewsItem[];
  stats: Record<string, number>;
  notes: string | null;
};

export type DatesResponse = {
  dates: string[];
  latest: string | null;
  total: number;
};

export type RefreshResponse = {
  triggered: boolean;
  message: string;
};

export async function listNewsDates(): Promise<DatesResponse> {
  const r = await api.get<DatesResponse>("/news/dates");
  return r.data;
}

export async function getNewsDay(
  opts: { date?: string; category?: NewsCategory } = {},
): Promise<NewsDay> {
  const params: Record<string, string> = {};
  if (opts.date) params.date = opts.date;
  if (opts.category) params.category = opts.category;
  const r = await api.get<NewsDay>("/news/list", { params });
  return r.data;
}

export async function refreshNews(): Promise<RefreshResponse> {
  const r = await api.post<RefreshResponse>("/news/refresh");
  return r.data;
}
