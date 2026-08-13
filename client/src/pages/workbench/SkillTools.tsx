import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  listTools, runTool, pinTool, listRuns, getRun, deleteRun, repairTool, enrichTool,
  type SkillToolMeta, type SkillInput, type SseEvent, type SkillRunSummary, type RepairResult,
} from "../../api/skillTools";
import SheetSelect from "../../components/SheetSelect";
import { deleteSkill, updateSkill } from "../../api/skill";
import { useConfirm } from "../../components/ConfirmDialog";
import { errText } from "../../lib/errText";

// ── Design tokens re-used below ──────────────────────────────────────────────
const S = {
  // accent border on left edge of headings
  accentBar: { borderLeft: "2px solid var(--acc)", paddingLeft: 8 } as React.CSSProperties,
  // thin section divider
  divider: { height: 1, background: "var(--b)", margin: "12px 0" } as React.CSSProperties,
  // section label (like .sl in CSS but without extra top margin here)
  sectionLabel: { fontSize: "var(--fs-9)", color: "var(--t3)", letterSpacing: ".10em", textTransform: "uppercase" as const, marginBottom: 6 },
};

// ── Kind badge (uses the existing .tag colour variants) ──────────────────────
const KIND_META: Record<string, { label: string; cls: string }> = {
  report:    { label: "报告",  cls: "tg"    },   // green
  transform: { label: "转换",  cls: "tb-tag" },   // blue
  lookup:    { label: "查询",  cls: "tc"    },   // cyan
  workflow:  { label: "流程",  cls: "ta"    },   // amber
};
const RUNTIME_META: Record<string, { label: string; cls: string }> = {
  mcp:       { label: "需数据", cls: "tr"    },   // red/warn
  "llm-only":{ label: "纯AI",  cls: "tp"    },   // purple
};

// 有声明式参数表单的是「参数化工具」；其余（文档型 skill）由 Agent 按文档执行。
function isParamTool(t: SkillToolMeta): boolean {
  return t.has_execution && (t.inputs.length > 0 || !!t.kind);
}

function KindBadge({ tool }: { tool: SkillToolMeta }) {
  const km = tool.kind ? KIND_META[tool.kind] : null;
  const rm = tool.runtime ? RUNTIME_META[tool.runtime] : null;
  const agent = !km && !rm && !isParamTool(tool);
  if (!km && !rm && !agent) return null;
  return (
    <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
      {km && <span className={`tag ${km.cls}`} style={{ fontSize: "var(--fs-8)" }}>{km.label}</span>}
      {rm && <span className={`tag ${rm.cls}`} style={{ fontSize: "var(--fs-8)" }}>{rm.label}</span>}
      {agent && <span className="tag tb-tag" style={{ fontSize: "var(--fs-8)" }}>Agent</span>}
    </span>
  );
}

// ── Small icon box (like .agent-ico) ─────────────────────────────────────────
function IconBox({ children, size = 28 }: { children: React.ReactNode; size?: number }) {
  return (
    <span style={{
      width: size, height: size, borderRadius: 4, flexShrink: 0,
      display: "flex", alignItems: "center", justifyContent: "center",
      fontSize: size * 0.55,
      background: "color-mix(in srgb, var(--acc) 10%, transparent)",
      border: "1px solid color-mix(in srgb, var(--acc) 22%, transparent)",
    }}>
      {children}
    </span>
  );
}

