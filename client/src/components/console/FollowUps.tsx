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
}: {
  items: string[];
  onPick: (text: string) => void;
  loading?: boolean;
}) {
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
      <div className="cc-followups-head"><span className="cs-icon">⌕</span> 你可能还想了解</div>
      {items.map((q, i) => (
        <button type="button" key={i} className="cc-followup" onClick={() => onPick(q)}>
          {q}
        </button>
      ))}
    </div>
  );
}
