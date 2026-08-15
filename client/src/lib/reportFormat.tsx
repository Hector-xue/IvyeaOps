/**
 * Shared report-formatting helpers: a lightweight Markdown renderer, CSV/HTML
 * exporters, and small utilities. Extracted so multiple workbench pages (market
 * research, launch playbook, …) can render and export AI reports consistently.
 *
 * Pure functions + one presentational React component — no app state.
 */
import React from "react";

// ─── Markdown → React ─────────────────────────────────────────────────────────

/**
 * ⚠️ 导出的是 `React.memo` 包过的版本（见文件末尾的 `MarkdownReport`）。
 * 流式对话里父组件每帧重渲染一次，不 memo 的话**每一轮**的整篇 markdown 都会
 * 跟着重新解析 —— 长报告下这是每秒几十次的整页重排。text 没变就别重算。
 */
function MarkdownReportImpl({ text }: { text: string }) {
  if (!text) return null;
  const lines = text.split("\n");
  const elements: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      elements.push(
        <pre key={i} className="md-pre">
          {lang && <div className="md-pre-lang">{lang}</div>}
          <code className="md-code">{codeLines.join("\n")}</code>
        </pre>
      );
      i++;
      continue;
    }

    if (line.startsWith("|") && i + 1 < lines.length && lines[i + 1].match(/^\|[\s\-|:]+\|$/)) {
      const headers = parseCells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        rows.push(parseCells(lines[i]));
        i++;
      }
      elements.push(
        <div key={i} className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {headers.map((h, hi) => (
                  <th key={hi} className="md-th">
                    {renderInline(h)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className="md-tr">
                  {row.map((cell, ci) => (
                    <td key={ci} className="md-td">
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    if (line.startsWith("# ")) {
      elements.push(<h1 key={i} className="md-h1">{line.slice(2)}</h1>);
    } else if (line.startsWith("## ")) {
      elements.push(<h2 key={i} className="md-h2">{line.slice(3)}</h2>);
    } else if (line.startsWith("### ")) {
      elements.push(<h3 key={i} className="md-h3">{line.slice(4)}</h3>);
    } else if (line.startsWith("> ")) {
      elements.push(
        <div key={i} className="md-quote">
          {renderInline(line.slice(2))}
        </div>
      );
    } else if (line.startsWith("- ") || line.startsWith("* ")) {
      elements.push(<div key={i} className="md-li"><span className="md-bullet">•</span><span>{renderInline(line.slice(2))}</span></div>);
    } else if (/^\d+\. /.test(line)) {
      const num = line.match(/^(\d+)\. /)?.[1] ?? "";
      elements.push(<div key={i} className="md-li"><span className="md-num">{num}.</span><span>{renderInline(line.replace(/^\d+\. /, ""))}</span></div>);
    } else if (line.startsWith("---") || line.startsWith("===")) {
      elements.push(<hr key={i} className="md-hr" />);
    } else if (line.trim() === "") {
      elements.push(<div key={i} className="md-gap" />);
    } else {
      elements.push(<div key={i} className="md-p">{renderInline(line)}</div>);
    }

    i++;
  }

  return <>{elements}</>;
}

export const MarkdownReport = React.memo(MarkdownReportImpl);

function parseCells(line: string): string[] {
  return line.split("|").slice(1, -1).map((c) => c.trim());
}

function renderInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|`(.+?)`|\*(.+?)\*/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      parts.push(<strong key={m.index} style={{ color: "var(--t)", fontWeight: 600 }}>{m[1]}</strong>);
    } else if (m[2] !== undefined) {
      parts.push(<code key={m.index} style={{ background: "var(--bg3)", padding: "1px 5px", borderRadius: 3, fontSize: "0.88em", border: "1px solid var(--b)" }}>{m[2]}</code>);
    } else if (m[3] !== undefined) {
      parts.push(<em key={m.index} style={{ color: "var(--t2)", fontStyle: "italic" }}>{m[3]}</em>);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length === 1 ? parts[0] : <>{parts}</>;
}

// ─── Download utility ─────────────────────────────────────────────────────────

export function triggerDownload(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ─── Fenced ```csv block extraction ───────────────────────────────────────────
// Returns the contents of the first ```csv fenced block (BOM-prefixed for Excel),
// or "" if the report contains no such block.

export function extractCsvBlock(text: string): string {
  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length) {
    const fence = lines[i].trim().toLowerCase();
    if (fence === "```csv" || fence === "``` csv") {
      const out: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        out.push(lines[i]);
        i++;
      }
      return "﻿" + out.join("\r\n");
    }
    i++;
  }
  return "";
}

// ─── CSV: extract all markdown tables; fall back to section→content pairs ──────

export function markdownToCsv(text: string): string {
  const lines = text.split("\n");
  const sections: Array<{ heading: string; headers: string[]; rows: string[][] }> = [];
  let currentHeading = "";
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (/^#{1,3} /.test(line)) {
      currentHeading = line.replace(/^#+\s*/, "").trim();
    }
    if (line.startsWith("|") && i + 1 < lines.length && lines[i + 1].match(/^\|[\s\-|:]+\|$/)) {
      const headers = parseCells(line).map(stripMd);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        rows.push(parseCells(lines[i]).map(stripMd));
        i++;
      }
      sections.push({ heading: currentHeading, headers, rows });
      continue;
    }
    i++;
  }

  if (sections.length === 0) return exportReportStructure(lines);

  const out: string[] = ["﻿"];
  for (const sec of sections) {
    if (sec.heading) out.push(`# ${sec.heading}`);
    out.push(sec.headers.map(csvCell).join(","));
    for (const row of sec.rows) out.push(row.map(csvCell).join(","));
    out.push("");
  }
  return out.join("\r\n");
}

function exportReportStructure(lines: string[]): string {
  const out: string[] = ["﻿章节,内容"];
  let heading = "";
  const buf: string[] = [];
  const flush = () => {
    const content = buf.join(" ").trim();
    if (heading || content) out.push(csvCell(heading) + "," + csvCell(content));
    buf.length = 0;
  };
  for (const line of lines) {
    if (/^#{1,3} /.test(line)) {
      flush();
      heading = line.replace(/^#+\s*/, "").trim();
    } else if (line.trim() && !line.startsWith("|")) {
      buf.push(stripMd(line));
    }
  }
  flush();
  return out.join("\r\n");
}

function stripMd(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/`(.+?)`/g, "$1")
    .trim();
}

function csvCell(s: string): string {
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

// ─── HTML: standalone page with Chart.js visualizations ───────────────────────

interface HtmlChartSpec {
  id: string;
  type: "line" | "bar" | "doughnut";
  title: string;
  labels: string[];
  datasets: Array<{ label: string; data: number[]; color: string }>;
}

export interface HtmlPageMeta {
  /** H1 heading, e.g. "亚马逊打法手册". */
  title: string;
  /** Icon glyph shown before the heading. */
  icon?: string;
  /** Meta chips under the heading, e.g. ["🔍 wireless earbuds", "🌍 US"]. */
  meta?: string[];
}

export function markdownToHtmlPage(text: string, page: HtmlPageMeta): string {
  const chartSpecs: HtmlChartSpec[] = [];
  const body = buildHtmlWithCharts(text, chartSpecs);
  const date = new Date().toLocaleString("zh-CN");
  const hasCharts = chartSpecs.length > 0;
  const metaChips = (page.meta ?? []).map((m) => `<span>${esc(m)}</span>`).join("\n    ");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>${esc(page.title)}</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;font-size:14px;line-height:1.8;color:#1a1a2e;background:#fff;max-width:960px;margin:0 auto;padding:36px 28px 72px}
  .rpt-header{border-bottom:3px solid #16a34a;padding-bottom:18px;margin-bottom:32px}
  .rpt-header h1{font-size:22px;color:#1a1a2e;font-weight:700;margin-bottom:8px;display:flex;align-items:center;gap:10px}
  .rpt-header h1 .ico{color:#16a34a}
  .rpt-meta{font-size:12px;color:#6b7280;display:flex;flex-wrap:wrap;gap:6px 20px}
  h1{font-size:19px;font-weight:700;color:#1a1a2e;border-bottom:2px solid #16a34a;padding-bottom:8px;margin:28px 0 14px}
  h2{font-size:16px;font-weight:600;color:#1a1a2e;margin:22px 0 10px;padding-left:10px;border-left:3px solid #16a34a}
  h3{font-size:14px;font-weight:600;color:#374151;margin:16px 0 8px}
  p{margin:6px 0;color:#374151}
  ul,ol{padding-left:22px;margin:8px 0}
  li{margin:4px 0;line-height:1.7;color:#374151}
  ul li::marker{color:#16a34a}
  blockquote{border-left:3px solid #16a34a;padding:6px 0 6px 16px;color:#6b7280;font-style:italic;margin:10px 0;background:#f0fdf4;border-radius:0 4px 4px 0}
  hr{border:none;border-top:1px solid #e5e7eb;margin:20px 0}
  code{background:#f0fdf4;padding:2px 6px;border-radius:4px;font-family:'JetBrains Mono','Fira Code',monospace;font-size:0.87em;color:#166534;border:1px solid #bbf7d0}
  pre{background:#f8fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;overflow-x:auto;margin:14px 0}
  pre .lang{font-size:10px;color:#9ca3af;letter-spacing:.06em;margin-bottom:8px;text-transform:uppercase}
  pre code{background:none;border:none;padding:0;font-size:12.5px;color:#1f2937;white-space:pre}
  table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13px;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.06)}
  thead{background:#f0fdf4}
  th{text-align:left;padding:9px 14px;border-bottom:2px solid #16a34a;font-weight:600;color:#1a1a2e;white-space:nowrap;font-size:12px;letter-spacing:.02em}
  td{padding:8px 14px;border-bottom:1px solid #f3f4f6;color:#374151;vertical-align:top}
  tr:last-child td{border-bottom:none}
  tbody tr:hover td{background:#fafff9}
  strong{font-weight:600;color:#111827}
  em{font-style:italic;color:#6b7280}
  .chart-wrap{margin:6px 0 28px;background:#fafff9;border:1px solid #dcfce7;border-radius:10px;padding:16px 20px}
  .chart-wrap canvas{max-height:300px}
  @media print{
    body{max-width:100%;padding:20px}
    .rpt-header{page-break-after:avoid}
    table,h2,h3,.chart-wrap{page-break-inside:avoid}
    pre{white-space:pre-wrap;word-break:break-word}
  }
</style>
</head>
<body>
<div class="rpt-header">
  <h1><span class="ico">${esc(page.icon ?? "◈")}</span> ${esc(page.title)}</h1>
  <div class="rpt-meta">
    ${metaChips}${metaChips ? "\n    " : ""}<span>📅 ${date}</span>
  </div>
</div>
${body}
${hasCharts ? `<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>\n<script>${buildChartInitJs(chartSpecs)}</script>` : ""}
</body>
</html>`;
}

function parseNum(s: string | undefined): number {
  if (!s) return NaN;
  return parseFloat(s.replace(/[,，%％万亿\s]/g, "").trim());
}

function detectChartForTable(
  heading: string,
  headers: string[],
  rows: string[][],
  chartId: string,
): HtmlChartSpec | null {
  const firstColVals = rows.map((r) => (r[0] || "").trim());

  const isMonthly =
    /趋势|月度|淡旺季|月份|搜索趋势/.test(heading) ||
    firstColVals.filter((v) => /^\d{1,2}月$/.test(v)).length >= 6;
  if (isMonthly) {
    const numericCols: number[] = [];
    for (let ci = 1; ci < headers.length; ci++) {
      const vals = rows.map((r) => parseNum(r[ci]));
      if (vals.filter((v) => !isNaN(v)).length >= Math.floor(rows.length * 0.5)) numericCols.push(ci);
    }
    if (numericCols.length === 0) return null;
    const palette = ["#16a34a", "#3b82f6", "#f59e0b"];
    return {
      id: chartId,
      type: "line",
      title: heading || "月度趋势",
      labels: firstColVals,
      datasets: numericCols.slice(0, 3).map((ci, i) => ({
        label: headers[ci] || `指标${i + 1}`,
        data: rows.map((r) => parseNum(r[ci])),
        color: palette[i % 3],
      })),
    };
  }

  const isPriceDist = /价格区间|价格带|价格分布/.test(heading) || /价格区间|价格段/.test(headers[0] || "");
  if (isPriceDist) {
    const preferOrder = ["产品数", "asin数", "月销量", "月均销量", "占比", "数量", "销售额"];
    let targetCol = 1;
    for (const pref of preferOrder) {
      const idx = headers.findIndex((h) => h.toLowerCase().includes(pref.toLowerCase()));
      if (idx > 0) { targetCol = idx; break; }
    }
    return {
      id: chartId,
      type: "bar",
      title: heading || "价格区间分布",
      labels: firstColVals,
      datasets: [{ label: headers[targetCol] || "数量", data: rows.map((r) => parseNum(r[targetCol])), color: "#16a34a" }],
    };
  }

  const shareColIdx = headers.findIndex((h) => /市场份额|占比|份额/.test(h));
  if (shareColIdx > 0 && rows.length <= 12 && /市场格局|垄断|竞争格局|份额|top|TOP/.test(heading)) {
    return {
      id: chartId,
      type: "doughnut",
      title: heading || "市场份额",
      labels: firstColVals.slice(0, 8),
      datasets: [{ label: "市场份额", data: rows.slice(0, 8).map((r) => parseNum(r[shareColIdx])), color: "#16a34a" }],
    };
  }

  return null;
}

function buildHtmlWithCharts(text: string, chartSpecs: HtmlChartSpec[]): string {
  const lines = text.split("\n");
  const out: string[] = [];
  let i = 0;
  let currentHeading = "";
  let chartCounter = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.startsWith("```")) {
      const lang = esc(line.slice(3).trim());
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(esc(lines[i]));
        i++;
      }
      out.push(`<pre>${lang ? `<div class="lang">${lang}</div>` : ""}<code>${codeLines.join("\n")}</code></pre>`);
      i++;
      continue;
    }

    if (line.startsWith("|") && i + 1 < lines.length && lines[i + 1].match(/^\|[\s\-|:]+\|$/)) {
      const headers = parseCells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        rows.push(parseCells(lines[i]));
        i++;
      }
      const ths = headers.map((h) => `<th>${inlineToHtml(h)}</th>`).join("");
      const trs = rows.map((row) => "<tr>" + row.map((c) => `<td>${inlineToHtml(c)}</td>`).join("") + "</tr>").join("\n");
      out.push(`<table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table>`);

      const chartId = `chrt${chartCounter++}`;
      const spec = detectChartForTable(currentHeading, headers.map(stripMd), rows.map((r) => r.map(stripMd)), chartId);
      if (spec) {
        chartSpecs.push(spec);
        out.push(`<div class="chart-wrap"><canvas id="${spec.id}"></canvas></div>`);
      }
      continue;
    }

    if (line.startsWith("# ")) {
      currentHeading = line.slice(2).trim();
      out.push(`<h1>${inlineToHtml(line.slice(2))}</h1>`);
    } else if (line.startsWith("## ")) {
      currentHeading = line.slice(3).trim();
      out.push(`<h2>${inlineToHtml(line.slice(3))}</h2>`);
    } else if (line.startsWith("### ")) {
      currentHeading = line.slice(4).trim();
      out.push(`<h3>${inlineToHtml(line.slice(4))}</h3>`);
    } else if (line.startsWith("> ")) {
      out.push(`<blockquote><p>${inlineToHtml(line.slice(2))}</p></blockquote>`);
    } else if (line.startsWith("- ") || line.startsWith("* ")) {
      out.push(`<ul><li>${inlineToHtml(line.slice(2))}</li></ul>`);
    } else if (/^\d+\. /.test(line)) {
      out.push(`<ol><li>${inlineToHtml(line.replace(/^\d+\. /, ""))}</li></ol>`);
    } else if (line.startsWith("---") || line.startsWith("===")) {
      out.push("<hr>");
    } else if (line.trim() === "") {
      out.push("");
    } else {
      out.push(`<p>${inlineToHtml(line)}</p>`);
    }
    i++;
  }

  return out.join("\n").replace(/<\/ul>\n<ul>/g, "").replace(/<\/ol>\n<ol>/g, "");
}

function buildChartInitJs(specs: HtmlChartSpec[]): string {
  const js = (s: string) => s.replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/[\r\n]/g, "");
  const PALETTE = ["#16a34a","#3b82f6","#f59e0b","#ef4444","#8b5cf6","#06b6d4","#ec4899","#14b8a6","#f97316","#6366f1"];
  const lines: string[] = ["(function(){var C=window.Chart;if(!C)return;"];
  for (const spec of specs) {
    lines.push(`(function(){var el=document.getElementById('${spec.id}');if(!el)return;`);
    const labels = JSON.stringify(spec.labels);
    if (spec.type === "doughnut") {
      const data = JSON.stringify(spec.datasets[0]?.data ?? []).replace(/\bNaN\b/g, "0");
      const colors = JSON.stringify(PALETTE.slice(0, spec.labels.length));
      lines.push(`new C(el,{type:'doughnut',data:{labels:${labels},datasets:[{data:${data},backgroundColor:${colors},borderWidth:2,borderColor:'#fff'}]},options:{responsive:true,plugins:{legend:{position:'right'},title:{display:true,text:'${js(spec.title)}',font:{size:13,weight:'600'}}}}});`);
    } else if (spec.type === "bar") {
      const data = JSON.stringify(spec.datasets[0]?.data ?? []).replace(/\bNaN\b/g, "0");
      const lbl = js(spec.datasets[0]?.label ?? "");
      lines.push(`new C(el,{type:'bar',data:{labels:${labels},datasets:[{label:'${lbl}',data:${data},backgroundColor:'rgba(22,163,74,0.65)',borderColor:'#16a34a',borderWidth:1,borderRadius:4}]},options:{responsive:true,plugins:{legend:{display:false},title:{display:true,text:'${js(spec.title)}',font:{size:13,weight:'600'}}},scales:{y:{beginAtZero:true,grid:{color:'#f3f4f6'}}}}});`);
    } else {
      const datasets = spec.datasets.map((ds) => {
        const data = JSON.stringify(ds.data).replace(/\bNaN\b/g, "null");
        return `{label:'${js(ds.label)}',data:${data},borderColor:'${ds.color}',backgroundColor:'${ds.color}22',tension:0.4,fill:false,pointRadius:4,pointHoverRadius:6}`;
      }).join(",");
      lines.push(`new C(el,{type:'line',data:{labels:${labels},datasets:[${datasets}]},options:{responsive:true,plugins:{title:{display:true,text:'${js(spec.title)}',font:{size:13,weight:'600'}}},scales:{y:{grid:{color:'#f3f4f6'}},x:{grid:{display:false}}}}});`);
    }
    lines.push("})();");
  }
  lines.push("})();");
  return lines.join("\n");
}

function inlineToHtml(text: string): string {
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins}分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}天前`;
  return new Date(ts).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}
