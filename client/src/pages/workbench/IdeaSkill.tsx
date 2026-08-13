import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import {
  architectPlan,
  architectGenerate,
  architectOneshot,
  type ArchitectPlan,
  type ArchitectInput,
  type ArchitectClarification,
  type ArchitectValidation,
} from "../../api/skill";
import SheetSelect from "../../components/SheetSelect";
import { errText } from "../../lib/errText";

interface GeneratedSkill {
  name: string;
  category: string | null;
  frontmatter: Record<string, unknown>;
  body: string;
  preview: string;
  validation?: ArchitectValidation;
}

const CATEGORIES = [
  "amazon",
  "amazon/listing",
  "amazon/ads",
  "research",
  "creative",
  "devops",
  "data-science",
  "productivity",
  "media",
  "software-development",
];

const INPUT_TYPES = [
  "text", "textarea", "number", "select", "boolean",
  "asin", "marketplace", "keyword", "date",
];

type Mode = "rigorous" | "fast";
const MODE_KEY = "skillArchitectMode";

// idle → working → clarify → plan → preview
type Phase = "idle" | "working" | "clarify" | "plan" | "preview";

export default function IdeaSkill({ embedded }: { embedded?: boolean } = {}) {
  const navigate = useNavigate();
  const [idea, setIdea] = useState("");
  const [category, setCategory] = useState("");
  const [mode, setMode] = useState<Mode>(
    () => (localStorage.getItem(MODE_KEY) as Mode) || "rigorous",
  );
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");

  // rigorous-mode intermediate state
  const [clarifications, setClarifications] = useState<ArchitectClarification[]>([]);
  const [clarAnswers, setClarAnswers] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState<ArchitectPlan | null>(null);

  // final preview
  const [generated, setGenerated] = useState<GeneratedSkill | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const busy = phase === "working";

  // Elapsed timer so the multi-stage LLM pipeline (30s–2min) doesn't feel dead.
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!busy) { setElapsed(0); return; }
    const t0 = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  const setModeAndStore = (m: Mode) => {
    setMode(m);
    localStorage.setItem(MODE_KEY, m);
  };

  const savedName = generated
    ? (generated.category ? `${generated.category}/${generated.name}` : generated.name)
    : "";

  const reset = () => {
    setPhase("idle");
    setProgress("");
    setError("");
    setClarifications([]);
    setClarAnswers({});
    setPlan(null);
    setGenerated(null);
    setSaved(false);
  };

  // ── Phase 1: kick off generation ──────────────────────────────────────
  const start = useCallback(async () => {
    if (!idea.trim() || busy) return;
    setError("");
    setGenerated(null);
    setSaved(false);
    setPlan(null);
    setClarifications([]);
    setClarAnswers({});
    setPhase("working");
    try {
      if (mode === "fast") {
        setProgress("AI 正在一条龙生成（理解 → 方案 → 复核 → 生成 → 自检）…");
        const res = await architectOneshot({ idea: idea.trim(), category: category || undefined });
        setGenerated(res);
        setPhase("preview");
      } else {
        setProgress("AI 正在理解需求、制定并复核方案…（约 30 秒）");
        const res = await architectPlan({ idea: idea.trim(), category: category || undefined });
        if (res.stage === "clarify" && res.clarifications?.length) {
          setClarifications(res.clarifications);
          setPhase("clarify");
        } else if (res.plan) {
          setPlan(res.plan);
          setPhase("plan");
        } else {
          setError("未能生成方案，请重试或换一种描述");
          setPhase("idle");
        }
      }
    } catch (e: any) {
      setError(errText(e, "生成失败"));
      setPhase("idle");
    }
  }, [idea, category, mode, busy]);

  // ── Phase 1b: answer clarifying questions, then re-plan ───────────────
  const submitClarifications = useCallback(async () => {
    setError("");
    setPhase("working");
    setProgress("AI 正在结合你的回答制定并复核方案…");
    try {
      const res = await architectPlan({
        idea: idea.trim(),
        category: category || undefined,
        clarifications: clarAnswers,
      });
      if (res.plan) {
        setPlan(res.plan);
        setPhase("plan");
      } else {
        setError("未能生成方案，请重试");
        setPhase("clarify");
      }
    } catch (e: any) {
      setError(errText(e, "生成失败"));
      setPhase("clarify");
    }
  }, [idea, category, clarAnswers]);

  // ── Phase 2: confirm the plan → render the SKILL.md ──────────────────
  const confirmAndGenerate = useCallback(async () => {
    if (!plan) return;
    setError("");
    setPhase("working");
    setProgress("AI 正在生成并自检 SKILL.md…");
    try {
      const res = await architectGenerate(plan);
      setGenerated(res);
      setPhase("preview");
    } catch (e: any) {
      setError(errText(e, "生成失败"));
      setPhase("plan");
    }
  }, [plan]);

  // ── Save ──────────────────────────────────────────────────────────────
  const save = useCallback(async (): Promise<boolean> => {
    if (!generated || saving) return false;
    if (saved) return true;
    setSaving(true);
    setError("");
    try {
      await api.post("/skill/item", {
        name: generated.category
          ? `${generated.category}/${generated.name}`
          : generated.name,
        description: generated.frontmatter?.description || "",
        body: generated.body,
        frontmatter_extras: generated.frontmatter,
      });
      setSaved(true);
      return true;
    } catch (e: any) {
      setError(errText(e, "保存失败"));
      return false;
    } finally {
      setSaving(false);
    }
  }, [generated, saving, saved]);

  // ── Plan editing helpers ─────────────────────────────────────────────
  const patchPlan = (p: Partial<ArchitectPlan>) =>
    setPlan((cur) => (cur ? { ...cur, ...p } : cur));

  const patchInput = (idx: number, patch: Partial<ArchitectInput>) =>
    setPlan((cur) => {
      if (!cur) return cur;
      const inputs = [...(cur.inputs || [])];
      inputs[idx] = { ...inputs[idx], ...patch };
      return { ...cur, inputs };
    });

  const removeInput = (idx: number) =>
    setPlan((cur) => {
      if (!cur) return cur;
      const inputs = [...(cur.inputs || [])];
      inputs.splice(idx, 1);
      return { ...cur, inputs };
    });

  const addInput = () =>
    setPlan((cur) =>
      cur ? { ...cur, inputs: [...(cur.inputs || []), { name: "", label: "", type: "text", required: false }] } : cur,
    );

  return (
    <div>
      {!embedded && <div className="ptitle">/ 想法工坊</div>}
      <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 16 }}>
        一句话描述你的想法，AI 深入理解需求 → 制定方案 → 复核优化 → 生成并自检 Skill
      </div>

      {/* Input area */}
      <div style={{ marginBottom: 14 }}>
        <textarea
          className="market-query-input"
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          placeholder="描述你想要的 Skill，例如：&#10;• 帮我自动分析竞品 Listing 的卖点差异&#10;• 根据关键词搜索量判断是否值得投放广告&#10;• 把中文售后邮件改写成专业的英文站内信"
          rows={4}
          disabled={busy}
          style={{ resize: "vertical", fontFamily: "inherit", width: "100%" }}
        />
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 14 }}>
        <SheetSelect
          className="market-query-input"
          style={{ flex: "0 0 160px" }}
          value={category}
          onChange={setCategory}
          disabled={busy}
          title="选择分类"
          options={[
            { value: "", label: "自动判断分类" },
            ...CATEGORIES.map((c) => ({ value: c, label: c })),
          ]}
        />

        {/* Mode toggle */}
        <div style={{ display: "flex", border: "1px solid var(--b)", borderRadius: 6, overflow: "hidden" }}>
          {(["rigorous", "fast"] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setModeAndStore(m)}
              disabled={busy}
              title={m === "rigorous" ? "理解→方案确认→生成，质量更高" : "一条龙直接生成，更快"}
              style={{
                fontSize: "var(--fs-10)", padding: "5px 10px", border: "none", cursor: "pointer",
                background: mode === m ? "var(--acc)" : "transparent",
                color: mode === m ? "#fff" : "var(--t2)",
              }}
            >
              {m === "rigorous" ? "◆ 严谨" : "⚡ 快速"}
            </button>
          ))}
        </div>

        <button
          className="market-btn market-btn-submit"
          onClick={start}
          disabled={busy || !idea.trim()}
        >
          {busy ? (
            <><span className="spin" style={{ marginRight: 6 }} />生成中…</>
          ) : (
            "◇ 生成 Skill"
          )}
        </button>

        {(phase !== "idle" && !busy) && (
          <button className="tbtn" onClick={reset} style={{ fontSize: "var(--fs-11)" }}>重置</button>
        )}
      </div>

      {error && <div className="market-error">{error}</div>}

      {busy && (
        <div className="pulse-loading">
          <span className="pulse-spin">◌</span> {progress}
          {elapsed > 2 && <span style={{ color: "var(--t3)", marginLeft: 8 }}>已等待 {elapsed}s</span>}
        </div>
      )}

      {/* ── Clarifying questions ── */}
      {phase === "clarify" && clarifications.length > 0 && (
        <div className="card" style={{ background: "var(--bg2)", marginBottom: 14 }}>
          <div style={{ fontSize: "var(--fs-12)", fontWeight: 600, marginBottom: 4 }}>先确认几个问题，让方案更贴合需求</div>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 12 }}>
            AI 觉得需求有歧义，回答后会据此制定方案
          </div>
          {clarifications.map((c, i) => (
            <div key={i} style={{ marginBottom: 12 }}>
              <label style={{ fontSize: "var(--fs-11)", color: "var(--t2)", display: "block", marginBottom: 4 }}>
                {c.question}
                {c.why && <span style={{ color: "var(--t3)", marginLeft: 6 }}>（{c.why}）</span>}
              </label>
              {c.options?.length ? (
                <SheetSelect
                  className="market-query-input"
                  value={clarAnswers[c.question] || ""}
                  onChange={(v) => setClarAnswers((a) => ({ ...a, [c.question]: v }))}
                  title={c.question}
                  options={[
                    { value: "", label: "请选择…" },
                    ...c.options.map((o) => ({ value: o, label: o })),
                  ]}
                />
              ) : (
                <input
                  className="market-query-input"
                  value={clarAnswers[c.question] || ""}
                  onChange={(e) => setClarAnswers((a) => ({ ...a, [c.question]: e.target.value }))}
                  placeholder="你的回答…"
                />
              )}
            </div>
          ))}
          <button className="market-btn market-btn-submit" onClick={submitClarifications}>
            ✓ 提交回答，制定方案
          </button>
        </div>
      )}

      {/* ── Editable plan card ── */}
      {phase === "plan" && plan && (
        <div className="card" style={{ background: "var(--bg2)", marginBottom: 14 }}>
          <div style={{ fontSize: "var(--fs-12)", fontWeight: 600, marginBottom: 4 }}>
            {String(plan.icon || "◇")} 方案已就绪，确认或微调后再生成
          </div>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 12 }}>
            类型: {String(plan.tool_kind || "-")} ｜ 运行时: {String(plan.runtime || "-")}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 10 }}>
            <Field label="名称">
              <input className="market-query-input" value={plan.name || ""}
                onChange={(e) => patchPlan({ name: e.target.value })} />
            </Field>
            <Field label="分类">
              <input className="market-query-input" value={plan.category || ""}
                onChange={(e) => patchPlan({ category: e.target.value })} />
            </Field>
          </div>
          <Field label="中文描述">
            <input className="market-query-input" value={plan.description_zh || ""}
              onChange={(e) => patchPlan({ description_zh: e.target.value })} />
          </Field>

          {/* inputs editor */}
          <div style={{ margin: "12px 0 6px", fontSize: "var(--fs-11)", fontWeight: 600 }}>输入参数（用户执行时要填的）</div>
          {(plan.inputs || []).map((inp, i) => (
            <div key={i} style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
              <input className="market-query-input" style={{ flex: "0 0 120px" }} placeholder="name"
                value={inp.name} onChange={(e) => patchInput(i, { name: e.target.value })} />
              <input className="market-query-input" style={{ flex: 1 }} placeholder="标签"
                value={inp.label || ""} onChange={(e) => patchInput(i, { label: e.target.value })} />
              <SheetSelect className="market-query-input" style={{ flex: "0 0 110px" }}
                value={inp.type || "text"} onChange={(v) => patchInput(i, { type: v })}
                title="参数类型" options={INPUT_TYPES} />
              <label style={{ fontSize: "var(--fs-10)", color: "var(--t2)", display: "flex", alignItems: "center", gap: 3 }}>
                <input type="checkbox" checked={!!inp.required}
                  onChange={(e) => patchInput(i, { required: e.target.checked })} />必填
              </label>
              <button className="tbtn" style={{ fontSize: "var(--fs-11)" }} onClick={() => removeInput(i)}>✕</button>
            </div>
          ))}
          <button className="tbtn" style={{ fontSize: "var(--fs-11)", marginBottom: 10 }} onClick={addInput}>+ 添加参数</button>

          {/* steps preview */}
          {plan.steps?.length ? (
            <div style={{ marginTop: 6 }}>
              <div style={{ fontSize: "var(--fs-11)", fontWeight: 600, marginBottom: 4 }}>执行步骤</div>
              <ol style={{ fontSize: "var(--fs-10)", color: "var(--t2)", lineHeight: 1.6, paddingLeft: 18, margin: 0 }}>
                {plan.steps.map((s, i) => <li key={i}>{s}</li>)}
              </ol>
            </div>
          ) : null}

          <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
            <button className="market-btn market-btn-submit" onClick={confirmAndGenerate}>
              🚀 确认并生成
            </button>
            <button className="tbtn" onClick={() => { setPhase("idle"); setPlan(null); }} style={{ fontSize: "var(--fs-11)" }}>
              取消
            </button>
          </div>
        </div>
      )}

      {/* ── Final preview ── */}
      {phase === "preview" && generated && (
        <div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
            <span style={{ fontSize: "var(--fs-14)", fontWeight: 600 }}>
              {String(generated.frontmatter?.icon || "⊞")} {generated.name}
            </span>
            {generated.category ? <span className="tag">{generated.category}</span> : null}
          </div>

          {/* validation banner */}
          {generated.validation && !generated.validation.ok && (
            <div className="market-error" style={{ marginBottom: 10 }}>
              ⚠ 自检发现未能自动修复的问题（已修复 {generated.validation.attempts} 次）：
              <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {generated.validation.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </div>
          )}
          {generated.validation?.ok && generated.validation.warnings.length > 0 && (
            <div style={{ fontSize: "var(--fs-10)", color: "var(--amber)", background: "rgba(245,158,11,.08)",
              border: "1px solid rgba(245,158,11,.25)", borderRadius: 6, padding: "8px 10px", marginBottom: 10, lineHeight: 1.6 }}>
              提示（不影响使用）：
              <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {generated.validation.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}
          {generated.validation?.ok && generated.validation.warnings.length === 0 && (
            <div style={{ fontSize: "var(--fs-10)", color: "var(--acc)", marginBottom: 10 }}>✓ 自检通过</div>
          )}

          {generated.frontmatter?.description_zh ? (
            <div style={{ fontSize: "var(--fs-11)", color: "var(--t2)", marginBottom: 10 }}>
              {String(generated.frontmatter.description_zh)}
            </div>
          ) : null}

          <div className="card" style={{ background: "var(--bg2)", marginBottom: 14 }}>
            <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 6 }}>SKILL.md 预览</div>
            <pre style={{
              fontSize: "var(--fs-10)", lineHeight: 1.6, maxHeight: 400, overflow: "auto", padding: 10,
              background: "var(--bg)", borderRadius: 4, whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}>
              {generated.preview}
            </pre>
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              className="market-btn market-btn-submit"
              onClick={async () => { const ok = await save(); if (ok && savedName) navigate(`/skill-hub?tab=tools&tool=${encodeURIComponent(savedName)}`); }}
              disabled={saving}
            >
              {saving ? "保存中…" : "🚀 保存并打开工具"}
            </button>
            <button className="tbtn" onClick={save} disabled={saving || saved} style={{ fontSize: "var(--fs-11)" }}>
              {saved ? "✓ 已保存" : "仅保存到 Skill 库"}
            </button>
            <button className="tbtn" onClick={reset} style={{ fontSize: "var(--fs-11)" }}>
              重新开始
            </button>
          </div>

          {saved && (
            <div style={{ marginTop: 10, fontSize: "var(--fs-11)", color: "var(--acc)" }}>
              ✓ Skill 已保存！可在「运营商店」执行，或在工具页右上角「☆ 固定到侧边栏」把它变成常驻入口。
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <label style={{ fontSize: "var(--fs-10)", color: "var(--t2)", display: "block", marginBottom: 3 }}>{label}</label>
      {children}
    </div>
  );
}
