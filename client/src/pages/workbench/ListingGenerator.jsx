import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useConfirm } from "../../components/ConfirmDialog";
import SheetSelect from "../../components/SheetSelect";
import { marketplaceOptions } from "../../lib/marketplaces";
import {
  aiAnalyze,
  applyTemplate,
  createProject,
  deleteProject,
  deleteUploadedImage,
  downloadPsd,
  generateAplusPrompts,
  generateCopy,
  generateImage,
  generateImagePrompt,
  generateMainPrompts,
  getProject,
  getReferenceImages,
  getTemplates,
  listProjects,
  reviewImagePrompt,
  saveImageSlots,
  saveProductInfo,
  saveTemplate,
  scrapeProject,
  uploadImage,
} from "../../api/listing";

const MAIN_DEFAULTS = [
  ["main", "主图-白底", "1600x1600"],
  ["sub1", "副图1-场景", "1600x1600"],
  ["sub2", "副图2-细节", "1600x1600"],
  ["sub3", "副图3-尺寸/规格", "1600x1600"],
  ["sub4", "副图4-多角度", "1600x1600"],
  ["sub5", "副图5-包装/配件", "1600x1600"],
  ["sub6", "副图6-场景/卖点", "1600x1600"],
];

const APLUS_DEFAULTS = [
  ["aplus_banner_desktop", "A+顶部横幅-桌面", "1464x600"],
  ["aplus_banner_mobile", "A+顶部横幅-手机", "600x450"],
  ["aplus_1_desktop", "A+模块1-桌面", "1464x600"],
  ["aplus_1_mobile", "A+模块1-手机", "600x450"],
  ["aplus_2_desktop", "A+模块2-桌面", "1464x600"],
  ["aplus_2_mobile", "A+模块2-手机", "600x450"],
  ["aplus_3_desktop", "A+模块3-桌面", "1464x600"],
  ["aplus_3_mobile", "A+模块3-手机", "600x450"],
  ["aplus_compare_desktop", "A+对比/规格-桌面", "1464x600"],
  ["aplus_compare_mobile", "A+对比/规格-手机", "600x450"],
  ["brand_story_desktop", "品牌故事-桌面", "1464x600"],
  ["brand_story_mobile", "品牌故事-手机", "600x450"],
];

const COLOR_OPTIONS = [
  { value: "auto", label: "自动" },
  { value: "warm earth tones with amber and brown", label: "暖色调" },
  { value: "cool blue-gray with teal accents", label: "冷色调" },
  { value: "forest green + black + orange", label: "森林绿" },
  { value: "black + gold luxury", label: "黑金奢华" },
  { value: "clean white + blue", label: "清爽蓝白" },
  { value: "custom", label: "自定义" },
];

const SIZE_PRESETS_MAIN = ["1400x1400", "1500x1500", "1600x1600", "2000x2000", "1024x1024"];
const SIZE_PRESETS_APLUS = ["1464x600", "600x450", "970x600", "300x300", "1024x1024", "1536x1024"];
const EMPTY_PRODUCT_INFO = { product_name: "", description: "", selling_points: "", target_audience: "" };
const EMPTY_TEMPLATE_DRAFTS = {
  main: { name: "", content: "" },
  aplus: { name: "", content: "" },
};

function makeSlots(defaults) {
  return defaults.map(([id, label, size]) => ({ id, label, size, prompt: "", url: "" }));
}

function colorValue(scheme, custom) {
  if (scheme === "auto") return undefined;
  if (scheme === "custom") return custom || undefined;
  return scheme;
}

function sizesOf(slots) {
  return Object.fromEntries(slots.map((s) => [s.id, s.size]));
}

function slotPayload(slots) {
  return slots.map(({ id, label, size }) => ({ id, label, size }));
}

function asList(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

function scrapeSummary(data) {
  if (!data) return { title: "", bullets: [], description: "", images: [] };
  const product = data.product || {};
  return {
    title: data.title || product.title || "",
    bullets: asList(data.bullets || product.bullets),
    description: data.description || product.description || "",
    images: asList(data.reference_images || data.imageUrls || data.images || product.images),
  };
}

function Btn({ onClick, disabled, children, primary, danger }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        background: danger ? "var(--red)" : primary ? "var(--acc)" : "var(--bg2)",
        color: primary || danger ? "#000" : "var(--t)",
        border: primary || danger ? "none" : "1px solid var(--b)",
        borderRadius: 4,
        padding: "5px 10px",
        fontSize: 10,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.55 : 1,
      }}
    >
      {children}
    </button>
  );
}

function Field({ children, label }) {
  return (
    <label style={{ display: "grid", gap: 4, fontSize: 10, color: "var(--t3)" }}>
      <span>{label}</span>
      {children}
    </label>
  );
}

const inputStyle = {
  background: "var(--bg1)",
  border: "1px solid var(--b)",
  borderRadius: 3,
  padding: "5px 7px",
  fontSize: 10,
  color: "var(--t)",
  outline: "none",
  fontFamily: "inherit",
  lineHeight: 1.5,
  boxSizing: "border-box",
};

