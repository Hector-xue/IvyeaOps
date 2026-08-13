import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import SheetSelect from "../../components/SheetSelect";
import { ToastProvider, useToast } from "../../components/toast";
import { Btn, Chip, LxTable, LxTableSkeleton, fmtTs, humanErr, inputStyle } from "./lingxingUi";
import LingXingSuggest from "./LingXingSuggest";
import LingXingOperate from "./LingXingOperate";
import LingXingAudit from "./LingXingAudit";
import LingXingDashboard from "./LingXingDashboard";
import LingXingConfig from "./LingXingConfig";

type Col = { key: string; label: string };
type Param = { name: string; type?: string; required?: boolean; default?: any; label?: string };
type Dataset = { key: string; label: string; group: string; params: Param[]; columns: Col[]; hint?: string };
type Status = {
  master_enabled: boolean; operate_active: boolean; openapi_configured: boolean;
  ticket_counts?: { awaiting_human?: number; reviewing?: number; executing?: number };
};

type View = "dashboard" | "browse" | "suggest" | "tickets" | "audit" | "config";
const VIEWS: [View, string][] = [
  ["dashboard", "大盘"], ["browse", "数据浏览"], ["suggest", "优化建议"],
  ["tickets", "工单"], ["audit", "审计"], ["config", "配置"],
];
const VALID_VIEWS = new Set(VIEWS.map(([v]) => v));
// 旧 8-tab 布局的 localStorage 值迁移到新 6-tab
const VIEW_MIGRATE: Record<string, View> = { optimizer: "suggest", auto: "suggest", operate: "tickets", help: "config" };

const LS_KEY = "lingxing.ui.v1";
function readLS(): any { try { return JSON.parse(localStorage.getItem(LS_KEY) || "{}"); } catch { return {}; } }
function normalizeView(v: any): View | null {
  if (VALID_VIEWS.has(v)) return v as View;
  if (v && VIEW_MIGRATE[v]) return VIEW_MIGRATE[v];
  return null;
}
// relative date token like "-7d" / "-1d" → real YYYY-MM-DD (for display + date inputs)
function resolveDate(token: any): string {
  if (typeof token !== "string") return token ?? "";
  const m = token.trim().match(/^(-?\d+)d$/);
  if (!m) return token;
  const d = new Date(); d.setDate(d.getDate() + Number(m[1]));
  return d.toISOString().slice(0, 10);
}

export default function LingXing() {
  return (
    <ToastProvider>
      <LingXingInner />
    </ToastProvider>
  );
}

