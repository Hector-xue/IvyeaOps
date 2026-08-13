// 优化建议 = 规则引擎（确定性候选）+ AI 分析（LLM 建议）两个子视图。
// 两边都支持勾选多条 → 批量生成工单（后台复核）→ 一键跳到「工单」tab。
import { useState } from "react";
import LingXingOptimizer from "./LingXingOptimizer";
import LingXingAutomation from "./LingXingAutomation";

export default function LingXingSuggest({ storeSid, onGoTickets }: {
  storeSid?: string; onGoTickets: (firstId?: string) => void;
}) {
  const [sub, setSub] = useState<"engine" | "ai">("engine");
  return (
    <div>
      <div style={{ display: "flex", gap: 2, marginBottom: 10 }}>
        {([["engine", "规则引擎"], ["ai", "AI 分析"]] as const).map(([v, l]) => (
          <button key={v} onClick={() => setSub(v)} style={{
            padding: "5px 12px", fontSize: "var(--fs-11)", border: "1px solid var(--b)", cursor: "pointer",
            borderRadius: v === "engine" ? "4px 0 0 4px" : "0 4px 4px 0",
            background: sub === v ? "var(--bg2)" : "transparent",
            color: sub === v ? "var(--t)" : "var(--t3)", fontWeight: sub === v ? 600 : 400,
          }}>{l}</button>
        ))}
        <span style={{ fontSize: "var(--fs-10)", color: "var(--t3)", alignSelf: "center", marginLeft: 8 }}>
          {sub === "engine" ? "确定性规则算出的候选（可审计，不经过大模型）" : "大模型基于聚合指标给的活动级建议"}
        </span>
      </div>
      <div key={sub} className="wb-enter">
        {sub === "engine"
          ? <LingXingOptimizer storeSid={storeSid} onGoTickets={onGoTickets} />
          : <LingXingAutomation onGoTickets={onGoTickets} />}
      </div>
    </div>
  );
}
