import { useRef, useState } from "react";
import { trafficDiagnosis } from "../../../api/deepAnalysis";
import AnalysisSkeleton from "./AnalysisSkeleton";
import SheetSelect from "../../../components/SheetSelect";
import { marketplaceOptions } from "../../../lib/marketplaces";
import { triggerDownload } from "../../../lib/reportFormat";
import { TrafficResult } from "./resultViews";

const MARKETPLACES = ["US", "UK", "DE", "CA", "JP", "FR", "ES", "IT", "MX", "AU"];

export default function TrafficDiagnosis() {
  const [asin, setAsin] = useState("");
  const [country, setCountry] = useState("US");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<any>(null);
  const abortRef = useRef<AbortController | null>(null);

  const run = async () => {
    if (!asin.trim() || loading) return;
    setLoading(true);
    setError("");
    setResult(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await trafficDiagnosis({ asin: asin.trim(), country }, ctrl.signal);
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
      <div style={{ fontSize: "var(--fs-14)", fontWeight: 600, marginBottom: 12 }}>⊘ 流量异动诊断</div>

      <div className="market-input-row" style={{ flexWrap: "wrap" }}>
        <input
          className="market-query-input"
          value={asin}
          onChange={(e) => setAsin(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="输入 ASIN，如 B0XXXXXXXX"
          disabled={loading}
        />
        <SheetSelect className="market-query-input" style={{ flex: "1 1 80px", minWidth: 0 }} value={country} onChange={setCountry}
          flags title="选择国家" options={marketplaceOptions(MARKETPLACES)} />
        <button className="market-btn market-btn-submit" onClick={run} disabled={loading || !asin.trim()}>
          {loading ? "诊断中…" : "开始诊断"}
        </button>
        {loading && (
          <button className="tbtn" onClick={() => abortRef.current?.abort()} style={{ fontSize: "var(--fs-10)" }}>
            停止
          </button>
        )}
      </div>

      {error && <div className="market-error" style={{ marginTop: 10 }}>{error}</div>}
      {loading && <AnalysisSkeleton label="正在分析流量数据…" />}

      {result && (
        <div className="wb-enter" style={{ marginTop: 14 }}>
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 6 }}>
            <button
              className="tbtn"
              style={{ fontSize: "var(--fs-10)" }}
              onClick={() => triggerDownload(JSON.stringify(result, null, 2), `traffic-${asin.trim()}-${country}.json`, "application/json")}
            >
              ⬇ 下载 JSON
            </button>
          </div>
          <TrafficResult data={result} asin={asin} />
        </div>
      )}
    </div>
  );
}
