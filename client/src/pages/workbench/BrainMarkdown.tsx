import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Lightweight Markdown renderer for the GBrain chat answers.
 * Self-contained (no agents-module coupling), styled with the workbench CSS
 * vars (--t / --t2 / --t3 / --b / --acc) so it matches the Brain page theme.
 */

function CodeBlock({ inline, className, children }: { inline?: boolean; className?: string; children?: React.ReactNode }) {
  const raw = Array.isArray(children) ? children.join("") : String(children ?? "");
  const isBlock = !inline && /[\r\n]/.test(raw.replace(/\n$/, "")) || /language-/.test(className || "");
  const [copied, setCopied] = useState(false);

  if (!isBlock) {
    return (
      <code style={{ background: "rgba(255,255,255,.06)", border: "1px solid var(--b)", borderRadius: 4, padding: "1px 5px", fontSize: "0.92em", fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" }}>
        {children}
      </code>
    );
  }

  const lang = (/language-(\w+)/.exec(className || "") || [])[1] || "";
  return (
    <div style={{ position: "relative", margin: "8px 0" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "4px 8px", background: "rgba(255,255,255,.04)", border: "1px solid var(--b)", borderBottom: "none", borderTopLeftRadius: 6, borderTopRightRadius: 6 }}>
        <span style={{ color: "var(--t3)", fontSize: "var(--fs-10)", textTransform: "uppercase", letterSpacing: 0.5 }}>{lang || "code"}</span>
        <button
          className="tbtn"
          style={{ padding: "1px 7px", fontSize: "var(--fs-10)" }}
          onClick={() => { navigator.clipboard?.writeText(raw.replace(/\n$/, "")).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }); }}
        >
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre style={{ margin: 0, padding: 10, background: "rgba(0,0,0,.22)", border: "1px solid var(--b)", borderTopLeftRadius: 0, borderTopRightRadius: 0, borderBottomLeftRadius: 6, borderBottomRightRadius: 6, overflow: "auto", fontSize: "var(--fs-12)", lineHeight: 1.55 }}>
        <code style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", color: "var(--t)" }}>{raw.replace(/\n$/, "")}</code>
      </pre>
    </div>
  );
}

const components = {
  code: CodeBlock as any,
  a: ({ href, children }: any) => <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: "var(--acc)", textDecoration: "none" }}>{children}</a>,
  p: ({ children }: any) => <p style={{ margin: "0 0 7px" }}>{children}</p>,
  ul: ({ children }: any) => <ul style={{ margin: "0 0 7px", paddingLeft: 20 }}>{children}</ul>,
  ol: ({ children }: any) => <ol style={{ margin: "0 0 7px", paddingLeft: 20 }}>{children}</ol>,
  li: ({ children }: any) => <li style={{ margin: "2px 0" }}>{children}</li>,
  h1: ({ children }: any) => <div style={{ fontSize: "var(--fs-15)", fontWeight: 700, margin: "10px 0 6px", color: "var(--t)" }}>{children}</div>,
  h2: ({ children }: any) => <div style={{ fontSize: "var(--fs-14)", fontWeight: 700, margin: "9px 0 5px", color: "var(--t)" }}>{children}</div>,
  h3: ({ children }: any) => <div style={{ fontSize: "var(--fs-13)", fontWeight: 700, margin: "8px 0 4px", color: "var(--t)" }}>{children}</div>,
  blockquote: ({ children }: any) => <blockquote style={{ margin: "7px 0", paddingLeft: 10, borderLeft: "3px solid var(--b)", color: "var(--t2)" }}>{children}</blockquote>,
  hr: () => <hr style={{ border: "none", borderTop: "1px solid var(--b)", margin: "10px 0" }} />,
  table: ({ children }: any) => <div style={{ overflowX: "auto", margin: "7px 0" }}><table style={{ borderCollapse: "collapse", width: "100%", fontSize: "var(--fs-115)" }}>{children}</table></div>,
  th: ({ children }: any) => <th style={{ border: "1px solid var(--b)", padding: "4px 8px", textAlign: "left", background: "rgba(255,255,255,.04)" }}>{children}</th>,
  td: ({ children }: any) => <td style={{ border: "1px solid var(--b)", padding: "4px 8px", verticalAlign: "top" }}>{children}</td>,
};

export default function BrainMarkdown({ children }: { children: string }) {
  return (
    <div style={{ fontSize: "var(--fs-12)", lineHeight: 1.65, color: "var(--t)", wordBreak: "break-word" }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children || ""}
      </ReactMarkdown>
    </div>
  );
}