// ── List page ─────────────────────────────────────────────────────────────────
export default function SkillTools({ embedded }: { embedded?: boolean } = {}) {
  const [tools, setTools]       = useState<SkillToolMeta[]>([]);
  const [categories, setCategories] = useState<Record<string, number>>({});
  const [filterCat, setFilterCat]   = useState<string>("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch]         = useState("");
  const [loading, setLoading]       = useState(true);
  const [activeTool, setActiveTool] = useState<SkillToolMeta | null>(null);
  const [collapsedCats, setCollapsedCats] = useState<Record<string, boolean>>({});
  const routerLoc = useLocation();
  const confirm   = useConfirm();

  // Debounce search → each keystroke used to trigger a full backend rescan
  // (list = 90 × SKILL.md read+parse per request).
  useEffect(() => {
    const t = window.setTimeout(() => setSearch(searchInput), 300);
    return () => window.clearTimeout(t);
  }, [searchInput]);

  const handleDelete = useCallback(async (tool: SkillToolMeta, e?: React.MouseEvent) => {
    e?.stopPropagation();
    const ok = await confirm({
      title: "删除工具",
      message: `确定删除「${tool.description_zh || tool.name.split("/").pop()}」？\n该 Skill 会移入回收站，7 天内可在 Skill 管理中恢复。`,
      confirmText: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      if (tool.pinned) { try { await pinTool(tool.name, false); } catch { /**/ } }
      await deleteSkill(tool.name);
      window.dispatchEvent(new CustomEvent("ivyea-ops:pinned-changed"));
      if (activeTool?.name === tool.name) setActiveTool(null);
      await loadTools();
    } catch { /**/ }
  }, [confirm, activeTool]);

  const loadTools = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listTools(filterCat || undefined, search || undefined);
      setTools(res.tools);
      setCategories(res.categories);
      const want = new URLSearchParams(window.location.search).get("tool");
      if (want && !activeTool) {
        const hit = res.tools.find((t) => t.name === want);
        if (hit) setActiveTool(hit);
      }
    } catch { /**/ }
    setLoading(false);
  }, [filterCat, search]);

  useEffect(() => { loadTools(); }, [loadTools]);

  useEffect(() => {
    const want = new URLSearchParams(routerLoc.search).get("tool");
    if (!want) { setActiveTool(null); return; }
    setTools((cur) => {
      const hit = cur.find((t) => t.name === want);
      if (hit) setActiveTool(hit);
      return cur;
    });
  }, [routerLoc.search]);

  // After 工具化/repair rewrites a skill, refresh the list and the open panel
  // so the new parameter form shows up immediately.
  const refreshActiveTool = useCallback(async () => {
    try {
      const res = await listTools(filterCat || undefined, search || undefined);
      setTools(res.tools);
      setCategories(res.categories);
      setActiveTool((cur) => (cur ? res.tools.find((t) => t.name === cur.name) ?? cur : cur));
    } catch { /**/ }
  }, [filterCat, search]);

  // ── Active tool: panel view ──
  if (activeTool) {
    return (
      <div>
        {/* Topbar row */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
          <button className="tbtn" onClick={() => setActiveTool(null)}>← 返回</button>
          <span style={{ fontSize: "var(--fs-10)", color: "var(--t3)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            运营商店 / <span style={{ color: "var(--t2)" }}>{activeTool.name.split("/").pop()}</span>
          </span>
          <button
            className="tbtn"
            style={{ color: "var(--red)", borderColor: "rgba(248,113,113,.35)" }}
            onClick={() => handleDelete(activeTool)}
          >
            删除工具
          </button>
        </div>
        <ToolPanel
          key={activeTool.name + ":" + activeTool.inputs.length}
          tool={activeTool}
          onToolChanged={refreshActiveTool}
        />
      </div>
    );
  }

  // ── List view ──
  // 参数化工具（有表单，开箱即用）置顶；文档型技能按分类分组，由 Agent 执行。
  const paramTools = tools.filter(isParamTool);
  const agentSkills = tools.filter((t) => !isParamTool(t));
  const grouped: Record<string, SkillToolMeta[]> = {};
  for (const t of agentSkills) {
    const cat = t.category || "(未分类)";
    if (!grouped[cat]) grouped[cat] = [];
    grouped[cat].push(t);
  }

  return (
    <div>
      {!embedded && <div className="ptitle">/ 运营商店</div>}

      {/* Search + filter bar */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 180px", position: "relative" }}>
          <span style={{ position: "absolute", left: 9, top: "50%", transform: "translateY(-50%)", color: "var(--t3)", fontSize: "var(--fs-10)", pointerEvents: "none" }}>◎</span>
          <input
            className="inp"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="搜索工具名称或描述…"
            style={{ paddingLeft: 26 }}
          />
        </div>
        <SheetSelect
          className="inp"
          style={{ flex: "0 0 150px" }}
          value={filterCat}
          onChange={setFilterCat}
          title="选择分类"
          options={[
            { value: "", label: `全部分类 (${tools.length})` },
            ...Object.entries(categories).map(([cat, count]) => ({ value: cat, label: `${cat} (${count})` })),
          ]}
        />
      </div>

      {loading && (
        <div aria-busy="true" aria-live="polite" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10, paddingTop: 6 }}>
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="card" style={{ padding: 12 }}>
              <div className="skeleton line md" />
              <div className="skeleton line lg" />
              <div className="skeleton line sm" />
            </div>
          ))}
        </div>
      )}

      {!loading && tools.length === 0 && (
        <div style={{ textAlign: "center", padding: "40px 0", color: "var(--t3)" }}>
          <div style={{ fontSize: 28, marginBottom: 10 }}>⊞</div>
          <div style={{ fontSize: "var(--fs-12)", color: "var(--t2)", marginBottom: 6 }}>暂无可执行工具</div>
          <div style={{ fontSize: "var(--fs-10)" }}>在「想法工坊」中创建 Skill 后，会自动出现在这里</div>
        </div>
      )}

      {/* ── 参数化工具（有表单，开箱即用）── */}
      {!loading && paramTools.length > 0 && (
        <div style={{ marginBottom: 22 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, ...S.accentBar }}>
            <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)", letterSpacing: ".10em", textTransform: "uppercase", flex: 1 }}>
              ⚡ 参数化工具 · 填参数即用
            </span>
            <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>{paramTools.length}</span>
          </div>
          <div className="g3">
            {paramTools.map((t) => (
              <ToolCard key={t.name} tool={t} onOpen={() => setActiveTool(t)} onDelete={(e) => handleDelete(t, e)} />
            ))}
          </div>
        </div>
      )}

      {/* ── Agent 技能（文档型，描述任务交给 Agent 执行）── */}
      {!loading && agentSkills.length > 0 && (
        <div style={{ fontSize: "var(--fs-9)", color: "var(--t3)", letterSpacing: ".06em", marginBottom: 10 }}>
          🤖 AGENT 技能 · 点开描述任务，由 Agent 按技能文档执行
        </div>
      )}
      {!loading && Object.entries(grouped).map(([cat, items]) => {
        const collapsed = !!collapsedCats[cat];
        return (
          <div key={cat} style={{ marginBottom: 20 }}>
            {/* Category header */}
            <div
              onClick={() => setCollapsedCats((m) => ({ ...m, [cat]: !m[cat] }))}
              style={{
                display: "flex", alignItems: "center", gap: 8, marginBottom: 8,
                cursor: "pointer", userSelect: "none",
                ...S.accentBar,
              }}
            >
              <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)", letterSpacing: ".10em", textTransform: "uppercase", flex: 1 }}>{cat}</span>
              <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>{items.length}</span>
              <span style={{
                fontSize: "var(--fs-8)", color: "var(--t3)",
                transform: collapsed ? "rotate(-90deg)" : "none",
                transition: "transform .14s",
                display: "inline-block",
              }}>▼</span>
            </div>

            {!collapsed && (
              <div className="g3">
                {items.map((t) => (
                  <ToolCard key={t.name} tool={t} onOpen={() => setActiveTool(t)} onDelete={(e) => handleDelete(t, e)} />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Tool card ─────────────────────────────────────────────────────────────────
function ToolCard({ tool: t, onOpen, onDelete }: {
  tool: SkillToolMeta;
  onOpen: () => void;
  onDelete: (e: React.MouseEvent) => void;
}) {
  return (
    <div className="skill-card" onClick={onOpen}>
      {/* Card header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 8 }}>
        <IconBox>{t.icon}</IconBox>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: "var(--fs-11)", fontWeight: 600, color: "var(--t)", lineHeight: 1.3, marginBottom: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {t.pinned && <span style={{ color: "var(--acc)", marginRight: 3 }} title="已固定到侧边栏">★</span>}
            {t.name.split("/").pop()}
          </div>
          <KindBadge tool={t} />
        </div>
        <button
          title="删除工具"
          onClick={onDelete}
          style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: "var(--fs-11)", padding: "0 2px", lineHeight: 1, flexShrink: 0 }}
        >✕</button>
      </div>

      {/* Description */}
      <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", lineHeight: 1.55, marginBottom: t.inputs.length ? 8 : 0, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
        {t.description_zh || t.description || "—"}
      </div>

      {/* Footer row */}
      {t.inputs.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6, borderTop: "1px solid var(--b)", paddingTop: 6 }}>
          <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>
            {t.inputs.length} 个参数
          </span>
        </div>
      )}
    </div>
  );
}

// ── Tool execution panel ──────────────────────────────────────────────────────
function ToolPanel({ tool, onToolChanged }: { tool: SkillToolMeta; onToolChanged?: () => Promise<void> | void }) {
  const [params, setParams] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const inp of tool.inputs) init[inp.name] = inp.default || "";
    return init;
  });
  // 文档型技能的通用任务输入（无声明式参数表单时显示）。
  const [task, setTask] = useState("");
  const needsTask = tool.inputs.length === 0 && !tool.has_execution;
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");
  const [output, setOutput]     = useState("");
  const [provider, setProvider] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const [runs, setRuns]               = useState<SkillRunSummary[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [repair, setRepair]           = useState<RepairResult | null>(null);
  const [repairing, setRepairing]     = useState(false);
  const [applying, setApplying]       = useState(false);
  const [enrich, setEnrich]           = useState<RepairResult | null>(null);
  const [enriching, setEnriching]     = useState(false);
  const [applyingEnrich, setApplyingEnrich] = useState(false);
  const [pinned, setPinned]           = useState(!!tool.pinned);
  const [pinning, setPinning]         = useState(false);

  const loadHistory = useCallback(async () => {
    try { setRuns(await listRuns(tool.name)); } catch { /**/ }
  }, [tool.name]);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  const setParam = (name: string, v: string) =>
    setParams((prev) => ({ ...prev, [name]: v }));

  const sample    = tool.sample_params || {};
  const hasSample = Object.keys(sample).length > 0;
  const fillSample = () =>
    setParams((prev) => {
      const next = { ...prev };
      for (const [k, v] of Object.entries(sample)) next[k] = String(v ?? "");
      return next;
    });

  const toBase64 = (file: File): Promise<string> =>
    new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result as string);
      r.onerror = rej;
      r.readAsDataURL(file);
    });

  const renderControl = (inp: SkillInput) => {
    const val = params[inp.name] || "";
    const set = (v: string) => setParam(inp.name, v);

    if (inp.type === "file") return (
      <div>
        <label style={{
          display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
          padding: "7px 10px", background: "var(--bg2)", border: "1px solid var(--b)",
          borderRadius: "var(--r)", fontSize: "var(--fs-11)", color: "var(--t2)", transition: "border-color .1s",
        }}
          onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--b2)")}
          onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--b)")}
        >
          <span style={{ fontSize: "var(--fs-14)" }}>📎</span>
          <span>{val ? "已选择图片 ✓" : (inp.placeholder || "点击选择图片…")}</span>
          <input type="file" accept="image/*,application/pdf" style={{ display: "none" }}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              try { set(await toBase64(file)); } catch { /**/ }
            }} />
        </label>
        {val && (
          <div style={{ display: "flex", gap: 8, marginTop: 4, alignItems: "center" }}>
            {val.startsWith("data:image/") && (
              <img src={val} alt="" style={{ height: 48, borderRadius: 3, border: "1px solid var(--b)", objectFit: "cover" }} />
            )}
            <button className="tbtn" style={{ fontSize: "var(--fs-9)" }} onClick={() => set("")}>移除</button>
          </div>
        )}
      </div>
    );

    if (inp.type === "select" && inp.options?.length) return (
      <SheetSelect className="inp" value={val} onChange={set} title={inp.label || "请选择"}
        options={[
          { value: "", label: inp.placeholder || "请选择…" },
          ...inp.options.map((o) => ({ value: o, label: o })),
        ]} />
    );
    if (inp.type === "textarea") return (
      <textarea className="inp" rows={3} value={val} onChange={(e) => set(e.target.value)}
        placeholder={inp.placeholder} style={{ resize: "vertical", fontFamily: "inherit" }} />
    );
    if (inp.type === "boolean") return (
      <label style={{ fontSize: "var(--fs-11)", color: "var(--t2)", display: "flex", alignItems: "center", gap: 7, cursor: "pointer" }}>
        <input type="checkbox" checked={val === "true"} onChange={(e) => set(e.target.checked ? "true" : "false")} /> 是
      </label>
    );
    if (inp.type === "number") return (
      <input type="number" className="inp" value={val} onChange={(e) => set(e.target.value)} placeholder={inp.placeholder} />
    );
    if (inp.type === "date") return (
      <input type="date" className="inp" value={val} onChange={(e) => set(e.target.value)} />
    );
    return <input className="inp" value={val} onChange={(e) => set(e.target.value)} placeholder={inp.placeholder || inp.label} />;
  };

  const run = useCallback(async (override?: Record<string, string>) => {
    let effective = override || params;
    if (override) setParams(override);
    for (const inp of tool.inputs) {
      if (inp.required && !effective[inp.name]?.trim()) {
        setError(`请填写必填参数：${inp.label}`);
        return;
      }
    }
    if (!override && tool.inputs.length === 0) {
      if (needsTask && !task.trim()) {
        setError("请先描述你的任务，Agent 会按该技能的文档执行");
        return;
      }
      if (task.trim()) effective = { ...effective, task: task.trim() };
    }
    setLoading(true); setError(""); setOutput(""); setProvider(""); setRepair(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await runTool(tool.name, effective, (evt: SseEvent) => {
        if (evt.type === "token") { setOutput((p) => p + evt.text); setProvider(evt.provider); }
        else if (evt.type === "error") setError(evt.detail);
      }, ctrl.signal);
    } catch (e: any) {
      if (e.name !== "AbortError") setError(e?.message || "执行失败");
    } finally {
      setLoading(false); abortRef.current = null; loadHistory();
    }
  }, [tool, params, task, needsTask, loadHistory]);

  const doEnrich = useCallback(async () => {
    setEnriching(true);
    setError("");
    try { setEnrich(await enrichTool(tool.name)); }
    catch (e: any) { setError(errText(e, "AI 工具化失败")); }
    finally { setEnriching(false); }
  }, [tool.name]);

  const applyEnrich = useCallback(async () => {
    if (!enrich) return;
    setApplyingEnrich(true);
    try {
      await updateSkill(tool.name, enrich.frontmatter, enrich.body);
      setEnrich(null);
      await onToolChanged?.();   // parent remounts the panel with the new form
    } catch (e: any) {
      setError(errText(e, "应用失败"));
    } finally { setApplyingEnrich(false); }
  }, [enrich, tool.name, onToolChanged]);

  const doRepair = useCallback(async () => {
    setRepairing(true);
    try { setRepair(await repairTool(tool.name, error)); }
    catch (e: any) { setError(errText(e, "AI 修复失败")); }
    finally { setRepairing(false); }
  }, [tool.name, error]);

  const applyRepair = useCallback(async () => {
    if (!repair) return;
    setApplying(true);
    try { await updateSkill(tool.name, repair.frontmatter, repair.body); setRepair(null); await run(); }
    catch (e: any) { setError(errText(e, "应用修复失败")); }
    finally { setApplying(false); }
  }, [repair, tool.name, run]);

  const viewRun = useCallback(async (id: string) => {
    try { const f = await getRun(tool.name, id); setOutput(f.output); setProvider(f.provider); setError(f.error || ""); }
    catch { /**/ }
  }, [tool.name]);

  const removeRun = useCallback(async (id: string) => {
    try { await deleteRun(tool.name, id); await loadHistory(); } catch { /**/ }
  }, [tool.name, loadHistory]);

  const togglePin = useCallback(async () => {
    setPinning(true);
    try {
      const next = !pinned;
      await pinTool(tool.name, next);
      setPinned(next);
      window.dispatchEvent(new CustomEvent("ivyea-ops:pinned-changed"));
    } catch { /**/ } finally { setPinning(false); }
  }, [pinned, tool.name]);

  const copyOut = () => { void navigator.clipboard?.writeText(output); };
  const exportOut = () => {
    const blob = new Blob([output], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${tool.name.split("/").pop()}.md`; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      {/* ── Panel header ── */}
      <div className="card" style={{ marginBottom: 14, background: "var(--bg2)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <IconBox size={34}>{tool.icon}</IconBox>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: "var(--fs-13)", fontWeight: 600, color: "var(--t)", marginBottom: 3 }}>
              {tool.name.split("/").pop()}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
              <KindBadge tool={tool} />
              {tool.category && (
                <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>{tool.category}</span>
              )}
            </div>
          </div>
          <button
            className="tbtn"
            onClick={togglePin}
            disabled={pinning}
            style={{ fontSize: "var(--fs-10)", color: pinned ? "var(--acc)" : "var(--t3)", borderColor: pinned ? "color-mix(in srgb, var(--acc) 40%, transparent)" : "var(--b)", background: pinned ? "color-mix(in srgb, var(--acc) 6%, transparent)" : "transparent" }}
            title={pinned ? "从侧边栏移除" : "固定到侧边栏"}
          >
            {pinned ? "★ 已固定" : "☆ 固定"}
          </button>
        </div>

        {(tool.description_zh || tool.description) && (
          <>
            <div style={S.divider} />
            <div style={{ fontSize: "var(--fs-10)", color: "var(--t2)", lineHeight: 1.65 }}>
              {tool.description_zh || tool.description}
            </div>
          </>
        )}
      </div>

      {/* ── Agent-execution notice for doc-style skills ── */}
      {needsTask && (
        <div style={{ fontSize: "var(--fs-10)", color: "var(--t2)", background: "color-mix(in srgb, var(--blue) 6%, transparent)", border: "1px solid color-mix(in srgb, var(--blue) 22%, transparent)", borderLeft: "3px solid var(--blue)", borderRadius: "var(--r)", padding: "10px 12px", marginBottom: 14, lineHeight: 1.7 }}>
          🤖 这是一个文档型技能，没有参数表单——在下面描述你的任务，Agent 会按该技能的文档来执行。
          需要固定参数表单的话，可在「管理」中编辑该 Skill 补充 inputs。
        </div>
      )}

      {/* ── Form ── */}
      {tool.inputs.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ ...S.sectionLabel, ...S.accentBar, marginBottom: 10 }}>参数</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {tool.inputs.map((inp: SkillInput) => (
              <div key={inp.name}>
                <label style={{ fontSize: "var(--fs-10)", color: "var(--t2)", display: "flex", alignItems: "center", gap: 4, marginBottom: 4 }}>
                  {inp.label}
                  {inp.required && <span style={{ color: "var(--red)", fontSize: "var(--fs-10)" }}>*</span>}
                  {inp.placeholder && inp.type !== "select" && (
                    <span style={{ color: "var(--t3)", fontSize: "var(--fs-9)", fontWeight: 400, marginLeft: 2 }}>— {inp.placeholder}</span>
                  )}
                </label>
                {renderControl(inp)}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Universal task input for skills without a declared form ── */}
      {tool.inputs.length === 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
            <div style={{ ...S.sectionLabel, ...S.accentBar, marginBottom: 0, flex: 1 }}>
              任务描述{needsTask ? "" : "（可选）"}
            </div>
            {!enrich && (
              <button
                className="tbtn"
                onClick={doEnrich}
                disabled={enriching || loading}
                title="让 AI 分析该技能文档，生成固定参数表单（审核后应用），把它变成填参数即用的可视化工具"
                style={{ fontSize: "var(--fs-9)", color: "var(--acc)", borderColor: "color-mix(in srgb, var(--acc) 35%, transparent)" }}
              >
                {enriching ? <><span className="spin" style={{ marginRight: 4 }} />分析中…</> : "⚡ AI 工具化（生成参数表单）"}
              </button>
            )}
          </div>
          <textarea
            className="inp"
            rows={3}
            value={task}
            onChange={(e) => setTask(e.target.value)}
            placeholder={needsTask
              ? "描述你要完成的任务，例如：帮我用这个技能处理…"
              : "可补充具体要求；留空则按技能默认流程执行"}
            style={{ resize: "vertical", fontFamily: "inherit", width: "100%" }}
            disabled={loading}
          />
        </div>
      )}

      {/* ── Execute row ── */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
        <button
          className="btn-acc"
          onClick={() => run()}
          disabled={loading || (needsTask && !task.trim())}
          style={{ fontSize: "var(--fs-11)", padding: "6px 16px", display: "flex", alignItems: "center", gap: 6 }}
        >
          {loading ? <><span className="spin" />执行中…</> : needsTask ? "▷ 交给 Agent 执行" : "▷ 执行"}
        </button>
        {hasSample && !loading && (
          <button className="tbtn" onClick={fillSample} style={{ fontSize: "var(--fs-10)" }}>填充示例</button>
        )}
        {loading && (
          <button className="tbtn" onClick={() => abortRef.current?.abort()} style={{ fontSize: "var(--fs-10)", color: "var(--red)", borderColor: "rgba(248,113,113,.35)" }}>
            停止
          </button>
        )}
      </div>

      {/* ── Error + repair ── */}
      {error && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--red)", background: "rgba(248,113,113,.07)", border: "1px solid rgba(248,113,113,.25)", borderLeft: "3px solid var(--red)", borderRadius: "var(--r)", padding: "8px 12px", lineHeight: 1.65 }}>
            {error}
          </div>
          {!repair && (
            <button className="tbtn" onClick={doRepair} disabled={repairing}
              style={{ fontSize: "var(--fs-10)", marginTop: 6, color: "var(--amber)", borderColor: "rgba(251,191,36,.35)" }}>
              {repairing ? <><span className="spin" style={{ marginRight: 5 }} />AI 分析中…</> : "🛠 让 AI 修复此工具"}
            </button>
          )}
        </div>
      )}

      {/* ── 工具化 proposal ── */}
      {enrich && (
        <div style={{ marginTop: 12, background: "var(--bg2)", border: "1px solid color-mix(in srgb, var(--acc) 30%, transparent)", borderLeft: "3px solid var(--acc)", borderRadius: "var(--r)", padding: "10px 12px" }}>
          <div style={{ fontSize: "var(--fs-10)", fontWeight: 600, color: "var(--acc)", marginBottom: 4 }}>
            ⚡ AI 提议的参数表单（审核后应用）
          </div>
          <div style={{ fontSize: "var(--fs-9)", color: "var(--t3)", marginBottom: 8 }}>
            提炼出 {(enrich.frontmatter as any)?.inputs?.length ?? 0} 个参数
            {(enrich.frontmatter as any)?.tool?.kind ? ` · 类型 ${(enrich.frontmatter as any).tool.kind}` : ""}
            {(enrich.frontmatter as any)?.tool?.runtime ? ` · 运行时 ${(enrich.frontmatter as any).tool.runtime}` : ""}
            ；应用后该技能变为填参数即用的可视化工具，原文档内容保留。
          </div>
          {!enrich.validation.ok && (
            <div style={{ fontSize: "var(--fs-9)", color: "var(--red)", marginBottom: 8 }}>
              仍有问题：{enrich.validation.errors.join("；")}
            </div>
          )}
          <pre style={{ fontSize: "var(--fs-10)", lineHeight: 1.6, maxHeight: 260, overflow: "auto", padding: 10, background: "var(--bg1)", borderRadius: "var(--r)", whiteSpace: "pre-wrap", wordBreak: "break-word", border: "1px solid var(--b)" }}>
            {enrich.preview}
          </pre>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="btn-acc" onClick={applyEnrich} disabled={applyingEnrich || !enrich.validation.ok}
              style={{ fontSize: "var(--fs-10)", padding: "5px 12px" }}>
              {applyingEnrich ? "应用中…" : "✓ 应用，变成参数化工具"}
            </button>
            <button className="tbtn" onClick={() => setEnrich(null)} style={{ fontSize: "var(--fs-10)" }}>取消</button>
          </div>
        </div>
      )}

      {/* ── Repair proposal ── */}
      {repair && (
        <div style={{ marginTop: 12, background: "var(--bg2)", border: "1px solid rgba(251,191,36,.30)", borderLeft: "3px solid var(--amber)", borderRadius: "var(--r)", padding: "10px 12px" }}>
          <div style={{ fontSize: "var(--fs-10)", fontWeight: 600, color: "var(--amber)", marginBottom: 8 }}>AI 提议的修复（审核后应用）</div>
          {!repair.validation.ok && (
            <div style={{ fontSize: "var(--fs-9)", color: "var(--red)", marginBottom: 8 }}>
              仍有问题：{repair.validation.errors.join("；")}
            </div>
          )}
          <pre style={{ fontSize: "var(--fs-10)", lineHeight: 1.6, maxHeight: 260, overflow: "auto", padding: 10, background: "var(--bg1)", borderRadius: "var(--r)", whiteSpace: "pre-wrap", wordBreak: "break-word", border: "1px solid var(--b)" }}>
            {repair.preview}
          </pre>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="btn-acc" onClick={applyRepair} disabled={applying}
              style={{ fontSize: "var(--fs-10)", padding: "5px 12px" }}>
              {applying ? "应用中…" : "✓ 应用并重试"}
            </button>
            <button className="tbtn" onClick={() => setRepair(null)} style={{ fontSize: "var(--fs-10)" }}>取消</button>
          </div>
        </div>
      )}

      {/* ── Output ── */}
      {(output || loading) && (
        <div style={{ marginTop: 14 }}>
          {/* Output header */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, ...S.accentBar }}>
            <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)", letterSpacing: ".10em", textTransform: "uppercase", flex: 1 }}>
              {loading ? <><span className="spin" style={{ marginRight: 4 }} />生成中…</> : "输出结果"}
            </span>
            {provider && <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>via {provider}</span>}
            {!loading && output && (
              <span style={{ display: "flex", gap: 6 }}>
                <button className="tbtn" onClick={copyOut} style={{ fontSize: "var(--fs-9)" }}>复制</button>
                <button className="tbtn" onClick={exportOut} style={{ fontSize: "var(--fs-9)" }}>导出 .md</button>
              </span>
            )}
          </div>
          {/* Output body */}
          <div
            style={{ background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: "var(--r)", padding: "12px 14px", fontSize: "var(--fs-11)", lineHeight: 1.8, color: "var(--t2)", overflowX: "auto" }}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(output) }}
          />
        </div>
      )}

      {/* ── Execution history ── */}
      {runs.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <div style={S.divider} />
          <button
            onClick={() => setShowHistory((v) => !v)}
            style={{ background: "transparent", border: "none", cursor: "pointer", padding: 0, display: "flex", alignItems: "center", gap: 6 }}
          >
            <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)", letterSpacing: ".10em", textTransform: "uppercase" }}>
              历史记录
            </span>
            <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>({runs.length})</span>
            <span style={{ fontSize: "var(--fs-8)", color: "var(--t3)", transform: showHistory ? "none" : "rotate(-90deg)", transition: "transform .14s", display: "inline-block" }}>▼</span>
          </button>

          {showHistory && (
            <div style={{ marginTop: 8 }}>
              <table className="tbl" style={{ width: "100%" }}>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>状态</th>
                    <th>provider</th>
                    <th style={{ textAlign: "right" }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id}>
                      <td style={{ color: "var(--t3)", fontSize: "var(--fs-10)" }}>{r.started_at}</td>
                      <td>
                        <span style={{ fontSize: "var(--fs-10)", color: r.status === "error" ? "var(--red)" : "var(--acc)" }}>
                          {r.status === "error" ? "✕ 失败" : "✓ 完成"}
                        </span>
                        {r.elapsed_s > 0 && <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)", marginLeft: 4 }}>{r.elapsed_s}s</span>}
                      </td>
                      <td style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>{r.provider}</td>
                      <td style={{ textAlign: "right" }}>
                        <span style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                          <button className="tbtn" style={{ fontSize: "var(--fs-9)" }} onClick={() => viewRun(r.id)}>查看</button>
                          <button className="tbtn" style={{ fontSize: "var(--fs-9)" }} onClick={() => run(Object.fromEntries(Object.entries(r.params).map(([k, v]) => [k, String(v)])))}>重跑</button>
                          <button className="tbtn" style={{ fontSize: "var(--fs-9)" }} onClick={() => removeRun(r.id)}>删</button>
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Markdown renderer (table-aware) ──────────────────────────────────────────
function renderMarkdown(md: string): string {
  const esc = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const inlineFormat = (s: string) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, '<code style="background:var(--bg3);padding:1px 5px;border-radius:2px;font-size:10px">$1</code>');

  const lines = md.split("\n");
  const out: string[] = [];
  let inTable = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // heading
    if (/^### /.test(line)) { if (inTable) { out.push("</table>"); inTable = false; } out.push(`<div style="font-size:12px;font-weight:700;color:var(--t);margin:12px 0 5px">${inlineFormat(line.slice(4))}</div>`); continue; }
    if (/^## /.test(line))  { if (inTable) { out.push("</table>"); inTable = false; } out.push(`<div style="font-size:13px;font-weight:700;color:var(--t);margin:14px 0 6px">${inlineFormat(line.slice(3))}</div>`); continue; }
    if (/^# /.test(line))   { if (inTable) { out.push("</table>"); inTable = false; } out.push(`<div style="font-size:14px;font-weight:700;color:var(--t);margin:16px 0 7px">${inlineFormat(line.slice(2))}</div>`); continue; }

    // hr
    if (/^---+$/.test(line.trim())) { if (inTable) { out.push("</table>"); inTable = false; } out.push('<hr style="border:none;border-top:1px solid var(--b);margin:10px 0">'); continue; }

    // table
    if (line.startsWith("|")) {
      const cells = line.split("|").filter((_, i2, a) => i2 > 0 && i2 < a.length - 1);
      const isSep = cells.every((c) => /^[-: ]+$/.test(c));
      if (isSep) { /* skip separator row */ continue; }
      if (!inTable) {
        out.push(`<table style="width:100%;border-collapse:collapse;font-size:10px;margin:8px 0">`);
        inTable = true;
      }
      const tag = (!inTable || out[out.length - 1]?.includes("<table")) ? "th" : "td";
      const tdStyle = tag === "th"
        ? `style="text-align:left;padding:5px 8px;border-bottom:1px solid var(--b);color:var(--t3);letter-spacing:.05em;font-weight:400"`
        : `style="padding:6px 8px;border-bottom:1px solid var(--b);color:var(--t2)"`;
      out.push(`<tr>${cells.map((c) => `<${tag} ${tdStyle}>${inlineFormat(c.trim())}</${tag}>`).join("")}</tr>`);
      continue;
    }

    // end table
    if (inTable) { out.push("</table>"); inTable = false; }

    // list item
    if (/^[*-] /.test(line)) { out.push(`<div style="padding:1px 0 1px 14px;color:var(--t2);font-size:11px">· ${inlineFormat(line.slice(2))}</div>`); continue; }
    if (/^\d+\. /.test(line)) { out.push(`<div style="padding:1px 0 1px 14px;color:var(--t2);font-size:11px">${inlineFormat(line)}</div>`); continue; }

    // blank line
    if (!line.trim()) { out.push('<div style="height:6px"></div>'); continue; }

    // paragraph
    out.push(`<div style="color:var(--t2);margin-bottom:2px">${inlineFormat(line)}</div>`);
  }
  if (inTable) out.push("</table>");
  return out.join("");
}
