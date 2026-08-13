import { useRef, useState } from "react";
import { keywordCompetition } from "../../../api/deepAnalysis";
import AnalysisSkeleton from "./AnalysisSkeleton";
import SheetSelect from "../../../components/SheetSelect";
import { marketplaceOptions } from "../../../lib/marketplaces";
import { triggerDownload } from "../../../lib/reportFormat";
import { KeywordResult } from "./resultViews";

const MARKETPLACES = ["US", "UK", "DE", "CA", "JP", "FR", "ES", "IT", "MX", "AU"];

export default function KeywordCompetition() {
  const [keyword, setKeyword] = useState("");
  const [country, setCountry] = useState("US");
  const [asin, setAsin] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<any>(null);
  const abortRef = useRef<AbortController | null>(null);

  const run = async () => {
    if (!keyword.trim() || loading) return;
    setLoading(true);
    setError("");
    setResult(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await keywordCompetition({ keyword: keyword.trim(), country, asin: asin.trim() }, ctrl.signal);
      setResult(res.data);
    } catch (e: any) {
      if (e?.name !== "CanceledError" && e?.code !== "ERR_CANCELED") setError(e?.message || "请求失败");
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  };

  return (
    <div>
      <div style={{ fontSize: "var(--fs-14)", fontWeight: 600, marginBottom: 12 }}>⊕ 关键词竞争分析</div>

      <div className="market-input-row" style={{ flexWrap: "wrap" }}>
        <input
          className="market-query-input"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="输入关键词，如: trail camera"
          disabled={loading}
        />
        <input
          className="market-query-input"
          style={{ flex: "1 1 130px", minWidth: 0, fontFamily: "monospace" }}
          value={asin}
          maxLength={10}
          onChange={(e) => setAsin(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="对标 ASIN（可选）"
          disabled={loading}
        />
        <SheetSelect
          className="market-query-input"
          style={{ flex: "1 1 80px", minWidth: 0 }}
          value={country}
          onChange={setCountry}
          flags
          title="选择国家"
          options={marketplaceOptions(MARKETPLACES)}
        />
        <button className="market-btn market-btn-submit" onClick={run} disabled={loading || !keyword.trim()}>
          {loading ? "分析中…" : "开始分析"}
        </button>
        {loading && (
          <button className="tbtn" onClick={() => abortRef.current?.abort()} style={{ fontSize: "var(--fs-10)" }}>
            停止
          </button>
        )}
      </div>

      {error && <div className="market-error" style={{ marginTop: 10 }}>{error}</div>}
      {loading && <AnalysisSkeleton label="正在分析关键词数据…" />}

      {result && (
        <div className="wb-enter" style={{ marginTop: 14 }}>
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 6 }}>
            <button
              className="tbtn"
              style={{ fontSize: "var(--fs-10)" }}
              onClick={() => triggerDownload(JSON.stringify(result, null, 2), `keyword-${keyword.trim()}-${country}.json`, "application/json")}
            >
              ⬇ 下载 JSON
            </button>
          </div>
          <KeywordResult data={result} keyword={keyword} />
        </div>
      )}
    </div>
  );
}
