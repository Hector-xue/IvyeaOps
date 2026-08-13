import { useEffect, useRef, useState } from "react";
import {
  listMarketWatch, addMarketWatch, deleteMarketWatch,
  fetchMarketSeries, recordMarketNow, backfillMarket, marketDailyBackfill,
  type MarketWatchItem, type MarketSeries,
} from "../../../api/home";
import TrendChart, { type TrendSeries } from "./TrendChart";
import type { DataSourceId } from "../../../lib/dataSource";
import { useToast } from "../../../components/toast";

function fmtVol(v: number): string {
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(1) + "K";
  return String(Math.round(v));
}

type View = "day" | "week" | "month";

// Monday of the week for a 'YYYY-MM-DD' date.
function weekKey(day: string): string {
  const d = new Date(day + "T00:00:00");
  const dow = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - dow);
  return d.toISOString().slice(0, 10);
}

// Aggregate daily points into day/week/month buckets (average within bucket).
function bucketPoints(points: { day: string; value: number }[], view: View): { day: string; value: number }[] {
  if (view === "day") return points;
  const keyOf = view === "week" ? weekKey : (d: string) => d.slice(0, 7) + "-01";
  const m = new Map<string, { sum: number; n: number }>();
  for (const p of points) {
    const k = keyOf(p.day);
    const e = m.get(k) || { sum: 0, n: 0 };
    e.sum += p.value; e.n += 1;
    m.set(k, e);
  }
  return [...m.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .map(([day, e]) => ({ day, value: Math.round((e.sum / e.n) * 100) / 100 }));
}

export default function MarketTraffic({ marketplace, dataSource }: { marketplace: string; dataSource: DataSourceId }) {
  const [items, setItems] = useState<MarketWatchItem[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [series, setSeries] = useState<MarketSeries | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [recording, setRecording] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [view, setView] = useState<View>(() => (localStorage.getItem("ivyea-ops-mkt-view") as View) || "month");
  const [catInput, setCatInput] = useState("");
  const [dailyBusy, setDailyBusy] = useState(false);
  const [dailyMsg, setDailyMsg] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const notify = useToast();
  const sourceName = dataSource === "sellersprite" ? "卖家精灵" : "Sorftime";

  useEffect(() => { localStorage.setItem("ivyea-ops-mkt-view", view); }, [view]);

  const mine = items.filter(it => it.marketplace === marketplace);

  // Load the watchlist and pick a baseline for the current site, based on the
  // freshly-fetched list (not stale state): keep the current selection when it
  // still exists for this site, otherwise fall back to the site's first entry.
  const loadList = async (pickQuery?: string) => {
    try {
      const all = await listMarketWatch(dataSource);
      setItems(all);
      const forMkt = all.filter(it => it.marketplace === marketplace);
      setSelected(prev => pickQuery ?? (forMkt.some(it => it.query === prev) ? prev : forMkt[0]?.query ?? ""));
    } catch { /* ignore */ }
  };

  useEffect(() => { loadList(); /* eslint-disable-next-line */ }, [marketplace, dataSource]);

  // Load series when selection changes.
  useEffect(() => {
    if (!selected) { setSeries(null); return; }
    let alive = true;
    setLoading(true);
    fetchMarketSeries(selected, marketplace, dataSource)
      .then(s => { if (alive) setSeries(s); })
      .catch(() => { if (alive) setSeries(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [selected, marketplace, dataSource]);

  const handleAdd = async () => {
    const q = input.trim();
    if (!q) return;
    setInput("");
    try {
      await addMarketWatch({ query: q, marketplace, data_source: dataSource });
      await loadList(q);
    } catch (e: any) {
      notify("error", `添加基线失败：${e?.message || "请求失败"}`);
    }
    inputRef.current?.focus();
  };

  const handleRemove = async (item: MarketWatchItem) => {
    await deleteMarketWatch(item.id).catch(() => notify("warn", `「${item.query}」服务端删除失败，刷新后可能回来`));
    await loadList();
  };

  const handleRecordNow = async () => {
    setRecording(true);
    try {
      await recordMarketNow(dataSource);
      if (selected) {
        const s = await fetchMarketSeries(selected, marketplace, dataSource);
        setSeries(s);
      }
    } catch (e: any) {
      notify("error", `记录失败：${e?.message || "请求失败"}`);
    } finally { setRecording(false); }
  };

  const handleBackfill = async () => {
    if (!selected) return;
    setBackfilling(true);
    try {
      await backfillMarket(selected, marketplace, dataSource);
      const s = await fetchMarketSeries(selected, marketplace, dataSource);
      setSeries(s);
    } catch (e: any) {
      notify("error", `导入历史失败：${e?.message || "请求失败"}`);
    } finally { setBackfilling(false); }
  };

  const handleDailyBackfill = async () => {
    if (!selected || !catInput.trim()) return;
    setDailyBusy(true);
    setDailyMsg("");
    try {
      const r = await marketDailyBackfill(selected, marketplace, catInput.trim(), 31, dataSource);
      setDailyMsg(r.error
        ? `失败：${r.error}`
        : `已拉 ${r.filled} 天日数据 · 类目：${r.category_name || r.node_id}`);
      const s = await fetchMarketSeries(selected, marketplace, dataSource);
      setSeries(s);
    } catch (e: any) {
      setDailyMsg(e?.message || "请求失败");
    } finally { setDailyBusy(false); }
  };

  // Build chart series, aggregated to the chosen view (day/week/month).
  const B = (pts: { day: string; value: number }[]) => bucketPoints(pts, view);

  // 大盘指标: 搜索量(左轴·需求·月) + 合计月销(右轴·吞吐·日), each on its own real axis.
  const metricTrend: TrendSeries[] = series ? [
    { name: "搜索量", color: "#4ade80", fmt: fmtVol, axis: "left" as const,
      points: B(series.market.filter(p => p.search_volume != null).map(p => ({ day: p.day, value: p.search_volume as number }))) },
    { name: "类目总销量", color: "#60a5fa", fmt: fmtVol, axis: "right" as const,
      points: B(series.market.filter(p => p.total_sales != null).map(p => ({ day: p.day, value: p.total_sales as number }))) },
  ].filter(s => s.points.length > 0) : [];

  // Attribution: 大盘(category total sales, left) vs 自有 / 竞对 (right, shared scale).
  const marketBaseline = series
    ? (series.market.some(p => p.total_sales != null)
        ? series.market.filter(p => p.total_sales != null).map(p => ({ day: p.day, value: p.total_sales as number }))
        : series.market.filter(p => p.search_volume != null).map(p => ({ day: p.day, value: p.search_volume as number })))
    : [];
  const attrTrend: TrendSeries[] = series ? [
    { name: "大盘(类目总销)", color: "#9ca3af", fmt: fmtVol, axis: "left" as const, area: false, points: B(marketBaseline) },
    { name: "我的销量", color: "#4ade80", fmt: fmtVol, axis: "right" as const, points: B(series.own.map(p => ({ day: p.day, value: p.value }))) },
    { name: "竞对销量", color: "#f87171", fmt: fmtVol, axis: "right" as const, points: B(series.competitor.map(p => ({ day: p.day, value: p.value }))) },
  ].filter(s => s.points.length > 0) : [];

  return (
    <div className="pulse-page">
      <div className="pulse-header">
        <span className="pulse-header-title">
          <span style={{ color: "var(--acc)" }}>↗</span> 大盘流量
        </span>
        <div className="pulse-input-wrap">
          <input
            ref={inputRef}
            className="pulse-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleAdd()}
            placeholder="添加类目基线(类目词) + Enter"
          />
          <button className="tbtn" onClick={handleAdd} disabled={!input.trim()}>+ 添加基线</button>
        </div>
        <button className="tbtn" onClick={handleBackfill} disabled={backfilling || !selected}
          title={`用 ${sourceName} 趋势数据回填历史月度点`}>
          {backfilling ? <><span className="spin" style={{ marginRight: 6 }} />导入中…</> : "⇣ 导入历史"}
        </button>
        <button className="tbtn tbtn-acc" onClick={handleRecordNow} disabled={recording || mine.length === 0}>
          {recording ? <><span className="spin" style={{ marginRight: 6 }} />记录中…</> : "↻ 立即记录"}
        </button>
      </div>

      {mine.length === 0 ? (
        <div className="pulse-onboard">
          <div className="pulse-onboard-icon">↗</div>
          <div className="pulse-onboard-title">大盘流量监控</div>
          <div className="pulse-onboard-sub">
            添加你关注的类目基线，系统每天自动记录大盘需求(搜索量)、TOP 合计销量与均价，
            累积成日曲线；并叠加自有 / 竞对销量，帮你判断涨跌是大盘还是自身原因
          </div>
          <div className="pulse-onboard-sub" style={{ marginTop: 8, fontSize: "var(--fs-11)", color: "var(--t3)" }}>
            提示：曲线从开始记录当天起累积，需几天数据才有趋势意义
          </div>
        </div>
      ) : (
        <>
          {/* Baseline chips + view toggle */}
          <div className="mkt-baselines">
            {mine.map(it => (
              <span key={it.id} className={"mkt-baseline" + (selected === it.query ? " active" : "")}>
                <button className="mkt-baseline-name" onClick={() => setSelected(it.query)}>{it.query}</button>
                <button className="mkt-baseline-del" onClick={() => handleRemove(it)} title="移除">✕</button>
              </span>
            ))}
            <div className="market-mode-toggle" style={{ marginLeft: "auto" }}>
              {([["day", "日"], ["week", "周"], ["month", "月"]] as [View, string][]).map(([v, lbl]) => (
                <button key={v} className={"market-mode-btn" + (view === v ? " active" : "")} onClick={() => setView(v)}>
                  {lbl}
                </button>
              ))}
            </div>
          </div>

          {loading && (
            <div aria-busy="true" aria-live="polite" style={{ paddingTop: 6 }}>
              <div className="skeleton" style={{ height: 140, borderRadius: 6, marginBottom: 10 }} />
              <div className="skeleton line lg" />
              <div className="skeleton line md" />
            </div>
          )}

          {!loading && series && (
            <>
              <div className="cat-block">
                <div className="cat-block-title">大盘热度 · {selected}（搜索量=左轴·需求；类目总销量=右轴·出货量）</div>
                {metricTrend.length
                  ? <TrendChart series={metricTrend} />
                  : <div className="lc-empty">暂无数据点 · 点「立即记录」或等每日自动记录</div>}
                <div className="cat-hint" style={{ marginTop: 4 }}>
                  {dataSource === "sellersprite" ? (
                    <>搜索量、类目销量与 ASIN 销量使用<b>卖家精灵月度趋势</b>；卖家精灵暂不提供近 31 天类目日历史。</>
                  ) : (
                    <>搜索量仅<b>月度</b>（Sorftime 无日粒度）；类目总销量可拉<b>近31天日数据</b>（按日历史，需正确类目）——它也是下方归因图的大盘基线，且会顺带补上你的自有/竞对日销量。</>
                  )}
                </div>
                {dataSource === "sorftime" ? <div className="mkt-daily">
                  <input className="pulse-input" style={{ width: "auto", flex: "1 1 180px", minWidth: 120 }}
                    value={catInput} onChange={e => setCatInput(e.target.value)}
                    placeholder="该品类真实 ASIN / nodeId（用于按日拉类目销量）" />
                  <button className="tbtn" onClick={handleDailyBackfill} disabled={dailyBusy || !catInput.trim()}
                    title="用 category_report_from_history 按日拉近 31 天类目合计月销/均价（约 31 次 Sorftime 调用）">
                    {dailyBusy ? <><span className="spin" style={{ marginRight: 5 }} />拉取中…</> : "拉近31天日数据"}
                  </button>
                  {dailyMsg && <span className="cat-hint">{dailyMsg}</span>}
                </div> : null}
              </div>

              <div className="cat-block">
                <div className="cat-block-title">涨跌归因 · 我的销量 vs 大盘 vs 竞对</div>
                {attrTrend.length > 0
                  ? <TrendChart series={attrTrend} />
                  : <div className="lc-empty">需在「竞品监控 / 自有 ASIN」添加并刷新若干天后显示</div>}
                <div className="cat-hint">
                  解读：自有线随大盘同涨同跌 → 大盘原因；大盘平稳而自有独跌 → listing/竞对原因
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
