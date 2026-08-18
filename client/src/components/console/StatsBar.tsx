/**
 * 会话统计条 —— 钉在输入框下方的一行。
 *
 * 它回答的是"刚才那 20 分钟到底花在哪了"：是模型在想（LLM 耗时占大头）、还是工具在跑
 * （总用时远大于 LLM 耗时）、还是上下文越滚越大（输入 tok 疯涨、缓存命中掉下来）。
 * 此前这些数只有 `prompt/completion` 两个躺在右栏信息面板里，读不出任何结论。
 *
 * **算不出来就显示「—」，绝不显示 0。** 与 CostChip 同一条判断：一个假的 0 比一个
 * 诚实的「—」危险得多 —— 用户会把"没测到"读成"没花"。
 *
 * 但"没测到就不显示这一项"和"把没测到的显示成 0"是两回事。老 agent 一项都不回报时，
 * 这里会排出「用时 — 首字 — — 缓存命中 — 输入 — · 输出 —」六个破折号钉在输入框
 * 底下——那是一整行噪声，还把真正有数的第一项（几轮几步）淹掉了。所以：**没有值的
 * 项直接不出现**，一旦模型服务商开始回报，它自己就长出来。
 *
 * **全部细账默认就摊开，而且永远只占一行。** 以前默认只露两项、其余藏在「细账」
 * 按钮后面 —— 一轮跑完想知道钱花在哪还得再点一下，而那个按钮本身也占着位置。
 * 现在整行由 DockMeta 统一按宽度缩字号（见那个组件），装不下就把字缩小，
 * 绝不换行 —— 换行会把输入框整块往上顶。
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
  if (stats.elapsedMs) items.push({
    label: "用时", value: fmtDuration(stats.elapsedMs),
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
  if (stats.firstTokenMs !== undefined) items.push({
    label: "首字", value: `${(stats.firstTokenMs / 1000).toFixed(1)}s`,
    title: "从发出到第一个字出现的平均耗时（端到端，含检索注入与首次工具调用）",
  });
  if (stats.tokensPerSec !== undefined) items.push({
    label: "", value: `${Math.round(stats.tokensPerSec)} tok/s`,
    title: "输出速度：输出 token ÷ 正文流式时长",
  });
  if (stats.cacheHitRate !== undefined) items.push({
    label: "缓存命中", value: `${Math.round(stats.cacheHitRate * 100)}%`,
    title: "命中提示词缓存的输入 token 占比。由模型服务商回报；不回报这项的模型按 0 计",
  });
  // 单位「tok」只写在提示里：这一项本来就是最长的一条，两个 tok 占掉的宽度
  // 会逼着整行再缩一档字号，而没有人会把这两个数误读成别的东西。
  if (stats.promptTokens !== undefined) items.push({
    label: "", value: `输入 ${fmtTokens(stats.promptTokens)} · 输出 ${fmtTokens(stats.completionTokens || 0)}`,
    title: "本会话累计 token 用量：输入 / 输出（由模型服务商回报）",
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
