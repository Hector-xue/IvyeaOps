import { useCallback, useRef, useState } from "react";
import { streamListingRewrite, type SseEvent } from "../../../api/deepAnalysis";
import AnalysisSkeleton from "./AnalysisSkeleton";
import SheetSelect from "../../../components/SheetSelect";
import { marketplaceOptions } from "../../../lib/marketplaces";
import DeepAnalysisPanel, { type DeepAnalysisType } from "../../../components/DeepAnalysisPanel";
import { MarkdownReport, triggerDownload } from "../../../lib/reportFormat";

const MARKETPLACES = ["US", "UK", "DE", "CA", "JP"];

const REWRITE_ANALYSIS_TYPES: readonly DeepAnalysisType[] = [
  {
    id: "polish", icon: "◈", label: "继续打磨",
    promptFn: (q, mkt, report) =>
      `以下是为「${q}」（${mkt} 站）改写的 Listing 文案：\n\n${report}\n\n请进一步打磨：\n1. 关键词覆盖与可读性的平衡优化\n2. 标题/五点的措辞精修（更有转化力）\n3. 合规风险词排查与替换\n4. 给出修改前后的对比要点`,
  },
  {
    id: "variant", icon: "⬡", label: "A/B 变体",
    promptFn: (q, mkt, report) =>
      `以下是为「${q}」（${mkt} 站）改写的 Listing 文案：\n\n${report}\n\n请生成可用于 A/B 测试的变体：\n1. 两套不同卖点侧重的标题变体\n2. 对应的五点描述差异\n3. 每套变体的目标人群与假设\n4. 建议的测试指标与周期`,
  },
];
const FIELD_OPTIONS = [
  { value: "title", label: "标题" },
  { value: "bullets", label: "五点" },
  { value: "description", label: "描述" },
  { value: "qa", label: "QA" },
];

export default function ListingRewrite() {
  const [asinsText, setAsinsText] = useState("");
  const [marketplace, setMarketplace] = useState("US");
  const [fields, setFields] = useState<string[]>(["title", "bullets"]);
  const [style, setStyle] = useState("professional");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [output, setOutput] = useState("");
  const [provider, setProvider] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const toggleField = (fv: string) => {
    setFields((prev) => (prev.includes(fv) ? prev.filter((f) => f !== fv) : [...prev, fv]));
  };

  const run = useCallback(async () => {
    const asins = asinsText
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!asins.length || loading) return;
    setLoading(true);
    setError("");
    setOutput("");
    setProvider("");
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await streamListingRewrite(
        { asins, marketplace, fields, style },
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
  }, [asinsText, marketplace, fields, style, loading]);

  return (
    <div>
      <div style={{ fontSize: "var(--fs-14)", fontWeight: 600, marginBottom: 12 }}>⊡ Listing 批量改写</div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
        <textarea
          className="market-query-input"
          value={asinsText}
          onChange={(e) => setAsinsText(e.target.value)}
          placeholder="输入 ASIN（每行一个或逗号分隔）"
          disabled={loading}
          rows={3}
          style={{ flex: "1 1 240px", resize: "vertical", fontFamily: "inherit" }}
        />
        <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: "0 0 auto" }}>
          <SheetSelect className="market-query-input" style={{ width: 100 }} value={marketplace} onChange={setMarketplace}
            flags title="选择站点" options={marketplaceOptions(MARKETPLACES)} />
          <SheetSelect className="market-query-input" style={{ width: 100 }} value={style} onChange={setStyle}
            title="选择风格" options={[
              { value: "professional", label: "专业" },
              { value: "casual", label: "轻松" },
              { value: "luxury", label: "奢华" },
            ]} />
        </div>
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        {FIELD_OPTIONS.map((f) => (
          <button
            key={f.value}
            className="tbtn"
            onClick={() => toggleField(f.value)}
            style={{
              fontSize: "var(--fs-10)",
              background: fields.includes(f.value) ? "var(--acc)" : undefined,
              color: fields.includes(f.value) ? "var(--bg)" : undefined,
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button className="market-btn market-btn-submit" onClick={run} disabled={loading || !asinsText.trim()}>
          {loading ? "改写中…" : "开始改写"}
        </button>
        {loading && (
          <button className="tbtn" onClick={() => abortRef.current?.abort()} style={{ fontSize: "var(--fs-10)" }}>
            停止
          </button>
        )}
      </div>

      {error && <div className="market-error" style={{ marginTop: 10 }}>{error}</div>}
      {loading && !output && <AnalysisSkeleton label="正在改写 Listing（约 1-2 分钟）…" sections={4} />}

      {output && (
        <div className="wb-enter" style={{ marginTop: 14 }}>
          <div style={{ display: "flex", alignItems: "center", marginBottom: 4 }}>
            {provider && <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>via {provider}</span>}
            {!loading && (
              <button
                className="tbtn"
                style={{ fontSize: "var(--fs-10)", marginLeft: "auto" }}
                onClick={() => triggerDownload(output, `listing-rewrite-${marketplace}.md`, "text/markdown")}
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
        <DeepAnalysisPanel
          types={REWRITE_ANALYSIS_TYPES}
          query={asinsText.split(/[\n,]+/)[0]?.trim() || "listing"}
          marketplace={marketplace}
          report={output}
          slug="listing-rewrite"
        />
      )}
    </div>
  );
}
