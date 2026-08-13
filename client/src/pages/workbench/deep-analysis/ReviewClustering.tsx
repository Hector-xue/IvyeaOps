import { useCallback, useRef, useState } from "react";
import { streamReviews, type SseEvent } from "../../../api/deepAnalysis";
import AnalysisSkeleton from "./AnalysisSkeleton";
import SheetSelect from "../../../components/SheetSelect";
import { marketplaceOptions } from "../../../lib/marketplaces";
import DeepAnalysisPanel, { type DeepAnalysisType } from "../../../components/DeepAnalysisPanel";
import { MarkdownReport, triggerDownload } from "../../../lib/reportFormat";

const MARKETPLACES = ["US", "UK", "DE", "CA", "JP"];

const REVIEW_ANALYSIS_TYPES: readonly DeepAnalysisType[] = [
  {
    id: "improve", icon: "◈", label: "产品改进",
    promptFn: (asin, mkt, report) =>
      `以下是 ASIN ${asin}（${mkt} 站）的评论聚类分析：\n\n${report}\n\n请据此给出产品改进方案：\n1. 按频率/严重度排序的痛点改进优先级\n2. 每项改进的可行性与成本预估\n3. 差评转化为卖点的机会\n4. 投入产出最高的前 3 项首批改进`,
  },
  {
    id: "listing", icon: "⬡", label: "Listing 卖点",
    promptFn: (asin, mkt, report) =>
      `以下是 ASIN ${asin}（${mkt} 站）的评论聚类分析：\n\n${report}\n\n请把评论洞察转化为 Listing 卖点与文案：\n1. 标题应强调的核心卖点（基于好评高频词）\n2. 五点描述的卖点排序与措辞\n3. 针对差评顾虑的预防性文案\n4. A+/图片应呈现的使用场景`,
  },
  {
    id: "compare", icon: "▦", label: "竞品对比",
    promptFn: (asin, mkt, report) =>
      `以下是 ASIN ${asin}（${mkt} 站）的评论聚类分析：\n\n${report}\n\n请基于评论洞察做竞品对比：\n1. 本品在评论中暴露的相对劣势\n2. 用户最在意但市场未满足的需求\n3. 差异化定位建议\n4. 可抢占的细分人群`,
  },
];

export default function ReviewClustering() {
  const [asin, setAsin] = useState("");
  const [country, setCountry] = useState("US");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [output, setOutput] = useState("");
  const [provider, setProvider] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(async () => {
    if (!asin.trim() || loading) return;
    setLoading(true);
    setError("");
    setOutput("");
    setProvider("");
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await streamReviews(
        { asin: asin.trim(), country },
        (evt: SseEvent) => {
          if (evt.type === "token") {
            setOutput((prev) => prev + evt.text);
            setProvider(evt.provider);
          } else if (evt.type === "error") {
            setError(evt.detail);
          }
        },
        ctrl.signal,
      );
    } catch (e: any) {
      if (e.name !== "AbortError") setError(e?.message || "请求失败");
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }, [asin, country, loading]);

  return (
    <div>
      <div style={{ fontSize: "var(--fs-14)", fontWeight: 600, marginBottom: 12 }}>⊙ 评论聚类分析</div>

      <div className="market-input-row" style={{ flexWrap: "wrap" }}>
        <input
          className="market-query-input"
          value={asin}
          onChange={(e) => setAsin(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="输入 ASIN"
          disabled={loading}
        />
        <SheetSelect className="market-query-input" style={{ flex: "1 1 80px", minWidth: 0 }} value={country} onChange={setCountry}
          flags title="选择国家" options={marketplaceOptions(MARKETPLACES)} />
        <button className="market-btn market-btn-submit" onClick={run} disabled={loading || !asin.trim()}>
          {loading ? "分析中…" : "开始分析"}
        </button>
        {loading && (
          <button className="tbtn" onClick={() => abortRef.current?.abort()} style={{ fontSize: "var(--fs-10)" }}>
            停止
          </button>
        )}
      </div>

      {error && <div className="market-error" style={{ marginTop: 10 }}>{error}</div>}
      {loading && !output && <AnalysisSkeleton label="正在采集评论并聚类分析（约 1-2 分钟）…" sections={4} />}

      {output && (
        <div className="wb-enter" style={{ marginTop: 14 }}>
          <div style={{ display: "flex", alignItems: "center", marginBottom: 4 }}>
            {provider && <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>via {provider}</span>}
            {!loading && (
              <button
                className="tbtn"
                style={{ fontSize: "var(--fs-10)", marginLeft: "auto" }}
                onClick={() => triggerDownload(output, `reviews-${asin.trim()}-${country}.md`, "text/markdown")}
              >
                ⬇ 下载 Markdown
              </button>
            )}
          </div>
          <div className="card market-report-body" style={{ background: "var(--bg2)" }}>
            <MarkdownReport text={output} />
          </div>
        </div>
      )}

      {output && !loading && (
        <DeepAnalysisPanel types={REVIEW_ANALYSIS_TYPES} query={asin} marketplace={country} report={output} slug={asin} />
      )}
    </div>
  );
}
