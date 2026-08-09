import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getBudgetSummary, type BudgetStatus } from "../api/notify";

/**
 * 顶栏常驻的「本月已花 $X」。
 *
 * 为什么值得占顶栏的位置：AI 花费是这个产品里唯一会**在你不看的时候持续增长**的
 * 东西。放在设置页里，用户只有在起疑心时才会去翻；放在顶栏，他每天都会顺眼扫到，
 * 异常增长当天就能发现。
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
export default function CostChip() {
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

  const money = st.known ? `$${st.spend_usd.toFixed(2)}` : "—";
  const color =
    st.level === "exceeded" ? "var(--err)" :
    st.level === "warn" ? "var(--amber)" : "var(--t3)";

  const title = [
    st.known ? `本月 AI 花费约 ${money}` : "本月花费还在统计中",
    st.enabled ? `预算 $${st.limit_usd.toFixed(2)}（已用 ${Math.round(st.ratio * 100)}%）`
               : "未设预算 —— 点这里可以设一个月度上限",
    st.level === "exceeded" ? "已超预算：自动任务已暂停，手动操作不受影响" : "",
    st.level === "warn" ? "接近预算：到 100% 时自动任务会暂停" : "",
    // 数据新鲜度要说出来。一个不标时间的金额，用户没法判断该不该信它。
    st.known && st.age_seconds > 60 ? `数据更新于 ${Math.round(st.age_seconds / 60)} 分钟前` : "",
    "按公开价目表对 token 的本地估算，不是账单",
  ].filter(Boolean).join("\n");

  return (
    <button
      className="tbtn cost-chip"
      style={{ color }}
      title={title}
      onClick={() => navigate("/hub-settings")}
    >
      <span className="cost-chip-dot" style={{ background: color }} />
      {money}
      {st.enabled && st.known && (
        <span className="cost-chip-ratio">{Math.round(st.ratio * 100)}%</span>
      )}
    </button>
  );
}
