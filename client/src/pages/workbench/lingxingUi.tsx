// 领星板块共享 UI 原语：按钮/输入样式/小节/状态标签/表格（排序+筛选+分页）/骨架屏
// —— 之前 8 个 LingXing*.tsx 各自抄一份，这里收拢成唯一来源。
import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { errText } from "../../lib/errText";

export const inputStyle: React.CSSProperties = {
  background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 3,
  padding: "5px 7px", fontSize: 11, color: "var(--t)", outline: "none",
  fontFamily: "inherit", boxSizing: "border-box",
};

export function Btn({ onClick, children, primary, danger, disabled, title }: any) {
  return (
    <button onClick={onClick} disabled={disabled} title={title} style={{
      background: danger ? "var(--red)" : primary ? "var(--acc)" : "var(--bg2)",
      color: danger || primary ? "#000" : "var(--t)",
      border: danger || primary ? "none" : "1px solid var(--b)",
      borderRadius: 4, padding: "5px 12px", fontSize: 11,
      cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.55 : 1,
    }}>{children}</button>
  );
}

export function L({ t, children }: { t: string; children: any }) {
  return <div style={{ display: "grid", gap: 3, fontSize: 10, color: "var(--t3)" }}><span>{t}</span>{children}</div>;
}

export function Section({ title, children }: any) {
  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--b)" }}>
      <div style={{ fontSize: 10, color: "var(--t3)", marginBottom: 4 }}>{title}</div>{children}
    </div>
  );
}

export function Chip({ on, label, warn }: { on: boolean; label: string; warn?: boolean }) {
  const color = warn ? "var(--amber)" : on ? "var(--acc)" : "var(--t3)";
  return (
    <span style={{ fontSize: 11, color, display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, display: "inline-block" }} />{label}
    </span>
  );
}

export const TICKET_STATUS_ZH: Record<string, string> = {
  reviewing: "复核中", awaiting_human: "待确认", executed: "已执行", rolled_back: "已回滚",
  guardrail_blocked: "护栏拦截", review_rejected: "复核否决", rejected: "已驳回", failed: "失败", executing: "执行中",
};
const TICKET_STATUS_COLOR: Record<string, string> = {
  reviewing: "var(--amber)", awaiting_human: "var(--amber)", executed: "var(--acc)", rolled_back: "var(--blue)",
  guardrail_blocked: "var(--red)", review_rejected: "var(--red)", rejected: "var(--t3)", failed: "var(--red)",
};
export function TicketStatus({ s }: { s: string }) {
  return (
    <span style={{ fontSize: 10, color: TICKET_STATUS_COLOR[s] || "var(--t3)", whiteSpace: "nowrap" }}>
      {s === "reviewing" && <span className="lx-spin-dot" />}{TICKET_STATUS_ZH[s] || s}
    </span>
  );
}

export const LEVER_COLOR: Record<string, string> = {
  "否词": "var(--red)", "降bid": "var(--amber)", "加bid": "var(--acc)", "加预算": "var(--blue)", "收割": "var(--purple)",
};

export const pct = (v: any) => (v == null ? "—" : (v * 100).toFixed(1) + "%");
export const pct0 = (v: any) => (v == null ? "—" : (v * 100).toFixed(0) + "%");
export const num = (v: any) => (v == null || v === "" ? "—" : Number(v).toLocaleString("en-US"));

