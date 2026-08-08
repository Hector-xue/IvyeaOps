/**
 * 「你可能还想了解」—— MyLevis 每轮收尾后那几张跟进建议卡。
 *
 * 纯展示组件；建议怎么来的（一次廉价的无工具文本轮次）由任务台负责，
 * 拿不到就整块不渲染，绝不摆假选项。
 */
export default function FollowUps({
  items,
  onPick,
  loading,
  enabled = true,
  onToggle,
}: {
  items: string[];
  onPick: (text: string) => void;
  loading?: boolean;
  /** 关掉之后每轮省一次模型调用。 */
  enabled?: boolean;
  onToggle?: (next: boolean) => void;
}) {
  // 关掉时留一个极轻的开关入口 —— 整块消失的话，用户就再也找不到怎么打开了。
  if (!enabled) {
    return onToggle ? (
      <div className="cc-followups off">
        <button type="button" className="cc-followup-toggle" onClick={() => onToggle(true)}>
          开启「你可能还想了解」
        </button>
      </div>
    ) : null;
  }
  if (loading) {
    return (
      <div className="cc-followups">
        <div className="cc-followups-head"><span className="cs-icon">⌕</span> 你可能还想了解</div>
        <div className="skeleton line lg" />
        <div className="skeleton line md" />
      </div>
    );
  }
  if (!items.length) return null;

  return (
    <div className="cc-followups">
      <div className="cc-followups-head">
        <span className="cs-icon">⌕</span> 你可能还想了解
        {onToggle && (
          <button
            type="button"
            className="cc-followup-toggle"
            title="每轮会额外跑一次不带工具的轻量模型调用来生成这几条建议。关掉可以省下这次开销。"
            onClick={() => onToggle(false)}
          >关闭</button>
        )}
      </div>
      {items.map((q, i) => (
        <button type="button" key={i} className="cc-followup" onClick={() => onPick(q)}>
          {q}
        </button>
      ))}
    </div>
  );
}
