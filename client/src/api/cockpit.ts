import { api } from "./client";

/* ── 促销倒计时 ───────────────────────────────────────────────────────────── */

export type PromoPhase = "running" | "upcoming" | "ended" | "closed" | "unknown";
export type PromoKind = "coupon" | "seckill" | "manage" | "vip_discount";

export interface PromoAsin {
  asin: string;
  title: string;
  msku: string;
  image: string;
  url: string;
  sales_price: number | null;
  stock: number | null;
}

export interface PromoItem {
  id: string;
  promotion_id: string;
  kind: PromoKind;
  kind_label: string;
  name: string;
  sid: number;
  store: string;
  marketplace: string;
  country: string;
  tz: string;
  currency_icon: string;
  status_raw: string;
  status_label: string;
  /** 带时区的绝对时刻 —— 倒计时在前端按这个跑秒，服务端只给一次。 */
  start_at: string | null;
  end_at: string | null;
  start_local: string;
  end_local: string;
  seconds_to_start: number | null;
  seconds_to_end: number | null;
  phase: PromoPhase;
  budget: number | null;
  cost: number | null;
  budget_used_pct: number | null;
  sales_amount: number | null;
  sales_volume: number | null;
  discount: string;
  draw_quantity?: number | null;
  exchange_quantity?: number | null;
  product_quantity?: number | null;
  type_label?: string;
  sold_rate?: number | null;
  last_sync_time: string | null;
  sync_age_hours: number | null;
  asins: PromoAsin[];
  asin_count: number;
  from_listing_only?: boolean;
}

export interface PromoBoard {
  generated_at: string;
  source: string;
  scope: { sids: number[]; store_count: number; horizon_days: number; include_ended: boolean };
  stores: { sid: number; name: string; code: string; currency: string }[];
  items: PromoItem[];
  summary: {
    total: number; running: number; upcoming: number;
    ending_24h: number; ending_72h: number; budget_risk: number; with_asin: number;
  };
  freshness: { known: boolean; stale: boolean; age_hours: number | null; hint: string };
  errors: { source: string; error: string }[];
}

export async function fetchPromotions(opts: {
  sids?: string; horizonDays?: number; includeEnded?: boolean; force?: boolean;
} = {}) {
  const { data } = await api.get<PromoBoard>("/cockpit/promotions", {
    params: {
      sids: opts.sids || "",
      horizon_days: opts.horizonDays ?? 30,
      include_ended: opts.includeEnded ?? false,
      force: opts.force ?? false,
    },
    timeout: 180000,
  });
  return data;
}

/* ── 广告看板 ─────────────────────────────────────────────────────────────── */

export type Health = "good" | "watch" | "bad" | "unknown";

export interface AdjustIntent {
  op_type: string;
  sid: number;
  target_id: string;
  target_name: string;
  cur_value: number;
  new_value: number;
  rationale: string;
}

export interface Anomaly {
  code: string;
  severity: "crit" | "warn" | "info";
  label: string;
  detail: string;
  intent: AdjustIntent | null;
  sid?: number;
  store?: string;
  campaign_id?: string;
  name?: string;
}

export interface Metrics {
  spend: number; sales: number; orders: number; clicks: number; impressions: number;
  acos: number | null; roas: number | null; ctr: number | null; cvr: number | null; cpc: number | null;
}

export interface Campaign extends Metrics {
  sid: number;
  store: string;
  marketplace: string;
  currency: string;
  campaign_id: string;
  name: string;
  state: string;
  serving_status: string;
  daily_budget: number;
  targeting_type: string;
  target_acos: number | null;
  breakeven_acos: number | null;
  today_spend: number;
  budget_used_pct: number | null;
  acos_vs_target: number | null;
  health: Health;
  prev: Metrics | null;
  spend_change_pct: number | null;
  anomalies: Anomaly[];
}

export interface AdsTarget {
  margin: number | null;
  breakeven_acos: number | null;
  target_acos: number | null;
  note: string;
}

