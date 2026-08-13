/**
 * 统一的结论卡片：证据 → 诊断 → 带阈值的动作。
 *
 * 为什么要有它：此前每个板块自己定义分析结论的结构，前端也就各写一遍渲染。
 * 同一条"广告在浪费钱"的结论在不同地方长得都不一样，用户没法一眼判断
 * **这个结论凭什么**。后端统一成 FindingList 之后，这里是唯一的渲染入口。
 *
 * 三个刻意的设计：
 * 1. **证据默认折叠、可展开看原始指标**。铺开会把卡片撑得没法扫读，
 *    但"能点开核对"是这套契约存在的全部意义 —— 不能省。
 * 2. **证据还能再点进一层，看它依据的原始数据行**。用户真正想问的下一句永远是
 *    "你从哪儿看出来的"。做不到这一层，证据页上的数字和模型编的数字在界面上
 *    长得一模一样。需要调用方传 traceUrl 才会出现这个入口 —— 拿不到源数据的
 *    板块不该显示一个点了没反应的按钮。
 * 3. **没有证据的结论显式标出来**，而不是悄悄混在里面。一条没有依据的建议
 *    和一条有 312 次点击零单撑着的建议，值不一样，用户有权知道。
 */
import { useState } from "react";

import { api } from "../api/client";
import type { Finding, FindingList } from "../api/client";
import { errText } from "../lib/errText";

type TraceResult = {
  ok: boolean;
  reason?: string;
  file?: string;
  columns?: string[];
  rows?: (string | number)[][];
  total?: number;
  truncated?: boolean;
};

const SEVERITY: Record<string, { label: string; color: string; bg: string }> = {
  critical: { label: "紧急", color: "var(--red)", bg: "color-mix(in srgb, var(--red) 12%, transparent)" },
  high: { label: "高", color: "var(--amber)", bg: "color-mix(in srgb, var(--amber) 12%, transparent)" },
  medium: { label: "中", color: "var(--cyan)", bg: "color-mix(in srgb, var(--cyan) 12%, transparent)" },
  low: { label: "低", color: "var(--t2)", bg: "var(--bg2)" },
};

