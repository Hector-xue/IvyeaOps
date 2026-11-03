import type { AsinPulse, PulseDelta } from "../../../api/home";
import { amazonDp } from "../../../lib/marketplaces";
import { symOfMarketplace } from "../lingxingCurrency";

export type AsinState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; pulse: AsinPulse; delta: PulseDelta; ts: number; cached?: boolean }
  | { kind: "err"; msg: string };

function fmtVol(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(1) + "K";
  return String(Math.round(v));
}

/** 价格 0 = 数据源没取到在售价（无购物车 / 缺货 / 下架），不是"免费"，显示成 — 。
 *  符号跟站点走：日本站不该显示 $。 */
function fmtPrice(v: number | null | undefined, marketplace: string): string {
  if (v == null || v <= 0) return "—";
  const sym = symOfMarketplace(marketplace);
  return sym + (sym === "¥" ? Math.round(v).toLocaleString("en-US") : v.toFixed(2));
}

function fmtWhen(ts: number): string {
  const d = new Date(ts);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function Delta({ v, kind }: { v?: number; kind?: "price" | "bsr" | "count" }) {
  if (v == null || v === 0) return null;
  const up = v > 0;
  const mag = kind === "price" ? Math.abs(v).toFixed(2) : fmtVol(Math.abs(v));
  return <span className={"asin-delta " + (up ? "up" : "down")}>{up ? "▲" : "▼"}{mag}</span>;
}

export default function AsinCard({ asin, label, marketplace, state, onRemove, onRefresh }: {
  asin: string;
  label?: string;
  marketplace: string;
  state: AsinState;
  onRemove: () => void;
  onRefresh?: () => void;
}) {
  const pulse = state.kind === "ok" ? state.pulse : null;
  const delta = state.kind === "ok" ? state.delta : {};
  const title = pulse?.title || label || asin;
  const busy = state.kind === "loading";

  return (
    <div className="asin-card">
      <div className="asin-card-hd">
        {pulse?.image
          ? <img className="asin-thumb" src={pulse.image} alt="" loading="lazy" />
          : <div className="asin-thumb asin-thumb-ph">{asin.slice(-2)}</div>}
        <div className="asin-card-id">
          <span className="asin-card-title" title={title}>{title}</span>
          <span className="asin-card-sub">
            <a href={amazonDp(asin, marketplace)} target="_blank" rel="noreferrer" className="asin-link">{asin}</a>
            {pulse?.brand && <span className="asin-brand">· {pulse.brand}</span>}
          </span>
        </div>
        <div className="asin-card-actions">
          {onRefresh && (
            <button className="asin-icon-btn" onClick={onRefresh} disabled={busy} title="实时刷新（消耗 1 次当前数据源调用）">
              {busy ? <span className="spin" /> : "↻"}
            </button>
          )}
          <button className="asin-icon-btn" onClick={onRemove} title="移除">✕</button>
        </div>
      </div>

      {state.kind === "idle" && (
        <button className="asin-fetch-hint" onClick={onRefresh}>点击拉取数据</button>
      )}
      {state.kind === "loading" && (
        <div aria-busy="true" aria-live="polite" style={{ display: "grid", gap: 7, paddingTop: 4 }}>
          <div className="skeleton" style={{ height: 56, borderRadius: 6 }} />
          <div className="skeleton line lg" />
          <div className="skeleton line sm" />
        </div>
      )}
      {state.kind === "err" && <div className="pulse-err">⚠ {state.msg}</div>}
      {state.kind === "ok" && pulse?.error && <div className="pulse-err">⚠ {pulse.error}</div>}

      {state.kind === "ok" && !pulse?.error && (
        <>
          <div className="asin-metrics">
            <div className="asin-metric">
              <div className="asin-metric-val">{fmtPrice(pulse!.price, marketplace)} <Delta v={delta.price} kind="price" /></div>
              <div className="asin-metric-label">价格</div>
            </div>
            <div className="asin-metric">
              <div className="asin-metric-val">
                {pulse!.bsr != null ? "#" + fmtVol(pulse!.bsr) : "—"} <Delta v={delta.bsr} kind="bsr" />
                {pulse!.sub_rank != null && <span className="asin-subrank">子#{fmtVol(pulse!.sub_rank)}</span>}
              </div>
              <div className="asin-metric-label" title={pulse!.bsr_category || undefined}>
                {pulse!.bsr_category ? `BSR · ${pulse!.bsr_category}` : "BSR 排名"}
              </div>
            </div>
            <div className="asin-metric">
              <div className="asin-metric-val">{fmtVol(pulse!.est_sales)} <Delta v={delta.est_sales} kind="count" /></div>
              <div className="asin-metric-label">估算月销</div>
            </div>
            <div className="asin-metric">
              <div className="asin-metric-val">{pulse!.rating != null ? pulse!.rating.toFixed(1) + "★" : "—"}</div>
              <div className="asin-metric-label">评分</div>
            </div>
            <div className="asin-metric">
              <div className="asin-metric-val">{fmtVol(pulse!.review_count)} <Delta v={delta.review_count} kind="count" /></div>
              <div className="asin-metric-label">评论数</div>
            </div>
            <div className="asin-metric">
              <div className="asin-metric-val">{pulse!.variations != null ? pulse!.variations : "—"}</div>
              <div className="asin-metric-label">变体</div>
            </div>
          </div>

          {(pulse!.coupon || pulse!.deal) && (
            <div className="asin-badges">
              {pulse!.coupon ? <span className="asin-badge coupon">Coupon</span> : null}
              {pulse!.deal ? <span className="asin-badge deal">活动</span> : null}
            </div>
          )}

          <div className="asin-foot">
            {state.cached
              ? <span className="asin-cached">缓存 · {fmtWhen(state.ts)}</span>
              : <span>{fmtWhen(state.ts)} 实时</span>}
          </div>
        </>
      )}
    </div>
  );
}
