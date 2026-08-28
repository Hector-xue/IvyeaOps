import { useEffect, useMemo, useRef, useState } from "react";
import { fetchPromotions, type PromoBoard, type PromoItem, type PromoKind } from "../../../api/cockpit";
import { errText } from "../../../lib/errText";
import LingXingGate from "./LingXingGate";

/**
 * 促销日历 —— 已报活动与优惠券的结束倒计时。
 *
 * 倒计时**在前端跑秒**：服务端只给一次带时区的绝对结束时刻（`end_at`），
 * 这里每秒重算剩余量。反过来做（服务端每秒给剩余秒数）既费请求，又会因为
 * 网络延迟和页面挂起而越走越偏。
 */

// 用几何字形而不是 emoji：服务器/精简 Linux 上没有 emoji 字体时，🎟🏷👑 会渲染成
// 豆腐块（headless 实测），而 ◈▣♛ 这类符号在基础字体里就有。其余标签页用的也是
// 这一族字形，视觉上本来就该一致。
const KIND_ICON: Record<PromoKind, string> = {
  coupon: "◈", seckill: "⚡", manage: "▣", vip_discount: "♛",
};

const KIND_ORDER: PromoKind[] = ["coupon", "seckill", "manage", "vip_discount"];

/** 剩余秒数 → 「2天3小时」/「4小时12分」/「38分12秒」。
 *  最后一小时才显示秒 —— 平时秒位一直跳只会分散注意力。 */
function humanize(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d > 0) return `${d}天${h}小时`;
  if (h > 0) return `${h}小时${m}分`;
  if (m > 0) return `${m}分${sec}秒`;
  return `${sec}秒`;
}

/** 紧急度分档，决定颜色。6 小时内是"今天必须处理"。 */
function urgency(seconds: number | null): "gone" | "now" | "soon" | "ok" {
  if (seconds == null) return "ok";
  if (seconds <= 0) return "gone";
  if (seconds <= 6 * 3600) return "now";
  if (seconds <= 24 * 3600) return "soon";
  return "ok";
}

