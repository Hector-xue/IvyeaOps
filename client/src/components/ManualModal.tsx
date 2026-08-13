import { useEffect, useState } from "react";
import { api } from "../api/client";
import { MarkdownReport } from "../lib/reportFormat";

// In-console documentation viewer — full-screen, OPAQUE (it's a document, not a
// floating panel), responsive on mobile. Renders docs/*.md from /api/help.

type DocMeta = { name: string; title: string };

export default function ManualModal({ onClose }: { onClose: () => void }) {
  const [docs, setDocs] = useState<DocMeta[]>([]);
  const [active, setActive] = useState<string>("usage");
  const [md, setMd] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get("/help/docs").then((r) => setDocs(r.data.docs || [])).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    api
      .get(`/help/doc/${active}`)
      .then((r) => setMd(r.data.markdown || ""))
      .catch(() => setMd("文档加载失败，请稍后重试。"))
      .finally(() => setLoading(false));
  }, [active]);

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 9000,
        background: "var(--bg)", // opaque — a clean document view, not see-through
        display: "flex", flexDirection: "column",
      }}
    >
      {/* Header: title + close on one row */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "12px 16px", borderBottom: "1px solid var(--b)", flex: "0 0 auto",
      }}>
        <span style={{ fontWeight: 700, color: "var(--t)", fontSize: "var(--fs-15)" }}>📖 使用手册</span>
        <button
          onClick={onClose}
          style={{
            marginLeft: "auto", border: "1px solid var(--b)", background: "var(--bg1)",
            color: "var(--t2)", borderRadius: 6, width: 30, height: 30, cursor: "pointer", fontSize: "var(--fs-14)",
          }}
          title="关闭"
        >
          ✕
        </button>
      </div>

      {/* Doc switcher: horizontally scrollable so it never wraps/overlaps on mobile */}
      <div style={{
        display: "flex", gap: 6, padding: "8px 16px", flex: "0 0 auto",
        borderBottom: "1px solid var(--b)", overflowX: "auto", whiteSpace: "nowrap",
      }}>
        {docs.map((d) => (
          <button
            key={d.name}
            onClick={() => setActive(d.name)}
            style={{
              flex: "0 0 auto", fontSize: "var(--fs-12)", padding: "5px 12px", borderRadius: 6, cursor: "pointer",
              border: "1px solid var(--b)",
              background: active === d.name ? "var(--acc)" : "var(--bg1)",
              color: active === d.name ? "#000" : "var(--t2)",
              fontWeight: active === d.name ? 600 : 400,
            }}
          >
            {d.title}
          </button>
        ))}
      </div>

      {/* Body */}
      <div style={{ flex: "1 1 auto", overflowY: "auto", padding: "16px 20px", background: "var(--bg)" }}>
        <div style={{ maxWidth: 860, margin: "0 auto" }}>
          {loading ? (
            <div style={{ color: "var(--t3)", fontSize: "var(--fs-12)" }}>加载中…</div>
          ) : (
            <MarkdownReport text={md} />
          )}
        </div>
      </div>
    </div>
  );
}
