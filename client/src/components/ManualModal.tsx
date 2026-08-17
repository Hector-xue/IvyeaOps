import { useEffect, useState } from "react";
import { api } from "../api/client";
import { MarkdownReport } from "../lib/reportFormat";
import AppDialog from "./AppDialog";

/**
 * 使用手册 —— 渲染 docs/*.md（走 /api/help）。
 *
 * 以前是铺满整屏的不透明浮层，看着和"跳到了另一个页面"没区别，看完还得找路回来。
 * 现在走统一的对话框外壳：左边是文档目录，右边是正文，背后那一页还在。
 */
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
    <AppDialog
      title="使用手册"
      icon="📖"
      onClose={onClose}
      nav={
        <nav className="app-dialog-nav-list">
          {docs.map((d) => (
            <button
              key={d.name}
              className={"app-dialog-nav-item" + (active === d.name ? " active" : "")}
              onClick={() => setActive(d.name)}
            >
              {d.title}
            </button>
          ))}
          {docs.length === 0 && <div className="app-dialog-nav-empty">加载中…</div>}
        </nav>
      }
    >
      <div className="manual-doc">
        {loading ? <div className="app-dialog-loading">加载中…</div> : <MarkdownReport text={md} />}
      </div>
    </AppDialog>
  );
}
