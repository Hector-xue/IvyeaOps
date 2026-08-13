/**
 * 分析类页面的结果骨架屏：在「开始分析 → 结果返回」的等待期，
 * 用接近结果形状的占位（标题 + 多段线条）替代生硬的转圈文字，
 * 配合 .wb-enter 让真实结果平滑淡入。纯 CSS 变量驱动，16 主题自适配。
 */
export default function AnalysisSkeleton({
  label = "正在分析…",
  sections = 3,
}: { label?: string; sections?: number }) {
  return (
    <div
      className="card wb-enter"
      style={{ marginTop: 14, background: "var(--bg2)" }}
      aria-busy="true"
      aria-live="polite"
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <span className="pulse-spin" style={{ fontSize: "var(--fs-12)", color: "var(--acc)" }}>◌</span>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--t2)" }}>{label}</span>
      </div>
      {Array.from({ length: Math.max(1, sections) }).map((_, s) => (
        <div key={s} style={{ marginBottom: 14 }}>
          <div className="skeleton line sm" />
          <div className="skeleton line lg" />
          <div className="skeleton line md" />
        </div>
      ))}
    </div>
  );
}
