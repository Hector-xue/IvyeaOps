import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getBudgetSummary, type BudgetStatus } from "../api/notify";
import { openSettings } from "./SettingsDialog";

/**
 * 顶栏常驻的「本月用量」。
 *
 * 为什么值得占顶栏的位置：AI 用量是这个产品里唯一会**在你不看的时候持续增长**的
 * 东西。放在设置页里，用户只有在起疑心时才会去翻；放在顶栏，他每天都会顺眼扫到，
 * 异常增长当天就能发现。
 *
 * **显示 token 而不是金额。** 金额是按公开价目表折算的估算值，天天挂在眼前会被
 * 当成账单看，而它不是；token 是实打实计出来的量，没有这层歧义。金额仍然算、
 * 仍然驱动预算告警，只是挪进悬浮说明和设置页。
 *
 * 三条实现上的约束
 * ----------------
 * * **只读缓存**。完整聚合要扫遍所有用量来源（本机实测 8.8 秒）。顶栏在每个页面
 *   都在，让它触发全盘扫描等于用户每开一个页面就给机器来一下。后端 /budget/summary
 *   只读缓存，算不出来就回 known=false。
 * * **算不出来时显示占位符，不显示 $0**。一个假的 0 比一个诚实的「—」危险得多 ——
 *   用户会以为这个月没花钱。
 * * **非管理员不渲染**。花费接口是管理员专属，普通成员拿到 403，这时候整块不出现，
 *   而不是挂一个点了报错的东西在顶栏上。
 */
/**
 * `chip`   顶栏那颗可点的芯片（原样保留，其余外壳/板块仍在用）。
 * `inline` 一段纯文字，供侧栏左下角账户行的副标题用 —— 不带边框、不占独立位置。
 *
 * 顶栏瘦身时用量挪到了账户行，但**没有挪进二级菜单**：上面那段注释讲的
 * "唯一会在你不看的时候持续增长、所以必须每天顺眼扫到"这条理由依然成立，
 * 而账户行同样是每天都在视野里的位置。挪的是位置，不是这条设计意图。
 */
export default function CostChip({ variant = "chip" }: { variant?: "chip" | "inline" } = {}) {
  const navigate = useNavigate();
  const [st, setSt] = useState<BudgetStatus | null>(null);
  const [hidden, setHidden] = useState(false);

  const poll = useCallback(async () => {
    try {
      setSt(await getBudgetSummary());
    } catch {
      // 403（非管理员）或后端没起来：都不该在顶栏留一个坏掉的东西。
      setHidden(true);
    }
  }, []);

  useEffect(() => {
    void poll();
    // 缓存 TTL 是 5 分钟，轮询比它略密一点即可；这个请求本身很轻。
    const t = window.setInterval(poll, 120_000);
    return () => window.clearInterval(t);
  }, [poll]);

  if (hidden || !st) return null;

  const fmtTokens = (n: number) =>
    n >= 1e9 ? `${(n / 1e9).toFixed(2)}B`
    : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M`
    : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n);
  const shown = st.known ? fmtTokens(st.total_tokens) : "—";
  const color =
    st.level === "exceeded" ? "var(--red)" :
    st.level === "warn" ? "var(--amber)" : "var(--t3)";

  const title = [
    st.known ? `本月已用 ${st.total_tokens.toLocaleString()} tokens` : "本月用量还在统计中",
    // 金额放在悬浮说明里：它是估算，不该在顶栏被当成账单读。
    st.known ? `按公开价目表折算约 $${st.spend_usd.toFixed(2)}（估算，不是账单）` : "",
    st.enabled ? `预算 $${st.limit_usd.toFixed(2)}（已用 ${Math.round(st.ratio * 100)}%）`
               : "未设预算 —— 点这里可以设一个月度上限",
    st.level === "exceeded" ? "已超预算：自动任务已暂停，手动操作不受影响" : "",
    st.level === "warn" ? "接近预算：到 100% 时自动任务会暂停" : "",
    // 数据新鲜度要说出来。一个不标时间的金额，用户没法判断该不该信它。
    st.known && st.age_seconds > 60 ? `数据更新于 ${Math.round(st.age_seconds / 60)} 分钟前` : "",
  ].filter(Boolean).join("\n");

  if (variant === "inline") {
    return (
      <span className="sb-acct-cost" style={{ color }} title={title}>
        {shown} tok
      </span>
    );
  }

  return (
    <button
      className="tbtn cost-chip"
      style={{ color }}
      title={title}
      onClick={() => openSettings()}
    >
      <span className="cost-chip-dot" style={{ background: color }} />
      {shown}
      <span className="cost-chip-unit">tok</span>
      {st.enabled && st.known && (
        <span className="cost-chip-ratio">{Math.round(st.ratio * 100)}%</span>
      )}
    </button>
  );
}
