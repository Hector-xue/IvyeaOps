import { useEffect, useMemo, useState } from "react";
import {
  fetchAdsBoard, fetchHourly,
  type AdsBoard as AdsBoardData, type AdjustIntent, type Campaign,
  type HourlyResult, type Metrics,
} from "../../../api/cockpit";
import AdjustDrawer from "./AdjustDrawer";
import { errText } from "../../../lib/errText";

/**
 * 广告看板。
 *
 * 和亚马逊后台的差别全在"把数字翻译成该不该动手"：每个活动都拿它自己的
 * 目标 ACOS（毛利率推的）当尺子，超了就标红并给出**能直接点的**调整建议；
 * 今日预算进度条让"下午就烧完"这件事在列表里一眼可见；小时曲线是后台最难看的
 * 那块，这里点开就有。
 */

const HEALTH_TEXT: Record<string, string> = {
  good: "达标", watch: "偏高", bad: "亏损", unknown: "无基准",
};

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function num(v: number | null | undefined, digits = 0): string {
  if (v == null) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function Delta({ value, invert }: { value: number | null; invert?: boolean }) {
  if (value == null) return null;
  const good = invert ? value < 0 : value > 0;
  return (
    <span className={"cp-delta " + (value === 0 ? "" : good ? "up" : "down")}>
      {value > 0 ? "+" : ""}{value.toFixed(1)}%
    </span>
  );
}

function Kpi({ label, value, delta, invert, hint }: {
  label: string; value: string; delta?: number | null; invert?: boolean; hint?: string;
}) {
  return (
    <div className="cp-kpi" title={hint}>
      <div className="cp-kpi-label">{label}</div>
      <div className="cp-kpi-value">{value}<Delta value={delta ?? null} invert={invert} /></div>
    </div>
  );
}

/** 24 小时曲线。纯 SVG —— 一个折线图不值得拉一个图表库进来。 */
function HourChart({ result }: { result: HourlyResult }) {
  const series = result.series.filter(s => s.points.length > 0);
  if (series.length === 0) return <div className="cp-empty-inline">这天没有小时数据</div>;
  const max = Math.max(...series.flatMap(s => s.points.map(p => p.spend_cumulative)), 1);
  const W = 640, H = 140, PAD = 24;
  return (
    <div className="cp-hour">
      <svg viewBox={`0 0 ${W} ${H}`} className="cp-hour-svg" role="img"
           aria-label="今日累计花费曲线">
        {[0, 6, 12, 18, 23].map(h => (
          <text key={h} x={PAD + (h / 23) * (W - PAD * 2)} y={H - 4}
                className="cp-hour-tick" textAnchor="middle">{h}时</text>
        ))}
        {series.map((s, i) => {
          const pts = s.points.map(p => {
            const x = PAD + (p.hour / 23) * (W - PAD * 2);
            const y = H - 18 - (p.spend_cumulative / max) * (H - 34);
            return `${x.toFixed(1)},${y.toFixed(1)}`;
          }).join(" ");
          return <polyline key={s.campaign_id} points={pts} className={`cp-hour-line c${i % 5}`} />;
        })}
      </svg>
      <div className="cp-hour-legend">
        {series.map((s, i) => (
          <span key={s.campaign_id} className={`cp-hour-key c${i % 5}`}>
            活动 {s.campaign_id} · 累计 {num(s.spend_total, 2)}
          </span>
        ))}
      </div>
    </div>
  );
}

function CampaignRow({ c, onAdjust, onHourly }: {
  c: Campaign;
  onAdjust: (intent: AdjustIntent, label: string, unit: string) => void;
  onHourly: (c: Campaign) => void;
}) {
  const [open, setOpen] = useState(false);
  const used = c.budget_used_pct;
  return (
    <>
      <tr className={`cp-row cp-health-${c.health}`}>
        <td className="cp-col-name">
          <button className="cp-row-toggle" onClick={() => setOpen(o => !o)}>
            {open ? "▾" : "▸"}
          </button>
          <span className="cp-camp-name" title={c.name}>{c.name}</span>
          <span className="cp-mkt">{c.marketplace}</span>
          {c.state !== "enabled" && <span className="cp-tag muted">{c.state}</span>}
          {c.serving_status?.includes("OUT_OF_BUDGET") && <span className="cp-tag hot">出预算</span>}
          {c.anomalies.length > 0 && (
            <span className="cp-badge" title={c.anomalies.map(a => a.label).join("、")}>
              {c.anomalies.length}
            </span>
          )}
        </td>
        <td>{num(c.spend, 2)}<Delta value={c.spend_change_pct} /></td>
        <td>{num(c.sales, 2)}</td>
        <td>{c.orders}</td>
        <td className={`cp-acos cp-health-${c.health}`}>
          {pct(c.acos)}
          <span className="cp-acos-target">
            /目标 {c.target_acos != null ? pct(c.target_acos, 0) : "—"}
          </span>
        </td>
        <td>{num(c.cpc, 2)}</td>
        <td>
          {used != null ? (
            // 百分比放在条子**旁边**而不是压在条子上：填充过半时数字会骑在
            // 填充色的边界上，两种底色各盖一半，怎么调都不够清楚。
            <div className="cp-pace" title={`今日已花 ${num(c.today_spend, 2)} / 预算 ${num(c.daily_budget, 2)}`}>
              <div className="cp-mini-bar">
                <div className={"cp-mini-fill" + (used >= 90 ? " hot" : "")}
                     style={{ width: `${Math.min(100, used)}%` }} />
              </div>
              <span className={"cp-pace-text" + (used >= 90 ? " hot" : "")}>{used}%</span>
            </div>
          ) : "—"}
        </td>
        <td className="cp-col-actions">
          <button className="cp-btn tiny" onClick={() => onHourly(c)}>小时</button>
          <button className="cp-btn tiny" onClick={() => onAdjust({
            op_type: "campaign_budget", sid: c.sid, target_id: c.campaign_id,
            target_name: c.name, cur_value: c.daily_budget,
            new_value: Number((c.daily_budget * 0.85).toFixed(2)),
            rationale: "驾驶舱手动调整",
          }, "调整日预算", "")}>调预算</button>
        </td>
      </tr>
      {open && (
        <tr className="cp-row-detail">
          <td colSpan={8}>
            <div className="cp-detail">
              <div className="cp-detail-metrics">
                <span>曝光 {num(c.impressions)}</span>
                <span>点击 {num(c.clicks)}</span>
                <span>CTR {pct(c.ctr, 2)}</span>
                <span>CVR {pct(c.cvr, 1)}</span>
                <span>ROAS {num(c.roas, 2)}</span>
                <span>健康度 {HEALTH_TEXT[c.health]}</span>
                {c.breakeven_acos != null && <span>盈亏平衡 {pct(c.breakeven_acos, 0)}</span>}
              </div>
              {c.anomalies.map(a => (
                <div key={a.code} className={`cp-anomaly sev-${a.severity}`}>
                  <span className="cp-anomaly-label">{a.label}</span>
                  <span className="cp-anomaly-detail">{a.detail}</span>
                  {a.intent && (
                    <button className="cp-btn tiny primary" onClick={() =>
                      onAdjust(a.intent!, a.label, "")}>
                      按建议调整 → {a.intent.new_value}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function AdsBoard() {
  const [board, setBoard] = useState<AdsBoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [days, setDays] = useState(7);
  const [store, setStore] = useState<number | "all">("all");
  const [hourly, setHourly] = useState<HourlyResult | null>(null);
  const [hourlyBusy, setHourlyBusy] = useState(false);
  const [drawer, setDrawer] = useState<
    { intent: AdjustIntent; label: string; unit: string } | null>(null);

  const load = (force = false) => {
    setLoading(true); setError("");
    fetchAdsBoard({ days, force })
      .then(setBoard)
      .catch(e => setError(errText(e, "加载失败")))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [days]);

  const campaigns = useMemo(() => {
    const list = board?.by_campaign ?? [];
    return store === "all" ? list : list.filter(c => c.sid === store);
  }, [board, store]);

  const showHourly = async (c: Campaign) => {
    setHourlyBusy(true);
    try {
      setHourly(await fetchHourly(c.sid, [c.campaign_id]));
    } catch (e: any) {
      setError(errText(e, "小时数据取不到"));
    } finally { setHourlyBusy(false); }
  };

  const t = board?.totals as Metrics | undefined;
  const target = board?.by_store?.[0]?.target;

  return (
    <div className="cp-page">
      <div className="cp-toolbar">
        <div className="cp-seg">
          {[1, 7, 14, 30].map(d => (
            <button key={d} className={"cp-seg-btn" + (days === d ? " active" : "")}
                    onClick={() => setDays(d)}>{d === 1 ? "昨天" : `${d}天`}</button>
          ))}
        </div>
        <select className="cp-select" value={store}
                onChange={e => setStore(e.target.value === "all" ? "all" : Number(e.target.value))}>
          <option value="all">全部店铺</option>
          {board?.by_store.map(s => (
            <option key={s.sid} value={s.sid}>{s.store}</option>
          ))}
        </select>
        <div className="cp-actions">
          <button className="cp-btn" onClick={() => load(true)} disabled={loading}>
            {loading ? "刷新中…" : "立即刷新"}
          </button>
        </div>
      </div>

      <div className="cp-kpis">
        <Kpi label="广告花费" value={num(t?.spend, 2)} delta={board?.delta.spend_pct} invert />
        <Kpi label="广告销售额" value={num(t?.sales, 2)} delta={board?.delta.sales_pct} />
        <Kpi label="订单" value={num(t?.orders)} delta={board?.delta.orders_pct} />
        <Kpi label="ACOS" value={pct(t?.acos)}
             hint={target?.note || ""}
             delta={board?.delta.acos_delta != null ? board.delta.acos_delta * 100 : null} invert />
        <Kpi label="CPC" value={num(t?.cpc, 2)} />
        <Kpi label="点击" value={num(t?.clicks)} />
      </div>

      {target?.note && <div className="cp-target-note">◈ {target.note}</div>}

      {(board?.scope.skipped?.length ?? 0) > 0 && (
        <div className="cp-skipped">
          已跳过 {board!.scope.skipped.map(s => `${s.name}（${s.reason}）`).join("、")}
        </div>
      )}

      {(board?.anomalies?.length ?? 0) > 0 && (
        <div className="cp-anomaly-strip">
          {board!.anomalies.slice(0, 6).map((a, i) => (
            <span key={i} className={`cp-anomaly-chip sev-${a.severity}`}
                  title={a.detail}>
              <b>{a.label}</b> {a.name}
            </span>
          ))}
        </div>
      )}

      {error && <div className="cp-error">{error}</div>}
      {loading && !board && <div className="cp-loading">正在汇总广告数据…</div>}

      {!loading && campaigns.length === 0 && !error && (
        <div className="cp-empty">
          <div className="cp-empty-title">这段时间没有广告数据</div>
          <div className="cp-empty-desc">
            活动全部处于归档/暂停，或者所选窗口内没有投放。
            未开通广告的店铺已自动跳过。
          </div>
        </div>
      )}

      {campaigns.length > 0 && (
        <div className="cp-table-wrap">
          <table className="cp-table">
            <thead>
              <tr>
                <th>活动</th><th>花费</th><th>销售额</th><th>订单</th>
                <th>ACOS</th><th>CPC</th><th>今日预算</th><th></th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map(c => (
                <CampaignRow key={`${c.sid}:${c.campaign_id}`} c={c}
                             onAdjust={(intent, label, unit) => setDrawer({ intent, label, unit })}
                             onHourly={showHourly} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hourlyBusy && <div className="cp-loading">正在取小时数据…</div>}
      {hourly && (
        <div className="cp-hour-panel">
          <div className="cp-hour-hd">
            <span>{hourly.date} · 累计花费（小时）</span>
            <button className="cp-drawer-x" onClick={() => setHourly(null)}>×</button>
          </div>
          <HourChart result={hourly} />
        </div>
      )}

      {drawer && (
        <AdjustDrawer
          payload={{ ...drawer.intent, label: drawer.label, unit: drawer.unit }}
          onClose={() => setDrawer(null)}
          onDone={() => { setDrawer(null); load(true); }}
        />
      )}
    </div>
  );
}
