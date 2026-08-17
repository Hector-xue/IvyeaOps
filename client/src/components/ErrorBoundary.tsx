import { Component, ErrorInfo, ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

/**
 * Catches render-time errors in the subtree and shows a minimal fallback
 * instead of a blank page. Paired with a "重试" button that resets state
 * so React can try to re-render the same tree (usually after navigating).
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[IvyeaOps] render error:", error, info.componentStack);

    // **报到服务端。** 只打 console 等于这条信息只存在于用户的浏览器里 ——
    // 用户说"知识库偶尔白屏、刷新才好"，而维护者这边什么都看不到，只能靠猜。
    // 现在它会落进服务端日志（journalctl -u ivyea-ops），带上是哪一页、什么错、
    // 组件栈。用裸 fetch 而不是 axios 实例：这一刻应用已经崩了，能少依赖一层是一层。
    try {
      fetch("/api/client-error", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        keepalive: true,          // 用户此时很可能马上刷新，keepalive 让请求活过卸载
        body: JSON.stringify({
          message: String(error?.message || error),
          stack: String(error?.stack || "").slice(0, 4000),
          component_stack: String(info?.componentStack || "").slice(0, 4000),
          path: location.pathname + location.search,
          ua: navigator.userAgent,
        }),
      }).catch(() => { /* 上报失败就算了，绝不能在错误处理里再抛一次 */ });
    } catch { /* 同上 */ }
  }

  reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div
        style={{
          padding: 24,
          maxWidth: 640,
          margin: "40px auto",
          fontSize: "var(--fs-12)",
          color: "var(--t2)",
          lineHeight: 1.7,
        }}
      >
        <div
          style={{
            fontSize: 28,
            color: "var(--amber)",
            fontFamily: "var(--font)",
            marginBottom: 10,
          }}
        >
          ⚠
        </div>
        <div style={{ fontSize: "var(--fs-13)", color: "var(--t)", marginBottom: 8 }}>
          页面渲染出错
        </div>
        <pre
          style={{
            background: "var(--bg2)",
            border: "1px solid var(--b)",
            borderRadius: "var(--r)",
            padding: 10,
            fontSize: "var(--fs-10)",
            color: "var(--t3)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 220,
            overflow: "auto",
          }}
        >
          {this.state.error.message || String(this.state.error)}
        </pre>
        <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
          <button className="tbtn" onClick={this.reset}>
            ↻ 重试
          </button>
          <button
            className="tbtn"
            onClick={() => {
              this.reset();
              window.location.href = "/";
            }}
          >
            ⌂ 返回首页
          </button>
        </div>
      </div>
    );
  }
}
