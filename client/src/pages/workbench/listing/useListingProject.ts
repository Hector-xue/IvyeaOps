// Listing 工作台中央状态：项目数据 + 后台任务订阅 + 产品信息自动保存。
//
// 设计原则（对齐 /agents 丝滑标准）：
// - 每个动作独立 busy —— 一个任务在跑不锁其它按钮；
// - 长任务全部走后台 job，刷新/切项目后从 /projects/{id}/jobs 恢复订阅；
// - 产品信息 debounce 自动保存，不再有"保存"按钮心智负担。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import { messageOf } from "./api";
import type {
  CopyResult, CreativeSets, Job, Plan, ProductInfo, ProjectDetail,
  ProjectSummary, RefImages, ScrapeSummary, ToastTone,
} from "./types";

const EMPTY_INFO: ProductInfo = { product_name: "", description: "", selling_points: "", target_audience: "" };
const EMPTY_REFS: RefImages = { scraped: [], uploaded: [], white_product_source: "" };

function asList(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter(Boolean).map(String);
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

export function parseScrape(project: ProjectDetail | null): { data: Record<string, unknown> | null; summary: ScrapeSummary } {
  const empty: ScrapeSummary = { title: "", bullets: [], description: "", images: [], source: "", fullImagesAvailable: false, browserMissing: false };
  if (!project?.scrape_data) return { data: null, summary: empty };
  try {
    const data = JSON.parse(project.scrape_data) as Record<string, unknown>;
    const product = (data.product || {}) as Record<string, unknown>;
    return {
      data,
      summary: {
        title: String(data.title || product.title || ""),
        bullets: asList(data.bullets || product.bullets),
        description: String(data.description || product.description || ""),
        images: asList(data.reference_images || data.imageUrls || data.images || product.images),
        source: String(data.scrape_source || ""),
        fullImagesAvailable: Boolean(data.full_images_available),
        // 采集时后端探到本机有没有 Chrome/Edge/Chromium：没有就少了浏览器兜底那一层
        browserMissing: data.browser_available === false,
      },
    };
  } catch {
    return { data: null, summary: empty };
  }
}

export type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

export interface ListingState {
  projects: ProjectSummary[];
  activeId: string | null;
  project: ProjectDetail | null;
  projectLoading: boolean;
  scrape: ReturnType<typeof parseScrape>;
  analysis: { text: string; warning: string } ;
  copyResult: CopyResult | null;
  creativeSets: CreativeSets;
  refImages: RefImages;
  productInfo: ProductInfo;
  saveState: SaveState;
  jobs: Record<string, Job>;          // 最新一个各 kind 的 job（render_image 例外，见 renderJobs）
  renderJobs: Record<number, Job>;    // 按分镜 index 的单卡渲染 job
  setActiveId: (id: string | null) => void;
  setProductInfo: (patch: Partial<ProductInfo>) => void;
  refreshProjects: () => Promise<void>;
  refreshProject: () => Promise<void>;
  refreshRefs: () => Promise<void>;
  createProject: (asin: string, marketplace: string) => Promise<void>;
  removeProject: (id: string) => Promise<void>;
  runScrape: () => Promise<void>;
  runAnalyze: () => Promise<void>;
  runCopy: (extraNotes?: string) => Promise<void>;
  runPlan: (params: { target_count: number; deliverable: string; visual_tone: string; language: string; brief: string; color_scheme?: string }) => Promise<void>;
  runRenderImage: (deliverable: string, index: number) => Promise<void>;
  runRenderSet: (deliverable: string, onlyMissing?: boolean) => Promise<void>;
  runReviewSet: (deliverable: string) => Promise<void>;
  applyPlan: (deliverable: "gallery" | "aplus", plan: Plan) => void;
  persistPlan: (deliverable: "gallery" | "aplus", plan: Plan) => Promise<Plan>;
  uploadRefs: (files: FileList | File[]) => Promise<void>;
  deleteRef: (filename: string) => Promise<void>;
}

export function useListingProject(notify: (tone: ToastTone, text: string) => void): ListingState {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [projectLoading, setProjectLoading] = useState(false);
  const [refImages, setRefImages] = useState<RefImages>(EMPTY_REFS);
  const [productInfo, setInfo] = useState<ProductInfo>(EMPTY_INFO);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [renderJobs, setRenderJobs] = useState<Record<number, Job>>({});
  const [creativeSetsOverride, setCreativeSetsOverride] = useState<CreativeSets | null>(null);

  const activeRef = useRef<string | null>(null);
  activeRef.current = activeId;
  const watchersRef = useRef<Map<string, () => void>>(new Map());
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const infoRef = useRef(productInfo);
  infoRef.current = productInfo;
  const notifyRef = useRef(notify);
  notifyRef.current = notify;

  // ── 数据加载 ────────────────────────────────────────────────────────────────

  const refreshProjects = useCallback(async () => {
    try {
      const list = await api.listProjects();
      setProjects(list);
      setActiveId((prev) => {
        if (prev && list.some((p) => p.id === prev)) return prev;
        return list[0]?.id ?? null;
      });
    } catch (error) {
      notifyRef.current("error", `项目列表加载失败：${messageOf(error)}`);
    }
  }, []);

  const refreshRefs = useCallback(async () => {
    const id = activeRef.current;
    if (!id) return;
    try {
      const refs = await api.getReferenceImages(id);
      if (activeRef.current === id) {
        setRefImages({
          scraped: refs.scraped || [],
          uploaded: refs.uploaded || [],
          white_product_source: refs.white_product_source || "",
        });
      }
    } catch { /* 项目可能刚被删 */ }
  }, []);

  const refreshProject = useCallback(async () => {
    const id = activeRef.current;
    if (!id) return;
    try {
      const detail = await api.getProject(id);
      if (activeRef.current !== id) return;
      setProject(detail);
      setCreativeSetsOverride(null);
      try {
        const manual = detail.scrape_data ? (JSON.parse(detail.scrape_data).manual ?? null) : null;
        const scraped = parseScrape(detail).summary;
        setInfo({
          product_name: manual?.product_name || scraped.title || "",
          description: manual?.description || scraped.description || "",
          selling_points: manual?.selling_points || scraped.bullets.join("\n"),
          target_audience: manual?.target_audience || "",
        });
      } catch {
        setInfo(EMPTY_INFO);
      }
      setSaveState("idle");
    } catch (error) {
      notifyRef.current("error", `项目加载失败：${messageOf(error)}`);
    }
  }, []);

  // ── job 订阅 ────────────────────────────────────────────────────────────────

  const finishMessages: Record<string, (job: Job) => string> = useMemo(() => ({
    scrape: () => "采集完成，产品数据已更新",
    analyze: () => "AI 分析完成",
    copy: () => "Listing 文案已生成",
    plan: (job) => {
      const result = job.result as { fallback?: boolean; plan?: { images?: unknown[] } } | undefined;
      return result?.fallback
        ? "AI 策划暂不可用，已生成可编辑的稳健方案"
        : `套图方案已生成，共 ${result?.plan?.images?.length ?? 0} 张`;
    },
    render_image: (job) => {
      const result = job.result as { ready?: boolean; index?: number } | undefined;
      const n = (result?.index ?? (job.params?.index as number) ?? 0) + 1;
      return result?.ready ? `第 ${n} 张已通过成图质检，请人工核对产品一致性` : `第 ${n} 张已生成，但成图质检未通过`;
    },
    render_set: (job) => {
      const result = job.result as { succeeded?: number; total?: number; failures?: string[]; set_qa?: { ready?: boolean } } | undefined;
      const failed = result?.failures?.length ?? 0;
      if (!result?.succeeded) return "整套生成没有产出图片，请检查素材与生成服务";
      return failed
        ? `整套生成完成 ${result.succeeded}/${result.total}，${failed} 张失败或被质检拦截`
        : result.set_qa?.ready
          ? `整套 ${result.succeeded} 张全部生成，单图与整套质检均通过`
          : `整套 ${result.succeeded} 张已生成，请查看质检结论`;
    },
    review_set: (job) => {
      const result = job.result as { set_qa?: { ready?: boolean; issues?: { message: string }[] } } | undefined;
      return result?.set_qa?.ready
        ? "整套设计一致性与叙事质检通过"
        : `整套质检未通过：${result?.set_qa?.issues?.[0]?.message ?? "请查看质检结果"}`;
    },
  }), []);

  const attachJob = useCallback((job: Job) => {
    const record = (next: Job) => {
      if (next.project_id !== activeRef.current) return;
      if (next.kind === "render_image") {
        const index = Number((next.params as { index?: number })?.index ?? -1);
        setRenderJobs((prev) => ({ ...prev, [index]: next }));
      } else {
        setJobs((prev) => ({ ...prev, [next.kind]: next }));
      }
    };
    record(job);
    if (job.status !== "running" || watchersRef.current.has(job.id)) return;
    const stop = api.watchJob(job.id, (next) => {
      record(next);
      if (next.status === "running") return;
      watchersRef.current.delete(job.id);
      if (next.project_id !== activeRef.current) return;
      if (next.status === "failed") {
        notifyRef.current("error", `${next.error || "任务失败"}`);
      } else {
        const describe = finishMessages[next.kind];
        const result = next.result as { fallback?: boolean } | undefined;
        notifyRef.current(
          next.kind === "plan" && result?.fallback ? "warn"
            : next.kind === "render_image" && !(next.result as { ready?: boolean })?.ready ? "warn"
              : "success",
          describe ? describe(next) : "任务完成",
        );
      }
      // 任务落库的数据（scrape_data / analysis / copy_result / creative_sets）
      void refreshProject();
      if (next.kind === "scrape" || next.kind === "plan") void refreshRefs();
      void refreshProjects();
    });
    watchersRef.current.set(job.id, stop);
  }, [finishMessages, refreshProject, refreshProjects, refreshRefs]);

  // ── 项目切换 ────────────────────────────────────────────────────────────────

  useEffect(() => { void refreshProjects(); }, [refreshProjects]);

  useEffect(() => {
    watchersRef.current.forEach((stop) => stop());
    watchersRef.current.clear();
    setJobs({});
    setRenderJobs({});
    setProject(null);
    setRefImages(EMPTY_REFS);
    setInfo(EMPTY_INFO);
    setCreativeSetsOverride(null);
    setSaveState("idle");
    if (!activeId) return;
    setProjectLoading(true);
    void (async () => {
      await Promise.all([refreshProject(), refreshRefs()]);
      setProjectLoading(false);
      // 恢复进行中的任务（刷新页面 / 切回项目）
      try {
        const { active } = await api.getProjectJobs(activeId);
        active.forEach(attachJob);
      } catch { /* ignore */ }
    })();
    return () => {
      watchersRef.current.forEach((stop) => stop());
      watchersRef.current.clear();
    };
  }, [activeId, attachJob, refreshProject, refreshRefs]);

  // ── 派生数据 ────────────────────────────────────────────────────────────────

  const scrape = useMemo(() => parseScrape(project), [project]);

  const analysis = useMemo(() => {
    if (!project?.analysis_data) return { text: "", warning: "" };
    try {
      const parsed = JSON.parse(project.analysis_data);
      return {
        text: String(parsed.analysis || parsed.ai_analysis || ""),
        warning: String(parsed.warning || ""),
      };
    } catch {
      return { text: "", warning: "" };
    }
  }, [project]);

  const copyResult = useMemo<CopyResult | null>(() => {
    if (!project?.copy_result) return null;
    try {
      return JSON.parse(project.copy_result) as CopyResult;
    } catch {
      return null;
    }
  }, [project]);

  const creativeSets = useMemo<CreativeSets>(() => {
    if (creativeSetsOverride) return creativeSetsOverride;
    if (!project?.creative_sets) return {};
    try {
      return JSON.parse(project.creative_sets) as CreativeSets;
    } catch {
      return {};
    }
  }, [project, creativeSetsOverride]);

  // ── 产品信息自动保存 ────────────────────────────────────────────────────────

  const setProductInfo = useCallback((patch: Partial<ProductInfo>) => {
    setInfo((prev) => ({ ...prev, ...patch }));
    setSaveState("dirty");
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    const id = activeRef.current;
    saveTimerRef.current = setTimeout(async () => {
      if (!id || activeRef.current !== id) return;
      setSaveState("saving");
      try {
        await api.saveProductInfo(id, { ...infoRef.current });
        if (activeRef.current === id) setSaveState("saved");
      } catch (error) {
        if (activeRef.current === id) {
          setSaveState("error");
          notifyRef.current("error", `产品信息保存失败：${messageOf(error)}`);
        }
      }
    }, 900);
  }, []);

  useEffect(() => () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
  }, []);

  // ── 动作 ────────────────────────────────────────────────────────────────────

  const createProjectAction = useCallback(async (asin: string, marketplace: string) => {
    try {
      const res = await api.createProject(asin.trim(), marketplace);
      await refreshProjects();
      setActiveId(res.id);
      notifyRef.current("success", "项目已创建");
    } catch (error) {
      notifyRef.current("error", `创建失败：${messageOf(error)}`);
    }
  }, [refreshProjects]);

  const removeProject = useCallback(async (id: string) => {
    try {
      await api.deleteProject(id);
      if (activeRef.current === id) setActiveId(null);
      await refreshProjects();
      notifyRef.current("success", "项目已删除");
    } catch (error) {
      notifyRef.current("error", `删除失败：${messageOf(error)}`);
    }
  }, [refreshProjects]);

  const startAction = useCallback(async (starter: () => Promise<Job>, label: string) => {
    try {
      attachJob(await starter());
    } catch (error) {
      notifyRef.current("error", `${label}启动失败：${messageOf(error)}`);
    }
  }, [attachJob]);

  const runScrape = useCallback(async () => {
    const id = activeRef.current;
    if (id) await startAction(() => api.startScrape(id), "采集");
  }, [startAction]);

  const runAnalyze = useCallback(async () => {
    const id = activeRef.current;
    if (id) await startAction(() => api.startAnalyze(id), "AI 分析");
  }, [startAction]);

  const runCopy = useCallback(async (extraNotes = "") => {
    const id = activeRef.current;
    if (id) await startAction(() => api.startCopy(id, extraNotes), "文案生成");
  }, [startAction]);

  const runPlan = useCallback(async (params: {
    target_count: number; deliverable: string; visual_tone: string;
    language: string; brief: string; color_scheme?: string;
  }) => {
    const id = activeRef.current;
    if (id) await startAction(() => api.startPlan(id, { color_scheme: "", ...params }), "套图策划");
  }, [startAction]);

  const runRenderImage = useCallback(async (deliverable: string, index: number) => {
    const id = activeRef.current;
    if (id) await startAction(() => api.startRenderImage(id, { deliverable, index }), "生成");
  }, [startAction]);

  const runRenderSet = useCallback(async (deliverable: string, onlyMissing = false) => {
    const id = activeRef.current;
    if (id) await startAction(() => api.startRenderSet(id, { deliverable, only_missing: onlyMissing }), "整套生成");
  }, [startAction]);

  const runReviewSet = useCallback(async (deliverable: string) => {
    const id = activeRef.current;
    if (id) await startAction(() => api.startReviewSet(id, deliverable), "整套复核");
  }, [startAction]);

  // 分镜编辑：本地即时生效（乐观），随后落库并以服务端规范化结果为准。
  const applyPlan = useCallback((deliverable: "gallery" | "aplus", plan: Plan) => {
    setCreativeSetsOverride((prev) => ({ ...(prev ?? creativeSets), [deliverable]: plan }));
  }, [creativeSets]);

  const persistPlan = useCallback(async (deliverable: "gallery" | "aplus", plan: Plan): Promise<Plan> => {
    const id = activeRef.current;
    applyPlan(deliverable, plan);
    if (!id) return plan;
    try {
      const response = await api.saveCreativeSet(id, deliverable, plan) as { plan?: Plan };
      if (response?.plan) {
        setCreativeSetsOverride((prev) => ({ ...(prev ?? {}), [deliverable]: response.plan! }));
        return response.plan;
      }
    } catch (error) {
      notifyRef.current("error", `保存分镜失败：${messageOf(error)}`);
    }
    return plan;
  }, [applyPlan]);

  const uploadRefs = useCallback(async (files: FileList | File[]) => {
    const id = activeRef.current;
    const list = Array.from(files).slice(0, 8);
    if (!id || !list.length) return;
    try {
      for (const file of list) await api.uploadImage(id, file);
      await refreshRefs();
      notifyRef.current("success", `上传了 ${list.length} 张素材图`);
    } catch (error) {
      notifyRef.current("error", `上传失败：${messageOf(error)}`);
    }
  }, [refreshRefs]);

  const deleteRef = useCallback(async (filename: string) => {
    const id = activeRef.current;
    if (!id) return;
    try {
      await api.deleteUploadedImage(id, filename);
      await refreshRefs();
    } catch (error) {
      notifyRef.current("error", `删除失败：${messageOf(error)}`);
    }
  }, [refreshRefs]);

  return {
    projects, activeId, project, projectLoading, scrape, analysis, copyResult,
    creativeSets, refImages, productInfo, saveState, jobs, renderJobs,
    setActiveId, setProductInfo, refreshProjects, refreshProject, refreshRefs,
    createProject: createProjectAction, removeProject,
    runScrape, runAnalyze, runCopy, runPlan, runRenderImage, runRenderSet,
    runReviewSet, applyPlan, persistPlan, uploadRefs, deleteRef,
  };
}
