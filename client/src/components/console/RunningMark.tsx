/**
 * 「这个正在进行」的那枚标记 —— 一颗呼吸的点 + 一圈向外泛开的光环。
 *
 * **全站只有这一种说法。** 左栏里"这条会话正在跑"、状态坞里"这一步正在做"，问的
 *是同一件事（"它现在在动吗"），就该长成同一个样子；各画各的会让人以为那是两种
 * 不同的状态。所以形状在这里定义一次，两处都用它。
 *
 * 它是个**状态点**，不是一个小动画场景：在状态坞的计划列表里，它上下都是 ✓ 和 ○，
 * 三者是同一类东西、排在同一列，形状语言必须齐。
 *
 * 动效在 workbench.css 的 `.pulse-mark` 里（含 prefers-reduced-motion 降级：
 * 退成一颗静止的实心点 —— 信息不能只靠动画传达）。
 */
export default function RunningMark({
  className = "",
  title = "正在进行",
}: {
  /** 位置/尺寸交给调用处（左栏和状态坞各有自己的排布）。 */
  className?: string;
  title?: string;
}) {
  return (
    <span className={"pulse-mark " + className} role="status" aria-label={title} title={title}>
      <i />
      <i />
    </span>
  );
}
