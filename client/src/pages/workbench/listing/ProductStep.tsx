// 第一步：素材与洞察 —— 采集(后台job) + 产品信息(自动保存) + 素材图 + AI 分析(后台job)。
import { useRef, useState } from "react";
import {
  Check, CloudDownload, ImagePlus, Loader2, RefreshCw, Sparkles, X,
} from "lucide-react";
import JobProgress from "./JobProgress";
import type { ListingState } from "./useListingProject";

function SaveBadge({ state }: { state: string }) {
  if (state === "saving") return <span className="lst-save-badge saving"><Loader2 size={10} className="spin" /> 保存中</span>;
  if (state === "saved") return <span className="lst-save-badge saved"><Check size={10} /> 已自动保存</span>;
  if (state === "dirty") return <span className="lst-save-badge">编辑中…</span>;
  if (state === "error") return <span className="lst-save-badge error">保存失败，请重试</span>;
  return <span className="lst-save-badge muted">修改后自动保存</span>;
}

export default function ProductStep({ state }: { state: ListingState }) {
  const {
    scrape, analysis, productInfo, setProductInfo, saveState, jobs,
    refImages, uploadRefs, deleteRef, runScrape, runAnalyze,
  } = state;
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const scraping = jobs.scrape?.status === "running";
  const analyzing = jobs.analyze?.status === "running";
  const { summary, data } = scrape;

  async function handleUpload(files: FileList | File[]) {
    setUploading(true);
    try {
      await uploadRefs(files);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="lst-step">
      {preview && (
        <div className="vs-lightbox" onClick={() => setPreview("")}>
          <img src={preview} alt="预览" onClick={(e) => e.stopPropagation()} />
        </div>
      )}

      {/* 采集 */}
      <section className="card lst-section">
        <div className="lst-section-head">
          <div>
            <h3>ASIN 采集</h3>
            <p>本机直连 Amazon 抓取标题、五点与完整主图组；被反爬拦截时自动改用本机浏览器渲染，无需安装任何东西。</p>
          </div>
          <div className="lst-section-actions">
            <button className="lst-btn primary" onClick={() => void runScrape()} disabled={scraping}>
              {scraping ? <Loader2 size={12} className="spin" /> : <CloudDownload size={12} />}
              {data ? "重新采集" : "采集 ASIN 数据"}
            </button>
          </div>
        </div>
        <JobProgress job={jobs.scrape} onRetry={() => void runScrape()} />
        {data && !scraping && (
          <div className="lst-scrape-result">
            <div className="lst-tag-row">
              <span className="tag tg">标题 {summary.title ? 1 : 0}</span>
              <span className="tag tg">五点 {summary.bullets.length}</span>
              <span className="tag tg">图片 {summary.images.length}</span>
              {summary.description && <span className="tag tg">描述 1</span>}
              {summary.source && <span className="tag">来源 {summary.source}</span>}
            </div>
            {summary.source === "sorftime" && (
              <div className="lst-callout warn">
                <strong>这次只采到 1 张白底主图。</strong>正常情况下系统先本机直连 Amazon 抓完整主图组（最多 7 张），
                被拦截时再用本机浏览器渲染一次，两条都没过才会退到这张。多半是临时被反爬拦截——
                <strong>点「重新采集」重试一两次</strong>通常就能拿到完整组。
                {summary.browserMissing && (
                  <> 另外本机没检测到 Chrome / Edge / Chromium，浏览器兜底这一层是跳过的；装上任意一个可多一次机会。</>
                )}
              </div>
            )}
            {summary.title && (
              <div className="lst-fact"><span>采集标题</span><div>{summary.title}</div></div>
            )}
            {summary.bullets.length > 0 && (
              <div className="lst-fact">
                <span>采集五点</span>
                <ol className="lst-bullets">
                  {summary.bullets.map((b, i) => <li key={i}>{b}</li>)}
                </ol>
              </div>
            )}
            {summary.images.length > 0 && (
              <div className="lst-fact">
                <span>参考图片</span>
                <div className="lst-thumb-grid">
                  {summary.images.slice(0, 12).map((src, i) => (
                    <img key={`${src}-${i}`} src={src} alt="" onClick={() => setPreview(src)} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
        {!data && !scraping && (
          <div className="lst-empty-hint">还没有采集结果。点「采集 ASIN 数据」，或直接在下方手动填写产品信息。</div>
        )}
      </section>

      {/* 产品信息（自动保存） */}
      <section className="card lst-section">
        <div className="lst-section-head">
          <div>
            <h3>产品信息</h3>
            <p>文案与套图的核心事实来源；采集结果会自动填入，可随时修正补充。</p>
          </div>
          <SaveBadge state={saveState} />
        </div>
        <div className="lst-form-grid">
          <label>
            <span>产品名称</span>
            <textarea rows={2} value={productInfo.product_name}
              onChange={(e) => setProductInfo({ product_name: e.target.value })} />
          </label>
          <label>
            <span>目标受众</span>
            <textarea rows={2} value={productInfo.target_audience}
              onChange={(e) => setProductInfo({ target_audience: e.target.value })}
              placeholder="例如：注重收纳效率的北美家庭主妇" />
          </label>
          <label>
            <span>核心卖点（每行一条）</span>
            <textarea rows={3} value={productInfo.selling_points}
              onChange={(e) => setProductInfo({ selling_points: e.target.value })} />
          </label>
          <label>
            <span>产品描述</span>
            <textarea rows={3} value={productInfo.description}
              onChange={(e) => setProductInfo({ description: e.target.value })} />
          </label>
        </div>
      </section>

      {/* 素材图 */}
      <section className="card lst-section">
        <div className="lst-section-head">
          <div>
            <h3>产品素材图</h3>
            <p>上传真实产品图（白底优先）。生成套图时优先用它做产品真值，替代采集到的竞品图。</p>
          </div>
          <span className="lst-muted">{refImages.uploaded.length} 张已上传</span>
        </div>
        {refImages.uploaded.length > 0 && (
          <div className="lst-upload-row">
            {refImages.uploaded.map((img) => (
              <div key={img.filename} className="lst-upload-thumb">
                <img src={img.url} alt="" onClick={() => setPreview(img.url)} />
                {img.white_ready && <span className="lst-white-badge" title="已通过白底检测，作为产品真值"><Check size={9} /> 白底真值</span>}
                <button title="删除" onClick={() => void deleteRef(img.filename)}><X size={10} /></button>
              </div>
            ))}
          </div>
        )}
        <div
          className={`lst-dropzone${dragOver ? " over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); void handleUpload(e.dataTransfer.files); }}
          onClick={() => fileRef.current?.click()}>
          <ImagePlus size={15} />
          <span>{uploading ? "上传中…" : "拖放或点击上传产品素材图（白底图、场景图、细节图）"}</span>
          <input ref={fileRef} type="file" accept="image/*" multiple style={{ display: "none" }}
            onChange={(e) => { if (e.target.files) void handleUpload(e.target.files); e.target.value = ""; }} />
        </div>
      </section>

      {/* AI 分析 */}
      <section className="card lst-section">
        <div className="lst-section-head">
          <div>
            <h3>AI 深度分析</h3>
            <p>视觉分析全部图片 + 技能增强的结构化分析（USP / 人群 / 场景 / 关键词 / 图片策略）。</p>
          </div>
          <div className="lst-section-actions">
            <button className="lst-btn" onClick={() => void runAnalyze()} disabled={analyzing}>
              {analyzing ? <Loader2 size={12} className="spin" /> : analysis.text ? <RefreshCw size={12} /> : <Sparkles size={12} />}
              {analysis.text ? "重新分析" : "开始 AI 分析"}
            </button>
          </div>
        </div>
        <JobProgress job={jobs.analyze} onRetry={() => void runAnalyze()} />
        {analysis.warning && <div className="lst-callout warn">{analysis.warning}</div>}
        {analysis.text && !analyzing && (
          <pre className="lst-analysis">{analysis.text}</pre>
        )}
        {!analysis.text && !analyzing && (
          <div className="lst-empty-hint">分析结果会喂给文案与套图策划。建议先完成采集或填好产品信息再分析。</div>
        )}
      </section>
    </div>
  );
}
