import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div style={{ padding: 40, textAlign: "center", fontSize: "var(--fs-12)", color: "var(--t2)" }}>
      <div
        style={{
          fontSize: 48,
          color: "var(--t3)",
          fontFamily: "var(--font)",
          marginBottom: 12,
          letterSpacing: ".1em",
        }}
      >
        404
      </div>
      <div style={{ marginBottom: 16, color: "var(--t2)" }}>页面不存在</div>
      <Link to="/" className="tbtn" style={{ textDecoration: "none" }}>
        ⌂ 返回首页
      </Link>
    </div>
  );
}
