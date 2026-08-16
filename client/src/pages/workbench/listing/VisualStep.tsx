// 第三步：视觉套图 —— 策划 / 逐张与整套生成 / 质检复核，全部后台 job 驱动。
// 布局沿用 Visual Studio 的 storyboard + inspector（vs-* 样式体系），
// 数据流重做：编排在服务端，刷新页面不丢任何进行中的工作。
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Check, ChevronDown, Download, Image as ImageIcon, Info,
  Layers3, Loader2, Maximize2, RefreshCw, ShieldCheck, Sparkles, WandSparkles,
} from "lucide-react";
import JobProgress from "./JobProgress";
import { useToast } from "./toast";
import type { ListingState } from "./useListingProject";
import type { Plan, PlanImage } from "./types";

const SHOT_LABELS: Record<string, string> = {
  white_main: "白底主图",
  hero_feature: "核心利益",
  lifestyle: "真实使用",
  detail: "细节特写",
  comparison: "对比证明",
  specs: "规格 / 兼容",
  in_box: "包装清单",
  trust: "信任收口",
  aplus_banner: "A+ 品牌首屏",
};
const PRESENCE_LABELS: Record<string, string> = {
  hero: "产品主体",
  supporting: "产品同台",
  environmental: "场景叙事",
  absent: "成果/信息",
};
const LAYOUT_LABELS: Record<string, string> = {
  white_main: "白底主图",
  poster_hero: "海报卖点",
  result_showcase: "成果展示",
  split_compare: "对比拼图",
  spec_grid: "规格网格",
  scenario_mosaic: "场景马赛克",
  human_context: "真人使用",
  in_box_flatlay: "开箱平铺",
  detail_macro: "细节微距",
  trust_close: "信任收口",
};
const ZONES: [string, string][] = [
  ["top-left", "左上"], ["top-center", "上中"], ["top-right", "右上"],
  ["center-left", "左中"], ["center-right", "右中"],
  ["bottom-left", "左下"], ["bottom-center", "下中"], ["bottom-right", "右下"],
];
const LAYOUTS: [string, string][] = [
  ["editorial", "编辑式留白"], ["minimal", "极简无底"], ["split", "分栏版式"],
  ["proof", "数据证明"], ["grid", "信息网格"],
];
const TONES: [string, string][] = [
  ["natural", "自然实拍"], ["studio", "克制棚拍"], ["editorial", "品牌编辑感"],
];
const LANGUAGES: [string, string][] = [
  ["en", "English"], ["de", "Deutsch"], ["fr", "Français"], ["es", "Español"],
  ["it", "Italiano"], ["ja", "日本語"], ["zh", "中文"],
];

function Pill({ tone = "neutral", children }: { tone?: string; children: React.ReactNode }) {
  return <span className={`vs-pill vs-pill-${tone}`}>{children}</span>;
}