export interface AdsBoard {
  generated_at: string;
  source: string;
  scope: {
    sids: number[]; days: number; store_count: number;
    skipped: { sid: number; name: string; reason: string }[];
  };
  totals: Metrics;
  prev_totals: Metrics | null;
  delta: {
    spend_pct: number | null; sales_pct: number | null;
    orders_pct: number | null; acos_delta: number | null;
  };
  by_store: (Metrics & { sid: number; store: string; marketplace: string; currency: string; target: AdsTarget })[];
  by_campaign: Campaign[];
  campaign_count: number;
  trend: (Metrics & { date: string })[];
  anomalies: Anomaly[];
  errors: { source: string; error: string }[];
}

export async function fetchAdsBoard(opts: {
  sids?: string; days?: number; top?: number; force?: boolean;
} = {}) {
  const { data } = await api.get<AdsBoard>("/cockpit/ads", {
    params: { sids: opts.sids || "", days: opts.days ?? 7, top: opts.top ?? 25, force: opts.force ?? false },
    timeout: 300000,
  });
  return data;
}

export interface HourPoint {
  hour: number; spend: number; sales: number; clicks: number;
  impressions: number; orders: number; acos: number | null; cpc: number | null;
  spend_cumulative: number;
}

export interface HourlyResult {
  sid: number;
  date: string;
  series: { campaign_id: string; points: HourPoint[]; spend_total: number }[];
  truncated: boolean;
  max_campaigns: number;
  errors: { campaign_id: string; error: string }[];
}

export async function fetchHourly(sid: number, campaignIds: string[], date?: string) {
  const { data } = await api.get<HourlyResult>("/cockpit/ads/hourly", {
    params: { sid, campaign_ids: campaignIds.join(","), date: date || "" },
    timeout: 180000,
  });
  return data;
}

/* ── 直接调整（工单制） ───────────────────────────────────────────────────── */

export interface FastLane {
  eligible: boolean;
  reason: string;
  checks: { name: string; ok: boolean; detail: string }[];
  max_pct: number;
}

export interface Ticket {
  id: string;
  created_at: string;
  source: string;
  status: "reviewing" | "awaiting_human" | "guardrail_blocked" | "review_rejected"
    | "executed" | "failed" | "rejected" | "rolled_back";
  intent: Record<string, any>;
  reviews: any | null;
  guardrail: { ok: boolean; checks: { name: string; ok: boolean; detail: string }[] } | null;
  fast_lane: FastLane | null;
  snapshot: Record<string, any> | null;
  result: Record<string, any> | null;
  decided_by: string;
  error: string;
}

export interface AdjustPayload {
  op_type: string;
  sid: number;
  target_id?: string;
  target_name?: string;
  new_value?: number | null;
  cur_value?: number | null;
  new_state?: string;
  cur_state?: string;
  rationale?: string;
}

export async function createAdjust(payload: AdjustPayload) {
  const { data } = await api.post<Ticket>("/cockpit/ads/adjust", payload, { timeout: 60000 });
  return data;
}

export async function fetchTicket(id: string) {
  const { data } = await api.get<Ticket>(`/cockpit/ads/adjust/${id}`);
  return data;
}

export async function confirmAdjust(id: string, dryRun = false) {
  const { data } = await api.post<Ticket>(`/cockpit/ads/adjust/${id}/confirm`, null, {
    params: { dry_run: dryRun }, timeout: 120000,
  });
  return data;
}

export async function rejectAdjust(id: string) {
  const { data } = await api.post<Ticket>(`/cockpit/ads/adjust/${id}/reject`);
  return data;
}

/* ── 状态 ─────────────────────────────────────────────────────────────────── */

export interface CockpitStatus {
  lingxing_enabled: boolean;
  operate_active: boolean;
  fast_lane: { enabled: boolean; max_pct: number; require_human: boolean };
  sync: {
    enabled: boolean; interval_minutes: number; days: number;
    last_started_at: string | null; last_finished_at: string | null;
    age_minutes: number | null; last_result: any; running: boolean;
  };
  op_types: { key: string; label: string; category: string }[];
}

export async function fetchCockpitStatus() {
  const { data } = await api.get<CockpitStatus>("/cockpit/status");
  return data;
}

export async function syncNow() {
  const { data } = await api.post<{ ok: boolean; steps: any[]; seconds: number; error?: string }>(
    "/cockpit/sync", null, { timeout: 600000 });
  return data;
}