function money(v: number | null, icon: string): string {
  if (v == null) return "—";
  return `${icon}${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function Countdown({ item, now }: { item: PromoItem; now: number }) {
  const target = item.phase === "upcoming" ? item.start_at : item.end_at;
  if (!target) return <span className="cp-count cp-count-unknown">时间未知</span>;
  const left = Math.floor((new Date(target).getTime() - now) / 1000);
  const level = urgency(left);
  const prefix = item.phase === "upcoming" ? "距开始" : "距结束";
  return (
    <span className={`cp-count cp-count-${level}`}>
      <span className="cp-count-label">{prefix}</span>
      <span className="cp-count-value">{left <= 0 ? "已结束" : humanize(left)}</span>
    </span>
  );
}

function PromoCard({ item, now }: { item: PromoItem; now: number }) {
  const [open, setOpen] = useState(false);
  const left = item.end_at ? Math.floor((new Date(item.end_at).getTime() - now) / 1000) : null;
  const level = urgency(item.phase === "upcoming" ? null : left);
  const budgetPct = item.budget_used_pct;

  return (
    <div className={`cp-card cp-card-${level}`}>
      <div className="cp-card-hd">
        <span className="cp-kind" title={item.kind_label}>{KIND_ICON[item.kind] ?? "◆"}</span>
        <span className="cp-name" title={item.name}>{item.name}</span>
        <span className="cp-mkt">{item.marketplace || item.country}</span>
        <Countdown item={item} now={now} />
      </div>

      <div className="cp-card-meta">
        <span className={`cp-phase cp-phase-${item.phase}`}>{item.status_label}</span>
        {item.type_label && <span className="cp-tag">{item.type_label}</span>}
        {item.discount && <span className="cp-tag">{item.discount}</span>}
        <span className="cp-store">{item.store}</span>
        <span className="cp-window" title={`站点时间（${item.tz}）`}>
          {item.start_local?.slice(5, 16)} → {item.end_local?.slice(5, 16)}
        </span>
      </div>

      {budgetPct != null && (
        <div className="cp-budget">
          <div className="cp-budget-bar">
            <div
              className={"cp-budget-fill" + (budgetPct >= 80 ? " hot" : "")}
              style={{ width: `${Math.min(100, budgetPct)}%` }}
            />
          </div>
          <span className="cp-budget-text">
            预算 {money(item.cost, item.currency_icon)} / {money(item.budget, item.currency_icon)}
            <b className={budgetPct >= 80 ? "hot" : ""}>（{budgetPct}%）</b>
          </span>
        </div>
      )}

      {item.asin_count > 0 && (
        <button className="cp-asin-toggle" onClick={() => setOpen(o => !o)}>
          {open ? "▾" : "▸"} {item.asin_count} 个 ASIN
        </button>
      )}
      {open && (
        <div className="cp-asins">
          {item.asins.map(a => (
            <a key={a.asin} className="cp-asin" href={a.url || undefined}
               target="_blank" rel="noreferrer" title={a.title}>
              {a.image && <img src={a.image} alt="" loading="lazy" />}
              <span className="cp-asin-id">{a.asin}</span>
              <span className="cp-asin-title">{a.title}</span>
              {a.stock != null && <span className="cp-asin-stock">库存 {a.stock}</span>}
            </a>
          ))}
        </div>
      )}
      {item.from_listing_only && (
        <div className="cp-note">仅出现在商品维度数据里 —— 活动列表尚未同步到这条</div>
      )}
    </div>
  );
}

export default function PromoCalendar() {
  const [board, setBoard] = useState<PromoBoard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [kind, setKind] = useState<PromoKind | "all">("all");
  const [includeEnded, setIncludeEnded] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const timer = useRef<number | null>(null);

  const load = (force = false) => {
    setLoading(true);
    setError("");
    fetchPromotions({ includeEnded, force })
      .then(setBoard)
      .catch(e => setError(errText(e, "加载失败")))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [includeEnded]);

  // 每秒推进一次"现在"，倒计时就动了。页面隐藏时浏览器会自动降频，不用特殊处理。
  useEffect(() => {
    timer.current = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, []);

  const items = useMemo(() => {
    const list = board?.items ?? [];
    return kind === "all" ? list : list.filter(i => i.kind === kind);
  }, [board, kind]);

  const counts = useMemo(() => {
    const map: Record<string, number> = { all: board?.items.length ?? 0 };
    for (const k of KIND_ORDER) map[k] = (board?.items ?? []).filter(i => i.kind === k).length;
    return map;
  }, [board]);

  const s = board?.summary;

  return (
    <div className="cp-page">
      <div className="cp-toolbar">
        <div className="cp-stats">
          <span className="cp-stat cp-stat-hot">
            <b>{s?.ending_24h ?? 0}</b> 24小时内结束
          </span>
          <span className="cp-stat"><b>{s?.ending_72h ?? 0}</b> 3天内结束</span>
          <span className="cp-stat"><b>{s?.running ?? 0}</b> 进行中</span>
          <span className="cp-stat"><b>{s?.upcoming ?? 0}</b> 未开始</span>
          {(s?.budget_risk ?? 0) > 0 && (
            <span className="cp-stat cp-stat-hot"><b>{s?.budget_risk}</b> 预算将耗尽</span>
          )}
        </div>
        <div className="cp-actions">
          <label className="cp-check">
            <input type="checkbox" checked={includeEnded}
                   onChange={e => setIncludeEnded(e.target.checked)} />
            含已结束
          </label>
          <button className="cp-btn" onClick={() => load(true)} disabled={loading}>
            {loading ? "刷新中…" : "立即刷新"}
          </button>
        </div>
      </div>

      {board?.freshness && (
        <div className={"cp-freshness" + (board.freshness.stale ? " stale" : "")}>
          <span className="cp-freshness-icon">{board.freshness.stale ? "⚠" : "◔"}</span>
          {board.freshness.hint}
        </div>
      )}

      <div className="cp-kinds">
        <button className={"cp-kind-btn" + (kind === "all" ? " active" : "")}
                onClick={() => setKind("all")}>全部 {counts.all}</button>
        {KIND_ORDER.map(k => (
          <button key={k} className={"cp-kind-btn" + (kind === k ? " active" : "")}
                  onClick={() => setKind(k)}>
            {KIND_ICON[k]} {k === "coupon" ? "优惠券" : k === "seckill" ? "秒杀"
              : k === "manage" ? "管理促销" : "会员折扣"} {counts[k] ?? 0}
          </button>
        ))}
      </div>

      <LingXingGate error={error} />

      {!loading && items.length === 0 && !error && (
        <div className="cp-empty">
          <div className="cp-empty-title">这段时间没有在跑的活动</div>
          <div className="cp-empty-desc">
            促销数据由领星的「LINGXING助手」浏览器插件同步 ——
            如果亚马逊后台确实有活动却没显示在这里，多半是助手没登录或已离线。
          </div>
        </div>
      )}

      {loading && !board && <div className="cp-loading">正在读取促销活动…</div>}

      <div className="cp-list">
        {items.map(item => <PromoCard key={item.id} item={item} now={now} />)}
      </div>

      {(board?.errors?.length ?? 0) > 0 && (
        <div className="cp-partial">
          部分数据没取到：{board!.errors.map(e => `${e.source}(${e.error})`).join("；")}
        </div>
      )}
    </div>
  );
}
