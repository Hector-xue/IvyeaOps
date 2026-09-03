/**
 * 目标模式的验收清单 —— 「这一轮什么时候才算完」的那张明细。
 *
 * 数据全部来自 agent 运行时的**判定结果**（`goal` 事件与 final 里的 goal），
 * 不是从正文里解析出来的。这一点是这张卡存在的前提：目标模式要消灭的正是
 * "模型自己宣布完成"，那界面上的进度就更不能靠读它的总结去猜。
 *
 * 四种状态各有各的话要说：
 *   ✓ 已达成      有真实证据（跑过、验过）
 *   ✗ 未达成      做了一半、或只有声称没有证据 —— agent 会接着干
 *   ◌ 无法验证    这个环境里客观验不了（缺权限/需线下确认），不算失败
 *   · 待判定      还没轮到它
 */
import type { IvyeaGoalState } from "../../api/ivyeaAgent";

const MARK: Record<string, { sign: string; label: string; cls: string }> = {
  met: { sign: "✓", label: "已达成", cls: "is-met" },
  unmet: { sign: "✗", label: "未达成", cls: "is-unmet" },
  unverifiable: { sign: "◌", label: "无法验证", cls: "is-unverifiable" },
  pending: { sign: "·", label: "待判定", cls: "is-pending" },
};

const HEAD: Record<string, string> = {
  active: "目标模式进行中",
  achieved: "目标已达成",
  stopped: "目标未达成，已停下",
};

export default function GoalCard({ goal }: { goal: IvyeaGoalState }) {
  const criteria = goal.criteria || [];
  if (!criteria.length) return null;
  const status = goal.status || "active";
  const met = typeof goal.met === "number" ? goal.met : criteria.filter((c) => c.status === "met").length;
  const total = typeof goal.total === "number" ? goal.total : criteria.length;
  const pct = total ? Math.round((met * 100) / total) : 0;

  return (
    <div className={"cc-goal cc-goal-" + status}>
      <div className="cc-goal-head">
        <span className="cc-goal-mark">◎</span>
        <b>{HEAD[status] || HEAD.active}</b>
        <span className="cc-goal-count">{met}/{total} 条验收通过</span>
      </div>
      {goal.objective && <div className="cc-goal-objective">{goal.objective}</div>}
      <div className="cc-goal-bar" role="presentation">
        <i style={{ width: pct + "%" }} />
      </div>
      <ol className="cc-goal-list">
        {criteria.map((c) => {
          const m = MARK[c.status] || MARK.pending;
          return (
            <li key={c.index} className={"cc-goal-item " + m.cls}>
              <span className="cc-goal-sign" title={m.label}>{m.sign}</span>
              <div>
                <div className="cc-goal-text">{c.text}</div>
                {/* 判定理由要摆出来 —— "还差什么"比"还差几条"有用得多。 */}
                {c.reason && <div className="cc-goal-reason">{c.reason}</div>}
                {!c.reason && c.verify && (
                  <div className="cc-goal-verify">验证方式：{c.verify}</div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {status === "stopped" && (
        <div className="cc-goal-stop">
          {goal.stop_reason || "已停下。"}说一句「继续」我就接着做剩下的。
        </div>
      )}
      {status === "active" && (
        <div className="cc-goal-tip">没达成我会自己接着干 —— 想停就按停止。</div>
      )}
    </div>
  );
}
