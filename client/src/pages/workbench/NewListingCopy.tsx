import { useCallback, useEffect, useRef, useState } from "react";
import SheetSelect from "../../components/SheetSelect";
import { marketplaceOptions } from "../../lib/marketplaces";

type JobStatus = "pending" | "uploaded" | "running" | "done" | "failed";

interface CopyJob {
  id: string;
  status: JobStatus;
  stage: number;
  stage_msg: string;
  marketplace: string;
  product_type: string;
  error?: string;
  created_at: number;
  updated_at: number;
  result?: {
    rationale?: string;
    titles?: string[];
    highlights?: string;
    bullets_a?: string[];
    bullets_b?: string[];
    search_terms?: string[];
    compliance_notes?: string[];
    raw?: string;
  };
}

const MARKETPLACES = ["US", "UK", "DE", "CA", "JP", "FR", "ES", "IT", "MX", "AU"];
const STAGE_LABELS = ["图片识别", "竞品数据", "文案生成", "完成"];

export default function NewListingCopy({ initialAsins }: { initialAsins?: string[] } = {}) {
  const [jobs, setJobs] = useState<CopyJob[]>([]);
  const [currentJob, setCurrentJob] = useState<CopyJob | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  // Form state
  const [marketplace, setMarketplace] = useState("US");
  const [productType, setProductType] = useState("");
  const [asinsRaw, setAsinsRaw] = useState(
    initialAsins && initialAsins.length > 0 ? initialAsins.join(", ") : ""
  );
  const [productNotes, setProductNotes] = useState("");
  const [images, setImages] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<string[]>([]);

  const [creating, setCreating] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const imgInputRef = useRef<HTMLInputElement | null>(null);
  const pollRef = useRef<number | null>(null);
  const [copiedIdx, setCopiedIdx] = useState<string | null>(null);

  const loadJobs = useCallback(async () => {
    try {
      const r = await fetch("/api/listing/copy-jobs", { credentials: "include" });
      if (r.ok) setJobs(await r.json());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { loadJobs(); }, [loadJobs]);

  const pollJob = useCallback(async (jobId: string) => {
    try {
      const r = await fetch(`/api/listing/copy-jobs/${jobId}`, { credentials: "include" });
      if (!r.ok) return;
      const job: CopyJob = await r.json();
      setCurrentJob(job);
      if (job.status === "running") {
        pollRef.current = window.setTimeout(() => pollJob(jobId), 2000);
      } else {
        loadJobs();
      }
    } catch { /* ignore */ }
  }, [loadJobs]);

  useEffect(() => {
    return () => { if (pollRef.current) clearTimeout(pollRef.current); };
  }, []);

  const addImages = (fileList: FileList | File[]) => {
    const arr = Array.from(fileList).filter(f => f.type.startsWith("image/")).slice(0, 10 - images.length);
    const newImages = [...images, ...arr].slice(0, 10);
    setImages(newImages);
    const previews = newImages.map(f => URL.createObjectURL(f));
    setImagePreviews(prev => {
      prev.forEach(u => URL.revokeObjectURL(u));
      return previews;
    });
  };

  const removeImage = (i: number) => {
    URL.revokeObjectURL(imagePreviews[i]);
    const newImgs = images.filter((_, idx) => idx !== i);
    const newPrevs = imagePreviews.filter((_, idx) => idx !== i);
    setImages(newImgs);
    setImagePreviews(newPrevs);
  };

  const handleStart = async () => {
    if (!productType.trim()) { alert("请填写产品类型"); return; }
    setCreating(true);
    if (pollRef.current) clearTimeout(pollRef.current);

    try {
      const asins = asinsRaw.split(/[\s,，\n]+/).map(a => a.trim().toUpperCase()).filter(a => /^[A-Z0-9]{10}$/.test(a));
      // 1. Create job
      const r = await fetch("/api/listing/copy-jobs", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ marketplace, product_type: productType, asins, product_notes: productNotes }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "创建失败");
      const { job_id } = await r.json();

      // 2. Upload images if any
      if (images.length > 0) {
        const fd = new FormData();
        images.forEach(f => fd.append("files", f));
        await fetch(`/api/listing/copy-jobs/${job_id}/images`, {
          method: "POST", credentials: "include", body: fd,
        });
      }

      // 3. Start job
      await fetch(`/api/listing/copy-jobs/${job_id}/start`, { method: "POST", credentials: "include" });

      // 4. Poll
      pollJob(job_id);
    } catch (e: any) {
      alert(e.message || "启动失败");
    } finally {
      setCreating(false);
    }
  };

  const copyText = (text: string, key: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedIdx(key);
      setTimeout(() => setCopiedIdx(null), 1500);
    });
  };

  const openHistoryJob = async (jobId: string) => {
    if (pollRef.current) clearTimeout(pollRef.current);
    try {
      const r = await fetch(`/api/listing/copy-jobs/${jobId}`, { credentials: "include" });
      if (r.ok) {
        const job = await r.json();
        setCurrentJob(job);
        setShowHistory(false);
        if (job.status === "running") pollJob(jobId);
      }
    } catch { /* ignore */ }
  };

  const deleteJob = async (jobId: string) => {
    await fetch(`/api/listing/copy-jobs/${jobId}`, { method: "DELETE", credentials: "include" });
    loadJobs();
    if (currentJob?.id === jobId) setCurrentJob(null);
  };

  const res = currentJob?.result;
  const isRunning = currentJob?.status === "running";
  const isDone = currentJob?.status === "done";
  const isFailed = currentJob?.status === "failed";

  return (
    <div style={{ padding: "16px 20px", height: "100%", overflow: "auto", boxSizing: "border-box",
      display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ fontSize: 18, fontWeight: 700, color: "var(--t)", fontFamily: "var(--font)" }}>
          新品上架文案生成
        </span>
        <span style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--font)" }}>
          · 标题 × 5 · 五点 × 2套 · Search Terms × 2
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button className="tbtn" onClick={() => { setShowHistory(v => !v); if (!showHistory) loadJobs(); }}>
            📜 历史 ({jobs.length})
          </button>
          {currentJob && (
            <button className="tbtn" onClick={() => setCurrentJob(null)}>+ 新建</button>
          )}
        </div>
      </div>

      {/* History panel */}
      {showHistory && (
        <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, overflow: "hidden" }}>
          {jobs.length === 0 ? (
            <div style={{ padding: 16, fontSize: 12, color: "var(--t3)", textAlign: "center", fontFamily: "var(--font)" }}>暂无历史记录</div>
          ) : jobs.map(j => (
            <div key={j.id} style={{ padding: "10px 14px", borderBottom: "1px solid var(--b)",
              display: "flex", gap: 10, alignItems: "center", cursor: "pointer" }}
              onClick={() => openHistoryJob(j.id)}>
              <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 3, fontFamily: "var(--font)",
                background: j.status === "done" ? "rgba(74,222,128,.15)" : j.status === "failed" ? "rgba(248,113,113,.15)" : "rgba(251,191,36,.15)",
                color: j.status === "done" ? "var(--acc)" : j.status === "failed" ? "var(--red)" : "var(--amber)" }}>
                {j.status}
              </span>
              <span style={{ fontSize: 12, color: "var(--t)", flex: 1, fontFamily: "var(--font)" }}>{j.product_type}</span>
              <span style={{ fontSize: 10, color: "var(--t3)", fontFamily: "var(--font)" }}>{j.marketplace}</span>
              <button className="tbtn" style={{ fontSize: 10, padding: "2px 6px", color: "var(--red)" }}
                onClick={e => { e.stopPropagation(); deleteJob(j.id); }}>删除</button>
            </div>
          ))}
        </div>
      )}

      {/* Input form */}
      {!currentJob && (
        <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, padding: "16px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--font)" }}>站点</label>
              <SheetSelect className="hub-input" value={marketplace} onChange={setMarketplace}
                style={{ fontSize: 12 }} flags title="选择站点"
                options={marketplaceOptions(MARKETPLACES)} />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--font)" }}>产品类型 *</label>
              <input className="hub-input" placeholder="如: bluetooth speaker, baby monitor"
                value={productType} onChange={e => setProductType(e.target.value)} />
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 14 }}>
            <label style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--font)" }}>
              竞品 ASIN（可选，每行或逗号分隔，最多10个）
            </label>
            <textarea className="hub-input" rows={2}
              placeholder="B0XXXXXX01, B0XXXXXX02"
              value={asinsRaw} onChange={e => setAsinsRaw(e.target.value)}
              style={{ resize: "vertical", fontSize: 12, fontFamily: "var(--font)" }} />
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 14 }}>
            <label style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--font)" }}>
              产品补充信息（材质/尺寸/卖点/配件等，越详细越好）
            </label>
            <textarea className="hub-input" rows={4}
              placeholder="如: 材质 ABS+硅胶, 尺寸 10x5x3cm, 防水IPX5, 续航20小时, 带充电线×1..."
              value={productNotes} onChange={e => setProductNotes(e.target.value)}
              style={{ resize: "vertical", fontSize: 12, fontFamily: "var(--font)" }} />
          </div>

          {/* Image upload */}
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 14 }}>
            <label style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--font)" }}>
              产品图片（可选，最多10张，用于AI识别卖点）
            </label>
            {imagePreviews.length > 0 && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                {imagePreviews.map((url, i) => (
                  <div key={i} style={{ position: "relative" }}>
                    <img src={url} alt="" style={{ width: 72, height: 72, objectFit: "cover",
                      borderRadius: 6, border: "1px solid var(--b)" }} />
                    <button onClick={() => removeImage(i)} style={{
                      position: "absolute", top: -4, right: -4, width: 16, height: 16,
                      background: "var(--red)", border: "none", borderRadius: "50%", cursor: "pointer",
                      color: "#fff", fontSize: 10, display: "flex", alignItems: "center", justifyContent: "center",
                    }}>×</button>
                  </div>
                ))}
              </div>
            )}
            <div
              onDragOver={e => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={e => { e.preventDefault(); setDragOver(false); addImages(e.dataTransfer.files); }}
              onClick={() => imgInputRef.current?.click()}
              style={{
                border: `2px dashed ${dragOver ? "var(--acc)" : "var(--b)"}`,
                borderRadius: 6, padding: "12px", textAlign: "center", cursor: "pointer",
                background: dragOver ? "rgba(74,222,128,.05)" : undefined, transition: "all .2s",
              }}
            >
              <span style={{ fontSize: 12, color: "var(--t3)", fontFamily: "var(--font)" }}>
                {images.length > 0 ? `已选 ${images.length} 张，继续拖放或点击添加` : "拖放或点击上传产品图片"}
              </span>
              <input ref={imgInputRef} type="file" accept="image/*" multiple style={{ display: "none" }}
                onChange={e => e.target.files && addImages(e.target.files)} />
            </div>
          </div>

          <button className="tbtn acc" onClick={handleStart} disabled={creating || !productType.trim()}
            style={{ width: "100%", padding: "10px", fontSize: 13, fontWeight: 600 }}>
            {creating ? "启动中…" : "生成文案"}
          </button>
        </div>
      )}

      {/* Job progress */}
      {currentJob && (isRunning || currentJob.status === "pending" || currentJob.status === "uploaded") && (
        <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, padding: "16px" }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--t)", marginBottom: 14, fontFamily: "var(--font)" }}>
            {currentJob.product_type} · {currentJob.marketplace}
          </div>
          {/* Stage progress */}
          <div style={{ display: "flex", gap: 0, marginBottom: 16 }}>
            {STAGE_LABELS.map((label, i) => {
              const done = i < (currentJob.stage || 0);
              const active = i === (currentJob.stage || 0);
              return (
                <div key={i} style={{ flex: 1, textAlign: "center" }}>
                  <div style={{
                    width: 28, height: 28, borderRadius: "50%", margin: "0 auto 6px",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    background: done ? "var(--acc)" : active ? "rgba(74,222,128,.2)" : "var(--bg2)",
                    border: active ? "2px solid var(--acc)" : "2px solid var(--b)",
                    fontSize: 12, fontWeight: 700,
                    color: done ? "#000" : active ? "var(--acc)" : "var(--t3)",
                  }}>
                    {done ? "✓" : i + 1}
                  </div>
                  <div style={{ fontSize: 10, color: active ? "var(--t)" : "var(--t3)", fontFamily: "var(--font)" }}>
                    {label}
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ fontSize: 12, color: "var(--t3)", textAlign: "center", fontFamily: "var(--font)" }}>
            {currentJob.stage_msg}
          </div>
        </div>
      )}

      {/* Error */}
      {isFailed && currentJob && (
        <div style={{ background: "rgba(248,113,113,.08)", border: "1px solid rgba(248,113,113,.25)",
          borderRadius: 8, padding: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--red)", marginBottom: 8 }}>生成失败</div>
          <div style={{ fontSize: 12, color: "var(--t2)", fontFamily: "var(--font)" }}>{currentJob.error}</div>
          <button className="tbtn" onClick={() => setCurrentJob(null)} style={{ marginTop: 12 }}>重新生成</button>
        </div>
      )}

      {/* Results */}
      {isDone && res && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* Rationale */}
          {res.rationale && (
            <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, padding: 14 }}>
              <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 8, fontFamily: "var(--font)" }}>策略说明</div>
              <div style={{ fontSize: 12, color: "var(--t2)", lineHeight: 1.6, fontFamily: "var(--sans)" }}>
                {res.rationale}
              </div>
            </div>
          )}

          {/* Titles */}
          {res.titles && res.titles.length > 0 && (
            <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, overflow: "hidden" }}>
              <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--b)", display: "flex",
                alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "var(--t)", fontFamily: "var(--font)" }}>
                  标题方案 ({res.titles.length}个)
                </span>
                <button className="tbtn" style={{ fontSize: 10 }}
                  onClick={() => copyText(res.titles!.join("\n\n"), "titles")}>
                  {copiedIdx === "titles" ? "✓ 已复制" : "复制全部"}
                </button>
              </div>
              {res.titles.map((t, i) => (
                <div key={i} style={{ padding: "10px 14px", borderBottom: i < res.titles!.length - 1 ? "1px solid var(--b)" : undefined,
                  display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <span style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--font)", minWidth: 20 }}>
                    T{i + 1}
                  </span>
                  <span style={{ flex: 1, fontSize: 12, color: "var(--t)", lineHeight: 1.5, fontFamily: "var(--sans)" }}>
                    {t}
                  </span>
                  <span title="字符数（含空格），亚马逊2026-07-27起标题上限75" style={{ fontSize: 10, flexShrink: 0,
                    fontFamily: "var(--font)", fontWeight: 600,
                    color: t.length > 75 ? "#dc2626" : "#16a34a" }}>
                    {t.length}/75
                  </span>
                  <button className="tbtn" style={{ fontSize: 10, flexShrink: 0 }}
                    onClick={() => copyText(t, `title-${i}`)}>
                    {copiedIdx === `title-${i}` ? "✓" : "复制"}
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Product Highlights (NEW Amazon field, effective 2026-07-27) */}
          {res.highlights && (
            <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, overflow: "hidden" }}>
              <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--b)", display: "flex",
                alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "var(--t)", fontFamily: "var(--font)" }}>
                  商品亮点 Product Highlights
                  <span style={{ marginLeft: 8, fontSize: 10, fontWeight: 600, fontFamily: "var(--font)",
                    color: res.highlights.length > 125 ? "#dc2626" : "#16a34a" }}>
                    {res.highlights.length}/125
                  </span>
                </span>
                <button className="tbtn" style={{ fontSize: 10 }}
                  onClick={() => copyText(res.highlights!, "highlights")}>
                  {copiedIdx === "highlights" ? "✓ 已复制" : "复制"}
                </button>
              </div>
              <div style={{ padding: "10px 14px", fontSize: 12, color: "var(--t)", lineHeight: 1.6, fontFamily: "var(--sans)" }}>
                {res.highlights}
              </div>
            </div>
          )}

          {/* Bullets A */}
          {res.bullets_a && res.bullets_a.length > 0 && (
            <BulletPanel title="五点描述 Set A（转化焦点）" bullets={res.bullets_a}
              copyKey="bullets_a" copiedIdx={copiedIdx} onCopy={copyText} />
          )}

          {/* Bullets B */}
          {res.bullets_b && res.bullets_b.length > 0 && (
            <BulletPanel title="五点描述 Set B（Rufus 问答焦点）" bullets={res.bullets_b}
              copyKey="bullets_b" copiedIdx={copiedIdx} onCopy={copyText} />
          )}

          {/* Search terms */}
          {res.search_terms && res.search_terms.length > 0 && (
            <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, overflow: "hidden" }}>
              <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--b)" }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "var(--t)", fontFamily: "var(--font)" }}>
                  后台 Search Terms
                </span>
              </div>
              {res.search_terms.map((st, i) => (
                <div key={i} style={{ padding: "10px 14px", borderBottom: i < res.search_terms!.length - 1 ? "1px solid var(--b)" : undefined,
                  display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <span style={{ fontSize: 11, color: "var(--t3)", fontFamily: "var(--font)", minWidth: 30 }}>
                    ST{i + 1} <span style={{ color: (st.length > 249 ? "var(--red)" : "var(--acc)") }}>
                      {st.length}字符
                    </span>
                  </span>
                  <span style={{ flex: 1, fontSize: 12, color: "var(--t2)", fontFamily: "var(--font)", wordBreak: "break-all" }}>
                    {st}
                  </span>
                  <button className="tbtn" style={{ fontSize: 10, flexShrink: 0 }}
                    onClick={() => copyText(st, `st-${i}`)}>
                    {copiedIdx === `st-${i}` ? "✓" : "复制"}
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Compliance */}
          {res.compliance_notes && res.compliance_notes.length > 0 && (
            <div style={{ background: "rgba(251,191,36,.06)", border: "1px solid rgba(251,191,36,.2)",
              borderRadius: 8, padding: 14 }}>
              <div style={{ fontSize: 11, color: "var(--amber)", marginBottom: 8, fontFamily: "var(--font)" }}>
                合规检查
              </div>
              {res.compliance_notes.map((n, i) => (
                <div key={i} style={{ fontSize: 12, color: "var(--t2)", lineHeight: 1.6, fontFamily: "var(--sans)" }}>
                  · {n}
                </div>
              ))}
            </div>
          )}

          {/* Raw fallback */}
          {!res.titles && res.raw && (
            <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, padding: 14 }}>
              <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 8, fontFamily: "var(--font)" }}>
                原始输出（JSON解析失败，显示原文）
              </div>
              <pre style={{ fontSize: 11, color: "var(--t2)", fontFamily: "var(--font)", whiteSpace: "pre-wrap",
                wordBreak: "break-all" }}>{res.raw}</pre>
            </div>
          )}

          <button className="tbtn" onClick={() => setCurrentJob(null)} style={{ alignSelf: "flex-start" }}>
            + 新建文案
          </button>
        </div>
      )}
    </div>
  );
}