function RawRows({ traceUrl, target }: { traceUrl: string; target: string }) {
  const [res, setRes] = useState<TraceResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = async () => {
    setBusy(true);
    setErr("");
    try {
      const { data } = await api.get<TraceResult>(traceUrl, { params: { target } });
      setRes(data);
    } catch (e) {
      setErr(errText(e, "读不到原始数据"));
    } finally { setBusy(false); }
  };

  if (!res && !err) {
    return (
      <button type="button" className="tbtn" onClick={load} disabled={busy}
              style={{ fontSize: 11, padding: "1px 7px" }}>
        {busy ? "查找中…" : "看原始数据"}
      </button>
    );
  }
  if (err) return <span style={{ fontSize: 11, color: "var(--red)" }}>{err}</span>;

  // **溯源失败和"没有证据"是两件事**，必须分开说。回一个空表格会让人以为
  // 这条结论本来就没依据。
  if (!res!.ok) {
    return <span style={{ fontSize: 11, color: "var(--amber)" }}>{res!.reason}</span>;
  }

  return (
    <div style={{ marginTop: 6, overflowX: "auto", border: "1px solid #e3e7e4", borderRadius: 6 }}>
      <div style={{ fontSize: 11, color: "var(--t3)", padding: "5px 8px" }}>
        {res!.file} · 命中 {res!.total} 行
        {res!.truncated ? `（只显示前 ${res!.rows!.length} 行）` : ""}
      </div>
      <table style={{ borderCollapse: "collapse", fontSize: 11.5, minWidth: 520 }}>
        <thead>
          <tr>
            {(res!.columns || []).map((c, i) => (
              <th key={i} style={{ padding: "3px 8px", textAlign: "left", color: "var(--t2)",
                                   borderTop: "1px solid #e3e7e4", whiteSpace: "nowrap" }}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(res!.rows || []).map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} style={{ padding: "3px 8px", borderTop: "1px solid #f0f2f1",
                                     whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                  {String(c)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EvidenceRow({ e, traceUrl }: {
  e: NonNullable<Finding["evidence"]>[number]; traceUrl?: string;
}) {
  const value = typeof e.value === "object" ? JSON.stringify(e.value) : String(e.value ?? "");
  return (
    <tr>
      <td style={{ padding: "4px 10px 4px 0", color: "var(--t2)", whiteSpace: "nowrap" }}>
        {e.metric}
      </td>
      <td style={{ padding: "4px 10px 4px 0", fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>
        {value}
        {e.unit ? <span style={{ color: "var(--t3)", fontWeight: 400 }}> {e.unit}</span> : null}
      </td>
      <td style={{ padding: "4px 10px 4px 0", color: "var(--t2)" }}>{e.target || "—"}</td>
      <td style={{ padding: "4px 0", color: "var(--t3)", fontSize: 12 }}>
        {[e.source, e.as_of].filter(Boolean).join(" · ") || "—"}
        {traceUrl && e.target ? (
          <div style={{ marginTop: 3 }}>
            <RawRows traceUrl={traceUrl} target={String(e.target)} />
          </div>
        ) : null}
      </td>
    </tr>
  );
}

function FindingCard({ item, unsupported, traceUrl }: {
  item: Finding; unsupported: boolean; traceUrl?: string;
}) {
  const [open, setOpen] = useState(false);
  const sev = SEVERITY[item.severity || "medium"] || SEVERITY.medium;
  const evidence = item.evidence || [];
  const actions = item.actions || [];

  return (
    <div
      style={{
        border: "1px solid #d2d8d3",
        borderLeft: `3px solid ${sev.color}`,
        borderRadius: 2,
        padding: "12px 14px",
        background: "var(--bg1)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span
          style={{
            fontSize: 11, fontWeight: 700, color: sev.color, background: sev.bg,
            padding: "2px 7px", borderRadius: 2, whiteSpace: "nowrap",
          }}
        >
          {sev.label}
        </span>
        <span style={{ fontWeight: 700, fontSize: 15 }}>{item.title}</span>
        {typeof item.priority_score === "number" && item.priority_score > 0 && (
          <span style={{ fontSize: 12, color: "var(--t3)", fontVariantNumeric: "tabular-nums" }}>
            优先级 {item.priority_score}
          </span>
        )}
        {unsupported && (
          <span
            title="这条结论没有附任何可核对的数据"
            style={{
              fontSize: 11, color: "var(--amber)", background: "color-mix(in srgb, var(--amber) 12%, transparent)",
              padding: "2px 7px", borderRadius: 2,
            }}
          >
            ⚠ 无证据
          </span>
        )}
      </div>

      {item.reasoning && (
        <div style={{ marginTop: 6, fontSize: 13.5, color: "var(--t)", lineHeight: 1.6 }}>
          {item.reasoning}
        </div>
      )}

      {actions.length > 0 && (
        <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
          {actions.map((a, i) => (
            <div key={i} style={{ fontSize: 13.5, display: "flex", gap: 8, alignItems: "baseline" }}>
              <span style={{ color: "var(--cyan)", fontWeight: 600, whiteSpace: "nowrap" }}>
                {a.type}
              </span>
              <span>
                {a.target ? <b>{a.target}</b> : null}
                {a.target && a.detail ? " — " : ""}
                {a.detail}
                {a.guardrail && (
                  <span style={{ color: "var(--t3)", fontSize: 12 }}>（前提：{a.guardrail}）</span>
                )}
                {a.reversible === false && (
                  <span style={{ color: "var(--red)", fontSize: 12 }}> · 不可回滚</span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      {evidence.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <button
            type="button"
            className="tbtn"
            onClick={() => setOpen((v) => !v)}
            style={{ fontSize: 12, padding: "3px 9px" }}
          >
            {open ? "收起证据" : `证据 ${evidence.length} 条`}
          </button>
          {open && (
            <div style={{ overflowX: "auto", marginTop: 8 }}>
              <table style={{ borderCollapse: "collapse", fontSize: 13, minWidth: 420 }}>
                <tbody>
                  {evidence.map((e, i) => (
                    <EvidenceRow key={i} e={e} traceUrl={traceUrl} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function FindingCards({ data, traceUrl }: {
  data?: FindingList | null;
  /** 溯源接口，例如 `/ad-audit/{job}/evidence`。不传就不显示"看原始数据"。 */
  traceUrl?: string;
}) {
  const findings = data?.findings || [];
  if (findings.length === 0) return null;
  const unsupported = new Set(data?.unsupported || []);

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>结论</h3>
        <span style={{ fontSize: 12, color: "var(--t3)" }}>
          {findings.length} 条
          {unsupported.size > 0 && ` · 其中 ${unsupported.size} 条无证据`}
        </span>
      </div>
      <div style={{ display: "grid", gap: 10 }}>
        {findings.map((f, i) => (
          <FindingCard
            key={f.id || i}
            item={f}
            unsupported={unsupported.has(f.id || f.title)}
            traceUrl={traceUrl}
          />
        ))}
      </div>
      {data?.data_notes && (
        <div style={{ marginTop: 8, fontSize: 12, color: "var(--t3)" }}>{data.data_notes}</div>
      )}
    </div>
  );
}
