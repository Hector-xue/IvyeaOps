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
    // Surface the stack in the dev console; production bundles strip this
    // anyway. No remote reporting yet — add when we wire in Sentry/similar.
    console.error("[IvyeaOps] render error:", error, info.componentStack);
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
