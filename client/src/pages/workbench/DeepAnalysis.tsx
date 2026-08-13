import { useState } from "react";
import KeywordCompetition from "./deep-analysis/KeywordCompetition";
import CompetitorLookup from "./deep-analysis/CompetitorLookup";
import TrafficDiagnosis from "./deep-analysis/TrafficDiagnosis";
import ReviewClustering from "./deep-analysis/ReviewClustering";
import ListingRewrite from "./deep-analysis/ListingRewrite";
import DeepHistory from "./deep-analysis/DeepHistory";

const TOOLS = [
  { key: "keyword", icon: "⊕", title: "关键词竞争分析", desc: "反查 ABA/搜索量与头部 ASIN 份额", component: KeywordCompetition },
  { key: "competitor", icon: "⊗", title: "竞品反查", desc: "竞品 ASIN 的流量词 / 排名 / 广告结构", component: CompetitorLookup },
  { key: "traffic", icon: "⊘", title: "流量异动诊断", desc: "自有 ASIN 流量下跌根因分析", component: TrafficDiagnosis },
  { key: "reviews", icon: "⊙", title: "评论聚类", desc: "差评差异化成因识别与修复建议", component: ReviewClustering },
  { key: "listing", icon: "⊡", title: "Listing 批量改写", desc: "多 ASIN 标题 / 五点 / QA 批量生成", component: ListingRewrite },
  { key: "history", icon: "≡", title: "分析历史", desc: "IvyeaAgent 生成的分析报告都保存在这里", component: DeepHistory },
] as const;

type ToolKey = (typeof TOOLS)[number]["key"];

export default function DeepAnalysis() {
  const [active, setActive] = useState<ToolKey | null>(null);
  const activeTool = TOOLS.find((t) => t.key === active);

  return (
    <div>
      <div className="ptitle">/ 深度分析</div>

      {!active || !activeTool ? (
        /* ── Tool grid ── */
        <div className="g3" style={{ marginTop: 8 }}>
          {TOOLS.map((t) => (
            <div
              key={t.key}
              className="card"
              style={{ cursor: "pointer" }}
              onClick={() => setActive(t.key)}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <span style={{ fontSize: 18 }}>{t.icon}</span>
                <span style={{ fontSize: "var(--fs-12)", fontWeight: 600, color: "var(--t)" }}>{t.title}</span>
              </div>
              <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", lineHeight: 1.5 }}>{t.desc}</div>
            </div>
          ))}
        </div>
      ) : (
        /* ── Active tool panel ── */
        <div>
          <button
            className="tbtn"
            onClick={() => setActive(null)}
            style={{ marginBottom: 12, fontSize: "var(--fs-11)" }}
          >
            ← 返回工具列表
          </button>
          <activeTool.component />
        </div>
      )}
    </div>
  );
}