export function fmtCell(v: any) {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
export function fmtTs(ts?: string) {
  if (!ts) return "—";
  try { return new Date(ts).toLocaleString("zh-CN", { hour12: false }); } catch { return ts; }
}
export function fmtDur(s: number) {
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h${m % 60}m` : `${m}m`;
}
export function humanErr(e: any): string {
  return errText(e, "请求失败");
}

/* ── 表格：列头排序 + 关键字筛选 + 客户端分页 ───────────────────────────── */
export type LxCol = {
  key: string; label: string;
  num?: boolean;                       // 数字列：右对齐 + 数值排序
  render?: (row: any) => ReactNode;    // 自定义单元格
  sortVal?: (row: any) => any;         // 自定义排序取值
  maxWidth?: number;
};

export function LxTable({ rows, cols, pageSize = 50, filterable = true, empty = "暂无数据", initSort }: {
  rows: any[]; cols: LxCol[]; pageSize?: number; filterable?: boolean; empty?: string;
  initSort?: { key: string; dir: "asc" | "desc" };
}) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(initSort || null);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return rows;
    return rows.filter((r) => JSON.stringify(r).toLowerCase().includes(s));
  }, [rows, q]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = cols.find((c) => c.key === sort.key);
    const val = (r: any) => (col?.sortVal ? col.sortVal(r) : r[sort.key]);
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;               // 空值永远沉底
      if (vb == null) return -1;
      const na = Number(va), nb = Number(vb);
      if (Number.isFinite(na) && Number.isFinite(nb)) return (na - nb) * dir;
      return String(va).localeCompare(String(vb), "zh-CN") * dir;
    });
  }, [filtered, sort, cols]);

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const p = Math.min(page, pages - 1);
  const shown = sorted.slice(p * pageSize, (p + 1) * pageSize);

  const toggleSort = (key: string) => {
    setPage(0);
    setSort((s) => (s?.key === key ? (s.dir === "desc" ? { key, dir: "asc" } : null) : { key, dir: "desc" }));
  };

  if (!rows.length) {
    return <div style={{ padding: 30, textAlign: "center", color: "var(--t3)", fontSize: 11 }}>{empty}</div>;
  }
  return (
    <div>
      {(filterable && rows.length > 10) && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", borderBottom: "1px solid var(--b)" }}>
          <input value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} placeholder="筛选…"
            style={{ ...inputStyle, width: 160 }} />
          <span style={{ fontSize: 10, color: "var(--t3)" }}>{q ? `${sorted.length}/${rows.length}` : `${rows.length}`} 条 · 点表头排序</span>
        </div>
      )}
      <div style={{ overflowX: "auto" }}>
        <table className="lx-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr>{cols.map((c) => (
              <th key={c.key} onClick={() => toggleSort(c.key)} style={{
                textAlign: c.num ? "right" : "left", padding: "7px 10px", color: sort?.key === c.key ? "var(--t)" : "var(--t3)",
                borderBottom: "1px solid var(--b)", whiteSpace: "nowrap", cursor: "pointer", userSelect: "none",
              }}>{c.label}{sort?.key === c.key ? (sort.dir === "desc" ? " ▾" : " ▴") : ""}</th>
            ))}</tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i} style={{ borderBottom: "1px solid var(--b)" }}>
                {cols.map((c) => (
                  <td key={c.key} style={{
                    padding: "6px 10px", color: "var(--t2)", whiteSpace: "nowrap", verticalAlign: "top",
                    textAlign: c.num ? "right" : "left", fontVariantNumeric: c.num ? "tabular-nums" : undefined,
                    maxWidth: c.maxWidth ?? 260, overflow: "hidden", textOverflow: "ellipsis",
                  }}>{c.render ? c.render(r) : fmtCell(r[c.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", borderTop: "1px solid var(--b)", fontSize: 10, color: "var(--t3)" }}>
          <Btn onClick={() => setPage(Math.max(0, p - 1))} disabled={p === 0}>‹ 上一页</Btn>
          <span>第 {p + 1} / {pages} 页 · 共 {sorted.length} 条</span>
          <Btn onClick={() => setPage(Math.min(pages - 1, p + 1))} disabled={p >= pages - 1}>下一页 ›</Btn>
        </div>
      )}
    </div>
  );
}

/* ── 骨架屏：表格形状/KPI 形状（复用 L1 全局 .skeleton 质感） ─────────────── */
export function LxTableSkeleton({ lines = 6 }: { lines?: number }) {
  return (
    <div style={{ padding: "10px 12px" }}>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className={`skeleton line ${i % 3 === 0 ? "lg" : i % 3 === 1 ? "md" : "lg"}`} />
      ))}
    </div>
  );
}
export function LxKpiSkeleton({ n = 7 }: { n?: number }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8, marginBottom: 10 }}>
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="card" style={{ padding: "10px 12px" }}>
          <div className="skeleton line sm" />
          <div className="skeleton line md" style={{ height: 16 }} />
        </div>
      ))}
    </div>
  );
}

/* ── 进度条（优化引擎后台任务用） ─────────────────────────────────────── */
export function LxProgress({ phase, done, total }: { phase?: string; done?: number; total?: number }) {
  const p = total ? Math.min(100, Math.round(((done || 0) / total) * 100)) : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 220, flex: 1 }}>
      <div style={{ flex: 1, height: 6, borderRadius: 3, background: "var(--bg2)", overflow: "hidden" }}>
        <div style={{ width: `${p}%`, height: "100%", background: "var(--acc)", transition: "width .4s ease" }} />
      </div>
      <span style={{ fontSize: 10, color: "var(--t3)", whiteSpace: "nowrap" }}>
        {phase || "运行中"} {total ? `${done}/${total}` : ""}（{p}%）
      </span>
    </div>
  );
}
