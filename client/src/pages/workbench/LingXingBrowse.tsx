import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { useToast } from "../../components/toast";
import { Btn, LxTable, LxTableSkeleton, fmtTs, humanErr, inputStyle } from "./lingxingUi";
import type { Dataset } from "./lingxingTypes";

/** 相对日期 token（"-7d" / "-1d"）→ 真实 YYYY-MM-DD，喂给 date 输入框。 */
function resolveDate(token: any): string {
  if (typeof token !== "string") return token ?? "";
  const m = token.trim().match(/^(-?\d+)d$/);
  if (!m) return token;
  const d = new Date(); d.setDate(d.getDate() + Number(m[1]));
  return d.toISOString().slice(0, 10);
}

/* ── 数据浏览（左侧数据集 + 参数 + 表格；服务端翻页 + 全列切换） ─────────── */
export default function LingXingBrowse({ datasets, active, setActive, storeSid }: {
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