function BulletPanel({ title, bullets, copyKey, copiedIdx, onCopy }: {
  title: string;
  bullets: string[];
  copyKey: string;
  copiedIdx: string | null;
  onCopy: (text: string, key: string) => void;
}) {
  return (
    <div style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 8, overflow: "hidden" }}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--b)", display: "flex",
        alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--t)", fontFamily: "var(--font)" }}>
          {title}
        </span>
        <button className="tbtn" style={{ fontSize: 10 }}
          onClick={() => onCopy(bullets.join("\n\n"), copyKey)}>
          {copiedIdx === copyKey ? "✓ 已复制" : "复制全部"}
        </button>
      </div>
      {bullets.map((b, i) => (
        <div key={i} style={{ padding: "10px 14px", borderBottom: i < bullets.length - 1 ? "1px solid var(--b)" : undefined,
          display: "flex", gap: 10, alignItems: "flex-start" }}>
          <span style={{ fontSize: 11, color: "var(--acc)", fontFamily: "var(--font)", minWidth: 20, fontWeight: 700 }}>
            {i + 1}.
          </span>
          <span style={{ flex: 1, fontSize: 12, color: "var(--t)", lineHeight: 1.6, fontFamily: "var(--sans)" }}>
            {b}
          </span>
          <button className="tbtn" style={{ fontSize: 10, flexShrink: 0 }}
            onClick={() => onCopy(b, `${copyKey}-${i}`)}>
            {copiedIdx === `${copyKey}-${i}` ? "✓" : "复制"}
          </button>
        </div>
      ))}
    </div>
  );
}
