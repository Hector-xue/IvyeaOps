/**
 * 「思考中」的那枚小图标 —— 三个点依次亮。
 *
 * 换掉的是一株生长的常春藤。藤是品牌意象（官网那条"移动鼠标让常春藤生长"的动效），
 * 放在这儿好看是一回事，**说不说得清这一刻发生了什么**是另一回事：一株在长的植物
 * 既可以是"在想"，也可以是"在跑"、"在等"。而三个点是全世界通用的"正在想 / 正在说"。
 *
 * 形状和动效都跟 `StepsMark` 一套：点、依次亮。两枚图标在同一栏里上下相邻，
 * 用同一种笔画和同一种节奏，看起来才像一家的东西，而不是各画各的。
 *
 * 尺寸跟着字号走（em），动效在 workbench.css 的 `.thinking-dots` 里（含
 * prefers-reduced-motion 降级）。
 */
export default function ThinkingDots({ title = "正在思考" }: { title?: string }) {
  return (
    <svg className="thinking-dots" viewBox="0 0 16 16" role="img" aria-label={title}>
      <title>{title}</title>
      {[3.2, 8, 12.8].map((cx, i) => (
        <circle className={`td-dot td-dot-${i + 1}`} key={cx} cx={cx} cy="8" r="1.5" />
      ))}
    </svg>
  );
}
