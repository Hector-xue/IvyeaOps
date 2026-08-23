// 第四步：交付 —— 完整文案导出（复制 / .txt 下载）+ 成图墙（PNG / PSD 单张下载）
// + 交付就绪清单。
import { useMemo, useState } from "react";
import { Check, Copy, Download, FileStack, FileText, Loader2, X } from "lucide-react";
import { fetchPsd, messageOf } from "./api";
import { useToast } from "./toast";
import type { ListingState } from "./useListingProject";

export default function DeliveryStep({ state }: { state: ListingState }) {
  const { project, copyResult, creativeSets, analysis } = state;
  const notify = useToast();
  const [copied, setCopied] = useState(false);
  const [preview, setPreview] = useState("");
  const [psdBusy, setPsdBusy] = useState("");

  const fullText = useMemo(() => {
    const res = copyResult;
    if (!res?.titles?.length) return "";
    return [
      `# Listing 交付 · ${project?.asin || ""} (${project?.marketplace || "US"})`,
      "",
      "=== 标题方案 (≤75字符) ===",
      ...(res.titles || []).map((t, i) => `T${i + 1}: ${t}  [${t.length}/75]`),
      "",
      ...(res.highlights ? ["=== 商品亮点 Highlights (≤125字符) ===", `${res.highlights}  [${res.highlights.length}/125]`, ""] : []),
      "=== 五点 Set A（转化焦点）===",
      ...(res.bullets_a || []).map((b, i) => `${i + 1}. ${b}`),
      "",
      "=== 五点 Set B（Rufus 问答焦点）===",
      ...(res.bullets_b || []).map((b, i) => `${i + 1}. ${b}`),
      "",
      "=== 后台 Search Terms ===",
      ...(res.search_terms || []).map((st, i) => `ST${i + 1}: ${st}`),
      ...(res.compliance_notes?.length ? ["", "=== 合规提示 ===", ...res.compliance_notes.map((n) => `- ${n}`)] : []),
    ].join("\n");
  }, [copyResult, project]);

  const finals = useMemo(() =>
    (["gallery", "aplus"] as const).flatMap((key) =>
      (creativeSets[key]?.images || [])
        .filter((item) => item.final_url)
        .map((item) => ({ ...item, deliverable: key }))),
  [creativeSets]);

  const checklist = useMemo(() => {
    const gallery = creativeSets.gallery;
    const items: { label: string; ok: boolean }[] = [
      { label: "Listing 文案已生成", ok: Boolean(copyResult?.titles?.length) },
      { label: "AI 深度分析完成", ok: Boolean(analysis.text) },
      { label: "套图方案已策划", ok: Boolean(gallery?.images?.length) },
    ];
    if (gallery?.images?.length) {
      const done = gallery.images.filter((i) => i.final_url).length;
      const qa = gallery.images.filter((i) => i.final_url && i.render_qa?.ready).length;
      const human = gallery.images.filter((i) => i.final_url && i.human_reviewed).length;
      items.push(
        { label: `成图 ${done}/${gallery.images.length}`, ok: done === gallery.images.length },
        { label: `成图质检 ${qa}/${gallery.images.length}`, ok: qa === gallery.images.length },
        { label: `人工核对 ${human}/${gallery.images.length}`, ok: human === gallery.images.length },
        { label: "整套一致性复核", ok: gallery.set_qa?.ready === true },
      );
    }
    return items;
  }, [creativeSets, copyResult, analysis]);

  /** 单张成图导出 PSD（成图层 + 隐藏的产品真值层）。服务端压缩要两三秒，给个 spinner。 */
  async function downloadPsd(deliverable: string, slot: string) {
    if (!project?.id) return;
    const key = `${deliverable}-${slot}`;
    setPsdBusy(key);
    try {
      const { blob, name } = await fetchPsd(project.id, deliverable, slot);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = name;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (error) {
      notify("error", `PSD 导出失败：${messageOf(error)}`);
    } finally {
      setPsdBusy("");
    }
  }

  function copyAll() {
    void navigator.clipboard.writeText(fullText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  function downloadTxt() {
    const blob = new Blob([fullText], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `listing_${project?.asin || "copy"}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="lst-step">
      {preview && (
        <div className="vs-lightbox" onClick={() => setPreview("")}>
          <img src={preview} alt="预览" onClick={(e) => e.stopPropagation()} />
        </div>
      )}

      <section className="card lst-section">
        <div className="lst-section-head">
          <div>
            <h3>交付就绪状态</h3>
            <p>文字与视觉双线的完成度总览。</p>
          </div>
        </div>
        <div className="lst-checklist">
          {checklist.map((item) => (
            <span key={item.label} className={`lst-check-item${item.ok ? " ok" : ""}`}>
              {item.ok ? <Check size={11} /> : <X size={11} />}{item.label}
            </span>
          ))}
        </div>
      </section>

      <section className="card lst-section">
        <div className="lst-section-head">
          <div>
            <h3>Listing 完整文案</h3>
            <p>标题 / 亮点 / 双套五点 / Search Terms 一次带走。</p>
          </div>
          <div className="lst-section-actions">
            <button className="lst-btn" onClick={copyAll} disabled={!fullText}>
              <Copy size={12} /> {copied ? "已复制" : "复制全部"}
            </button>
            <button className="lst-btn" onClick={downloadTxt} disabled={!fullText}>
              <FileText size={12} /> 下载 .txt
            </button>
          </div>
        </div>
        {fullText ? (
          <pre className="lst-analysis">{fullText}</pre>
        ) : (
          <div className="lst-empty-hint">还没有文案。请先到「② Listing 文案」生成。</div>
        )}
      </section>

      <section className="card lst-section">
        <div className="lst-section-head">
          <div>
            <h3>成图墙</h3>
            <p>已生成的套图与 A+ 图片。单张可下 PNG 或 PSD（PSD 带成图层 + 隐藏的产品真值层）；整套打包在「③ 视觉套图」里下载。</p>
          </div>
        </div>
        {finals.length ? (
          <div className="lst-final-wall">
            {finals.map((item) => {
              const key = `${item.deliverable}-${item.slot}`;
              return (
                <figure key={key} onClick={() => setPreview(item.final_url!)}>
                  <img src={item.final_url!} alt={item.role} />
                  <figcaption>
                    <span>{item.deliverable === "aplus" ? "A+" : "套图"} · {item.role}</span>
                    {item.human_reviewed
                      ? <i className="ok"><Check size={10} /> 可交付</i>
                      : <i>草稿</i>}
                  </figcaption>
                  <div className="lst-final-actions" onClick={(e) => e.stopPropagation()}>
                    <a href={item.final_url!} download title="下载 PNG"><Download size={11} /> PNG</a>
                    <button type="button" disabled={psdBusy === key}
                      title="下载 PSD（成图 + 隐藏的产品真值图层，可直接进 Photoshop）"
                      onClick={() => void downloadPsd(String(item.deliverable), String(item.slot || ""))}>
                      {psdBusy === key
                        ? <><Loader2 size={11} className="spin" /> 导出中</>
                        : <><FileStack size={11} /> PSD</>}
                    </button>
                  </div>
                </figure>
              );
            })}
          </div>
        ) : (
          <div className="lst-empty-hint">还没有成图。请先到「③ 视觉套图」策划并生成。</div>
        )}
      </section>
    </div>
  );
}
