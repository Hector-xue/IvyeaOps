// Listing 工作台 Shell：项目栏 + 四步流程导航 + 各步内容。
// 全面重做版：长任务后台化（SSE 进度 + 刷新恢复）、自动保存、toast 通知、
// 每个动作独立 busy —— 详见 useListingProject。
import { useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import "./listing.css";
import CopyStep from "./CopyStep";
import DeliveryStep from "./DeliveryStep";
import ProductStep from "./ProductStep";
import ProjectRail from "./ProjectRail";
import { ToastProvider, useToast } from "./toast";
import { useListingProject } from "./useListingProject";
import VisualStep from "./VisualStep";

type StepKey = "product" | "copy" | "visual" | "delivery";

const STEPS: { key: StepKey; label: string }[] = [
  { key: "product", label: "① 素材与洞察" },
  { key: "copy", label: "② Listing 文案" },
  { key: "visual", label: "③ 视觉套图" },
  { key: "delivery", label: "④ 交付" },
];

const STEP_JOBS: Record<StepKey, string[]> = {
  product: ["scrape", "analyze"],
  copy: ["copy"],
  visual: ["plan", "render_set", "review_set"],
  delivery: [],
};

function WorkbenchInner() {
  const notify = useToast();
  const state = useListingProject(notify);
  const [step, setStep] = useState<StepKey>("product");
  const {
    projects, activeId, project, projectLoading, scrape, analysis,
    copyResult, creativeSets, jobs, renderJobs,
  } = state;

  const stepDone: Record<StepKey, boolean> = useMemo(() => {
    const gallery = creativeSets.gallery;
    const galleryDone = Boolean(gallery?.images?.length
      && gallery.images.every((i) => i.final_url && i.render_qa?.ready));
    return {
      product: Boolean(scrape.summary.title && analysis.text),
      copy: Boolean(copyResult?.titles?.length),
      visual: galleryDone,
      delivery: galleryDone && Boolean(copyResult?.titles?.length),
    };
  }, [scrape, analysis, copyResult, creativeSets]);

  const stepRunning: Record<StepKey, boolean> = useMemo(() => {
    const anyRender = Object.values(renderJobs).some((j) => j.status === "running");
    return {
      product: STEP_JOBS.product.some((k) => jobs[k]?.status === "running"),
      copy: STEP_JOBS.copy.some((k) => jobs[k]?.status === "running"),
      visual: STEP_JOBS.visual.some((k) => jobs[k]?.status === "running") || anyRender,
      delivery: false,
    };
  }, [jobs, renderJobs]);

  return (
    <div>
      <div className="listing-page-head">
        <div>
          <div className="ptitle">/ Listing 工作台</div>
          <p className="listing-page-sub">从产品事实到文案、视觉套图和最终交付的完整生产流程 · 长任务全部后台运行，可随时离开页面</p>
        </div>
        {project && (
          <div className="listing-page-context">
            <span>{project.marketplace || "US"}</span>
            <strong>{project.asin}</strong>
          </div>
        )}
      </div>

      <div className="listing-layout lst-layout">
        <ProjectRail
          projects={projects}
          activeId={activeId}
          onSelect={state.setActiveId}
          onCreate={state.createProject}
          onDelete={state.removeProject}
        />

        <div className="listing-main">
          {!activeId ? (
            <div className="card lst-blank">
              <h3>开始一个 Listing 项目</h3>
              <p>在左侧输入 ASIN 新建项目。采集、AI 分析、文案与整套视觉都会围绕这个项目沉淀，全部后台执行、随时恢复。</p>
            </div>
          ) : projectLoading || !project ? (
            <div className="card wb-enter lst-loading" aria-busy="true" aria-live="polite">
              <div className="skeleton" style={{ height: 32, marginBottom: 12 }} />
              <div className="skeleton line lg" />
              <div className="skeleton line md" />
              <div className="skeleton line lg" />
              <div className="skeleton line sm" />
            </div>
          ) : (
            <div className="wb-enter" key={activeId}>
              <div data-tour="listing-tabs" className="listing-workflow-tabs lst-tabs">
                {STEPS.map(({ key, label }) => (
                  <button key={key} className={step === key ? "active" : ""} onClick={() => setStep(key)}>
                    {label}
                    {stepRunning[key] && <Loader2 size={10} className="spin lst-tab-spin" />}
                    {!stepRunning[key] && stepDone[key] && <span className="lst-tab-done">✓</span>}
                  </button>
                ))}
              </div>

              <div key={step} className="wb-enter">
                {step === "product" && <ProductStep state={state} />}
                {step === "copy" && <CopyStep state={state} />}
                {step === "visual" && <VisualStep state={state} />}
                {step === "delivery" && <DeliveryStep state={state} />}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ListingWorkbench() {
  return (
    <ToastProvider>
      <WorkbenchInner />
    </ToastProvider>
  );
}
