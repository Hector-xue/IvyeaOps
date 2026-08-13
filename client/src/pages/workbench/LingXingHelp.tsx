import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../../api/client";

export default function LingXingHelp() {
  const [md, setMd] = useState("");
  useEffect(() => {
    api.get("/lingxing/help").then((r) => setMd(r.data.markdown || "")).catch(() => setMd("# 文档加载失败"));
  }, []);
  return (
    <div className="card" style={{ padding: "16px 22px", lineHeight: 1.7, fontSize: "var(--fs-13)", color: "var(--t2)" }}>
      <div className="lx-help">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
          h1: (p) => <h1 style={{ fontSize: 20, color: "var(--t)", margin: "4px 0 10px" }} {...p} />,
          h2: (p) => <h2 style={{ fontSize: "var(--fs-15)", color: "var(--t)", margin: "20px 0 8px", borderLeft: "3px solid var(--acc)", paddingLeft: 8 }} {...p} />,
          h3: (p) => <h3 style={{ fontSize: "var(--fs-13)", color: "var(--t)", margin: "14px 0 6px" }} {...p} />,
          a: (p) => <a style={{ color: "var(--acc)" }} {...p} />,
          code: (p) => <code style={{ background: "rgba(255,255,255,.06)", border: "1px solid var(--b)", borderRadius: 4, padding: "1px 5px", fontSize: "0.92em" }} {...p} />,
          table: (p) => <table style={{ borderCollapse: "collapse", width: "100%", margin: "8px 0", fontSize: "var(--fs-12)" }} {...p} />,
          th: (p) => <th style={{ border: "1px solid var(--b)", padding: "5px 8px", textAlign: "left", background: "var(--bg2)" }} {...p} />,
          td: (p) => <td style={{ border: "1px solid var(--b)", padding: "5px 8px" }} {...p} />,
          blockquote: (p) => <blockquote style={{ borderLeft: "3px solid var(--b)", margin: "8px 0", padding: "2px 12px", color: "var(--t3)" }} {...p} />,
        }}>{md}</ReactMarkdown>
      </div>
    </div>
  );
}
