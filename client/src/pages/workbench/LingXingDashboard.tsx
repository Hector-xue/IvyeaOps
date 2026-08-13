import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import TrendChart, { type TrendSeries } from "./home/TrendChart";
import SheetSelect from "../../components/SheetSelect";
import { sidCurrencyMap, fmtBudget, type Cur } from "./lingxingCurrency";
import { Btn, LxKpiSkeleton, humanErr, inputStyle, num, pct } from "./lingxingUi";

const CMP_LS = "lingxing.dash.cmp";

export default function LingXingDashboard({ storeSid }: { storeSid?: string }) {
  const [sellers, setSellers] = useState<any[]>([]);
  const sid = storeSid || "";   // store is driven by the page-level selector
  const [days, setDays] = useState<number>(7);
  const [data, setData] = useState<any>(null);
  const [cmp, setCmp] = useState<any>(null);       // all-store comparison (opt-in, remembered)
  const [cmpOn, setCmpOn] = useState<boolean>(() => localStorage.getItem(CMP_LS) === "1");
  const [loading, setLoading] = useState(false);
  const [cmpLoading, setCmpLoading] = useState(false);
  const [err, setErr] = useState("");
  const curMap = useMemo(() => sidCurrencyMap(sellers), [sellers]);
  const cur: Cur | undefined = curMap[sid];

  useEffect(() => { void loadSellers(); }, []);
  async function loadSellers() {
    try {
      const r = await api.post("/lingxing/read/sellers", { params: {} });
      setSellers(r.data.rows || []);
    } catch (e: any) { setErr(humanErr(e)); }
  }

  useEffect(() => { if (sid) void load(); /* eslint-disable-next-line */ }, [storeSid, days]);
  async function load() {
    setLoading(true); setErr("");
    try { setData((await api.get(`/lingxing/dashboard?sids=${sid}&days=${days}`)).data); }
    catch (e: any) { setErr(humanErr(e)); setData(null); }
    finally { setLoading(false); }
  }
  async function loadCmp() {
    setCmpLoading(true);
    try { setCmp((await api.get(`/lingxing/dashboard?sids=&days=${days}`)).data); }
    catch (e: any) { setErr(humanErr(e)); } finally { setCmpLoading(false); }
  }
  function toggleCmp() {
    const next = !cmpOn;
    setCmpOn(next); localStorage.setItem(CMP_LS, next ? "1" : "0");
    if (next && !cmp) void loadCmp();
  }
  /* remembered preference: auto-load the all-store comparison on entry */
  useEffect(() => { if (cmpOn && !cmp && sellers.length) void loadCmp(); /* eslint-disable-next-line */ }, [sellers, days]);

  const t = data?.totals;
  const prev = data?.prev_totals;
  const trendSeries: TrendSeries[] = useMemo(() => {
    const tr = data?.trend || [];
    return [
      { name: "花费", color: "#f87171", points: tr.map((d: any) => ({ day: d.date, value: d.spend || 0 })), fmt: (n) => fmtBudget(Math.round(n), cur), area: true },
      { name: "销售额", color: "#4ade80", points: tr.map((d: any) => ({ day: d.date, value: d.sales || 0 })), fmt: (n) => fmtBudget(Math.round(n), cur), area: true },
      { name: "ACOS", color: "#fbbf24", axis: "right", area: false, points: tr.map((d: any) => ({ day: d.date, value: d.acos != null ? d.acos * 100 : 0 })), fmt: (n) => n.toFixed(0) + "%" },
    ];
  }, [data, cur]);

  return (
    <div>
      {/* scope */}
      <div className="card" style={{ padding: 12, marginBottom: 10, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>店铺：{sellers.find((s) => String(s.sid) === sid)?.name || sid || "（上方选择）"}</span>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>窗口</span>
        <SheetSelect value={String(days)} onChange={(v) => setDays(Number(v))} title="时间窗口" style={{ ...inputStyle, width: 100 }}
          options={[7, 14, 30, 60].map((d) => ({ value: String(d), label: `近 ${d} 天` }))} />
        <Btn onClick={load} disabled={loading}>{loading ? "聚合中…" : "刷新"}</Btn>
        {err && <span style={{ fontSize: "var(--fs-11)", color: "var(--red)" }}>{err}</span>}
        <span style={{ marginLeft: "auto", fontSize: "var(--fs-10)", color: "var(--t3)" }}>环比 = 上一个 {days} 天 · 币种随店铺（{cur?.code || "—"}）</span>
      </div>

      {/* KPI cards (single store, native currency, period-over-period) */}
      {loading && !t ? <LxKpiSkeleton n={7} /> : (
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8, marginBottom: 10 }}>
        <Kpi label="花费" value={t ? fmtBudget(t.spend, cur) : "—"} delta={delta(t?.spend, prev?.spend)} invert />
        <Kpi label="销售额" value={t ? fmtBudget(t.sales, cur) : "—"} delta={delta(t?.sales, prev?.sales)} />
        <Kpi label="ACOS" value={pct(t?.acos)} hint={t?.acos != null && t.acos > 0.35 ? "偏高" : ""} delta={delta(t?.acos, prev?.acos)} invert />
        <Kpi label="ROAS" value={t?.roas ?? "—"} delta={delta(t?.roas, prev?.roas)} />
        <Kpi label="订单" value={num(t?.orders)} delta={delta(t?.orders, prev?.orders)} />
        <Kpi label="点击 / 曝光" value={t ? `${num(t.clicks)} / ${num(t.impressions)}` : "—"} />
        <Kpi label="CTR / CVR" value={t ? `${pct(t.ctr)} / ${pct(t.cvr)}` : "—"} />
      </div>
      )}

      {/* trend */}
      <div className="card" style={{ padding: 12, marginBottom: 10 }}>
        <div style={{ fontSize: "var(--fs-11)", color: "var(--t3)", marginBottom: 4 }}>花费 / 销售额 / ACOS 趋势（近 {days} 天）</div>
        <TrendChart series={trendSeries} height={200} />
      </div>

      {/* top campaigns of this store */}
      <Card title={`花费 Top 活动 · ${sellers.find((s) => String(s.sid) === sid)?.name || sid}`}>
        <CampTable rows={data?.by_campaign || []} cur={cur} loading={loading} />
      </Card>

      {/* all-store comparison (opt-in but remembered; each native currency) */}
      <div className="card" style={{ padding: 0, marginTop: 10 }}>
        <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--b)", display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>全部店铺对比（各店本币，不跨币种汇总）</span>
          <label style={{ display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer", fontSize: "var(--fs-10)", color: "var(--t3)" }}>
            <input type="checkbox" checked={cmpOn} onChange={toggleCmp} />进入时自动加载
          </label>
          <Btn onClick={loadCmp} disabled={cmpLoading}>{cmpLoading ? "加载中…(首次较慢)" : cmp ? "刷新对比" : "加载全部店铺对比"}</Btn>
        </div>
        {cmpOn && cmp && (
          <div style={{ overflowX: "auto" }}>
            <table className="lx-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--fs-11)" }}>
              <thead><tr>{["店铺", "花费", "销售额", "ACOS", "ROAS", "订单"].map((h) => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {(cmp.by_store || []).map((s: any) => (
                  <tr key={s.sid} style={{ borderBottom: "1px solid var(--b)" }}>
                    <td style={td}>{s.store}</td>
                    <td style={td}>{fmtBudget(s.spend, curMap[String(s.sid)])}</td>
                    <td style={td}>{fmtBudget(s.sales, curMap[String(s.sid)])}</td>
                    <td style={{ ...td, color: s.acos != null && s.acos > 0.35 ? "var(--amber)" : "var(--t2)" }}>{pct(s.acos)}</td>
                    <td style={td}>{s.roas ?? "—"}</td>
                    <td style={td}>{num(s.orders)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

/** percentage change of `cur` vs `prev`; null when not computable */
function delta(cur: any, prev: any): number | null {
  const c = Number(cur), p = Number(prev);
  if (!Number.isFinite(c) || !Number.isFinite(p) || p === 0) return null;
  return (c - p) / Math.abs(p) * 100;
}
function Kpi({ label, value, hint, delta: d, invert }: { label: string; value: any; hint?: string; delta?: number | null; invert?: boolean }) {
  // invert=true → 上升是坏事（花费/ACOS），用红/绿相反着色
  const good = d == null ? false : invert ? d < 0 : d > 0;
  const color = d == null || Math.abs(d) < 0.05 ? "var(--t3)" : good ? "var(--acc)" : "var(--amber)";
  return (
    <div className="card" style={{ padding: "10px 12px" }}>
      <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 600, marginTop: 2 }}>{value}{hint && <span style={{ fontSize: "var(--fs-10)", color: "var(--amber)", marginLeft: 4 }}>{hint}</span>}</div>
      {d != null && <div style={{ fontSize: "var(--fs-10)", color, marginTop: 1 }}>{d > 0 ? "▲" : d < 0 ? "▼" : "＝"} {Math.abs(d).toFixed(1)}% 环比</div>}
    </div>
  );
}
function Card({ title, children }: any) {
  return <div className="card" style={{ padding: 0 }}><div style={{ padding: "8px 12px", borderBottom: "1px solid var(--b)", fontSize: "var(--fs-11)", color: "var(--t3)" }}>{title}</div>{children}</div>;
}
function CampTable({ rows, cur, loading }: { rows: any[]; cur?: Cur; loading: boolean }) {
  if (loading) return <div style={{ padding: 20, textAlign: "center", color: "var(--t3)", fontSize: "var(--fs-11)" }}>聚合中…</div>;
  if (!rows.length) return <div style={{ padding: 20, textAlign: "center", color: "var(--t3)", fontSize: "var(--fs-11)" }}>窗口内无广告数据</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="lx-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--fs-11)" }}>
        <thead><tr>{["活动", "花费", "销售额", "ACOS", "ROAS", "订单", "CTR", "CVR"].map((h) => <th key={h} style={th}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderBottom: "1px solid var(--b)" }}>
              <td style={{ ...td, maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis" }}>{r.name || r.campaign_id}</td>
              <td style={td}>{fmtBudget(r.spend, cur)}</td>
              <td style={td}>{fmtBudget(r.sales, cur)}</td>
              <td style={{ ...td, color: r.acos != null && r.acos > 0.35 ? "var(--amber)" : "var(--t2)" }}>{pct(r.acos)}</td>
              <td style={td}>{r.roas ?? "—"}</td>
              <td style={td}>{num(r.orders)}</td>
              <td style={td}>{pct(r.ctr)}</td>
              <td style={td}>{pct(r.cvr)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
const th: React.CSSProperties = { textAlign: "left", padding: "6px 10px", color: "var(--t3)", borderBottom: "1px solid var(--b)", whiteSpace: "nowrap" };
const td: React.CSSProperties = { padding: "6px 10px", color: "var(--t2)", whiteSpace: "nowrap" };