export default function VisualStep({ state }: { state: ListingState }) {
  const notify = useToast();
  const {
    creativeSets, refImages, scrape, analysis, copyResult, jobs, renderJobs,
    runPlan, runRenderImage, runRenderSet, runReviewSet, persistPlan, applyPlan,
  } = state;

  const [deliverable, setDeliverable] = useState<"gallery" | "aplus">("gallery");
  const [count, setCount] = useState(7);
  const [visualTone, setVisualTone] = useState("natural");
  const [language, setLanguage] = useState("en");
  const [brief, setBrief] = useState("");
  const [selected, setSelected] = useState(0);
  const [preview, setPreview] = useState("");
  const [exporting, setExporting] = useState(false);

  const plan: Plan | null = creativeSets[deliverable] ?? null;
  const images = useMemo(() => plan?.images ?? [], [plan]);
  const active: PlanImage | null = images[selected] ?? null;

  useEffect(() => {
    setSelected(0);
    setCount(creativeSets[deliverable]?.images?.length || (deliverable === "aplus" ? 5 : 7));
    setBrief(creativeSets[deliverable]?.creative_brief || "");
    setLanguage(creativeSets[deliverable]?.language || "en");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deliverable]);

  const planning = jobs.plan?.status === "running";
  const setJob = jobs.render_set;
  const settingAll = setJob?.status === "running";
  const reviewing = jobs.review_set?.status === "running";

  // render_set job 的 stage 是 "card-N"，据此点亮对应卡片的 spinner。
  const setRunningIndex = useMemo(() => {
    if (!settingAll) return -1;
    const match = /^card-(\d+)$/.exec(setJob?.stage || "");
    return match ? Number(match[1]) : -1;
  }, [settingAll, setJob?.stage]);

  const cardRunning = (index: number) =>
    renderJobs[index]?.status === "running" || setRunningIndex === index;

  const completed = images.filter((item) => item.final_url).length;
  const reviewed = images.filter((item) => item.final_url && item.human_reviewed).length;
  const qaPassed = images.filter((item) => item.final_url && item.render_qa?.ready).length;
  const autoSource = plan?.product_source_url
    || refImages.uploaded.find((u) => u.white_ready)?.url
    || refImages.white_product_source || "";
  const blocked = images.filter((item) => !(item.product_source_url || autoSource)).length;
  const canDeliver = images.length > 0 && reviewed === images.length && qaPassed === images.length
    && !blocked && plan?.quality?.ready !== false && plan?.set_qa?.ready === true;

  const styleLine = plan?.style
    ? [plan.style.direction, plan.style.palette, plan.style.lighting].filter(Boolean).join(" · ")
    : "";

  const contextReady = {
    white: Boolean(refImages.white_product_source || refImages.uploaded.some((u) => u.white_ready)),
    scraped: Boolean(scrape.summary.title || scrape.summary.images.length),
    analysis: Boolean(analysis.text),
    copy: Boolean(copyResult?.titles?.length),
  };

  function patchActive(patch: Partial<PlanImage>, persist = false) {
    if (!plan || !active) return;
    const invalidates = Object.keys(patch).some((key) => [
      "eyebrow", "headline", "subline", "big_number", "callout", "supporting_text", "proof", "text_on_image",
      "text_zone", "text_pos", "layout_style", "layout", "render_prompt", "size",
    ].includes(key));
    const reviewOnly = Object.keys(patch).every((key) => key === "human_reviewed");
    const next: Plan = {
      ...plan,
      set_qa: reviewOnly ? plan.set_qa : null,
      images: images.map((item, i) => {
        if (i !== selected) return item;
        const updated = { ...item, ...patch };
        if (!persist || !invalidates || !item.final_url) return updated;
        // 改了文字/版式/提示词 → 当前成图进历史版本，需重新生成
        const version = { url: item.final_url, base_url: item.base_url || "", render_qa: item.render_qa, created_at: new Date().toISOString() };
        return { ...updated, base_url: "", final_url: "", render_qa: null, human_reviewed: false,
          versions: [...(item.versions || []), version].slice(-8) };
      }),
    };
    if (persist) void persistPlan(deliverable, next);
    else applyPlan(deliverable, next);
  }

  async function restoreVersion(versionIndex: number) {
    if (!active?.versions?.[versionIndex] || !plan) return;
    const version = active.versions[versionIndex];
    const current = active.final_url
      ? { url: active.final_url, base_url: active.base_url || "", render_qa: active.render_qa, created_at: new Date().toISOString() }
      : null;
    const versions = active.versions.filter((_, i) => i !== versionIndex);
    if (current) versions.push(current);
    patchActive({
      final_url: version.url,
      base_url: version.base_url || active.base_url,
      render_qa: version.render_qa || null,
      versions: versions.slice(-8),
      human_reviewed: false,
    }, true);
  }

  async function downloadSet() {
    const ready = images.filter((image) => image.final_url);
    if (!ready.length) return;
    setExporting(true);
    try {
      const { default: JSZip } = await import("jszip");
      const zip = new JSZip();
      for (let index = 0; index < ready.length; index += 1) {
        const image = ready[index];
        const response = await fetch(image.final_url!, { credentials: "include" });
        if (!response.ok) throw new Error(`第 ${index + 1} 张下载失败`);
        const blob = await response.blob();
        const ext = blob.type.includes("jpeg") ? "jpg" : blob.type.includes("webp") ? "webp" : "png";
        const role = String(image.role || image.slot).replace(/[\\/:*?"<>|]/g, "-");
        const draft = image.human_reviewed ? "" : "_DRAFT";
        zip.file(`${String(index + 1).padStart(2, "0")}_${role}${draft}.${ext}`, blob);
      }
      zip.file("review-manifest.json", JSON.stringify({
        deliverable,
        exported_at: new Date().toISOString(),
        commercially_reviewed: canDeliver,
        images: ready.map((image, index) => ({
          order: index + 1, slot: image.slot, role: image.role,
          human_reviewed: !!image.human_reviewed, evidence: image.evidence || "",
        })),
      }, null, 2));
      const archive = await zip.generateAsync({ type: "blob" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(archive);
      link.download = `listing_${deliverable}_${canDeliver ? "reviewed" : "draft"}.zip`;
      link.click();
      URL.revokeObjectURL(link.href);
      if (!canDeliver) notify("warn", "已导出草稿包；未人工复核的图片文件名带 DRAFT");
    } catch (error) {
      notify("error", `整套导出失败：${(error as Error).message || "未知错误"}`);
    } finally {
      setExporting(false);
    }
  }

  const activeBlocked = !!active && !(active.product_source_url || autoSource);

  return (
    <section className={`visual-studio ${deliverable === "aplus" ? "vs-aplus" : "vs-gallery"}`}>
      {preview && (
        <div className="vs-lightbox" onClick={() => setPreview("")}>
          <img src={preview} alt="大图预览" onClick={(e) => e.stopPropagation()} />
        </div>
      )}

      <header className="vs-header">
        <div>
          <div className="vs-kicker"><Sparkles size={14} /> 视觉套图</div>
          <h2>一张白底图，直出整套品牌视觉</h2>
          <p>自动吸收采集洞察与 Listing 文案；生成、质检、自动重画全在服务端后台跑，随时可以离开页面。</p>
        </div>
        {plan?.quality && (
          <div className={`vs-quality ${plan.quality.issues.some((i) => i.severity === "error") ? "has-error" : "is-ready"}`}>
            <div className="vs-quality-score">{plan.quality.score}</div>
            <div>
              <div className="vs-quality-title">策略质检</div>
              <div className="vs-quality-copy">
                {(() => {
                  const errors = plan.quality!.issues.filter((i) => i.severity === "error").length;
                  return errors ? `${errors} 项阻塞问题` : plan.quality!.issues.length ? `${plan.quality!.issues.length} 项建议` : "结构与文案检查通过";
                })()}
              </div>
            </div>
          </div>
        )}
      </header>

      <div className="vs-input-foundation">
        <div className="vs-context-sources">
          <div><span>生成依据</span><strong>前两步内容自动接入</strong></div>
          <Pill tone={contextReady.white ? "success" : "warning"}>{contextReady.white ? "白底产品图已就绪" : "缺少白底产品图"}</Pill>
          <Pill tone={contextReady.scraped ? "success" : "warning"}>采集资料{contextReady.scraped ? "已接入" : "待补充"}</Pill>
          <Pill tone={contextReady.analysis ? "success" : "warning"}>AI 洞察{contextReady.analysis ? "已接入" : "待生成"}</Pill>
          <Pill tone={contextReady.copy ? "success" : "warning"}>Listing 文案{contextReady.copy ? "已接入" : "待生成"}</Pill>
        </div>
        <label className="vs-manual-brief">
          <span><strong>手动创意需求</strong><em>优先于自动风格建议，但不覆盖产品真值和事实合规</em></span>
          <textarea value={brief} onChange={(e) => setBrief(e.target.value)} rows={3}
            placeholder="例如：面向注重品质的户外家庭；使用森林绿与暖米色；避免科技蓝；第 4 张必须强调快速安装。"
            disabled={planning} />
        </label>
      </div>

      <div className="vs-toolbar">
        <div className="vs-segment" aria-label="交付物类型">
          <button className={deliverable === "gallery" ? "active" : ""} onClick={() => setDeliverable("gallery")}>
            <ImageIcon size={14} /> 商品套图
          </button>
          <button className={deliverable === "aplus" ? "active" : ""} onClick={() => setDeliverable("aplus")}>
            <Layers3 size={14} /> A+ 模块
          </button>
        </div>
        <label className="vs-compact-field">
          <span>张数</span>
          <select value={count} onChange={(e) => setCount(Number(e.target.value))} disabled={planning}>
            {(deliverable === "aplus" ? [4, 5, 6] : [5, 6, 7, 8]).map((v) => <option key={v} value={v}>{v} 张</option>)}
          </select>
        </label>
        <label className="vs-compact-field">
          <span>视觉基调</span>
          <select value={visualTone} onChange={(e) => setVisualTone(e.target.value)} disabled={planning}>
            {TONES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="vs-compact-field">
          <span>图上语言</span>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} disabled={planning}>
            {LANGUAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <button className="vs-button secondary"
          onClick={() => void runPlan({ target_count: count, deliverable, visual_tone: visualTone, language, brief })}
          disabled={planning || settingAll}>
          {planning ? <><Loader2 className="spin" size={14} /> 策划中</> : <><WandSparkles size={14} /> {plan ? "重新策划" : "生成整套方案"}</>}
        </button>
        {plan && (
          <button className="vs-button primary" onClick={() => void runRenderSet(deliverable)} disabled={planning || settingAll}>
            {settingAll
              ? <><Loader2 className="spin" size={14} /> 生成中 {setJob?.done_count ?? 0}/{setJob?.total || images.length}</>
              : <><Sparkles size={14} /> 一键生成整套</>}
          </button>
        )}
        {plan && completed > 0 && completed < images.length && !settingAll && (
          <button className="vs-button secondary" onClick={() => void runRenderSet(deliverable, true)}>
            <RefreshCw size={14} /> 只补缺失 {images.length - completed} 张
          </button>
        )}
        {completed > 0 && (
          <button className="vs-button secondary" onClick={() => void runReviewSet(deliverable)}
            disabled={reviewing || settingAll || completed !== images.length || qaPassed !== images.length}>
            {reviewing ? <Loader2 className="spin" size={14} /> : <ShieldCheck size={14} />} 复核整套
          </button>
        )}
        {completed > 0 && (
          <button className="vs-button secondary" onClick={() => void downloadSet()} disabled={exporting}>
            {exporting ? <><Loader2 className="spin" size={14} /> 打包中</> : <><Download size={14} /> 下载整套</>}
          </button>
        )}
      </div>

      <JobProgress job={jobs.plan} />
      <JobProgress job={jobs.render_set} />
      <JobProgress job={jobs.review_set} />

      {!plan ? (
        <div className="vs-empty">
          <div className="vs-empty-icon"><Layers3 size={28} /></div>
          <h3>先编译整套图文提示词</h3>
          <p>系统会锁定白底产品真值、统一色板与设计语言，再把每张图的场景、排版和精确文案编译成最终生图指令。策划在后台运行，完成后自动出现在这里。</p>
          <div className="vs-empty-flow">
            <span>白底真值 + 前两步内容</span><b>→</b><span>整套提示词</span><b>→</b><span>图文一次直出</span><b>→</b><span>质检 + 自动重画</span>
          </div>
        </div>
      ) : (
        <>
          {plan.planner === "fallback" && (
            <div className="lst-callout warn">
              这套方案是 <strong>AI 策划失败后的兜底模板</strong>（按内置精品叙事骨架生成，可编辑可生成）。
              建议点「重新策划」再试一次拿到针对本品的定制方案。
            </div>
          )}
          <div className="vs-strategy-strip">
            <div><span>套图叙事</span><strong>{plan.story || "按购买决策顺序逐张解决疑虑"}</strong></div>
            <div><span>产品视觉身份</span><strong>{[
              plan.product_profile?.category_family, plan.product_profile?.object_behavior,
            ].filter(Boolean).join(" · ") || "已按产品形态与材质分析"}</strong></div>
            <div><span>美术与统一色系</span><strong>{styleLine || "产品定制、整套统一"}</strong></div>
            <div className="vs-delivery-state">
              <span>交付状态</span>
              <strong className={canDeliver ? "ready" : "pending"}>
                {canDeliver ? <><ShieldCheck size={14} /> 可交付</> : <><AlertTriangle size={14} /> 单图 {qaPassed}/{images.length} · 整套 {plan.set_qa?.ready ? "通过" : "待通过"} · 人审 {reviewed}/{images.length}</>}
              </strong>
            </div>
          </div>

          {/* 降级不是缺陷，但**必须说出来**。此前无视觉模型时版式逆向直接
              return []，用户看到的是一份莫名其妙缺了参考版式的方案，
              没有任何解释、也不知道配个视觉模型就能解锁。 */}
          {(plan.quality?.skipped_analyses?.length ?? 0) > 0 && (
            <div className="vs-degraded">
              <Info size={14} />
              <div>
                <strong>
                  当前视觉能力：{plan.quality?.vision_tier_label || "本地 CV 度量"}
                </strong>
                {plan.quality!.skipped_analyses!.map((note) => (
                  <p key={note.stage}>{note.message}</p>
                ))}
              </div>
            </div>
          )}

          {(plan.quality?.issues?.length ?? 0) > 0 && (
            <details className="vs-issues">
              <summary><AlertTriangle size={14} /> 策略质检发现 {plan.quality!.issues.length} 项 <ChevronDown size={14} /></summary>
              <div>
                {plan.quality!.issues.map((issue, index) => (
                  <p
                    key={`${issue.code}-${index}`}
                    className={issue.severity === "error" ? "error" : issue.severity === "info" ? "info" : "warning"}
                  >
                    {issue.severity === "error" ? "阻塞" : issue.severity === "info" ? "说明" : "建议"} · {issue.message}
                  </p>
                ))}
              </div>
            </details>
          )}

          {(plan.set_qa?.issues?.length ?? 0) > 0 && (() => {
            const setIssues = plan.set_qa!.issues!;
            const hasError = setIssues.some((issue) => issue.severity === "error");
            return (
              <details className="vs-issues" open={hasError}>
                <summary><AlertTriangle size={14} /> {hasError ? "整套质检未通过" : "整套复核提示"} <ChevronDown size={14} /></summary>
                <div>{setIssues.map((issue, index) => (
                  <p key={`${issue.code}-${index}`} className={issue.severity === "error" ? "error" : "warning"}>
                    {issue.severity === "error" ? "阻塞" : "建议"} · {issue.message}
                  </p>
                ))}</div>
              </details>
            );
          })()}

          <div className="vs-workspace">
            <div className="vs-board">
              <div className="vs-board-head">
                <div>
                  <h3>套图分镜</h3>
                  <p>{completed}/{images.length} 已处理 · {qaPassed}/{images.length} 成图质检通过 · {reviewed}/{images.length} 已人工复核{blocked ? ` · ${blocked} 张待产品真值` : ""}</p>
                </div>
              </div>
              <div className="vs-story-grid">
                {images.map((image, index) => {
                  const running = cardRunning(index);
                  const bound = !!(image.product_source_url || autoSource);
                  return (
                    <article key={image.slot} className={`vs-shot-card ${selected === index ? "selected" : ""}`}
                      onClick={() => setSelected(index)}>
                      <div className="vs-shot-preview" onDoubleClick={() => image.final_url && setPreview(image.final_url)}>
                        {image.final_url && (
                          <button type="button" className="vs-shot-zoom" title="预览大图"
                            onClick={(e) => { e.stopPropagation(); setPreview(image.final_url!); }}>
                            <Maximize2 size={15} />
                          </button>
                        )}
                        {image.final_url ? <img src={image.final_url} alt={image.role} /> : (
                          <div className="vs-shot-placeholder">
                            {running ? <Loader2 className="spin" size={20} /> : bound ? <ImageIcon size={20} /> : <AlertTriangle size={20} />}
                            {!running && bound && <small>产品真值已绑定 · 尚未生成</small>}
                            {running && <small>{renderJobs[index]?.message || setJob?.message || "生成中…"}</small>}
                          </div>
                        )}
                        {image.final_url && running && (
                          <span className="lst-card-refreshing"><Loader2 className="spin" size={12} /> 重画中</span>
                        )}
                        <span className="vs-shot-number">{String(index + 1).padStart(2, "0")}</span>
                        {image.human_reviewed && <span className="vs-reviewed"><Check size={11} /> 已核对</span>}
                      </div>
                      <div className="vs-shot-body">
                        <div className="vs-shot-title"><strong>{image.role}</strong><Pill>{SHOT_LABELS[image.shot_type] || image.shot_type}</Pill></div>
                        <div className="vs-card-meta">
                          {image.layout && <Pill>{LAYOUT_LABELS[image.layout] || image.layout}</Pill>}
                          {image.product_presence && <Pill tone={image.product_presence === "hero" ? "neutral" : "success"}>{PRESENCE_LABELS[image.product_presence] || image.product_presence}</Pill>}
                        </div>
                        <p>{image.buyer_question || image.selling_point || "待补充购买问题"}</p>
                        <div className="vs-card-meta">
                          <Pill tone={bound ? "success" : "warning"}>{bound ? "产品真值已绑定" : "缺少产品真值"}</Pill>
                          {image.final_url && image.render_qa?.ready && <Pill tone="success">{image.render_qa.manual_visual_review_required ? "硬规则通过" : "成图质检通过"}</Pill>}
                          {image.final_url && !image.render_qa?.ready && <Pill tone="warning">成图被拦截</Pill>}
                          {!!image.auto_retry_count && <Pill tone="warning">已自动重画</Pill>}
                          {image.final_url && !image.human_reviewed && <Pill tone="warning">待核对产品</Pill>}
                          {image.final_url && image.human_reviewed && <Pill tone="success">可交付</Pill>}
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            </div>

            {active && (
              <aside className="vs-inspector">
                <div className="vs-inspector-head">
                  <div>
                    <span>分镜 {selected + 1} / {images.length}</span>
                    <h3>{active.role}</h3>
                  </div>
                  <Pill>{SHOT_LABELS[active.shot_type] || active.shot_type}</Pill>
                </div>

                <div className="vs-inspector-actions">
                  <button className="vs-button primary"
                    onClick={() => void runRenderImage(deliverable, selected)}
                    disabled={cardRunning(selected) || settingAll || activeBlocked}>
                    {cardRunning(selected) ? <><Loader2 className="spin" size={14} /> 处理中</> : active.final_url
                      ? <><RefreshCw size={14} /> 重做这张</> : <><Sparkles size={14} /> 图文直出</>}
                  </button>
                  {active.final_url && <a className="vs-icon-button" href={active.final_url} download title="下载"><Download size={15} /></a>}
                </div>

                <div className={`vs-source-warning ${activeBlocked ? "is-missing" : "is-bound"}`}>
                  {activeBlocked ? <AlertTriangle size={16} /> : <Check size={16} />}
                  <div>
                    <strong>{activeBlocked ? "缺少产品真值素材" : "已锁定产品真值 · 模型整图直出"}</strong>
                    <p>系统优先使用上传白底图，否则使用通过检测的采集主图。产品外形、比例、颜色、材质、Logo、接口、配件和数量必须不变。</p>
                    {(active.product_source_url || autoSource) && (
                      <div className="vs-bound-assets">
                        <figure><img src={active.product_source_url || autoSource} alt="产品真值" /><figcaption>不可变产品真值</figcaption></figure>
                      </div>
                    )}
                  </div>
                </div>

                <div className="vs-form-section">
                  <h4>销售任务与证据</h4>
                  <label><span>购买者的问题</span><textarea value={active.buyer_question || ""} rows={2}
                    onChange={(e) => patchActive({ buyer_question: e.target.value })}
                    onBlur={(e) => patchActive({ buyer_question: e.target.value }, true)} /></label>
                  <label><span>卖点依据</span><textarea value={active.evidence || ""} rows={2}
                    onChange={(e) => patchActive({ evidence: e.target.value })}
                    onBlur={(e) => patchActive({ evidence: e.target.value }, true)} /></label>
                </div>

                {active.shot_type !== "white_main" && (
                  <div className="vs-form-section">
                    <div className="vs-section-title"><h4>图上文案 · 图片模型直出</h4><label className="vs-switch"><input type="checkbox" checked={!!active.text_on_image}
                      onChange={(e) => patchActive({ text_on_image: e.target.checked }, true)} /><span />上文字</label></div>
                    <p className="vs-copy-note">修改任一文字或版式后，当前成图会进入历史版本，需要重新生成。</p>
                    <label><span>眉题（可选）</span><input value={active.eyebrow || ""} maxLength={20}
                      onChange={(e) => patchActive({ eyebrow: e.target.value })}
                      onBlur={(e) => patchActive({ eyebrow: e.target.value }, true)} /></label>
                    <label><span>主标题 <em>{(active.headline || "").length}/42</em></span><input value={active.headline || ""} maxLength={42}
                      onChange={(e) => patchActive({ headline: e.target.value })}
                      onBlur={(e) => patchActive({ headline: e.target.value }, true)} /></label>
                    <label><span>副标题一行 <em>{(active.subline || "").length}/110</em></span><input value={active.subline || ""} maxLength={110}
                      onChange={(e) => patchActive({ subline: e.target.value })}
                      onBlur={(e) => patchActive({ subline: e.target.value }, true)} /></label>
                    <label><span>大数字锚（如 0.1s / 36MP / IP66）</span><input value={active.big_number || ""} maxLength={16}
                      onChange={(e) => patchActive({ big_number: e.target.value })}
                      onBlur={(e) => patchActive({ big_number: e.target.value }, true)} /></label>
                    <label><span>短标注（可选）</span><input value={active.callout || ""} maxLength={32}
                      onChange={(e) => patchActive({ callout: e.target.value })}
                      onBlur={(e) => patchActive({ callout: e.target.value }, true)} /></label>
                    <label><span>辅助说明 <em>{(active.supporting_text || "").length}/72</em></span><textarea value={active.supporting_text || ""} rows={2} maxLength={72}
                      onChange={(e) => patchActive({ supporting_text: e.target.value })}
                      onBlur={(e) => patchActive({ supporting_text: e.target.value }, true)} /></label>
                    <label><span>公开数据（仅短数字，如 8K/30fps）</span><input value={active.proof || ""} maxLength={20}
                      onChange={(e) => patchActive({ proof: e.target.value })}
                      onBlur={(e) => patchActive({ proof: e.target.value }, true)} /></label>
                    <div className="vs-form-row">
                      <label><span>版式</span><select value={active.layout_style || "editorial"}
                        onChange={(e) => patchActive({ layout_style: e.target.value }, true)}>
                        {LAYOUTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
                      <label><span>文字区</span><select value={active.text_zone || "top-left"}
                        onChange={(e) => patchActive({ text_zone: e.target.value, text_pos: e.target.value }, true)}>
                        {ZONES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
                    </div>
                  </div>
                )}

                <div className="vs-form-section">
                  <h4>成图验收</h4>
                  {active.final_url && (
                    <div className={`vs-render-qa ${active.render_qa?.ready ? "pass" : "fail"}`}>
                      <strong>{active.render_qa?.ready
                        ? active.render_qa.manual_visual_review_required ? "硬规则通过 · 待人工审美" : "成图质检通过"
                        : "成图质检未通过"}</strong>
                      <span>{active.render_qa?.score ?? 0} / 100</span>
                      {(active.render_qa?.issues || []).map((issue, index) => <p key={`${issue.code}-${index}`}>{issue.message}</p>)}
                    </div>
                  )}
                  <ul className="vs-criteria">
                    {(active.acceptance_criteria || []).map((item, index) => <li key={index}><Check size={12} />{item}</li>)}
                    <li><Check size={12} />产品、配件、Logo、接口和数量与实物一致</li>
                  </ul>
                  {active.final_url && (
                    <label className={`vs-review-check ${active.human_reviewed ? "checked" : ""}`}>
                      <input type="checkbox" checked={!!active.human_reviewed}
                        disabled={!active.render_qa?.ready && !active.render_qa?.manual_visual_review_required}
                        onChange={(e) => patchActive({ human_reviewed: e.target.checked }, true)} />
                      <ShieldCheck size={17} />
                      <span><strong>我已核对产品一致性</strong><small>{active.render_qa?.ready
                        ? active.render_qa?.manual_visual_review_required
                          ? "机审未运行——请务必人工核对产品外观、文字拼写后再勾选"
                          : "仅人工确认后，这张图才进入可交付状态"
                        : active.render_qa?.manual_visual_review_required
                          ? "机审不可用，人工核对通过即可交付"
                          : "必须先通过成图质检，当前不可勾选"}</small></span>
                    </label>
                  )}
                </div>

                {(active.versions?.length ?? 0) > 0 && (
                  <div className="vs-form-section">
                    <h4>历史版本</h4>
                    <div className="vs-versions">
                      {active.versions!.map((version, index) => (
                        <button key={`${version.url}-${index}`} onClick={() => void restoreVersion(index)} title={`恢复版本 ${index + 1}`}>
                          <img src={version.url} alt="" /><span>v{index + 1}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                <details className="vs-advanced">
                  <summary>高级设置 <ChevronDown size={13} /></summary>
                  <div>
                    <div className="vs-form-row">
                      <label><span>制作方式</span><input value="图片模型图文整图直出 · 高保真产品参考" disabled /></label>
                      <label><span>画布</span><input value={active.size || ""}
                        onChange={(e) => patchActive({ size: e.target.value })}
                        onBlur={(e) => patchActive({ size: e.target.value }, true)} /></label>
                    </div>
                    <label><span>最终图文生成指令</span><textarea value={active.render_prompt || ""} rows={10}
                      onChange={(e) => patchActive({ render_prompt: e.target.value })}
                      onBlur={(e) => patchActive({ render_prompt: e.target.value }, true)} /></label>
                  </div>
                </details>
              </aside>
            )}
          </div>
        </>
      )}
    </section>
  );
}
