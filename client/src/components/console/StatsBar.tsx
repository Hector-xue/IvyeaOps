/**
 * 会话统计条 —— 钉在输入框下方的一行。
 *
 * 它回答的是"刚才那 20 分钟到底花在哪了"：是模型在想（LLM 耗时占大头）、还是工具在跑
 * （总用时远大于 LLM 耗时）、还是上下文越滚越大（输入 tok 疯涨、缓存命中掉下来）。
 * 此前这些数只有 `prompt/completion` 两个躺在右栏信息面板里，读不出任何结论。
 *
 * **算不出来就显示「—」，绝不显示 0。** 与 CostChip 同一条判断：一个假的 0 比一个
 * 诚实的「—」危险得多 —— 用户会把"没测到"读成"没花"。
 */
import type { TurnStats } from "../../lib/turnStats";

function fmtTokens(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${(n / 1e3).toFixed(1)}K`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

function fmtDuration(ms: number): string {
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}秒`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s - m * 60);
  if (m < 60) return `${m}分${rest}秒`;
  return `${Math.floor(m / 60)}小时${m % 60}分`;
}

export default function StatsBar({ stats }: { stats: TurnStats }) {
  if (!stats.turns) return null;

  const items: { label: string; value: string; title: string }[] = [];

  items.push({
    label: "", value: `${stats.turns} 轮 · ${stats.steps} 步`,
    title: "本会话的问答轮数与 Agent 执行的步数（不含规划/汇报类步骤）",
  });
  items.push({
    label: "用时", value: stats.elapsedMs ? fmtDuration(stats.elapsedMs) : "—",
    title: "各轮从发出到收尾的挂钟时间之和",
  });
  // LLM 耗时只有新版 agent 会回报。老 agent 连着时整项不出现 —— 显示一个「—」会让人
  // 以为是"这轮没调模型"，而真相是"这个版本测不了"。
  if (stats.llmMs !== undefined) {
    items.push({
      label: "LLM", value: fmtDuration(stats.llmMs),
      title: "纯模型时间（不含工具执行）。它和总用时的差额就是工具花掉的时间",
    });
  }
  items.push({
    label: "首字", value: stats.firstTokenMs !== undefined ? `${(stats.firstTokenMs / 1000).toFixed(1)}s` : "—",
    title: "从发出到第一个字出现的平均耗时（端到端，含检索注入与首次工具调用）",
  });
  items.push({
    label: "", value: stats.tokensPerSec !== undefined ? `${Math.round(stats.tokensPerSec)} tok/s` : "—",
    title: "输出速度：输出 token ÷ 正文流式时长",
  });
  items.push({
    label: "缓存命中", value: stats.cacheHitRate !== undefined ? `${Math.round(stats.cacheHitRate * 100)}%` : "—",
    title: "命中提示词缓存的输入 token 占比。由模型服务商回报；不回报这项的模型按 0 计",
  });
  items.push({
    label: "", value: stats.promptTokens !== undefined
      ? `输入 ${fmtTokens(stats.promptTokens)} tok · 输出 ${fmtTokens(stats.completionTokens || 0)} tok`
      : "输入 — · 输出 —",
    title: "本会话累计 token 用量（由模型服务商回报）",
  });

  return (
    <div className="cc-stats" role="status" aria-label="会话统计">
      {items.map((it, i) => (
        <span className="cc-stat" key={i} title={it.title}>
          {it.label && <span className="cc-stat-k">{it.label}</span>}
          <span className="cc-stat-v">{it.value}</span>
        </span>
      ))}
    </div>
  );
}
