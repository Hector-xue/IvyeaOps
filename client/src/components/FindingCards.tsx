/**
 * 统一的结论卡片：证据 → 诊断 → 带阈值的动作。
 *
 * 为什么要有它：此前每个板块自己定义分析结论的结构，前端也就各写一遍渲染。
 * 同一条"广告在浪费钱"的结论在不同地方长得都不一样，用户没法一眼判断
 * **这个结论凭什么**。后端统一成 FindingList 之后，这里是唯一的渲染入口。
 *
 * 两个刻意的设计：
 * 1. **证据默认折叠、可展开看原始指标**。铺开会把卡片撑得没法扫读，
 *    但"能点开核对"是这套契约存在的全部意义 —— 不能省。
 * 2. **没有证据的结论显式标出来**，而不是悄悄混在里面。一条没有依据的建议
 *    和一条有 312 次点击零单撑着的建议，值不一样，用户有权知道。
 */
import { useState } from "react";

import type { Finding, FindingList } from "../api/client";

const SEVERITY: Record<string, { label: string; color: string; bg: string }> = {
  critical: { label: "紧急", color: "#a8382c", bg: "#fdecea" },
  high: { label: "高", color: "#8a5410", bg: "#fdf3e3" },
  medium: { label: "中", color: "#1d4e5a", bg: "#e6f0f2" },
  low: { label: "低", color: "#5b6560", bg: "#eef1ee" },
};

function EvidenceRow({ e }: { e: NonNullable<Finding["evidence"]>[number] }) {
  const value = typeof e.value === "object" ? JSON.stringify(e.value) : String(e.value ?? "");
  return (
    <tr>
      <td style={{ padding: "4px 10px 4px 0", color: "#5b6560", whiteSpace: "nowrap" }}>
        {e.metric}
      </td>
      <td style={{ padding: "4px 10px 4px 0", fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>
        {value}
        {e.unit ? <span style={{ color: "#78827e", fontWeight: 400 }}> {e.unit}</span> : null}
      </td>
      <td style={{ padding: "4px 10px 4px 0", color: "#5b6560" }}>{e.target || "—"}</td>
      <td style={{ padding: "4px 0", color: "#78827e", fontSize: 12 }}>
        {[e.source, e.as_of].filter(Boolean).join(" · ") || "—"}
      </td>
    </tr>
  );
}

function FindingCard({ item, unsupported }: { item: Finding; unsupported: boolean }) {
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
        background: "#fff",
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
          <span style={{ fontSize: 12, color: "#78827e", fontVariantNumeric: "tabular-nums" }}>
            优先级 {item.priority_score}
          </span>
        )}
        {unsupported && (
          <span
            title="这条结论没有附任何可核对的数据"
            style={{
              fontSize: 11, color: "#8a5410", background: "#fdf3e3",
              padding: "2px 7px", borderRadius: 2,
            }}
          >
            ⚠ 无证据
          </span>
        )}
      </div>

      {item.reasoning && (
        <div style={{ marginTop: 6, fontSize: 13.5, color: "#47514d", lineHeight: 1.6 }}>
          {item.reasoning}
        </div>
      )}

      {actions.length > 0 && (
        <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
          {actions.map((a, i) => (
            <div key={i} style={{ fontSize: 13.5, display: "flex", gap: 8, alignItems: "baseline" }}>
              <span style={{ color: "#1d4e5a", fontWeight: 600, whiteSpace: "nowrap" }}>
                {a.type}
              </span>
              <span>
                {a.target ? <b>{a.target}</b> : null}
                {a.target && a.detail ? " — " : ""}
                {a.detail}
                {a.guardrail && (
                  <span style={{ color: "#78827e", fontSize: 12 }}>（前提：{a.guardrail}）</span>
                )}
                {a.reversible === false && (
                  <span style={{ color: "#a8382c", fontSize: 12 }}> · 不可回滚</span>
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
                    <EvidenceRow key={i} e={e} />
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

export default function FindingCards({ data }: { data?: FindingList | null }) {
  const findings = data?.findings || [];
  if (findings.length === 0) return null;
  const unsupported = new Set(data?.unsupported || []);

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>结论</h3>
        <span style={{ fontSize: 12, color: "#78827e" }}>
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
          />
        ))}
      </div>
      {data?.data_notes && (
        <div style={{ marginTop: 8, fontSize: 12, color: "#78827e" }}>{data.data_notes}</div>
      )}
    </div>
  );
}
