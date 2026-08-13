import { useEffect, useState } from "react";
import { MarkdownReport, relativeTime, triggerDownload } from "../../../lib/reportFormat";
import { KeywordResult, CompetitorResult, TrafficResult } from "./resultViews";

interface Entry {
  id: string;
  tool: string;
  title: string;
  query: string;
  country: string;
  provider: string;
  elapsed_s: number;
  ts: number;
  report: string;
}

// 结构化条目（关键词/竞品/流量面板保存的原始 JSON 结果）→ 解析出数据；
// 解析不出（agent 生成的 Markdown 叙述报告）返回 null 走 MarkdownReport。
function parseStructured(e: Entry): any | null {
  if (!["keyword", "competitor", "traffic"].includes(e.tool)) return null;
  const text = (e.report || "").trim();
  if (!text.startsWith("{")) return null;
  try { return JSON.parse(text); } catch { return null; }
}

function StructuredView({ entry, data }: { entry: Entry; data: any }) {
  if (entry.tool === "keyword") return <KeywordResult data={data} keyword={entry.query} />;
  if (entry.tool === "competitor") return <CompetitorResult data={data} asin={entry.query} />;
  return <TrafficResult data={data} asin={entry.query} />;
}

export default function DeepHistory() {
  const [rows, setRows] = useState<Entry[]>([]);
  const [active, setActive] = useState<Entry | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    fetch("/api/deep-analysis/history", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setRows(Array.isArray(d) ? d : []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const del = async (id: string) => {
    await fetch(`/api/deep-analysis/history/${encodeURIComponent(id)}`, {
      method: "DELETE",
      credentials: "include",
    });
    setActive(null);
    load();
  };

  if (active) {
    const structured = parseStructured(active);
    const slug = `${active.tool}-${active.query}-${active.country}`.replace(/[^\w一-龥.-]+/g, "_");
    return (
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          <button className="tbtn" style={{ fontSize: "var(--fs-11)" }} onClick={() => setActive(null)}>
            ← 返回历史
          </button>
          <button
            className="tbtn"
            style={{ fontSize: "var(--fs-10)", marginLeft: "auto" }}
            onClick={() =>
              structured
                ? triggerDownload(JSON.stringify(structured, null, 2), `${slug}.json`, "application/json")
                : triggerDownload(active.report, `${slug}.md`, "text/markdown")}
          >
            ⬇ 下载 {structured ? "JSON" : "Markdown"}
          </button>
        </div>
        <div style={{ fontSize: "var(--fs-11)", color: "var(--t3)", marginBottom: 8 }}>
          {active.title} · {active.query} · {active.country} · {relativeTime(active.ts * 1000)}
        </div>
        {structured ? (
          <StructuredView entry={active} data={structured} />
        ) : (
          <div className="market-report-body">
            <MarkdownReport text={active.report} />
          </div>
        )}
      </div>
    );
  }

  if (loading) return <div style={{ color: "var(--t3)", fontSize: "var(--fs-11)" }}>加载中…</div>;
  if (rows.length === 0)
    return (
      <div style={{ color: "var(--t3)", fontSize: "var(--fs-11)", lineHeight: 1.7 }}>
        暂无分析历史。在本页任一分析工具跑出的结果、或右下角 IvyeaAgent
        生成的分析报告，都会自动保存到这里。
      </div>
    );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {rows.map((e) => (
        <div
          key={e.id}
          className="card"
          style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "center" }}
          onClick={() => setActive(e)}
        >
          <div>
            <div style={{ fontSize: "var(--fs-12)", fontWeight: 600, color: "var(--t)" }}>
              {e.title} · {e.query}
            </div>
            <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>
              {e.country} · {relativeTime(e.ts * 1000)} · {e.provider || "ivyea-agent"}
            </div>
          </div>
          <button
            className="tbtn"
            style={{ fontSize: "var(--fs-10)" }}
            onClick={(ev) => {
              ev.stopPropagation();
              del(e.id);
            }}
          >
            删除
          </button>
        </div>
      ))}
    </div>
  );
}
