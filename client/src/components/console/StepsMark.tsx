/**
 * 「执行过程」那一栏的头部图标 —— 三行「点 + 横线」。
 *
 * ── 为什么换掉原来那两只 ──────────────────────────────────────────────────
 * 这一栏此前有两个状态、两个图标：跑完是一枚心电图（lucide 的 Activity），跑着是
 * 一株生长的常春藤。用户的评价是两只都不好看，而更实在的问题是**它们都不在说
 * 这一栏在说的事**：心电图是"有心跳"，藤是品牌意象，而这一栏的内容是
 * **一串按顺序发生的步骤**。
 *
 * 所以画的就是下面那几行本身：三行短横线，每行前面一个点。横向的笔画在 13px 下
 * 的轮廓比竖着的一串点清楚得多（竖排的点会糊成一条竖条）—— 图标最终是靠轮廓
 * 被认出来的，不是靠细节。
 *
 * 运行时三行自上而下依次亮起（一轮 1.5s），意思也直白：**它在往下走**。
 * 静止时三行同色 —— 跑完了，都在那儿了。
 *
 * 动效写在 workbench.css 的 `.steps-mark` 里（含 prefers-reduced-motion 降级），
 * 这里只有形状。
 */
export default function StepsMark({
  running = false,
  title,
}: {
  running?: boolean;
  title?: string;
}) {
  const label = title ?? (running ? "正在执行" : "执行过程");
  return (
    <svg
      className={"steps-mark" + (running ? " is-running" : "")}
      viewBox="0 0 16 16"
      role="img"
      aria-label={label}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
    >
      <title>{label}</title>
      {[4, 8, 12].map((y, i) => (
        <g className={`steps-row steps-row-${i + 1}`} key={y}>
          <circle cx="3.4" cy={y} r="1.3" fill="currentColor" stroke="none" />
          <path d={`M7 ${y}h6.2`} />
        </g>
      ))}
    </svg>
  );
}