function LingXingInner() {
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState<Status | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [view, setViewRaw] = useState<View>(() =>
    normalizeView(params.get("tab")) || normalizeView(readLS().view) || "dashboard");
  const [sellers, setSellers] = useState<any[]>([]);
  const [storeSid, setStoreSid] = useState<string>(() => readLS().storeSid || "");
  const [active, setActive] = useState<string>(() => readLS().active || "sellers");
  const [focusTicket, setFocusTicket] = useState<string>("");
  const [err, setErr] = useState<string>("");
  const toast = useToast();

  const setView = (v: View) => { setViewRaw(v); };

  /* remember view / dataset / store; mirror the tab into the URL for deep links */
  useEffect(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify({ view, active, storeSid })); } catch { /* */ }
    if (params.get("tab") !== view) {
      const next = new URLSearchParams(params);
      next.set("tab", view);
      setParams(next, { replace: true });
    }
  }, [view, active, storeSid]);

  /* initial load + status polling (badge / switch states stay fresh) */
  useEffect(() => {
    void boot();
    const t = setInterval(() => { void refreshStatus(); }, 10000);
    return () => clearInterval(t);
  }, []);
  async function boot() {
    try {
      const [st, dl] = await Promise.all([
        api.get("/lingxing/status"), api.get("/lingxing/datasets"),
      ]);
      setStatus(st.data); setDatasets(dl.data.datasets || []);
      if (!st.data.openapi_configured) setViewRaw("config");  // onboarding: land on 配置
      if (st.data.master_enabled) void loadSellers();
    } catch (e: any) { setErr(humanErr(e)); }
  }
  async function refreshStatus() {
    try { setStatus((await api.get("/lingxing/status")).data); } catch { /* transient */ }
  }
  async function loadSellers() {
    try {
      const r = await api.post("/lingxing/read/sellers", { params: {} });
      const list = r.data.rows || [];
      setSellers(list);
      if (list.length && (!storeSid || !list.some((s: any) => String(s.sid) === storeSid))) {
        setStoreSid(String(list[0].sid));
      }
    } catch { /* master may be off */ }
  }

  async function enableMaster() {
    try {
      await api.patch("/settings", { settings: { lingxing_enabled: true } });
      toast("success", "领星数据已启用（只读）");
      await boot();
    } catch (e: any) { toast("error", humanErr(e)); }
  }

  /* suggest → tickets 一键衔接：批量生成后带着新工单跳到工单 tab */
  function goTickets(firstId?: string) {
    if (firstId) setFocusTicket(firstId);
    setViewRaw("tickets");
  }

  const pending = status?.ticket_counts?.awaiting_human || 0;
  const reviewing = status?.ticket_counts?.reviewing || 0;

  return (
    <div>
      <div className="ptitle">/ 领星 ERP</div>

      {/* boot error / loading — never leave the page a dead end */}
      {!status && (
        <div className="card" style={{ padding: 12, marginBottom: 10, fontSize: "var(--fs-11)", display: "flex", gap: 10, alignItems: "center", color: err ? "var(--red)" : "var(--t3)" }}>
          {err ? <>加载领星状态失败：{err}（后端可能未重启，新接口未生效）</> : "加载中…"}
          <span style={{ marginLeft: "auto" }}><Btn onClick={boot}>重试</Btn></span>
        </div>
      )}

      {/* status bar */}
      <div className="card lx-statusbar" style={{ padding: "8px 12px", marginBottom: 10, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <Chip on={!!status?.openapi_configured} label={status?.openapi_configured ? "OpenAPI 已配置" : "未配置凭证"} />
        <Chip on={!!status?.master_enabled} label={status?.master_enabled ? "数据已启用" : "数据未启用"} />
        <Chip on={!!status?.operate_active} label={status?.operate_active ? "操作开关：开" : "操作开关：关(只读)"} warn={!!status?.operate_active} />
        {status && !status.master_enabled && (
          <span style={{ marginLeft: "auto" }}><Btn primary onClick={enableMaster}>启用领星数据(只读)</Btn></span>
        )}
        {status?.master_enabled && (
          <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>店铺</span>
            <SheetSelect value={storeSid} onChange={setStoreSid} title="选择店铺" placeholder="（加载中/无）"
              style={{ ...inputStyle, minWidth: 160 }}
              options={sellers.map((s) => ({ value: String(s.sid), label: String(s.name || s.sid), sub: String(s.sid) }))} />
          </span>
        )}
      </div>

      <div className="lx-tabs">
        {VIEWS.map(([v, l]) => (
          <button key={v} onClick={() => setView(v)} style={{
            padding: "6px 14px", fontSize: "var(--fs-11)", border: "none", borderRadius: 4, cursor: "pointer",
            background: view === v ? "var(--acc)" : "var(--bg2)", color: view === v ? "#000" : "var(--t2)",
            fontWeight: view === v ? 600 : 400, position: "relative",
          }}>
            {l}
            {v === "tickets" && pending > 0 && (
              <span className="lx-badge" title={`${pending} 张工单待确认`}>{pending}</span>
            )}
            {v === "tickets" && pending === 0 && reviewing > 0 && (
              <span className="lx-badge lx-badge-soft" title={`${reviewing} 张工单复核中`}>{reviewing}</span>
            )}
          </button>
        ))}
      </div>

      {view === "config" ? <LingXingConfig />
        : !status?.master_enabled ? (
          <div className="card wb-enter" style={{ padding: 40, textAlign: "center", color: "var(--t3)", fontSize: "var(--fs-12)" }}>
            领星数据未启用。请到「配置」tab 填好 OpenAPI 凭证、测试连接后启用。<br />
            <span style={{ fontSize: "var(--fs-11)" }}>（写操作另有独立「操作开关」+ 三重复核，默认关闭）</span>
          </div>
        )
        : view === "dashboard" ? <LingXingDashboard storeSid={storeSid} />
        : view === "suggest" ? <LingXingSuggest storeSid={storeSid} onGoTickets={goTickets} />
        : view === "tickets" ? <LingXingOperate focusTicket={focusTicket} onFocusConsumed={() => setFocusTicket("")} />
        : view === "audit" ? <LingXingAudit />
        : <Browse datasets={datasets} active={active} setActive={setActive} storeSid={storeSid} />}
    </div>
  );
}

/* ── 数据浏览（左侧数据集 + 参数 + 表格；服务端翻页 + 全列切换） ─────────── */
function Browse({ datasets, active, setActive, storeSid }: {
  datasets: Dataset[]; active: string; setActive: (k: string) => void; storeSid: string;
}) {
  const [form, setForm] = useState<Record<string, any>>({});
  const [rows, setRows] = useState<any[]>([]);
  const [meta, setMeta] = useState<{ count?: number; synced_at?: string; cached?: boolean } | null>(null);
  const [loading, setLoading] = useState(false);
  const [allCols, setAllCols] = useState(false);
  const [err, setErr] = useState("");
  const toast = useToast();
  const ds = useMemo(() => datasets.find((d) => d.key === active), [datasets, active]);
  const reqSeq = useRef(0);

  /* when dataset changes, seed the form from its param defaults + current store */
  useEffect(() => {
    if (!ds) return;
    const f: Record<string, any> = {};
    for (const p of ds.params) {
      if (p.name === "sid" || p.name === "sids") f[p.name] = storeSid;
      else if (p.type === "date") f[p.name] = resolveDate(p.default);
      else f[p.name] = p.default ?? "";
    }
    setForm(f); setRows([]); setMeta(null); setErr("");
    const ready = ds.params.filter((p) => p.required).every((p) => f[p.name] !== "" && f[p.name] != null);
    if (ready) void run(false, f);
  }, [active, ds, storeSid]);

  async function run(force = false, override?: Record<string, any>) {
    if (!ds) return;
    const seq = ++reqSeq.current;
    setLoading(true); setErr("");
    try {
      const r = await api.post(`/lingxing/read/${ds.key}`, { params: override || form, force });
      if (seq !== reqSeq.current) return;  // 过期响应（已切数据集/翻页）丢弃
      const data = r.data;
      setRows(Array.isArray(data.rows) ? data.rows : []);
      setMeta({ count: data.count, synced_at: data.synced_at, cached: data.cached });
    } catch (e: any) {
      if (seq !== reqSeq.current) return;
      setErr(humanErr(e)); setRows([]); setMeta(null);
    } finally { if (seq === reqSeq.current) setLoading(false); }
  }

  /* 服务端翻页：有 offset/length 参数的数据集给上一页/下一页 */
  const pageLen = Number(form.length) || 0;
  const pageOff = Number(form.offset) || 0;
  const canPage = !!ds?.params.some((p) => p.name === "offset") && pageLen > 0;
  function turnPage(dir: 1 | -1) {
    const next = { ...form, offset: Math.max(0, pageOff + dir * pageLen) };
    setForm(next); void run(false, next);
  }

  async function exportCsv() {
    if (!rows.length) return;
    const cs = cols.map((c) => c.key);
    const esc = (v: any) => { const s = v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const csv = [cols.map((c) => esc(c.label)).join(","), ...rows.map((r) => cs.map((k) => esc(r[k])).join(","))].join("\n");
    const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = `lingxing-${ds?.key || "data"}.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 15000);
    toast("success", `已导出 ${rows.length} 条`);
  }

  const groups = useMemo(() => {
    const m: Record<string, Dataset[]> = {};
    for (const d of datasets) (m[d.group || "其它"] ||= []).push(d);
    return m;
  }, [datasets]);

  const cols = useMemo(() => {
    if (allCols && rows.length) {
      const keys = new Set<string>();
      for (const r of rows.slice(0, 50)) for (const k of Object.keys(r || {})) keys.add(k);
      return Array.from(keys).map((k) => ({ key: k, label: k }));
    }
    if (ds?.columns?.length) return ds.columns;
    return rows[0] ? Object.keys(rows[0]).map((k) => ({ key: k, label: k })) : [];
  }, [ds, rows, allCols]);

  return (
    <div className="lx-split">
      {/* dataset list */}
      <div style={{ width: 180 }} className="lx-side">
        {Object.entries(groups).map(([g, items]) => (
          <div key={g} className="card" style={{ padding: 8, marginBottom: 8 }}>
            <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 4 }}>{g}</div>
            {items.map((d) => (
              <div key={d.key} onClick={() => setActive(d.key)} style={{
                padding: "6px 8px", borderRadius: 4, cursor: "pointer", fontSize: "var(--fs-11)", marginBottom: 2,
                background: active === d.key ? "var(--acc)" : "transparent",
                color: active === d.key ? "#000" : "var(--t2)", fontWeight: active === d.key ? 600 : 400,
              }}>{d.label}</div>
            ))}
          </div>
        ))}
      </div>

      {/* main */}
      <div className="lx-main">
        <div className="card" style={{ padding: 12, marginBottom: 10 }}>
          {ds?.hint && <div style={{ fontSize: "var(--fs-11)", color: "var(--t3)", marginBottom: 8 }}>{ds.hint}</div>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
            {ds?.params.map((p) => (
              <label key={p.name} style={{ display: "grid", gap: 3, fontSize: "var(--fs-10)", color: "var(--t3)" }}>
                <span>{p.label || p.name}{p.required ? " *" : ""}</span>
                <input type={p.type === "date" ? "date" : "text"} value={form[p.name] ?? ""}
                  placeholder={p.type === "date" ? "" : p.type}
                  onChange={(e) => setForm((f) => ({ ...f, [p.name]: e.target.value }))}
                  style={{ ...inputStyle, width: p.type === "int" ? 90 : p.type === "date" ? 140 : 150 }} />
              </label>
            ))}
            <Btn primary onClick={() => run(false)} disabled={loading}>{loading ? "查询中…" : "查询"}</Btn>
            <Btn onClick={() => run(true)} disabled={loading} title="跳过本地缓存，直连领星拉最新">强制刷新</Btn>
            {canPage && (
              <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
                <Btn onClick={() => turnPage(-1)} disabled={loading || pageOff <= 0}>‹ 上一页</Btn>
                <Btn onClick={() => turnPage(1)} disabled={loading || rows.length < pageLen}>下一页 ›</Btn>
              </span>
            )}
          </div>
          {err && <div style={{ marginTop: 8, fontSize: "var(--fs-11)", color: "var(--red)" }}>{err}</div>}
          {meta && (
            <div style={{ marginTop: 8, fontSize: "var(--fs-10)", color: "var(--t3)", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <span>{meta.count ?? 0} 条 · {meta.cached ? "缓存" : "实时"} · 数据时间 {fmtTs(meta.synced_at)}{canPage && pageLen > 0 ? ` · 第 ${Math.floor(pageOff / pageLen) + 1} 页` : ""}</span>
              <label style={{ display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer" }}>
                <input type="checkbox" checked={allCols} onChange={(e) => setAllCols(e.target.checked)} />全部列
              </label>
              <span style={{ cursor: "pointer", color: "var(--t3)", textDecoration: "underline" }} onClick={exportCsv}>导出 CSV</span>
            </div>
          )}
        </div>

        {/* table */}
        <div className="card" style={{ padding: 0 }}>
          {loading && rows.length === 0 ? <LxTableSkeleton lines={8} /> : (
            <div className="wb-enter" key={`${active}:${pageOff}:${allCols ? 1 : 0}`}>
              <LxTable rows={rows} cols={cols as any} empty="暂无数据，点「查询」" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
