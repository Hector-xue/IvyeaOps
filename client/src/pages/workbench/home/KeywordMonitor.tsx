import { Fragment, useEffect, useRef, useState, useMemo } from "react";
import {
  listKeywords, addKeyword as apiAddKeyword, deleteKeyword, pulseKeyword,
  fetchKeywordExtendsCached, pulseKeywordExtends, deepKeywordExtendSales,
  type KeywordItem, type KeywordData, type KeywordExtendItem,
} from "../../../api/home";
import type { DataSourceId } from "../../../lib/dataSource";
import { runPool } from "../../../lib/pool";
import { useToast } from "../../../components/toast";

const STORAGE_KEY = "ivyea-ops-pulse-keywords-v1";

type CardState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; data: KeywordData; ts: number; cached?: boolean }
  | { kind: "err"; msg: string };

// Legacy localStorage list — read once to migrate to the server, then cleared.
function loadLegacyKeywords(): string[] {
  try { const r = localStorage.getItem(STORAGE_KEY); if (r) return JSON.parse(r); } catch {}
  return [];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function num(v: any): number | null { const n = parseFloat(v); return isNaN(n) ? null : n; }

function fmtVol(v: number | null): string {
  if (v === null) return "—";
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
  if (v >= 1_000) return (v / 1_000).toFixed(0) + "K";
  return String(v);
}

// Same provider-neutral 0–100 pressure formula the server derives for
// SellerSprite (sellersprite_service._competition_index): bounded share of
// competing products vs (products + searches).
function deriveCompetition(products: number | null, searches: number | null): number | null {
  if (products == null || searches == null) return null;
  return Math.max(0, Math.min(100, (100 * products) / Math.max(products + searches, 1)));
}

function parseDetail(d: Record<string, any> | null) {
  if (!d) return null;
  // Live Sorftime wraps answers as { doc: {…field docs}, data: {…} }; keys are
  // English snake_case (monthly_search_volume …). Older Chinese keys kept as
  // fallbacks so a cached/legacy payload still renders.
  const root = d.data ?? d;
  const searchVolume = num(
    root.monthly_search_volume ?? root["月搜索量"] ?? root.searchVolume
    ?? root.search_volume ?? root.searches ?? null,
  );
  // SellerSprite ships a derived competitionIndex; Sorftime has no such field,
  // but exposes the competing-product count — derive the same index client-side
  // so the opportunity matrix / insight panels work on both providers.
  const competition = num(root.competitionIndex ?? root.competition_index ?? root.competition ?? null)
    ?? deriveCompetition(
      num(root.search_result_competitor_count ?? root["搜索结果竞品数量"] ?? root.products ?? null),
      searchVolume,
    );
  return {
    searchVolume,
    competition,
    cpc:          num(root.recommended_cpc_bid ?? root["推荐cpc竞价"] ?? root.averageCpc
                      ?? root.average_cpc ?? root.cpc ?? null),
    purchaseRate: num(root.purchaseRate ?? root.purchase_rate ?? root.buyRate ?? null),
  };
}

function parseTrend(t: Record<string, any> | null): number[] {
  if (!t) return [];
  // Live keyword_trend → { data: { search_volume_trend: ["2024-05 search volume 144680", …] } }
  // (older shape: { 搜索量趋势: ["2024年05月搜索量144680", …] }).
  const root = t.data ?? t;
  const arr: any[] = root.search_volume_trend ?? root["搜索量趋势"] ?? root.trend
    ?? root.results ?? root.items ?? (Array.isArray(root) ? root : []);
  return arr
    .map((item: any) => {
      if (typeof item === "string") {
        const m = item.match(/(\d+)\s*$/);
        return m ? parseFloat(m[1]) : NaN;
      }
      return parseFloat(item?.searchVolume ?? item?.search_volume ?? item?.value ?? item?.searches ?? "");
    })
    .filter((n) => !isNaN(n))
    .slice(-12);
}

function trendDir(vals: number[]): "up" | "down" | "flat" | null {
  if (vals.length < 4) return null;
  const recent = vals.slice(-3).reduce((a, b) => a + b, 0) / 3;
  const older  = vals.slice(-6, -3).reduce((a, b) => a + b, 0) / Math.max(vals.slice(-6, -3).length, 1);
  const pct = (recent - older) / (older || 1);
  return pct > 0.05 ? "up" : pct < -0.05 ? "down" : "flat";
}

function compLevel(v: number | null) {
  if (v == null) return null;
  return v > 70 ? "high" : v > 40 ? "mid" : "low";
}

// ── Sparkline ─────────────────────────────────────────────────────────────────

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const w = 110, h = 28;
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const pts = values.map((v, i) => ({
    x: +((i / (values.length - 1)) * w).toFixed(1),
    y: +(h - ((v - min) / range) * (h - 6) - 3).toFixed(1),
  }));
  const rising = values[values.length - 1] >= values[values.length - 2];
  const linePts = pts.map(p => `${p.x},${p.y}`).join(" ");
  const fillPts = `0,${h} ${linePts} ${pts[pts.length - 1].x},${h}`;
  return (
    <svg width={w} height={h} style={{ display: "block", overflow: "visible" }}>
      <polygon points={fillPts} fill={rising ? "rgba(74,222,128,.1)" : "rgba(248,113,113,.1)"} />
      <polyline points={linePts} fill="none" stroke={rising ? "var(--acc)" : "var(--red)"}
        strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ── Summary bar ───────────────────────────────────────────────────────────────

function SummaryBar({ keywords, states }: { keywords: string[]; states: Record<string, CardState> }) {
  const loaded = keywords.filter(k => states[k]?.kind === "ok");
  const loading = keywords.filter(k => states[k]?.kind === "loading").length;

  let totVol = 0, compSum = 0, compN = 0, cpcSum = 0, cpcN = 0, highComp = 0, opps = 0;
  for (const k of loaded) {
    const d = parseDetail((states[k] as any).data.detail);
    totVol  += d?.searchVolume  ?? 0;
    if (d?.competition != null) { compSum += d.competition; compN++; }
    if (d?.cpc != null) { cpcSum += d.cpc; cpcN++; }
    if ((d?.competition ?? 0) > 70) highComp++;
    if ((d?.competition ?? 101) < 40 && (d?.searchVolume ?? 0) > 2000) opps++;
  }
  const avgComp = compN ? Math.round(compSum / compN) : null;
  const avgCpc  = cpcN ? (cpcSum / cpcN) : null;

  const items = [
    { val: keywords.length, label: "监控词", color: undefined },
    { val: loaded.length ? fmtVol(totVol || null) : "—", label: "合计月搜索", color: loaded.length ? "var(--acc)" : undefined },
    { val: avgComp ?? "—", label: "均竞争指数", color: avgComp == null ? undefined : avgComp > 70 ? "var(--red)" : avgComp > 40 ? "var(--amber)" : "var(--acc)" },
    { val: avgCpc != null ? `$${avgCpc.toFixed(2)}` : "—", label: "均CPC", color: undefined },
    { val: loaded.length ? opps : "—", label: "机会词", color: opps > 0 ? "var(--acc)" : "var(--t3)" },
    { val: loading > 0 ? loading : `${loaded.length}/${keywords.length}`, label: loading > 0 ? "查询中" : "已加载", color: loading > 0 ? "var(--acc)" : loaded.length === keywords.length ? "var(--acc)" : "var(--t3)" },
  ];

  return (
    <div className="pulse-summary">
      {items.map((it, i) => (
        <Fragment key={it.label}>
          {i > 0 && <div className="pulse-summary-sep" />}
          <div className="pulse-summary-item">
            <div className="pulse-summary-val" style={{ color: it.color as any }}>
              {loading > 0 && it.label === "查询中" ? <><span className="pulse-spin" style={{ fontSize: "var(--fs-11)" }}>◌</span> {it.val}</> : it.val}
            </div>
            <div className="pulse-summary-label">{it.label}</div>
          </div>
        </Fragment>
      ))}
    </div>
  );
}

// ── Insight panels ────────────────────────────────────────────────────────────

type InsightItem = { keyword: string; val: string };

function InsightPanel({ icon, title, accent, items, emptyText }: {
  icon: string; title: string; accent: string; items: InsightItem[]; emptyText: string;
}) {
  return (
    <div className="pulse-insight">
      <div className="pulse-insight-hd">
        <span className="pulse-insight-icon" style={{ color: `var(--${accent})` }}>{icon}</span>
        <span className="pulse-insight-title">{title}</span>
        <span className="pulse-insight-badge" style={{ color: items.length ? `var(--${accent})` : undefined }}>{items.length}</span>
      </div>
      {items.length === 0
        ? <div className="pulse-insight-empty">{emptyText}</div>
        : <ul className="pulse-insight-list">
            {items.slice(0, 5).map(it => (
              <li key={it.keyword} className="pulse-insight-row">
                <span className="pulse-insight-kw">{it.keyword}</span>
                <span className="pulse-insight-val" style={{ color: `var(--${accent})` }}>{it.val}</span>
              </li>
            ))}
          </ul>
      }
    </div>
  );
}

function InsightPanels({ keywords, states }: { keywords: string[]; states: Record<string, CardState> }) {
  const loaded = keywords
    .filter(k => states[k]?.kind === "ok")
    .map(k => {
      const data = (states[k] as any).data;
      const d = parseDetail(data.detail);
      return { keyword: k, detail: d, dir: trendDir(parseTrend(data.trend)) };
    });

  if (loaded.length === 0) return null;

  const opportunities = loaded
    .filter(p => (p.detail?.competition ?? 101) < 40 && (p.detail?.searchVolume ?? 0) > 2000)
    .sort((a, b) => (b.detail?.searchVolume ?? 0) - (a.detail?.searchVolume ?? 0))
    .map(p => ({ keyword: p.keyword, val: fmtVol(p.detail?.searchVolume ?? null) }));

  const rising = loaded
    .filter(p => p.dir === "up")
    .sort((a, b) => (b.detail?.searchVolume ?? 0) - (a.detail?.searchVolume ?? 0))
    .map(p => ({ keyword: p.keyword, val: fmtVol(p.detail?.searchVolume ?? null) }));

  const alerts = loaded
    .filter(p => (p.detail?.competition ?? 0) > 70)
    .sort((a, b) => (b.detail?.competition ?? 0) - (a.detail?.competition ?? 0))
    .map(p => ({ keyword: p.keyword, val: `竞争 ${Math.round(p.detail?.competition ?? 0)}` }));

  return (
    <div className="pulse-insights">
      <InsightPanel icon="◎" title="低竞机会词" accent="acc"   items={opportunities} emptyText="暂无低竞争高搜索量词" />
      <InsightPanel icon="↑" title="趋势上升"   accent="acc"   items={rising}        emptyText="暂无上升趋势词" />
      <InsightPanel icon="⚠" title="竞争预警"   accent="red"   items={alerts}        emptyText="当前无高竞争词" />
    </div>
  );
}

// ── Opportunity scatter plot ───────────────────────────────────────────────────

type PlotPoint = { keyword: string; competition: number; searchVolume: number };

function ScatterPlot({ points }: { points: PlotPoint[] }) {
  if (points.length < 2) return null;

  const VW = 580, VH = 190;
  const L = 12, R = 12, T = 18, B = 24;
  const PW = VW - L - R, PH = VH - T - B;
  const midX = L + PW / 2, midY = T + PH / 2;

  const vols = points.map(p => Math.max(p.searchVolume, 200));
  const logMin = Math.log10(Math.min(...vols));
  const logMax = Math.log10(Math.max(...vols));
  const logRange = logMax - logMin || 1;

  const toX = (c: number) => L + (Math.min(Math.max(c, 0), 100) / 100) * PW;
  const toY = (v: number) => T + PH - ((Math.log10(Math.max(v, 200)) - logMin) / logRange) * PH;

  function dotColor(cx: number, cy: number) {
    const lowC = cx < midX, hiV = cy < midY;
    if (lowC && hiV)  return "#4ade80";
    if (!lowC && hiV) return "#fbbf24";
    if (lowC && !hiV) return "#60a5fa";
    return "#f87171";
  }

  return (
    <div className="pulse-matrix">
      <div className="pulse-matrix-hd">
        <span className="pulse-matrix-title">◉ 机会象限</span>
        <div className="pulse-matrix-legend">
          <span><i className="pml pml-g" />低竞高量·机会</span>
          <span><i className="pml pml-a" />高量高竞·激战</span>
          <span><i className="pml pml-b" />低竞低量·细分</span>
          <span><i className="pml pml-r" />高竞低量·避开</span>
        </div>
      </div>
      <div className="pulse-matrix-body">
        <svg viewBox={`0 0 ${VW} ${VH}`} className="pulse-matrix-svg" preserveAspectRatio="xMidYMid meet">
          {/* Quadrant fills */}
          <rect x={L}    y={T}    width={PW/2} height={PH/2} fill="rgba(74,222,128,.05)" />
          <rect x={midX} y={T}    width={PW/2} height={PH/2} fill="rgba(251,191,36,.04)" />
          <rect x={L}    y={midY} width={PW/2} height={PH/2} fill="rgba(96,165,250,.04)" />
          <rect x={midX} y={midY} width={PW/2} height={PH/2} fill="rgba(248,113,113,.04)" />
          {/* Dividers */}
          <line x1={midX} y1={T}   x2={midX} y2={T+PH} stroke="var(--b2)" strokeWidth="0.8" />
          <line x1={L}    y1={midY} x2={L+PW} y2={midY} stroke="var(--b2)" strokeWidth="0.8" />
          {/* Axis labels */}
          <text x={L+3}    y={T+PH+18} fill="var(--t3)" fontSize="9" fontFamily="sans-serif">低竞争</text>
          <text x={L+PW-30} y={T+PH+18} fill="var(--t3)" fontSize="9" fontFamily="sans-serif">高竞争</text>
          <text x={L+3}    y={T+13}    fill="var(--t3)" fontSize="9" fontFamily="sans-serif">↑ 高搜索量</text>
          <text x={L+3}    y={T+PH-3}  fill="var(--t3)" fontSize="9" fontFamily="sans-serif">↓ 低搜索量</text>
          {/* Points */}
          {points.map(p => {
            const cx = toX(p.competition), cy = toY(p.searchVolume);
            const color = dotColor(cx, cy);
            const label = p.keyword.length > 16 ? p.keyword.slice(0, 15) + "…" : p.keyword;
            const lx = cx > L + PW * 0.78 ? cx - 8 : cx + 8;
            const anchor = cx > L + PW * 0.78 ? "end" : "start";
            return (
              <g key={p.keyword}>
                <circle cx={cx} cy={cy} r={6} fill={color} fillOpacity="0.8" />
                <circle cx={cx} cy={cy} r={9} fill={color} fillOpacity="0.15" />
                <text x={lx} y={cy + 4} fill="var(--t2)" fontSize="9" fontFamily="sans-serif" textAnchor={anchor}>{label}</text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

// ── Keyword card ──────────────────────────────────────────────────────────────

function KeywordCard({ keyword, state, marketplace, dataSource, onRemove, onRefresh, onAdd, isMonitored }: {
  keyword: string; state: CardState; marketplace: string;
  dataSource: DataSourceId;
  onRemove: () => void; onRefresh?: () => void;
  onAdd?: (kw: string) => void; isMonitored?: (kw: string) => boolean;
}) {
  const detail = state.kind === "ok" ? parseDetail(state.data.detail) : null;
  const trend  = state.kind === "ok" ? parseTrend(state.data.trend)   : [];
  const comp   = compLevel(detail?.competition ?? null);
  const dir    = trendDir(trend);
  const accent = comp === "high" ? "var(--red)" : comp === "mid" ? "var(--amber)" : comp === "low" ? "var(--acc)" : "var(--b)";
  const busy   = state.kind === "loading";
  const sourceName = dataSource === "sellersprite" ? "卖家精灵" : "Sorftime";

  const [open, setOpen] = useState(false);
  const [ext, setExt] = useState<KeywordExtendItem[] | null>(null);
  const [extBusy, setExtBusy] = useState(false);
  const [deepBusy, setDeepBusy] = useState(false);

  const toggleExt = async () => {
    const next = !open;
    setOpen(next);
    if (next && ext === null) {
      try { const c = await fetchKeywordExtendsCached(keyword, marketplace, dataSource); setExt(c.items); }
      catch { setExt([]); }
    }
  };
  const pullExt = async () => {
    setExtBusy(true);
    try { const r = await pulseKeywordExtends(keyword, marketplace, dataSource); setExt(r.items); }
    catch { /* ignore */ } finally { setExtBusy(false); }
  };
  const deepExt = async () => {
    setDeepBusy(true);
    try { const r = await deepKeywordExtendSales(keyword, marketplace, dataSource); setExt(r.items); }
    catch { /* ignore */ } finally { setDeepBusy(false); }
  };

  return (
    <div className="pulse-card" style={state.kind === "ok" ? { borderLeftColor: accent } : undefined}>
      <div className="pulse-card-hd">
        <span className="pulse-kw">{keyword}</span>
        {dir && <span className={`pulse-dir pulse-dir-${dir}`}>{dir === "up" ? "↑" : dir === "down" ? "↓" : "→"}</span>}
        <div className="asin-card-actions" style={{ marginLeft: "auto" }}>
          <button className={"asin-icon-btn" + (open ? " active" : "")} onClick={toggleExt} title="拓展词">⊕</button>
          {onRefresh && (
            <button className="asin-icon-btn" onClick={onRefresh} disabled={busy} title={`实时刷新（消耗 1 次 ${sourceName} 调用）`}>
              {busy ? <span className="spin" /> : "↻"}
            </button>
          )}
          <button className="asin-icon-btn" onClick={onRemove} title="移除">✕</button>
        </div>
      </div>
      {state.kind === "idle"    && <button className="asin-fetch-hint" onClick={onRefresh}>点击拉取数据 ↻</button>}
      {state.kind === "loading" && (
        <div aria-busy="true" aria-live="polite" style={{ display: "grid", gap: 8, paddingTop: 4 }}>
          <div className="skeleton" style={{ height: 90, borderRadius: 6 }} />
          <div className="skeleton line lg" />
          <div className="skeleton line md" />
        </div>
      )}
      {state.kind === "err"     && <div className="pulse-err">⚠ {state.msg}</div>}
      {state.kind === "ok" && (
        <>
          <div className="pulse-metrics">
            <div className="pulse-metric">
              <div className="pulse-metric-val pulse-vol">{fmtVol(detail?.searchVolume ?? null)}</div>
              <div className="pulse-metric-label">月搜索量</div>
            </div>
            <div className="pulse-metric">
              <div className={`pulse-metric-val pulse-comp-${comp}`}>
                {detail?.competition != null ? Math.round(detail.competition) : "—"}
              </div>
              <div className="pulse-metric-label">竞争指数</div>
            </div>
            <div className="pulse-metric">
              <div className="pulse-metric-val">{detail?.cpc != null ? `$${detail.cpc.toFixed(2)}` : "—"}</div>
              <div className="pulse-metric-label">CPC</div>
            </div>
            <div className="pulse-metric">
              <div className="pulse-metric-val">{detail?.purchaseRate != null ? `${(detail.purchaseRate * 100).toFixed(1)}%` : "—"}</div>
              <div className="pulse-metric-label">购买率</div>
            </div>
          </div>
          {trend.length >= 2 && (
            <div className="pulse-trend">
              <Sparkline values={trend} />
              <span className="pulse-trend-label">近{trend.length}月</span>
            </div>
          )}
          <div className="pulse-ts">{state.cached ? "缓存 · " : ""}{new Date(state.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</div>
        </>
      )}

      {open && (
        <div className="kx-panel">
          <div className="kx-actions">
            <button className="tbtn" onClick={pullExt} disabled={extBusy}>
              {extBusy ? <><span className="spin" style={{ marginRight: 5 }} />拉取中…</> : (ext && ext.length ? "↻ 刷新拓展词" : "拉取拓展词")}
            </button>
            <button className="tbtn" onClick={deepExt} disabled={deepBusy || !ext || !ext.length}
              title={dataSource === "sellersprite"
                ? "用卖家精灵月购买量补充出单佐证"
                : "对前若干拓展词查 TOP 产品月销量作为出单佐证（每词消耗 1 次 Sorftime）"}>
              {deepBusy ? <><span className="spin" style={{ marginRight: 5 }} />评估中…</> : "深度出单佐证"}
            </button>
          </div>
          {ext === null ? (
            <div className="kx-hint">加载中…</div>
          ) : ext.length === 0 ? (
            <div className="kx-hint">暂无拓展词 · 点「拉取拓展词」（消耗 1 次 {sourceName} 调用）</div>
          ) : (
            <div className="kx-list">
              <div className="kx-row kx-head">
                <span className="kx-kw">关键词</span><span>月搜</span><span>CPC</span><span>机会分</span><span>出单佐证</span><span></span>
              </div>
              {ext.map(it => {
                const monitored = isMonitored?.(it.keyword) ?? false;
                return (
                  <div key={it.keyword} className="kx-row">
                    <span className="kx-kw" title={it.keyword}>
                      {it.related && <i className="kx-rel" title="高相关">●</i>}{it.keyword}
                    </span>
                    <span>{it.monthly_search != null ? fmtVol(it.monthly_search) : "—"}</span>
                    <span>{it.cpc != null ? `$${it.cpc.toFixed(2)}` : "—"}</span>
                    <span><i className="kx-score" style={{ ["--s" as any]: it.score }}>{it.score}</i></span>
                    <span>{it.evidence_sales != null ? fmtVol(it.evidence_sales) : "·"}</span>
                    <span>
                      {onAdd && (monitored
                        ? <span className="kx-added">已监控</span>
                        : <button className="kx-add" onClick={() => onAdd(it.keyword)} title="加入监控">+ 监控</button>)}
                    </span>
                  </div>
                );
              })}
              <div className="kx-foot">机会分 = 需求(月搜) × 商业意图(CPC) 近似；出单佐证 = 该词 TOP 产品月销量中位数（深度评估后显示）</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

const SUGGESTED = ["wireless earbuds", "yoga mat", "phone stand", "led strip lights", "air fryer", "ring light"];

export default function KeywordMonitor({ marketplace, dataSource }: { marketplace: string; dataSource: DataSourceId }) {
  const [items, setItems] = useState<KeywordItem[]>([]);
  const [states, setStates] = useState<Record<string, CardState>>({});
  const [input, setInput] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const notify = useToast();
  const sourceName = dataSource === "sellersprite" ? "卖家精灵" : "Sorftime";

  const keywords = items.filter(it => it.marketplace === marketplace).map(it => it.keyword);
  const idByKw = (kw: string) => items.find(it => it.keyword === kw && it.marketplace === marketplace)?.id;

  // Cache-first load (+ one-time migration of legacy localStorage list). No
  // Provider call on open — cards render from source-isolated server cache.
  const loadCache = async () => {
    try {
      // Migrate any legacy localStorage keywords to the server (list only).
      const legacy = loadLegacyKeywords();
      if (legacy.length > 0) {
        const existing = await listKeywords(dataSource);
        const have = new Set(existing.filter(e => e.marketplace === marketplace).map(e => e.keyword));
        for (const kw of legacy) {
          if (!have.has(kw)) await apiAddKeyword(kw, marketplace, dataSource).catch(() => {});
        }
        try { localStorage.removeItem(STORAGE_KEY); } catch {}
      }
      const list = await listKeywords(dataSource);
      setItems(list);
      const next: Record<string, CardState> = {};
      for (const it of list.filter(e => e.marketplace === marketplace)) {
        next[it.keyword] = it.data
          ? { kind: "ok", data: it.data, ts: it.data_ts ?? it.ts, cached: true }
          : { kind: "idle" };
      }
      setStates(next);
    } catch { /* ignore */ }
  };

  useEffect(() => { loadCache(); /* eslint-disable-next-line */ }, [marketplace, dataSource]);

  // Live pulse one keyword (spends quota) — explicit only.
  const fetchOne = async (kw: string) => {
    setStates(p => ({ ...p, [kw]: { kind: "loading" } }));
    try {
      const data = await pulseKeyword(kw, marketplace, dataSource);
      setStates(p => ({ ...p, [kw]: { kind: "ok", data, ts: Date.now(), cached: false } }));
    } catch (e: any) {
      setStates(p => ({ ...p, [kw]: { kind: "err", msg: e?.message || "请求失败" } }));
    }
  };

  const refreshAll = async () => {
    if (keywords.length === 0) return;
    setRefreshing(true);
    await runPool(keywords, fetchOne);
    setRefreshing(false);
  };

  const addOne = async (raw: string) => {
    const kw = raw.trim().toLowerCase();
    if (!kw || keywords.includes(kw)) return;
    try {
      const created = await apiAddKeyword(kw, marketplace, dataSource);
      setItems(p => [...p, { id: created.id, keyword: kw, marketplace, data_source: dataSource, label: "", ts: Date.now(), data: null, data_ts: null }]);
      fetchOne(kw); // one live fetch for the newly added keyword
    } catch (e: any) {
      notify("error", `添加关键词失败：${e?.message || "请求失败"}`);
    }
  };

  const addKeyword = async () => {
    const kw = input.trim().toLowerCase();
    if (!kw || keywords.includes(kw)) { setInput(""); return; }
    setInput("");
    await addOne(kw);
    inputRef.current?.focus();
  };

  const removeKeyword = async (kw: string) => {
    const id = idByKw(kw);
    if (id) await deleteKeyword(id).catch(() => notify("warn", `「${kw}」服务端删除失败，刷新后可能回来`));
    setItems(p => p.filter(it => !(it.keyword === kw && it.marketplace === marketplace)));
    setStates(p => { const n = { ...p }; delete n[kw]; return n; });
  };

  // Sort: ok (by search vol desc) → loading → idle/err
  const sorted = useMemo(() => [...keywords].sort((a, b) => {
    const sa = states[a], sb = states[b];
    if (sa?.kind === "ok" && sb?.kind === "ok") {
      const da = parseDetail((sa as any).data.detail);
      const db = parseDetail((sb as any).data.detail);
      return (db?.searchVolume ?? 0) - (da?.searchVolume ?? 0);
    }
    if (sa?.kind === "ok") return -1;
    if (sb?.kind === "ok") return 1;
    if (sa?.kind === "loading") return -1;
    if (sb?.kind === "loading") return 1;
    return 0;
  }), [keywords, states]);

  // Scatter data: only keywords with both competition + searchVolume
  const scatterPoints = useMemo((): PlotPoint[] =>
    sorted.flatMap(k => {
      if (states[k]?.kind !== "ok") return [];
      const d = parseDetail((states[k] as any).data.detail);
      if (d?.competition == null || d?.searchVolume == null) return [];
      return [{ keyword: k, competition: d.competition, searchVolume: d.searchVolume }];
    }),
  [sorted, states]);

  return (
    <div className="pulse-page">
      {/* ── Toolbar ── */}
      <div className="pulse-header">
        <span className="pulse-header-title">
          <span style={{ color: "var(--acc)" }}>◈</span> 关键词监控
        </span>
        <div className="pulse-input-wrap">
          <input ref={inputRef} className="pulse-input" value={input}
            onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === "Enter" && addKeyword()}
            placeholder="输入关键词 + Enter" />
          <button className="tbtn" onClick={addKeyword} disabled={!input.trim()}>+ 添加</button>
        </div>
        <button className="tbtn tbtn-acc"
          onClick={refreshAll}
          disabled={refreshing || keywords.length === 0}
          title={`实时刷新全部（每个词消耗 ${sourceName} 调用）`}>
          {refreshing ? <><span className="spin" style={{ marginRight: 6 }} />查询中…</> : "↻ 全部刷新"}
        </button>
      </div>

      {keywords.length === 0 ? (
        /* ── Empty / Onboarding ── */
        <div className="pulse-onboard">
          <div className="pulse-onboard-icon">◈</div>
          <div className="pulse-onboard-title">关键词监控台</div>
          <div className="pulse-onboard-sub">
            输入你关注的亚马逊关键词，自动拉取搜索量、竞争指数、CPC、月度趋势，并生成机会象限分析
          </div>
          <div className="pulse-examples">
            {SUGGESTED.map(ex => (
              <button key={ex} className="pulse-example-btn"
                onClick={() => { setInput(ex); inputRef.current?.focus(); }}>{ex}</button>
            ))}
          </div>
        </div>
      ) : (
        <>
          {/* ── Summary bar ── */}
          <SummaryBar keywords={keywords} states={states} />

          {/* ── Insight panels ── */}
          <InsightPanels keywords={keywords} states={states} />

          {/* ── Scatter plot ── */}
          <ScatterPlot points={scatterPoints} />

          {/* ── Keyword cards ── */}
          <div className="pulse-section-label">◈ 关键词详情</div>
          <div className="pulse-grid">
            {sorted.map(kw => (
              <KeywordCard key={kw} keyword={kw} state={states[kw] ?? { kind: "idle" }} marketplace={marketplace} dataSource={dataSource}
                onRemove={() => removeKeyword(kw)} onRefresh={() => fetchOne(kw)}
                onAdd={addOne} isMonitored={(w) => keywords.includes(w.toLowerCase())} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