export default function ListingGenerator({ onProjectAsin } = {}) {
  const confirm = useConfirm();
  const [projects, setProjects] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [project, setProject] = useState(null);
  const [tab, setTab] = useState("scrape");
  const [loading, setLoading] = useState("");
  const busy = !!loading;

  const [newAsin, setNewAsin] = useState("");
  const [newMkt, setNewMkt] = useState("US");
  const [copyResult, setCopyResult] = useState({});
  const [analysisResult, setAnalysisResult] = useState("");
  const [productInfo, setProductInfo] = useState(EMPTY_PRODUCT_INFO);

  const [imageSlots, setImageSlots] = useState(makeSlots(MAIN_DEFAULTS));
  const [aplusSlots, setAplusSlots] = useState(makeSlots(APLUS_DEFAULTS));
  const [colorScheme, setColorScheme] = useState("auto");
  const [customColor, setCustomColor] = useState("");
  const [aplusColorScheme, setAplusColorScheme] = useState("auto");
  const [aplusCustomColor, setAplusCustomColor] = useState("");

  const [templates, setTemplates] = useState([]);
  const [templateDrafts, setTemplateDrafts] = useState(EMPTY_TEMPLATE_DRAFTS);
  const [templatePanel, setTemplatePanel] = useState({ main: false, aplus: false });
  const [previewUrl, setPreviewUrl] = useState(null);
  const [feedback, setFeedback] = useState(null);

  // Reference / source images
  const [refImages, setRefImages] = useState({ scraped: [], uploaded: [] });
  const [refUploading, setRefUploading] = useState(false);
  const [refDragOver, setRefDragOver] = useState(false);
  const refInputRef = useRef(null);

  // Advanced copy job state
  const [advCopyJob, setAdvCopyJob] = useState(null);
  const [advCopyLaunching, setAdvCopyLaunching] = useState(false);
  const [advCopyCopied, setAdvCopyCopied] = useState(null);
  const advCopyPollRef = useRef(null);

  useEffect(() => { loadProjects(); }, []);
  useEffect(() => { if (activeId) loadProject(activeId); }, [activeId]);

  async function loadProjects() {
    const ps = await listProjects();
    setProjects(ps);
    if (ps.length === 0) {
      setActiveId(null);
      setProject(null);
      return;
    }
    if (!activeId || !ps.some((p) => p.id === activeId)) setActiveId(ps[0].id);
  }

  async function loadProject(id) {
    const p = await getProject(id);
    setProject(p);
    setFeedback(null);
    setProductInfo(EMPTY_PRODUCT_INFO);
    setRefImages({ scraped: [], uploaded: [] });
    setAdvCopyJob(null);
    if (advCopyPollRef.current) { clearTimeout(advCopyPollRef.current); advCopyPollRef.current = null; }
    // Load reference images for this project
    try {
      const ri = await getReferenceImages(id);
      setRefImages({ scraped: ri.scraped || [], uploaded: ri.uploaded || [] });
    } catch { /* ignore */ }
    setImageSlots(makeSlots(MAIN_DEFAULTS));
    setAplusSlots(makeSlots(APLUS_DEFAULTS));
    const cr = {};
    if (p.title) cr.title = p.title;
    if (p.bullets) cr.bullets = p.bullets;
    if (p.search_terms) cr.search_terms = p.search_terms;
    if (p.aplus_copy) cr.aplus = p.aplus_copy;
    setCopyResult(cr);

    if (p.analysis_data) {
      try {
        const parsed = JSON.parse(p.analysis_data);
        setAnalysisResult(parsed.analysis || parsed.ai_analysis || "");
      } catch {
        setAnalysisResult("");
      }
    } else {
      setAnalysisResult("");
    }

    if (p.scrape_data) {
      try {
        const sd = JSON.parse(p.scrape_data);
        const summary = scrapeSummary(sd);
        setProductInfo({
          product_name: summary.title,
          description: summary.description,
          selling_points: summary.bullets.join("\n"),
          target_audience: "",
          ...(sd.manual || {}),
        });
      } catch {}
    }

    if (p.image_slots) {
      try {
        const saved = JSON.parse(p.image_slots);
        if (Array.isArray(saved.main)) setImageSlots(saved.main);
        if (Array.isArray(saved.aplus)) setAplusSlots(saved.aplus);
      } catch {}
    }

    // Restore the most recent advanced-copy result saved on this project,
    // so generated copy survives a page refresh / project re-selection.
    if (p.copy_result) {
      try {
        setAdvCopyJob({ id: p.copy_job_id || null, status: "done", result: JSON.parse(p.copy_result) });
      } catch { /* ignore malformed copy_result */ }
    }
  }

  const scrapeData = useMemo(() => {
    if (!project?.scrape_data) return null;
    try { return JSON.parse(project.scrape_data); } catch { return null; }
  }, [project]);
  const scraped = useMemo(() => scrapeSummary(scrapeData), [scrapeData]);

  const flowStatus = useMemo(() => {
    const hasProductInfo = Object.values(productInfo).some((v) => String(v || "").trim());
    const hasCopy = advCopyJob?.status === "done" || ["title", "bullets", "search_terms", "aplus"].some((k) => copyResult[k]);
    const mainPromptCount = imageSlots.filter((s) => s.prompt).length;
    const aplusPromptCount = aplusSlots.filter((s) => s.prompt).length;
    const imageCount = [...imageSlots, ...aplusSlots].filter((s) => s.url).length;
    return [
      { label: "产品信息", ok: Boolean(scraped.title || hasProductInfo) },
      { label: "AI分析", ok: Boolean(analysisResult) },
      { label: "文案", ok: hasCopy },
      { label: `主图提示词 ${mainPromptCount}/${imageSlots.length}`, ok: mainPromptCount > 0 },
      { label: `A+提示词 ${aplusPromptCount}/${aplusSlots.length}`, ok: aplusPromptCount > 0 },
      { label: `图片 ${imageCount}`, ok: imageCount > 0 },
    ];
  }, [advCopyJob, analysisResult, aplusSlots, copyResult, imageSlots, productInfo, scraped.title]);

  function messageOf(e) {
    return e?.response?.data?.detail || e?.message || String(e);
  }

  function notify(type, text) {
    setFeedback({ type, text });
  }

  function updateTemplateDraft(kind, patch) {
    setTemplateDrafts((prev) => ({ ...prev, [kind]: { ...prev[kind], ...patch } }));
  }

  function setTemplateOpen(kind, open) {
    setTemplatePanel((prev) => ({ ...prev, [kind]: open }));
  }

  async function handleCreate() {
    if (!newAsin.trim()) return;
    const asin = newAsin.trim();
    setLoading("creating");
    try {
      const res = await createProject(asin, newMkt);
      onProjectAsin?.(asin);
      setNewAsin("");
      await loadProjects();
      setActiveId(res.id);
      notify("success", "项目已创建");
    } catch (e) {
      notify("error", "创建失败: " + messageOf(e));
    }
    setLoading("");
  }

  async function handleScrape() {
    if (!activeId) return;
    setLoading("scraping");
    try {
      await scrapeProject(activeId);
      await loadProject(activeId);
      notify("success", "采集完成，已更新产品数据");
    } catch (e) {
      notify("error", "采集失败: " + messageOf(e));
    }
    setLoading("");
  }

  async function handleSaveInfo() {
    if (!activeId) return;
    setLoading("saving");
    try {
      await saveProductInfo(activeId, productInfo);
      await loadProject(activeId);
      notify("success", "产品信息已保存");
    } catch (e) {
      notify("error", "保存失败: " + messageOf(e));
    }
    setLoading("");
  }

  async function handleUploadRefImages(files) {
    if (!activeId || !files.length) return;
    setRefUploading(true);
    try {
      for (const file of Array.from(files).slice(0, 8)) {
        await uploadImage(activeId, file);
      }
      const ri = await getReferenceImages(activeId);
      setRefImages({ scraped: ri.scraped || [], uploaded: ri.uploaded || [] });
      notify("success", `上传了 ${Math.min(files.length, 8)} 张素材图`);
    } catch (e) {
      notify("error", "上传失败: " + (e.message || "未知错误"));
    } finally {
      setRefUploading(false);
    }
  }

  async function handleDeleteRefImage(filename) {
    if (!activeId) return;
    try {
      await deleteUploadedImage(activeId, filename);
      setRefImages(prev => ({ ...prev, uploaded: prev.uploaded.filter(u => u.filename !== filename) }));
    } catch (e) {
      notify("error", "删除失败: " + (e.message || ""));
    }
  }

  async function handleAnalyze() {
    if (!activeId) return;
    setLoading("analyzing");
    try {
      const res = await aiAnalyze(activeId);
      setAnalysisResult(res.analysis || res.ai_analysis || JSON.stringify(res, null, 2));
      notify("success", "AI分析完成");
    } catch (e) {
      notify("error", "分析失败: " + messageOf(e));
    }
    setLoading("");
  }

  async function handleCopy(type) {
    if (!activeId) return;
    setLoading(`copy-${type}`);
    try {
      const res = await generateCopy(activeId, type);
      setCopyResult((prev) => ({ ...prev, [type]: res.content }));
      notify(res.fallback ? "warn" : "success", res.warning || "文案已生成");
    } catch (e) {
      notify("error", "生成失败: " + messageOf(e));
    }
    setLoading("");
  }

  const pollAdvCopy = useCallback(async (jobId) => {
    try {
      const r = await fetch(`/api/listing/copy-jobs/${jobId}`, { credentials: "include" });
      if (!r.ok) return;
      const job = await r.json();
      setAdvCopyJob(job);
      if (job.status === "running" || job.status === "pending" || job.status === "uploaded") {
        advCopyPollRef.current = setTimeout(() => pollAdvCopy(jobId), 2000);
      }
    } catch {}
  }, []);

  useEffect(() => () => { if (advCopyPollRef.current) clearTimeout(advCopyPollRef.current); }, []);

  async function handleAdvancedCopy() {
    if (!activeId || !project) return;
    if (advCopyPollRef.current) { clearTimeout(advCopyPollRef.current); advCopyPollRef.current = null; }
    setAdvCopyLaunching(true);
    try {
      const notes = [
        productInfo.product_name && `产品: ${productInfo.product_name}`,
        productInfo.target_audience && `目标受众: ${productInfo.target_audience}`,
        productInfo.selling_points && `卖点: ${productInfo.selling_points}`,
        productInfo.description && `描述: ${productInfo.description}`,
        scraped.title && `参考标题: ${scraped.title}`,
        scraped.bullets.length > 0 && `参考五点:\n${scraped.bullets.join("\n")}`,
      ].filter(Boolean).join("\n");
      const r = await fetch("/api/listing/copy-jobs", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          marketplace: project.marketplace || "US",
          product_type: productInfo.product_name || project.asin || "product",
          asins: project.asin ? [project.asin] : [],
          product_notes: notes,
          project_id: activeId,
        }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "创建失败");
      const { job_id } = await r.json();
      await fetch(`/api/listing/copy-jobs/${job_id}/start`, { method: "POST", credentials: "include" });
      setAdvCopyJob({ id: job_id, status: "running", stage: 0, stage_msg: "启动中..." });
      pollAdvCopy(job_id);
    } catch (e) {
      notify("error", "文案生成失败: " + (e.message || "未知错误"));
    } finally {
      setAdvCopyLaunching(false);
    }
  }

  function copyAdvText(text, key) {
    navigator.clipboard.writeText(text).then(() => {
      setAdvCopyCopied(key);
      setTimeout(() => setAdvCopyCopied(null), 1500);
    });
  }

  async function persistSlots(nextMain = imageSlots, nextAplus = aplusSlots) {
    if (!activeId) return;
    await saveImageSlots(activeId, { main: nextMain, aplus: nextAplus });
  }

  async function handleSaveSlotConfig() {
    if (!activeId) return;
    setLoading("save-slots");
    try {
      await persistSlots();
      notify("success", "图片配置已保存");
    } catch (e) {
      notify("error", "保存配置失败: " + messageOf(e));
    }
    setLoading("");
  }

  function addSlot(kind) {
    const id = `${kind}_${Date.now().toString(36)}`;
    const slot = {
      id,
      label: kind === "aplus" ? "自定义A+图片" : "自定义主图/副图",
      size: kind === "aplus" ? "1464x600" : "1600x1600",
      prompt: "",
      url: "",
    };
    if (kind === "aplus") setAplusSlots((prev) => [...prev, slot]);
    else setImageSlots((prev) => [...prev, slot]);
  }

  function updateSlot(kind, id, patch) {
    const setter = kind === "aplus" ? setAplusSlots : setImageSlots;
    setter((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  }

  function removeSlot(kind, id) {
    const slots = kind === "aplus" ? aplusSlots : imageSlots;
    if (slots.length <= 1) {
      notify("warn", "至少保留一个图片位");
      return;
    }
    const setter = kind === "aplus" ? setAplusSlots : setImageSlots;
    setter((prev) => prev.filter((s) => s.id !== id));
  }

  async function handleGenOnePrompt(slot, isAplus) {
    if (!activeId) return;
    const kind = isAplus ? "aplus" : "main";
    const colorArg = isAplus ? colorValue(aplusColorScheme, aplusCustomColor) : colorValue(colorScheme, customColor);

    setLoading(`prompt-gen-${slot.id}`);
    try {
      // Stage 1: Generate initial prompt
      const res = await generateImagePrompt(activeId, {
        slot: slot.id,
        label: slot.label,
        size: slot.size,
        color_scheme: colorArg,
      });
      // Show the draft immediately so the user sees something before review
      updateSlot(kind, slot.id, { prompt: res.prompt });

      // Stage 2: Self-review and optimize
      setLoading(`prompt-review-${slot.id}`);
      let finalPrompt = res.prompt;
      try {
        const reviewed = await reviewImagePrompt(activeId, {
          slot: slot.id,
          prompt: res.prompt,
          label: slot.label,
          size: slot.size,
          color_scheme: colorArg,
        });
        if (reviewed.prompt) {
          finalPrompt = reviewed.prompt;
          updateSlot(kind, slot.id, { prompt: finalPrompt });
        }
      } catch {
        // Review failed silently — keep the draft
      }

      const nextSlots = (isAplus ? aplusSlots : imageSlots).map((s) =>
        s.id === slot.id ? { ...s, prompt: finalPrompt } : s
      );
      await persistSlots(isAplus ? imageSlots : nextSlots, isAplus ? nextSlots : aplusSlots);
      notify("success", `${slot.label || slot.id} 提示词已生成 · 自检优化完成`);
    } catch (e) {
      notify("error", "提示词生成失败: " + messageOf(e));
    }
    setLoading("");
  }

  async function handleGenOneImage(slot, isAplus) {
    if (!slot.prompt || !activeId) {
      notify("warn", "请先生成或填写提示词");
      return;
    }
    setLoading(`img-${slot.id}`);
    try {
      const res = await generateImage(activeId, slot.prompt, slot.id, slot.size);
      updateSlot(isAplus ? "aplus" : "main", slot.id, { url: res.url || res.imageUrl || "" });
      const url = res.url || res.imageUrl || "";
      const nextSlots = (isAplus ? aplusSlots : imageSlots).map((s) => (s.id === slot.id ? { ...s, url } : s));
      await persistSlots(isAplus ? imageSlots : nextSlots, isAplus ? nextSlots : aplusSlots);
      notify("success", `${slot.label || slot.id} 的图片已生成并保存`);
    } catch (e) {
      notify("error", "图片生成失败: " + messageOf(e));
    }
    setLoading("");
  }

  async function handleGenGroupPrompts(isAplus) {
    if (!activeId) return;
    const slots = isAplus ? aplusSlots : imageSlots;
    setLoading(isAplus ? "gen-aplus-prompts" : "gen-main-prompts");
    try {
      const fn = isAplus ? generateAplusPrompts : generateMainPrompts;
      const res = await fn(activeId, {
        sizes: sizesOf(slots),
        slots: slotPayload(slots),
        color_scheme: isAplus ? colorValue(aplusColorScheme, aplusCustomColor) : colorValue(colorScheme, customColor),
      });
      if (res.prompts) {
        const setter = isAplus ? setAplusSlots : setImageSlots;
        const nextSlots = slots.map((s) => (res.prompts[s.id] ? { ...s, prompt: res.prompts[s.id] } : s));
        setter(nextSlots);
        await persistSlots(isAplus ? imageSlots : nextSlots, isAplus ? nextSlots : aplusSlots);
        notify("success", `已生成 ${Object.keys(res.prompts).length} 个提示词并完成自检优化`);
      } else {
        notify("warn", res.error || "接口没有返回可用提示词");
      }
    } catch (e) {
      notify("error", "生成失败: " + messageOf(e));
    }
    setLoading("");
  }

  async function handleGenGroupImages(isAplus) {
    if (!activeId) return;
    const slots = isAplus ? aplusSlots : imageSlots;
    setLoading(isAplus ? "gen-aplus-images" : "gen-main-images");
    try {
      const nextSlots = [...slots];
      let generated = 0;
      for (const slot of slots) {
        if (!slot.prompt) continue;
        const res = await generateImage(activeId, slot.prompt, slot.id, slot.size);
        const url = res.url || res.imageUrl || "";
        updateSlot(isAplus ? "aplus" : "main", slot.id, { url });
        const index = nextSlots.findIndex((s) => s.id === slot.id);
        if (index >= 0) nextSlots[index] = { ...nextSlots[index], url };
        generated += 1;
      }
      await persistSlots(isAplus ? imageSlots : nextSlots, isAplus ? nextSlots : aplusSlots);
      notify(generated ? "success" : "warn", generated ? `已生成 ${generated} 张图片并保存当前配置` : "没有可生成的图片，请先填写提示词");
    } catch (e) {
      notify("error", "图片生成失败: " + messageOf(e));
    }
    setLoading("");
  }

  async function handleDownloadImage(slot) {
    try {
      const resp = await fetch(slot.url);
      if (!resp.ok) throw new Error("请求失败");
      const blob = await resp.blob();
      const ext = blob.type.includes("png") ? "png" : blob.type.includes("webp") ? "webp" : "jpg";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${activeId}_${slot.id}.${ext}`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch {
      window.open(slot.url, "_blank");
    }
  }

  async function handleDownloadPsd(slot) {
    if (!slot.url || !activeId) return;
    setLoading(`psd-${slot.id}`);
    try {
      await downloadPsd(activeId, slot.url, slot.id);
    } catch (e) {
      notify("error", "PSD下载失败: " + messageOf(e));
    }
    setLoading("");
  }

  async function handleSaveTemplate(isAplus) {
    if (!activeId) return;
    const kind = isAplus ? "aplus" : "main";
    const draft = templateDrafts[kind];
    if (!draft.name.trim()) return notify("warn", "请输入模板名称");
    if (!draft.content.trim()) return notify("warn", "请先粘贴提示词内容");
    setLoading("save-template");
    try {
      await saveTemplate(activeId, { name: draft.name.trim(), content: draft.content.trim() });
      updateTemplateDraft(kind, { name: "", content: "" });
      const ts = await getTemplates(activeId);
      setTemplates(ts.templates || ts || []);
      notify("success", "模板已保存，可在下方智能套用");
    } catch (e) {
      notify("error", "保存失败: " + messageOf(e));
    }
    setLoading("");
  }

  async function loadTemplates() {
    if (!activeId) return;
    try {
      const ts = await getTemplates(activeId);
      setTemplates(ts.templates || ts || []);
    } catch {}
  }

  async function handleApplyTemplate(templateId, isAplus) {
    if (!activeId) return;
    const slots = isAplus ? aplusSlots : imageSlots;
    const firstSlot = slots.find((s) => !s.prompt) || slots[0];
    setLoading(isAplus ? "apply-aplus-template" : "apply-main-template");
    try {
      const res = await applyTemplate(activeId, {
        template_id: templateId,
        slot: firstSlot.id,
        target_group: isAplus ? "aplus" : "main",
        slots: slotPayload(slots),
        color_scheme: isAplus ? colorValue(aplusColorScheme, aplusCustomColor) : colorValue(colorScheme, customColor),
      });
      if (res.prompts && Object.keys(res.prompts).length) {
        const setter = isAplus ? setAplusSlots : setImageSlots;
        const applied = slots.filter((s) => res.prompts[s.id]).map((s) => s.label || s.id);
        const nextSlots = slots.map((s) => (res.prompts[s.id] ? { ...s, prompt: res.prompts[s.id] } : s));
        setter(nextSlots);
        await persistSlots(isAplus ? imageSlots : nextSlots, isAplus ? nextSlots : aplusSlots);
        setTemplateOpen(isAplus ? "aplus" : "main", false);
        notify("success", `已应用并保存到：${applied.join("、")}`);
      } else {
        notify("warn", "模板接口没有返回提示词");
      }
    } catch (e) {
      notify("error", "应用失败: " + messageOf(e));
    }
    setLoading("");
  }

  function renderTemplates(isAplus) {
    const kind = isAplus ? "aplus" : "main";
    const open = templatePanel[kind];
    const draft = templateDrafts[kind];
    return (
      <div style={{ marginTop: 12, border: "1px solid var(--b)", borderRadius: 4 }}>
        <div
          onClick={() => { setTemplateOpen(kind, !open); if (!open) loadTemplates(); }}
          style={{ padding: "8px 10px", fontSize: 10, fontWeight: 600, cursor: "pointer", background: "var(--bg2)" }}
        >
          {open ? "提示词模板 ▼" : "提示词模板 ▶"}
        </div>
        {open && (
          <div style={{ padding: 10 }}>
            <div style={{ fontSize: 10, color: "var(--t3)", lineHeight: 1.6, marginBottom: 6 }}>
              {isAplus
                ? "粘贴一整套 A+ 模板后保存，再点“智能套用到A+”。AI 会按当前 A+ 图片位、桌面/手机尺寸拆分提示词。"
                : "粘贴主图/副图模板后保存，再点“智能套用到主图”。AI 会按当前主图图片位拆分或套用提示词。"}
            </div>
            <textarea
              value={draft.content}
              onChange={(e) => updateTemplateDraft(kind, { content: e.target.value })}
              placeholder={isAplus ? "粘贴整套A+模板或单张A+提示词..." : "粘贴主图/副图提示词模板..."}
              rows={4}
              style={{ ...inputStyle, width: "100%", resize: "vertical", marginBottom: 6 }}
            />
            <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
              <input value={draft.name} onChange={(e) => updateTemplateDraft(kind, { name: e.target.value })} placeholder="模板名称..." style={{ ...inputStyle, flex: 1 }} />
              <Btn onClick={() => handleSaveTemplate(isAplus)} primary disabled={busy}>保存模板</Btn>
            </div>
            {templates.length > 0 && (
              <div>
                <div style={{ fontSize: 10, fontWeight: 600, marginBottom: 4 }}>已保存模板</div>
                {templates.map((tpl, i) => (
                  <div key={tpl.id || i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "5px 6px", background: "var(--bg2)", borderRadius: 3, marginBottom: 4, fontSize: 10, gap: 8 }}>
                    <span>{tpl.name || `模板${i + 1}`}</span>
                    <Btn onClick={() => handleApplyTemplate(tpl.id, isAplus)} disabled={busy}>{isAplus ? "智能套用到A+" : "智能套用到主图"}</Btn>
                  </div>
                ))}
              </div>
            )}
            {templates.length === 0 && (
              <div style={{ fontSize: 10, color: "var(--t3)", padding: "6px 0" }}>当前项目还没有保存模板</div>
            )}
          </div>
        )}
      </div>
    );
  }

  function renderSlotGrid(slots, isAplus) {
    const kind = isAplus ? "aplus" : "main";
    const sizePresets = isAplus ? SIZE_PRESETS_APLUS : SIZE_PRESETS_MAIN;
    return (
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 8 }}>
        {slots.map((slot) => (
          <div key={slot.id} style={{ border: "1px solid var(--b)", borderRadius: 4, padding: 8 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 118px auto", gap: 5, alignItems: "center", marginBottom: 6 }}>
              <input value={slot.label} onChange={(e) => updateSlot(kind, slot.id, { label: e.target.value })} style={inputStyle} />
              <input list={`${slot.id}-sizes`} value={slot.size} onChange={(e) => updateSlot(kind, slot.id, { size: e.target.value })} style={inputStyle} />
              <datalist id={`${slot.id}-sizes`}>
                {sizePresets.map((s) => <option key={s} value={s} />)}
              </datalist>
              <Btn danger disabled={busy || slots.length <= 1} onClick={() => removeSlot(kind, slot.id)}>删</Btn>
            </div>
            {slot.url && <img src={slot.url} onClick={() => setPreviewUrl(slot.url)} style={{ width: "100%", height: isAplus ? 110 : 135, objectFit: "cover", borderRadius: 3, marginBottom: 5, cursor: "zoom-in" }} />}
            <textarea
              value={slot.prompt}
              onChange={(e) => updateSlot(kind, slot.id, { prompt: e.target.value })}
              placeholder="图片提示词..."
              rows={4}
              style={{ ...inputStyle, width: "100%", resize: "vertical", marginBottom: 5, fontSize: 9 }}
            />
            <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
              <Btn onClick={() => handleGenOnePrompt(slot, isAplus)} disabled={busy}>
                {loading === `prompt-gen-${slot.id}` ? "生成中..." : loading === `prompt-review-${slot.id}` ? "自检中..." : "单张提示词"}
              </Btn>
              <Btn onClick={() => handleGenOneImage(slot, isAplus)} primary disabled={busy}>生成图片</Btn>
              <Btn onClick={() => handleDownloadImage(slot)} disabled={!slot.url}>下载图片</Btn>
              <Btn onClick={() => handleDownloadPsd(slot)} disabled={busy || !slot.url}>
                {loading === `psd-${slot.id}` ? "下载中..." : "下载PSD"}
              </Btn>
            </div>
          </div>
        ))}
      </div>
    );
  }

  function renderImageTab(isAplus) {
    const slots = isAplus ? aplusSlots : imageSlots;
    return (
      <div className="card" style={{ padding: 12 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
          <span style={{ fontSize: 10, fontWeight: 600 }}>色系</span>
          <SheetSelect value={isAplus ? aplusColorScheme : colorScheme} onChange={(v) => isAplus ? setAplusColorScheme(v) : setColorScheme(v)} style={inputStyle} title="选择色系" options={COLOR_OPTIONS} />
          {(isAplus ? aplusColorScheme : colorScheme) === "custom" && (
            <input value={isAplus ? aplusCustomColor : customColor} onChange={(e) => isAplus ? setAplusCustomColor(e.target.value) : setCustomColor(e.target.value)} placeholder="输入自定义色系..." style={{ ...inputStyle, width: 180 }} />
          )}
          <Btn onClick={() => addSlot(isAplus ? "aplus" : "main")} disabled={busy}>新增图片位</Btn>
          <Btn onClick={handleSaveSlotConfig} disabled={busy}>保存图片配置</Btn>
          <Btn onClick={() => handleGenGroupPrompts(isAplus)} primary disabled={busy}>
            {loading === (isAplus ? "gen-aplus-prompts" : "gen-main-prompts")
              ? "生成&自检中..."
              : (isAplus ? "按当前配置生成A+提示词" : "按当前配置生成主图提示词")}
          </Btn>
          <Btn onClick={() => handleGenGroupImages(isAplus)} primary disabled={busy}>{isAplus ? "生成A+图片" : "生成主图图片"}</Btn>
        </div>
        <div style={{ fontSize: 10, color: "var(--t3)", marginBottom: 8 }}>
          {isAplus ? "A+ 支持自定义数量。默认包含桌面端 1464x600 与手机端 600x450。" : "主图/副图支持自定义数量。默认 1600x1600，适合 1400x1400 以上交付。"}
        </div>
        {renderSlotGrid(slots, isAplus)}
        {renderTemplates(isAplus)}
      </div>
    );
  }

  function renderFeedback() {
    if (!feedback) return null;
    const color = feedback.type === "error" ? "var(--red)" : feedback.type === "warn" ? "var(--amber)" : "var(--acc)";
    return (
      <div style={{ border: `1px solid ${color}`, color, background: "var(--bg2)", borderRadius: 4, padding: "7px 10px", fontSize: 10, marginBottom: 10, display: "flex", gap: 8, alignItems: "center" }}>
        <span style={{ flex: 1, lineHeight: 1.5 }}>{feedback.text}</span>
        <button onClick={() => setFeedback(null)} style={{ background: "transparent", border: "none", color, cursor: "pointer", fontSize: 11 }}>x</button>
      </div>
    );
  }

  function renderFlowStatus() {
    return (
      <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginBottom: 8 }}>
        {flowStatus.map((item) => (
          <span key={item.label} className={item.ok ? "tag tg" : "tag"} style={!item.ok ? { border: "1px solid var(--b)", color: "var(--t3)", background: "var(--bg2)" } : undefined}>
            {item.ok ? "✓ " : "· "}{item.label}
          </span>
        ))}
      </div>
    );
  }

  function renderScrapeResult() {
    if (!scrapeData) {
      return (
        <div style={{ fontSize: 10, color: "var(--t3)", background: "var(--bg2)", border: "1px solid var(--b)", borderRadius: 4, padding: 10, marginTop: 10 }}>
          还没有采集结果。点击“采集ASIN数据”后，这里会显示标题、五点、描述和参考图。
        </div>
      );
    }
    return (
      <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <span className="tag tg">标题 {scraped.title ? 1 : 0}</span>
          <span className="tag tg">五点 {scraped.bullets.length}</span>
          <span className="tag tg">图片 {scraped.images.length}</span>
          {scraped.description && <span className="tag tg">描述 1</span>}
        </div>
        {scrapeData.scrape_source === "sorftime" && (
          <div style={{ fontSize: 10, padding: 10, background: "rgba(245,158,11,.10)", border: "1px solid rgba(245,158,11,.35)", borderRadius: 4, lineHeight: 1.6, color: "var(--t2)" }}>
            <strong>只采到 1 张白底主图？</strong> 完整主图组（全部 7 张）需要本地「采集服务」（amazon-image-workflow，Docker）。
            当前未检测到它，已回退到 sorftime（只返回 1 张主图）。启用方式：
            <code style={{ display: "block", marginTop: 4, color: "var(--t)" }}>cd amazon-image-workflow &amp;&amp; docker compose up -d --build</code>
            启动后重新「采集ASIN数据」即可拿到完整主图组。
          </div>
        )}
        <div style={{ fontSize: 10, padding: 10, background: "var(--bg2)", border: "1px solid var(--b)", borderRadius: 4, lineHeight: 1.6 }}>
          <div style={{ color: "var(--t3)", marginBottom: 4 }}>采集标题</div>
          <div style={{ color: "var(--t)" }}>{scraped.title || "未采集到标题"}</div>
        </div>
        {scraped.bullets.length > 0 && (
          <div style={{ fontSize: 10, padding: 10, background: "var(--bg2)", border: "1px solid var(--b)", borderRadius: 4 }}>
            <div style={{ color: "var(--t3)", marginBottom: 6 }}>采集五点</div>
            <div style={{ display: "grid", gap: 5 }}>
              {scraped.bullets.map((b, i) => (
                <div key={i} style={{ color: "var(--t2)", lineHeight: 1.6, display: "grid", gridTemplateColumns: "18px 1fr", gap: 6 }}>
                  <span style={{ color: "var(--t3)" }}>{i + 1}.</span>
                  <span>{b}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {scraped.description && (
          <div style={{ fontSize: 10, padding: 10, background: "var(--bg2)", border: "1px solid var(--b)", borderRadius: 4, color: "var(--t2)", lineHeight: 1.6 }}>
            <div style={{ color: "var(--t3)", marginBottom: 4 }}>采集描述</div>
            {scraped.description}
          </div>
        )}
        {scraped.images.length > 0 && (
          <div style={{ fontSize: 10, padding: 10, background: "var(--bg2)", border: "1px solid var(--b)", borderRadius: 4 }}>
            <div style={{ color: "var(--t3)", marginBottom: 6 }}>参考图片</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(86px, 1fr))", gap: 6 }}>
              {scraped.images.slice(0, 12).map((src, i) => (
                <img key={`${src}-${i}`} src={src} alt="" onClick={() => setPreviewUrl(src)} style={{ width: "100%", aspectRatio: "1 / 1", objectFit: "cover", border: "1px solid var(--b)", borderRadius: 4, background: "var(--bg1)", cursor: "zoom-in" }} />
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div>
      {previewUrl && (
        <div onClick={() => setPreviewUrl(null)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.85)", zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center", cursor: "zoom-out" }}>
          <img src={previewUrl} alt="" style={{ maxWidth: "90vw", maxHeight: "90vh", objectFit: "contain", borderRadius: 6, boxShadow: "0 8px 40px rgba(0,0,0,0.6)" }} onClick={(e) => e.stopPropagation()} />
        </div>
      )}
      <div className="ptitle">/ Listing 生成器</div>
      {renderFeedback()}
      <div className="listing-layout" style={{ display: "flex", gap: 12 }}>
        <div className="listing-sidebar" style={{ width: 210, flexShrink: 0 }}>
          <div className="card" style={{ padding: "8px 10px" }}>
            <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
              <input value={newAsin} onChange={(e) => setNewAsin(e.target.value)} placeholder="ASIN..." style={{ ...inputStyle, flex: 1, minWidth: 0 }} />
              <SheetSelect value={newMkt} onChange={setNewMkt} style={inputStyle} className="xsel-compact" flags title="选择站点"
                options={marketplaceOptions(["US", "UK", "DE", "JP", "FR", "IT", "ES", "CA", "AU"])} />
            </div>
            <Btn onClick={handleCreate} primary disabled={busy || !newAsin.trim()}>+ 新建</Btn>
            <div style={{ marginTop: 8 }}>
              {projects.length === 0 && <div style={{ color: "var(--t3)", fontSize: 10, lineHeight: 1.6, padding: "6px 0" }}>暂无项目，输入 ASIN 后新建。</div>}
              {projects.map((p) => (
                <div key={p.id} onClick={() => setActiveId(p.id)} style={{ padding: "6px 7px", borderRadius: 3, marginBottom: 3, cursor: "pointer", background: activeId === p.id ? "var(--bg2)" : "transparent", border: activeId === p.id ? "1px solid var(--acc)" : "1px solid transparent" }}>
                  <div style={{ fontSize: 10, fontWeight: 500, display: "flex", justifyContent: "space-between" }}>
                    <span>{p.asin}</span>
                    <span onClick={async (e) => { e.stopPropagation(); if (await confirm({ title: "删除项目", message: "确定删除此项目？", confirmText: "删除", danger: true })) { await deleteProject(p.id); await loadProjects(); notify("success", "项目已删除"); } }} style={{ color: "var(--red)", cursor: "pointer", fontSize: 9 }}>x</span>
                  </div>
                  <div style={{ fontSize: 9, color: "var(--t3)" }}>{p.marketplace} · {p.status}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          {!project ? (
            activeId ? (
              <div className="card wb-enter" style={{ padding: 12 }} aria-busy="true" aria-live="polite">
                <div className="skeleton" style={{ height: 30, marginBottom: 12 }} />
                <div className="skeleton line lg" />
                <div className="skeleton line md" />
                <div className="skeleton line lg" />
                <div className="skeleton line sm" />
              </div>
            ) : (
              <div className="card" style={{ textAlign: "center", padding: 40, color: "var(--t3)", fontSize: 11 }}>选择或新建项目</div>
            )
          ) : (
            <div className="wb-enter" key={activeId}>
              <div data-tour="listing-tabs" style={{ display: "flex", gap: 2, marginBottom: 8 }}>
                {[["scrape", "① 采集"], ["copy", "② 文案"], ["images", "③ 主图"], ["aplus", "④ A+"], ["output", "⑤ 输出"]].map(([t, l]) => (
                  <button key={t} onClick={() => setTab(t)} style={{ flex: 1, padding: "7px 0", fontSize: 10, border: "none", borderRadius: 3, cursor: "pointer", background: tab === t ? "var(--acc)" : "var(--bg2)", color: tab === t ? "#000" : "var(--t2)", fontWeight: tab === t ? 600 : 400 }}>{l}</button>
                ))}
              </div>
              {renderFlowStatus()}

              <div key={tab} className="wb-enter">
              {tab === "scrape" && (
                <div className="card" style={{ padding: 12 }}>
                  <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                    <Btn onClick={handleScrape} primary disabled={busy}>{loading === "scraping" ? "采集中..." : "采集ASIN数据"}</Btn>
                    <Btn onClick={handleAnalyze} disabled={busy}>{loading === "analyzing" ? "分析中..." : "AI分析"}</Btn>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8, marginBottom: 10, alignItems: "start" }}>
                    <Field label="产品名称"><textarea value={productInfo.product_name} onChange={(e) => setProductInfo((p) => ({ ...p, product_name: e.target.value }))} rows={2} style={{ ...inputStyle, resize: "vertical" }} /></Field>
                    <Field label="目标受众"><textarea value={productInfo.target_audience} onChange={(e) => setProductInfo((p) => ({ ...p, target_audience: e.target.value }))} rows={2} style={{ ...inputStyle, resize: "vertical" }} /></Field>
                    <Field label="核心卖点"><textarea value={productInfo.selling_points} onChange={(e) => setProductInfo((p) => ({ ...p, selling_points: e.target.value }))} rows={2} style={{ ...inputStyle, resize: "vertical" }} /></Field>
                    <Field label="产品描述"><textarea value={productInfo.description} onChange={(e) => setProductInfo((p) => ({ ...p, description: e.target.value }))} rows={2} style={{ ...inputStyle, resize: "vertical" }} /></Field>
                  </div>
                  <Btn onClick={handleSaveInfo} disabled={busy}>保存产品信息</Btn>
                  {renderScrapeResult()}
                  {analysisResult && <pre style={{ whiteSpace: "pre-wrap", color: "var(--t2)", fontSize: 10, background: "var(--bg2)", padding: 10, borderRadius: 4, maxHeight: 260, overflowY: "auto" }}>{analysisResult}</pre>}

                  {/* ── 素材图上传 ── */}
                  <div style={{ marginTop: 14, border: "1px solid var(--b)", borderRadius: 4 }}>
                    <div style={{ padding: "7px 10px", background: "var(--bg2)", fontSize: 10, fontWeight: 600, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span>素材图 / 参考图 &nbsp;<span style={{ color: "var(--t3)", fontWeight: 400 }}>生成图片时优先使用，替代采集到的竞品图</span></span>
                      <span style={{ color: "var(--t3)" }}>{refImages.uploaded.length} 张已上传</span>
                    </div>
                    <div style={{ padding: 10 }}>
                      {/* Uploaded thumbnails */}
                      {refImages.uploaded.length > 0 && (
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                          {refImages.uploaded.map((img) => (
                            <div key={img.filename} style={{ position: "relative" }}>
                              <img
                                src={img.url} alt=""
                                onClick={() => setPreviewUrl(img.url)}
                                style={{ width: 70, height: 70, objectFit: "cover", borderRadius: 3, border: "1px solid var(--b)", cursor: "zoom-in" }}
                              />
                              <button
                                onClick={() => handleDeleteRefImage(img.filename)}
                                style={{ position: "absolute", top: -4, right: -4, width: 15, height: 15, borderRadius: "50%", background: "var(--red)", border: "none", color: "#fff", fontSize: 9, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", padding: 0 }}
                              >×</button>
                            </div>
                          ))}
                        </div>
                      )}
                      {/* Upload zone */}
                      <div
                        onDragOver={e => { e.preventDefault(); setRefDragOver(true); }}
                        onDragLeave={() => setRefDragOver(false)}
                        onDrop={e => { e.preventDefault(); setRefDragOver(false); handleUploadRefImages(e.dataTransfer.files); }}
                        onClick={() => refInputRef.current?.click()}
                        style={{
                          border: `2px dashed ${refDragOver ? "var(--acc)" : "var(--b)"}`,
                          borderRadius: 4, padding: "10px", textAlign: "center", cursor: "pointer",
                          background: refDragOver ? "rgba(74,222,128,.05)" : undefined, transition: "all .15s",
                        }}
                      >
                        <span style={{ fontSize: 10, color: "var(--t3)" }}>
                          {refUploading ? "上传中…" : "拖放或点击上传本地产品素材图（白底图、场景图等）"}
                        </span>
                        <input ref={refInputRef} type="file" accept="image/*" multiple style={{ display: "none" }}
                          onChange={e => e.target.files && handleUploadRefImages(e.target.files)} />
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {tab === "copy" && (
                <div className="card" style={{ padding: 12 }}>
                  {/* Header + generate button */}
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 10, color: "var(--t3)" }}>
                        站点: <b style={{ color: "var(--t)" }}>{project.marketplace || "US"}</b>
                        &nbsp;·&nbsp;ASIN: <b style={{ color: "var(--t)" }}>{project.asin || "-"}</b>
                        &nbsp;·&nbsp;产品: <b style={{ color: "var(--t)" }}>{productInfo.product_name || "（未填写）"}</b>
                      </div>
                    </div>
                    <Btn onClick={handleAdvancedCopy} primary
                      disabled={busy || advCopyLaunching || advCopyJob?.status === "running"}>
                      {advCopyLaunching ? "启动中…" : advCopyJob?.status === "running" ? "生成中…" : "生成文案"}
                    </Btn>
                    {advCopyJob && advCopyJob.status !== "running" && (
                      <Btn onClick={() => setAdvCopyJob(null)}>重新生成</Btn>
                    )}
                  </div>

                  {/* Progress */}
                  {advCopyJob && (advCopyJob.status === "running" || advCopyJob.status === "pending") && (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ display: "flex", gap: 0, marginBottom: 8 }}>
                        {["图片识别", "竞品数据", "文案生成", "完成"].map((label, i) => {
                          const done = i < (advCopyJob.stage || 0);
                          const active = i === (advCopyJob.stage || 0);
                          return (
                            <div key={i} style={{ flex: 1, textAlign: "center" }}>
                              <div style={{
                                width: 22, height: 22, borderRadius: "50%", margin: "0 auto 4px",
                                display: "flex", alignItems: "center", justifyContent: "center",
                                background: done ? "var(--acc)" : active ? "rgba(74,222,128,.15)" : "var(--bg2)",
                                border: active ? "2px solid var(--acc)" : "2px solid var(--b)",
                                fontSize: 10, fontWeight: 700,
                                color: done ? "#000" : active ? "var(--acc)" : "var(--t3)",
                              }}>
                                {done ? "✓" : i + 1}
                              </div>
                              <div style={{ fontSize: 9, color: active ? "var(--t)" : "var(--t3)" }}>{label}</div>
                            </div>
                          );
                        })}
                      </div>
                      <div style={{ fontSize: 10, color: "var(--t3)", textAlign: "center" }}>{advCopyJob.stage_msg}</div>
                    </div>
                  )}

                  {/* Error */}
                  {advCopyJob?.status === "failed" && (
                    <div style={{ fontSize: 10, color: "var(--red)", background: "rgba(248,113,113,.08)", border: "1px solid rgba(248,113,113,.25)", borderRadius: 4, padding: "8px 10px", marginBottom: 12 }}>
                      生成失败：{advCopyJob.error}
                    </div>
                  )}

                  {/* Results */}
                  {advCopyJob?.status === "done" && advCopyJob.result && (() => {
                    const res = advCopyJob.result;
                    return (
                      <div style={{ display: "grid", gap: 10 }}>
                        {res.rationale && (
                          <div style={{ fontSize: 10, color: "var(--t2)", background: "var(--bg2)", padding: "8px 10px", borderRadius: 4, lineHeight: 1.6, borderLeft: "3px solid var(--acc)" }}>
                            {res.rationale}
                          </div>
                        )}

                        {/* Titles */}
                        {res.titles?.length > 0 && (
                          <div style={{ border: "1px solid var(--b)", borderRadius: 4, overflow: "hidden" }}>
                            <div style={{ padding: "7px 10px", background: "var(--bg2)", fontSize: 10, fontWeight: 600, display: "flex", justifyContent: "space-between" }}>
                              <span>标题方案 ({res.titles.length}个)</span>
                              <button onClick={() => copyAdvText(res.titles.join("\n\n"), "titles")} style={{ ...inputStyle, padding: "1px 6px", cursor: "pointer", fontSize: 9 }}>
                                {advCopyCopied === "titles" ? "✓ 已复制" : "复制全部"}
                              </button>
                            </div>
                            {res.titles.map((t, i) => (
                              <div key={i} style={{ padding: "7px 10px", borderTop: "1px solid var(--b)", display: "flex", gap: 8, alignItems: "flex-start" }}>
                                <span style={{ fontSize: 9, color: "var(--t3)", minWidth: 18 }}>T{i+1}</span>
                                <span style={{ flex: 1, fontSize: 10, color: "var(--t)", lineHeight: 1.6 }}>{t}</span>
                                <button onClick={() => copyAdvText(t, `t${i}`)} style={{ ...inputStyle, padding: "1px 6px", cursor: "pointer", fontSize: 9, flexShrink: 0 }}>
                                  {advCopyCopied === `t${i}` ? "✓" : "复制"}
                                </button>
                              </div>
                            ))}
                          </div>
                        )}

                        {/* Bullets A */}
                        {res.bullets_a?.length > 0 && (
                          <div style={{ border: "1px solid var(--b)", borderRadius: 4, overflow: "hidden" }}>
                            <div style={{ padding: "7px 10px", background: "var(--bg2)", fontSize: 10, fontWeight: 600, display: "flex", justifyContent: "space-between" }}>
                              <span>五点描述 Set A（转化焦点）</span>
                              <button onClick={() => copyAdvText(res.bullets_a.join("\n\n"), "ba")} style={{ ...inputStyle, padding: "1px 6px", cursor: "pointer", fontSize: 9 }}>
                                {advCopyCopied === "ba" ? "✓" : "复制全部"}
                              </button>
                            </div>
                            {res.bullets_a.map((b, i) => (
                              <div key={i} style={{ padding: "7px 10px", borderTop: "1px solid var(--b)", display: "flex", gap: 8, alignItems: "flex-start" }}>
                                <span style={{ fontSize: 10, color: "var(--acc)", fontWeight: 700, minWidth: 18 }}>{i+1}.</span>
                                <span style={{ flex: 1, fontSize: 10, color: "var(--t)", lineHeight: 1.6 }}>{b}</span>
                                <button onClick={() => copyAdvText(b, `ba${i}`)} style={{ ...inputStyle, padding: "1px 6px", cursor: "pointer", fontSize: 9, flexShrink: 0 }}>
                                  {advCopyCopied === `ba${i}` ? "✓" : "复制"}
                                </button>
                              </div>
                            ))}
                          </div>
                        )}

                        {/* Bullets B */}
                        {res.bullets_b?.length > 0 && (
                          <div style={{ border: "1px solid var(--b)", borderRadius: 4, overflow: "hidden" }}>
                            <div style={{ padding: "7px 10px", background: "var(--bg2)", fontSize: 10, fontWeight: 600, display: "flex", justifyContent: "space-between" }}>
                              <span>五点描述 Set B（Rufus 问答焦点）</span>
                              <button onClick={() => copyAdvText(res.bullets_b.join("\n\n"), "bb")} style={{ ...inputStyle, padding: "1px 6px", cursor: "pointer", fontSize: 9 }}>
                                {advCopyCopied === "bb" ? "✓" : "复制全部"}
                              </button>
                            </div>
                            {res.bullets_b.map((b, i) => (
                              <div key={i} style={{ padding: "7px 10px", borderTop: "1px solid var(--b)", display: "flex", gap: 8, alignItems: "flex-start" }}>
                                <span style={{ fontSize: 10, color: "var(--acc)", fontWeight: 700, minWidth: 18 }}>{i+1}.</span>
                                <span style={{ flex: 1, fontSize: 10, color: "var(--t)", lineHeight: 1.6 }}>{b}</span>
                                <button onClick={() => copyAdvText(b, `bb${i}`)} style={{ ...inputStyle, padding: "1px 6px", cursor: "pointer", fontSize: 9, flexShrink: 0 }}>
                                  {advCopyCopied === `bb${i}` ? "✓" : "复制"}
                                </button>
                              </div>
                            ))}
                          </div>
                        )}

                        {/* Search Terms */}
                        {res.search_terms?.length > 0 && (
                          <div style={{ border: "1px solid var(--b)", borderRadius: 4, overflow: "hidden" }}>
                            <div style={{ padding: "7px 10px", background: "var(--bg2)", fontSize: 10, fontWeight: 600 }}>后台 Search Terms</div>
                            {res.search_terms.map((st, i) => (
                              <div key={i} style={{ padding: "7px 10px", borderTop: "1px solid var(--b)", display: "flex", gap: 8, alignItems: "flex-start" }}>
                                <span style={{ fontSize: 9, color: st.length > 249 ? "var(--red)" : "var(--acc)", minWidth: 44 }}>ST{i+1} {st.length}字</span>
                                <span style={{ flex: 1, fontSize: 10, color: "var(--t2)", wordBreak: "break-all", lineHeight: 1.6 }}>{st}</span>
                                <button onClick={() => copyAdvText(st, `st${i}`)} style={{ ...inputStyle, padding: "1px 6px", cursor: "pointer", fontSize: 9, flexShrink: 0 }}>
                                  {advCopyCopied === `st${i}` ? "✓" : "复制"}
                                </button>
                              </div>
                            ))}
                          </div>
                        )}

                        {/* Compliance */}
                        {res.compliance_notes?.length > 0 && (
                          <div style={{ fontSize: 10, background: "rgba(251,191,36,.06)", border: "1px solid rgba(251,191,36,.2)", borderRadius: 4, padding: "8px 10px" }}>
                            <div style={{ color: "var(--amber)", fontWeight: 600, marginBottom: 4 }}>合规检查</div>
                            {res.compliance_notes.map((n, i) => <div key={i} style={{ color: "var(--t2)", lineHeight: 1.6 }}>· {n}</div>)}
                          </div>
                        )}

                        {/* Raw fallback */}
                        {!res.titles && res.raw && (
                          <pre style={{ fontSize: 10, color: "var(--t2)", background: "var(--bg2)", padding: 10, borderRadius: 4, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{res.raw}</pre>
                        )}
                      </div>
                    );
                  })()}

                  {/* Empty state */}
                  {!advCopyJob && (
                    <div style={{ fontSize: 10, color: "var(--t3)", textAlign: "center", padding: "24px 0" }}>
                      点击"生成文案"，AI 将结合产品信息和竞品数据生成<br />标题×5 · 五点×2套 · Search Terms×2
                    </div>
                  )}
                </div>
              )}

              {tab === "images" && renderImageTab(false)}
              {tab === "aplus" && renderImageTab(true)}

              {tab === "output" && (
                <div className="card" style={{ padding: 12 }}>
                  <div className="ct" style={{ marginBottom: 8 }}>Listing 完整输出</div>
                  {advCopyJob?.status === "done" && advCopyJob.result?.titles ? (
                    <pre style={{ whiteSpace: "pre-wrap", color: "var(--t2)", fontSize: 10, background: "var(--bg2)", padding: 10, borderRadius: 4 }}>
                      {[
                        "=== 标题方案 ===",
                        ...(advCopyJob.result.titles || []).map((t, i) => `T${i+1}: ${t}`),
                        "",
                        "=== 五点 Set A ===",
                        ...(advCopyJob.result.bullets_a || []).map((b, i) => `${i+1}. ${b}`),
                        "",
                        "=== 五点 Set B ===",
                        ...(advCopyJob.result.bullets_b || []).map((b, i) => `${i+1}. ${b}`),
                        "",
                        "=== Search Terms ===",
                        ...(advCopyJob.result.search_terms || []).map((st, i) => `ST${i+1}: ${st}`),
                      ].join("\n")}
                    </pre>
                  ) : (
                    <pre style={{ whiteSpace: "pre-wrap", color: "var(--t2)", fontSize: 10, background: "var(--bg2)", padding: 10, borderRadius: 4 }}>{`TITLE:\n${copyResult.title || ""}\n\nBULLETS:\n${copyResult.bullets || ""}\n\nSEARCH TERMS:\n${copyResult.search_terms || ""}\n\nA+ COPY:\n${copyResult.aplus || ""}`}</pre>
                  )}
                  <div style={{ display: "flex", gap: 4, overflowX: "auto", marginTop: 8 }}>
                    {[...imageSlots, ...aplusSlots].filter((s) => s.url).map((s) => <img key={s.id} src={s.url} title={s.label} style={{ height: 80, borderRadius: 3 }} />)}
                  </div>
                </div>
              )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
