import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import SheetSelect from "../../components/SheetSelect";
import {
  getSettings, patchSettings, getHealth, changePassword,
  testSetting, autodetectSettings, selfCheckSettings, getAgentVersion,
  startAgentUpgrade, getAgentUpgradeProgress, slotModelCatalog,
  getFeishuStatus, feishuAction, getAmazonStatus, saveAmazonConfig, amazonAction,
  type HubSettings, type HealthResp, type TestResult, type SelfCheckResp,
  type FeishuStatus, type FeishuPatrolJob,
  type AmazonStatus, type AmazonMarketplace, type AmazonVerifyResp,
} from "../../api/settings";
import { installAgentStreamUrl } from "../../api/setup";
import {
  listMcpTokens, issueMcpToken, revokeMcpToken, getMcpClientConfig,
  type McpToken, type IssuedToken,
} from "../../api/mcp";
import {
  getNotifyConfig, testNotify, getBudget,
  type NotifyConfig, type BudgetStatus,
} from "../../api/notify";
import { lockBodyScroll } from "../../lib/scrollLock";
import {
  FONT_OPTIONS, ZOOM_OPTIONS, WEIGHT_OPTIONS,
  getFontId, getZoom, getWeight, applyFont, applyZoom, applyWeight,
} from "../../lib/appearance";
import { useAuth } from "../../App";
import SubscriptionLogin from "../../components/settings/SubscriptionLogin";
import { errText } from "../../lib/errText";

type SaveStatus = "idle" | "saving" | "ok" | "error";

// ── Tiny UI building blocks ───────────────────────────────────────────────────

function Dot({ ok, loading }: { ok?: boolean; loading?: boolean }) {
  if (loading) return <span className="hs-dot hs-dot-loading">…</span>;
  return <span className={"hs-dot " + (ok ? "hs-dot-ok" : "hs-dot-err")}>{ok ? "✓" : "✗"}</span>;
}

function Section({
  title, desc, children, keys, vals, onSave, dataTour,
}: {
  title: React.ReactNode; desc?: React.ReactNode; children: React.ReactNode;
  keys: (keyof HubSettings)[]; vals: Partial<HubSettings>;
  onSave: (keys: (keyof HubSettings)[], vals: Partial<HubSettings>) => Promise<void>;
  dataTour?: string;
}) {
  const [status, setStatus] = useState<SaveStatus>("idle");
  const save = async () => {
    setStatus("saving");
    try { await onSave(keys, vals); setStatus("ok"); setTimeout(() => setStatus("idle"), 2200); }
    catch { setStatus("error"); setTimeout(() => setStatus("idle"), 3000); }
  };
  return (
    <div className="hs-section" data-tour={dataTour}>
      <div className="hs-section-hd">
        <div>
          <div className="hs-section-title">{title}</div>
          {desc && <div className="hs-section-desc">{desc}</div>}
        </div>
        <button className={"hs-save-btn" + (status !== "idle" ? " hs-save-" + status : "")}
          onClick={save} disabled={status === "saving"}>
          {status === "saving" ? "保存中…" : status === "ok" ? "✓ 已保存" : status === "error" ? "× 失败" : "保存"}
        </button>
      </div>
      <div className="hs-fields">{children}</div>
    </div>
  );
}

function Field({ label, hint, children }: { label: React.ReactNode; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="hs-field">
      <label className="hs-label">{label}</label>
      {hint && <div className="hs-hint">{hint}</div>}
      {children}
    </div>
  );
}

function Tag({ kind, children }: { kind: "req" | "opt" | "rec"; children: React.ReactNode }) {
  return <span className={`hs-tag hs-tag-${kind}`}>{children}</span>;
}

function TestButton({ settingKey, value, label = "测试" }: {
  settingKey: keyof HubSettings;
  value: string | undefined;
  label?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const run = async () => {
    setBusy(true); setResult(null);
    try { setResult(await testSetting(settingKey, value)); }
    catch (e: any) { setResult({ ok: false, detail: errText(e, "请求失败") }); }
    finally { setBusy(false); setTimeout(() => setResult(null), 12000); }
  };
  return (
    <div className="hs-test-row">
      <button className="hs-test-btn" onClick={run} disabled={busy} type="button">
        {busy ? "测试中…" : label}
      </button>
      {result && (
        <span className={"hs-test-result " + (result.ok ? "ok" : "err")}>
          {result.ok ? "✓" : "✗"} {result.detail}
        </span>
      )}
    </div>
  );
}

function AutodetectPanel({ onApply }: {
  onApply: (suggestions: Partial<Record<keyof HubSettings, string>>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [suggestions, setSuggestions] = useState<Partial<Record<keyof HubSettings, string>>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [err, setErr] = useState("");

  const scan = async () => {
    setLoading(true); setErr("");
    try {
      const r = await autodetectSettings();
      setSuggestions(r.suggestions);
      setSelected(new Set(Object.keys(r.suggestions)));
      setOpen(true);
    } catch (e: any) { setErr(errText(e, "检测失败")); }
    finally { setLoading(false); }
  };

  const apply = () => {
    const filtered: Partial<Record<keyof HubSettings, string>> = {};
    for (const k of Object.keys(suggestions)) {
      if (selected.has(k)) (filtered as any)[k] = (suggestions as any)[k];
    }
    onApply(filtered); setOpen(false);
  };

  const toggle = (k: string) => setSelected(prev => {
    const next = new Set(prev);
    if (next.has(k)) next.delete(k); else next.add(k);
    return next;
  });

  const entries = Object.entries(suggestions);

  return (
    <div className="hs-autodetect">
      <button className="hs-autodetect-btn" onClick={scan} disabled={loading} type="button">
        {loading ? "扫描中…" : "🔍 自动检测路径"}
      </button>
      {err && <div className="hs-autodetect-err">{err}</div>}
      {open && (
        <div className="hs-autodetect-modal-backdrop" onClick={() => setOpen(false)}>
          <div className="hs-autodetect-modal" onClick={(e) => e.stopPropagation()}>
            <div className="hs-autodetect-modal-hd">
              <div>
                <div className="hs-section-title">扫描到 {entries.length} 项</div>
                <div className="hs-section-desc">勾选项点「应用」后写入对应字段（只填当前为空的字段）。</div>
              </div>
              <button className="hs-test-btn" onClick={() => setOpen(false)} type="button">取消</button>
            </div>
            {entries.length === 0 ? (
              <div className="terminal-empty" style={{ padding: 20 }}>没有可建议的项。</div>
            ) : (
              <>
                <div className="hs-autodetect-list">
                  {entries.map(([k, v]) => (
                    <label key={k} className="hs-autodetect-item">
                      <input type="checkbox" checked={selected.has(k)} onChange={() => toggle(k)} />
                      <span className="hs-autodetect-key">{k}</span>
                      <span className="hs-autodetect-val">{v}</span>
                    </label>
                  ))}
                </div>
                <div className="hs-autodetect-modal-ft">
                  <button className="hs-test-btn" onClick={() => setSelected(new Set(entries.map(([k]) => k)))} type="button">全选</button>
                  <button className="hs-test-btn" onClick={() => setSelected(new Set())} type="button">清空</button>
                  <button className="hs-save-btn" onClick={apply} disabled={selected.size === 0} type="button" style={{ marginLeft: "auto" }}>
                    应用 {selected.size} 项 →
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function TxtInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return <input className="hs-input" type="text" value={value} onChange={e => onChange(e.target.value)}
    placeholder={placeholder} spellCheck={false} autoComplete="off" />;
}

function AreaInput({ value, onChange, placeholder, rows = 4 }: { value: string; onChange: (v: string) => void; placeholder?: string; rows?: number }) {
  return <textarea className="hs-input" value={value} onChange={e => onChange(e.target.value)}
    placeholder={placeholder} spellCheck={false} rows={rows} style={{ resize: "vertical", fontFamily: "var(--font)", lineHeight: 1.5 }} />;
}

function NumInput({ value, onChange, min, max, unit }: { value: number; onChange: (v: number) => void; min?: number; max?: number; unit?: string }) {
  return (
    <div className="hs-num-wrap">
      <input className="hs-input hs-input-num" type="number" value={value} min={min} max={max}
        onChange={e => onChange(Number(e.target.value))} />
      {unit && <span className="hs-unit">{unit}</span>}
    </div>
  );
}

function SecretInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const [show, setShow] = useState(false);
  return (
    <div className="hs-secret-row">
      <input className="hs-input" type={show ? "text" : "password"} value={value}
        onChange={e => onChange(e.target.value)} placeholder={placeholder || "未配置"}
        spellCheck={false} autoComplete="new-password" />
      <button className="hs-eye" onClick={() => setShow(s => !s)} title={show ? "隐藏" : "显示"}>
        {show ? "●" : "○"}
      </button>
    </div>
  );
}

// ── 模型名：从"背默写"变成"挑一个" ────────────────────────────────────────────
//
// 这四个槽位的模型名此前都是自由文本框：用户得自己记住
// "Qwen/Qwen3-VL-30B-A3B-Instruct" 这种字符串，记错了还要等到真调用时才报错。
// 现在按槽位去问那个端点支持哪些模型，挑一个就行。
//
// **手输必须留着**：中转商的 /models 会因为余额不足（实测 apimart 返回 402）、
// 网络不通、或者压根没有这个接口而拉不到清单。那种时候下拉是空的，输入框是唯一出路。

/** 视觉 / 生图模型的名字特征。用来把长清单收窄到"可能能用的那几个"。 */
const MODEL_HINTS: Record<string, RegExp> = {
  vision: /(vl|vision|visual|omni|gpt-4o|gpt-5|claude|gemini|glm-4v|qwen-vl|internvl|llava)/i,
  image: /(image|img|flux|dall-?e|sd[-_.]?\d|stable-?diffusion|seedream|z-image|imagen|kolors|playground)/i,
};

function ModelNameInput({
  slot, provider, baseUrl, apiKey, value, onChange, placeholder, hintKind, fallbackModels,
}: {
  /** 后端按它解析去问哪个端点：agent | assistant | vision | image。 */
  slot: string;
  provider: string; baseUrl: string; apiKey: string;
  value: string; onChange: (v: string) => void;
  placeholder?: string;
  /** 给了就先按这类模型的名字特征过滤，可一键切回全部。 */
  hintKind?: "vision" | "image";
  /**
   * 端点拉不到清单时的候选。生图这一档几乎必然走到这里 —— 实测 Apimart 的
   * `/models` 在余额不足时返回 402，清单就是空的，而空下拉等于这个功能不存在。
   * 只是**常见名字**，不保证对方平台支持，所以文案上要说清楚。
   */
  fallbackModels?: string[];
}) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<string[] | null>(null);
  const [source, setSource] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [onlyHinted, setOnlyHinted] = useState(!!hintKind);
  const boxRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true); setErr("");
    try {
      const d = await slotModelCatalog({ slot, provider, base_url: baseUrl, api_key: apiKey, refresh });
      setModels(d?.catalog?.models || []);
      setSource(String(d?.catalog?.source || ""));
      setErr(String(d?.catalog?.error || ""));
    } catch (e: any) {
      setModels([]);
      setErr(errText(e, "取模型清单失败"));
    } finally {
      setLoading(false);
    }
  }, [slot, provider, baseUrl, apiKey]);

  // 换了 provider / 地址 / 密钥，上一份清单就不作数了 —— 留着它会让人从 A 家的
  // 清单里挑一个模型填进 B 家的槽位。
  useEffect(() => { setModels(null); setOpen(false); }, [provider, baseUrl, apiKey]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && models === null && !loading) void load(false);
  };

  const usingFallback = !!(models && models.length === 0 && fallbackModels?.length);
  const shown = (usingFallback ? fallbackModels! : (models || [])).filter((m) => {
    if (!onlyHinted || !hintKind || usingFallback) return true;
    return MODEL_HINTS[hintKind].test(m);
  });

  return (
    <div className="hs-model-pick" ref={boxRef}>
      <div className="hs-model-row">
        <input className="hs-input" type="text" value={value} onChange={e => onChange(e.target.value)}
          placeholder={placeholder} spellCheck={false} autoComplete="off" />
        <button type="button" className="hs-model-btn" onClick={toggle}
          title="列出这套账号支持的模型">{loading ? "…" : open ? "▴" : "▾"}</button>
      </div>
      {open && (
        <div className="hs-model-dd">
          <div className="hs-model-dd-hd">
            <span>
              {loading ? "正在取清单…"
                : usingFallback ? "常见模型名（没能问到这个平台）"
                : source === "live" ? "实时清单"
                : source === "cache" ? "缓存清单"
                : models?.length ? "内置清单" : "没取到清单"}
            </span>
            <span className="hs-model-dd-acts">
              {hintKind && !usingFallback && (
                <button type="button" onClick={() => setOnlyHinted(v => !v)}>
                  {onlyHinted ? "显示全部" : (hintKind === "vision" ? "只看视觉" : "只看生图")}
                </button>
              )}
              <button type="button" onClick={() => void load(true)} disabled={loading}>刷新</button>
            </span>
          </div>
          {err && (
            <div className="hs-model-dd-err">
              {err}<br />
              {usingFallback
                ? "下面是常见的模型名，不保证你这个平台支持；也可以直接把模型名填进上面的输入框。"
                : "拉不到清单不影响使用：直接把模型名填进上面的输入框即可。"}
            </div>
          )}
          {!loading && shown.length === 0 && !err && (
            <div className="hs-model-dd-err">没有可选项{onlyHinted ? "（试试「显示全部」）" : ""}。</div>
          )}
          <div className="hs-model-dd-list">
            {shown.map(m => (
              <button key={m} type="button"
                className={"hs-model-opt" + (m === value ? " active" : "")}
                onClick={() => { onChange(m); setOpen(false); }}>{m}</button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── LLM model block ───────────────────────────────────────────────────────────

type ProviderDef = { id: string; label: string; defaultModel: string; envVar: string; hint?: string; examples?: string };

const PROVIDERS: ProviderDef[] = [
  { id: "deepseek",   label: "DeepSeek",   defaultModel: "deepseek-chat",                      envVar: "DEEPSEEK_API_KEY",            hint: "国内可直连，性价比高", examples: "deepseek-chat / deepseek-reasoner" },
  { id: "xiaomi",     label: "MiMo",        defaultModel: "mimo-v2.5-pro",                      envVar: "XIAOMI_API_KEY",              hint: "小米大模型，国内可用" },
  { id: "anthropic",  label: "Anthropic",  defaultModel: "claude-sonnet-4-6",                  envVar: "ANTHROPIC_API_KEY",           hint: "Claude 系列" },
  { id: "openai",     label: "OpenAI",     defaultModel: "gpt-4o",                             envVar: "OPENAI_API_KEY" },
  { id: "openrouter", label: "OpenRouter", defaultModel: "anthropic/claude-sonnet-4-6",        envVar: "OPENROUTER_API_KEY",          hint: "聚合多家，一个 key 换模型" },
  { id: "google",     label: "Google",     defaultModel: "gemini-2.0-flash",                   envVar: "GOOGLE_GENERATIVE_AI_API_KEY" },
  { id: "kimi",       label: "Kimi",       defaultModel: "kimi-k2.5",                          envVar: "KIMI_API_KEY",                hint: "国内可用" },
  { id: "groq",       label: "Groq",       defaultModel: "llama-3.3-70b-versatile",            envVar: "GROQ_API_KEY",                hint: "超快推理速度" },
  { id: "together",   label: "Together",   defaultModel: "meta-llama/Llama-3.3-70B-Instruct-Turbo", envVar: "TOGETHER_API_KEY" },
  { id: "siliconflow", label: "硅基流动",  defaultModel: "deepseek-ai/DeepSeek-V3.2",          envVar: "SILICONFLOW_API_KEY",         hint: "国内直连，有免费档，含 Qwen-VL 视觉", examples: "deepseek-ai/DeepSeek-V3.2 / Qwen/Qwen3-VL-30B-A3B-Instruct" },
  { id: "dashscope",  label: "阿里云百炼", defaultModel: "qwen-plus",                          envVar: "DASHSCOPE_API_KEY",           hint: "国内直连，qwen-vl 系列可做视觉" },
  { id: "zhipu",      label: "智谱",       defaultModel: "glm-4-plus",                         envVar: "ZHIPUAI_API_KEY",             hint: "GLM-4V-Flash 视觉免费" },
  // GLM Coding Plan（订阅）单列两条：它的地址和普通 API **不是一个** ——
  // 官方要求 coding 专用端点 /api/coding/paas/v4，填成通用端点不通，而报错完全
  // 指不到"地址错了"上。把它做成可选项，用户就不必自己去翻文档拼地址。
  { id: "zai-coding", label: "GLM Coding Plan · Z.AI", defaultModel: "glm-5.3",                envVar: "ZAI_API_KEY",                 hint: "订阅制套餐（海外站），走 coding 专用地址", examples: "glm-5.3 / glm-5.2 / glm-4.7" },
  { id: "glm-coding", label: "GLM Coding Plan · 智谱", defaultModel: "glm-5.3",                envVar: "ZHIPUAI_API_KEY",             hint: "订阅制套餐（国内站），走 coding 专用地址", examples: "glm-5.3 / glm-5.2 / glm-4.7" },
  { id: "custom",     label: "自定义",     defaultModel: "",                                   envVar: "",                            hint: "OpenAI 兼容接口" },
];

function ProviderPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string, defaultModel: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  const selected = PROVIDERS.find(p => p.id === value);

  // Lock body scroll while the modal is open.
  useEffect(() => {
    if (!open) return;
    const releaseScroll = lockBodyScroll();
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", onEsc);
    return () => {
      releaseScroll();
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        style={{
          width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "8px 12px", borderRadius: 6,
          border: open ? "1px solid var(--acc)" : "1px solid var(--b)",
          background: "var(--bg2)",
          color: selected ? "var(--t)" : "var(--t3)",
          fontSize: "var(--fs-125)", fontFamily: "var(--font)", cursor: "pointer",
          outline: "none", transition: "border .12s",
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={{ fontWeight: selected ? 500 : 400 }}>
            {selected ? selected.label : "选择 Provider"}
          </span>
          {selected?.hint && (
            <span style={{ color: "var(--t3)", fontSize: "var(--fs-11)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {selected.hint}
            </span>
          )}
        </span>
        <span style={{ color: "var(--t3)", fontSize: "var(--fs-9)", marginLeft: 8, flexShrink: 0 }}>▼</span>
      </button>

      {/* centered modal — overlay + dialog */}
      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{
            position: "fixed", inset: 0, zIndex: 9999,
            background: "rgba(0,0,0,.5)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 16,
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              width: "min(420px, 100%)", maxHeight: "70vh", display: "flex", flexDirection: "column",
              background: "var(--bg1, var(--bg2))",
              border: "1px solid var(--b)", borderRadius: 12,
              boxShadow: "0 16px 48px rgba(0,0,0,.5)",
              overflow: "hidden",
            }}
          >
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "14px 16px", borderBottom: "1px solid var(--b)",
            }}>
              <span style={{ fontSize: "var(--fs-13)", fontWeight: 600, color: "var(--t)" }}>选择模型 Provider</span>
              <span onClick={() => setOpen(false)} style={{ cursor: "pointer", color: "var(--t3)", fontSize: "var(--fs-16)", lineHeight: 1 }}>✕</span>
            </div>
            <div style={{ overflowY: "auto", WebkitOverflowScrolling: "touch", padding: 6 }}>
              {PROVIDERS.map(p => {
                const isSel = value === p.id;
                const isHover = hovered === p.id;
                return (
                  <div
                    key={p.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => { onChange(p.id, p.defaultModel); setOpen(false); }}
                    onMouseEnter={() => setHovered(p.id)}
                    onMouseLeave={() => setHovered(h => (h === p.id ? null : h))}
                    style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "11px 12px", borderRadius: 8, marginBottom: 2,
                      background: isSel
                        ? "color-mix(in srgb, var(--acc) 16%, transparent)"
                        : isHover
                        ? "color-mix(in srgb, var(--t) 7%, transparent)"
                        : "transparent",
                      color: isSel ? "var(--acc)" : "var(--t)",
                      fontSize: "var(--fs-13)", fontFamily: "var(--font)", cursor: "pointer",
                      userSelect: "none", transition: "background .1s",
                    }}
                  >
                    <span style={{ flex: 1, fontWeight: isSel ? 500 : 400 }}>{p.label}</span>
                    {p.hint && (
                      <span style={{ color: isSel ? "color-mix(in srgb, var(--acc) 70%, var(--t3))" : "var(--t3)", fontSize: "var(--fs-11)" }}>
                        {p.hint}
                      </span>
                    )}
                    <span style={{ width: 12, textAlign: "center", color: "var(--acc)", fontSize: "var(--fs-12)", flexShrink: 0 }}>
                      {isSel ? "✓" : ""}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function LLMModelBlock({
  title, hint,
  providerKey, modelKey, apiKeyKey, baseUrlKey,
  slot, hintKind, inherit,
  vals, set,
}: {
  title: string; hint?: string;
  providerKey: keyof HubSettings; modelKey: keyof HubSettings;
  apiKeyKey: keyof HubSettings; baseUrlKey: keyof HubSettings;
  /** 给了就把模型名换成可选清单（后端按 slot 解析去问哪个端点）。 */
  slot?: string;
  hintKind?: "vision" | "image";
  /** 「沿用某某账号」：一键把 provider + 地址 + 密钥抄过来，只剩挑一个模型。 */
  inherit?: { label: string; title?: string; run: () => void }[];
  vals: HubSettings;
  set: <K extends keyof HubSettings>(k: K, v: HubSettings[K]) => void;
}) {
  const provider = (vals[providerKey] as string) || "";
  const model    = (vals[modelKey]    as string) || "";
  const apiKey   = (vals[apiKeyKey]   as string) || "";
  const baseUrl  = (vals[baseUrlKey]  as string) || "";
  const info     = PROVIDERS.find(p => p.id === provider);

  return (
    <div>
      <div style={{ fontSize: "var(--fs-11)", color: "var(--t2)", fontWeight: 600, marginBottom: 10 }}>
        {title}
        {hint && <span className="hs-inline-hint" style={{ fontWeight: 400, color: "var(--t3)", marginLeft: 8 }}>{hint}</span>}
      </div>

      {inherit && inherit.length > 0 && (
        /*
         * 每个槽位都要填 provider + 模型 + key + 地址四样，是"配置复杂"的大头。
         * 大多数人其实就是想用和主脑同一套账号 —— 那就给一键抄过来。
         */
        <div className="hs-inherit">
          <span>快速配置：</span>
          {inherit.map(it => (
            <button key={it.label} type="button" className="hs-inherit-btn"
              title={it.title} onClick={it.run}>{it.label}</button>
          ))}
        </div>
      )}

      <div className="hs-label" style={{ marginBottom: 6 }}>选择 Provider</div>
      <ProviderPicker
        value={provider}
        onChange={(id, defaultModel) => {
          set(providerKey, id as HubSettings[typeof providerKey]);
          if (!model && defaultModel)
            set(modelKey, defaultModel as HubSettings[typeof modelKey]);
        }}
      />

      {provider && (
        /* 用 .hs-row2 而不是内联 grid：这一页别处的两列都靠它，而它带着
           `@media(max-width:600px)` 收成单列的规则 —— 内联样式媒体查询管不着，
           手机上就会挤成一个 70px 宽的模型名输入框。 */
        <div className="hs-row2" style={{ marginTop: 10 }}>
          <Field label="模型名称" hint={info?.examples ? `可用：${info.examples}` : (info?.defaultModel ? `推荐：${info.defaultModel}` : undefined)}>
            {slot ? (
              <ModelNameInput
                slot={slot}
                provider={provider} baseUrl={baseUrl} apiKey={apiKey}
                value={model}
                onChange={v => set(modelKey, v as HubSettings[typeof modelKey])}
                placeholder={info?.defaultModel || "模型名称"}
                hintKind={hintKind}
              />
            ) : (
              <TxtInput
                value={model}
                onChange={v => set(modelKey, v as HubSettings[typeof modelKey])}
                placeholder={info?.defaultModel || "模型名称"}
              />
            )}
          </Field>
          <Field label={info?.envVar || "API Key"}>
            <SecretInput
              value={apiKey}
              onChange={v => set(apiKeyKey, v as HubSettings[typeof apiKeyKey])}
              placeholder={info?.envVar || "API Key"}
            />
          </Field>
          {(provider === "custom" || baseUrl) && (
            <Field label="Base URL" hint="自定义地址才需填，其他 provider 留空">
              <TxtInput
                value={baseUrl}
                onChange={v => set(baseUrlKey, v as HubSettings[typeof baseUrlKey])}
                placeholder="https://api.example.com/v1"
              />
            </Field>
          )}
        </div>
      )}
      {provider && apiKey && (
        <div style={{ marginTop: 8 }}>
          <TestButton settingKey={apiKeyKey} value={apiKey} label="测试连通（真实调用一次）" />
        </div>
      )}
    </div>
  );
}

// ── Health panel (simplified) ─────────────────────────────────────────────────

function HealthPanel() {
  const [health, setHealth] = useState<HealthResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [installing, setInstalling] = useState<string | null>(null);
  const [installLog, setInstallLog] = useState<string[]>([]);

  const check = useCallback(async () => {
    setLoading(true); setErr("");
    try { setHealth(await getHealth()); }
    catch (e: any) { setErr(e?.message || "检测失败"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { check(); }, [check]);

  type InstallableComponent = "ivyea-agent" | "legacy" | "hermes" | "codex" | "claude" | "all";

  const installComponent = useCallback((component: InstallableComponent) => {
    if (installing) return;
    setInstalling(component);
    setInstallLog([]);
    const es = new EventSource(installAgentStreamUrl(component));
    es.onmessage = (ev) => {
      const line = ev.data as string;
      if (line === "__DONE__") {
        es.close();
        setInstalling(null);
        setInstallLog(prev => [...prev, "✓ 安装/修复完成，正在重新检测…"]);
        check();
      } else if (line === "__ERROR__") {
        es.close();
        setInstalling(null);
        setInstallLog(prev => [...prev, "✗ 安装失败，可查看上方日志后重试"]);
      } else {
        setInstallLog(prev => [...prev, line]);
      }
    };
    es.onerror = () => {
      es.close();
      setInstalling(null);
      setInstallLog(prev => [...prev, "连接中断，请稍后重试"]);
    };
  }, [check, installing]);

  const rows: Array<{ label: string; key: keyof HealthResp | string; nested?: string; install?: InstallableComponent }> = [
    { label: "IvyeaAgent · 内置服务",      key: "ivyea_agent", install: "ivyea-agent" },
    { label: "AI · 文本链可用",           key: "ai_chain", nested: "text" },
    { label: "AI · 全局兜底大模型",       key: "ai_chain", nested: "global_fallback" },
    { label: "AI · 视觉识别",             key: "ai_chain", nested: "vision" },
    { label: "Apimart · 图片 / AI 服务", key: "apimart" },
    { label: "Sorftime · 市场数据",       key: "sorftime" },
    { label: "外部 Agent · Hermes",       key: "runners", nested: "hermes", install: "hermes" },
    { label: "外部 Agent · Codex",        key: "runners", nested: "codex", install: "codex" },
    { label: "外部 Agent · Claude",       key: "runners", nested: "claude", install: "claude" },
    { label: "外部 Agent · Kiro",         key: "runners", nested: "kiro" },
  ];

  const get = (row: typeof rows[0]) => {
    if (!health) return undefined;
    const top = health[row.key as keyof HealthResp] as any;
    if (row.nested) return top?.[row.nested];
    return top;
  };

  const shortDetail = (detail: string): string => {
    if (!detail) return "";
    // Abbreviate long absolute paths: show only the last segment
    if (detail.startsWith("/") && detail.length > 32) {
      const last = detail.split("/").filter(Boolean).pop() ?? detail;
      return "…/" + last;
    }
    return detail;
  };

  const isInstallErrorLine = (line: string): boolean => {
    return /(^|\s)(ERROR|Error|error|失败|中断|exited with code|Traceback)/.test(line);
  };

  return (
    <div className="hs-health" data-tour="settings-health">
      <div className="hs-health-hd">
        <div>
          <div className="hs-section-title">系统状态</div>
          <div className="hs-section-desc" style={{ marginTop: 4 }}>
            <span style={{ color: "var(--acc)" }}>✓</span> 已就绪；
            <span style={{ color: "var(--red)", marginLeft: 6 }}>✗</span> 未配置或检测失败。
          </div>
        </div>
        <button className="hs-refresh-btn" onClick={check} disabled={loading}>
          {loading ? "检测中…" : "↻ 重新检测"}
        </button>
      </div>
      {err && <div className="hs-health-err">{err}</div>}
      <div className="hs-health-grid">
        {rows.map(row => {
          const item = get(row);
          const full = item?.detail || "";
          return (
            <div key={row.label} className="hs-health-row">
              <Dot ok={item?.ok} loading={loading || (!health && !err)} />
              <span className="hs-health-label">{row.label}</span>
              {/* 视觉是三档链，光一个绿点说不清"能到什么程度"。档位徽标让用户
                  一眼看出自己在 T1/T2/T3，detail 里再讲这一档少了什么。 */}
              {typeof item?.tier === "number" && item.tier > 0 && (
                <span
                  className="hs-tier-chip"
                  data-tier={item.tier}
                  title={item.tier_label || ""}
                >
                  T{item.tier}
                </span>
              )}
              <span className="hs-health-detail" title={full}>{shortDetail(full)}</span>
              {row.install && (
                <button
                  className="hs-refresh-btn"
                  style={{ padding: "2px 8px", fontSize: "var(--fs-10)" }}
                  disabled={!!installing}
                  // Always offer this, even when detected as installed: a broken /
                  // incompatible build (e.g. an old GBrain v0.35) reports "ok" yet
                  // still needs a clean reinstall, so hiding the button would trap
                  // the user with no way to repair it.
                  onClick={() => installComponent(row.install!)}
                  title={item?.ok ? "已安装，可重装/修复以替换损坏或不兼容的版本" : undefined}
                >
                  {installing === row.install ? "安装中…" : (item?.ok ? "重装/修复" : "安装/修复")}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {installLog.length > 0 && (
        <div className="hs-install-log" role="log" aria-live="polite">
          {installLog.slice(-80).map((line, i) => (
            <div key={`${i}-${line}`} className={isInstallErrorLine(line) ? "err" : undefined}>
              {line || " "}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Change password ───────────────────────────────────────────────────────────

function ChangePassword() {
  const [old, setOld] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [msg, setMsg] = useState("");

  const save = async () => {
    if (next !== confirm) { setMsg("两次输入的新密码不一致"); return; }
    if (next.length < 8) { setMsg("新密码至少 8 位"); return; }
    setMsg(""); setStatus("saving");
    try {
      await changePassword(old, next);
      setStatus("ok"); setMsg("密码已更新");
      setOld(""); setNext(""); setConfirm("");
      setTimeout(() => { setStatus("idle"); setMsg(""); }, 3000);
    } catch (e: any) {
      setStatus("error"); setMsg(errText(e, "修改失败"));
      setTimeout(() => setStatus("idle"), 3000);
    }
  };

  return (
    <div className="hs-section">
      <div className="hs-section-hd">
        <div>
          <div className="hs-section-title">账号安全</div>
          <div className="hs-section-desc">
            修改登录密码（至少 8 位）。忘记密码时删掉 <code>data/hub_settings.json</code> 里的 <code>password_hash</code> 字段后重启服务。
          </div>
        </div>
        <button className={"hs-save-btn" + (status !== "idle" ? " hs-save-" + status : "")}
          onClick={save} disabled={status === "saving"}>
          {status === "saving" ? "保存中…" : status === "ok" ? "✓ 已更新" : status === "error" ? "× 失败" : "修改密码"}
        </button>
      </div>
      <div className="hs-fields">
        <div className="hs-row3">
          <Field label="当前密码">
            <SecretInput value={old} onChange={setOld} placeholder="当前密码" />
          </Field>
          <Field label="新密码">
            <SecretInput value={next} onChange={setNext} placeholder="至少 8 位" />
          </Field>
          <Field label="确认新密码">
            <SecretInput value={confirm} onChange={setConfirm} placeholder="再次输入" />
          </Field>
        </div>
        {msg && <div className={"hs-pw-msg" + (status === "ok" ? " ok" : " err")}>{msg}</div>}
      </div>
    </div>
  );
}

// ── Advanced accordion ────────────────────────────────────────────────────────

function AdvancedBlock({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="hs-advanced">
      <button
        type="button"
        className="hs-advanced-toggle"
        onClick={() => setOpen(o => !o)}
      >
        <span style={{ display: "inline-block", transition: "transform .15s", transform: open ? "rotate(90deg)" : "none" }}>▶</span>
        <span className="hs-advanced-toggle-label">高级选项</span>
        <span className="hs-advanced-toggle-sub">
          {open ? "点击收起" : "Token 监控 · Imgflow · 内嵌服务 · Kiro · 资讯源"}
        </span>
      </button>
      {open && <div className="hs-advanced-body">{children}</div>}
    </div>
  );
}

// ── One-click self-check: test every configured item, show green/red ──────────
function SelfCheckPanel() {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<SelfCheckResp | null>(null);
  const [err, setErr] = useState("");
  const run = async () => {
    setBusy(true); setErr(""); setRes(null);
    try { setRes(await selfCheckSettings()); }
    catch (e: any) { setErr(errText(e, "自检失败")); }
    finally { setBusy(false); }
  };
  return (
    <div className="card" style={{ padding: 14, marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: "var(--fs-13)", fontWeight: 600, color: "var(--t)" }}>一键全部自检</div>
          <div className="hs-inline-hint" style={{ fontSize: "var(--fs-11)", color: "var(--t3)", marginTop: 2 }}>
            对每个已配置项做一次真实在线测试，一眼看清"配了但用不了"的项。
          </div>
        </div>
        <button className="hs-test-btn" onClick={run} disabled={busy} type="button" style={{ whiteSpace: "nowrap" }}>
          {busy ? "自检中…（每项真实调用一次）" : "开始自检"}
        </button>
      </div>
      {err && <div className="hs-test-result err" style={{ marginTop: 8 }}>✗ {err}</div>}
      {res && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: "var(--fs-11)", color: "var(--t3)", marginBottom: 6 }}>
            通过 {res.ok} · 失败 {res.err} · 未配置 {res.skip} · 共 {res.total}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {res.results.map((r) => (
              <div key={r.key} style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: "var(--fs-11)" }}>
                <span style={{ width: 14, color: r.status === "ok" ? "var(--ok,#16a34a)" : r.status === "err" ? "var(--err,#dc2626)" : "var(--t3)" }}>
                  {r.status === "ok" ? "✓" : r.status === "err" ? "✗" : "—"}
                </span>
                <span style={{ minWidth: 130, fontWeight: 600, color: "var(--t2)" }}>{r.label}</span>
                <span style={{ color: r.status === "err" ? "var(--err,#dc2626)" : "var(--t3)" }}>{r.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── IvyeaAgent version + 一键更新（后台任务 + 进度条，不再阻塞超时）──────────────
const _PHASE_LABEL: Record<string, string> = {
  preparing: "准备中…", downloading: "拉取最新 IvyeaAgent…（可能需要 1–2 分钟）",
  restarting: "重启本机服务…", done: "完成", error: "失败",
};

function AgentUpdateRow() {
  const [ver, setVer] = useState<string>("");
  const [latest, setLatest] = useState<string>("");
  const [hasUpd, setHasUpd] = useState(false);
  const [frozen, setFrozen] = useState(false);
  const [latestKnown, setLatestKnown] = useState(true);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<string>("");
  const [percent, setPercent] = useState(0);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const timer = useRef<number | null>(null);
  const load = () => {
    getAgentVersion().then(r => {
      setVer(r.installed || r.version || "");
      setLatest(r.latest || "");
      setHasUpd(!!r.update_available);
      setFrozen(!!r.frozen);
      setLatestKnown(r.latest_known !== false);
    }).catch(() => setVer(""));
  };
  useEffect(load, []);
  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);

  const poll = () => {
    timer.current = window.setInterval(async () => {
      try {
        const p = await getAgentUpgradeProgress();
        setPhase(p.phase); setPercent(p.percent || 0);
        if (p.phase === "done" || p.phase === "error") {
          if (timer.current) window.clearInterval(timer.current);
          setBusy(false);
          setVer(p.after || ver);
          if (p.ok) load();   // 刷新 最新版/有更新 徽标
          setMsg(p.ok
            ? { ok: true, text: p.note ? p.note                               // frozen: 随 IvyeaOps 更新的说明
                : (p.before === p.after ? `已是最新（${p.after || "未知"}）` : `已更新 ${p.before || "?"} → ${p.after || "?"}`) }
            : { ok: false, text: p.note || p.error || "更新失败" });
        }
      } catch { /* keep polling; transient errors during serve restart are expected */ }
    }, 1500);
  };

  const run = async () => {
    if (!confirm("将从 GitHub 拉取最新 IvyeaAgent 并重启本机服务（约 1–2 分钟），期间右下角 Agent 会短暂中断。继续？")) return;
    setBusy(true); setMsg(null); setPercent(0); setPhase("preparing");
    try {
      await startAgentUpgrade();
      poll();
    } catch (e: any) {
      setBusy(false);
      setMsg({ ok: false, text: errText(e, "启动更新失败") });
    }
  };

  return (
    <div className="hs-agent-card">
      <div className="hs-agent-card-title">版本与更新
        {hasUpd && <span style={{ marginLeft: 8, fontSize: "var(--fs-10)", color: "#fff", background: "#dc2626", borderRadius: 8, padding: "1px 7px" }}>有新版</span>}
      </div>
      <div className="hs-agent-card-desc">
        当前 IvyeaAgent 版本 <b style={{ color: "var(--t)" }}>{ver || "未知/未运行"}</b>
        {latest && <> · 最新 <b style={{ color: hasUpd ? "#dc2626" : "var(--t)" }}>{latest}</b></>}
        {!latestKnown
          ? "（暂时无法连 GitHub 检查最新版，可能网络问题，稍后再试）。"
          : hasUpd
            ? (frozen
                ? "，有新版：内置 IvyeaAgent 随 IvyeaOps 一起更新——请用左下角「更新」升级 IvyeaOps 即可获得。"
                : "，点「检查并更新」升级。")
            : "（已是最新）。"}
        {frozen && <>{" "}<span style={{ color: "var(--t3)" }}>（内置版本，随 IvyeaOps 更新）</span></>}
      </div>
      {busy && (
        <div style={{ margin: "8px 0" }}>
          <div style={{ height: 6, borderRadius: 3, background: "var(--line,#e5e7eb)", overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${Math.max(percent, 8)}%`, background: "var(--acc,#16a34a)", transition: "width .4s ease" }} />
          </div>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginTop: 4 }}>{_PHASE_LABEL[phase] || "更新中…"}（{percent}%）</div>
        </div>
      )}
      <div className="hs-test-row" style={{ marginTop: 6 }}>
        <button className="hs-test-btn" onClick={run} disabled={busy} type="button">
          {busy ? "更新中…" : "检查并更新"}
        </button>
        {msg && <span className={"hs-test-result " + (msg.ok ? "ok" : "err")}>{msg.ok ? "✓" : "✗"} {msg.text}</span>}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

const EMPTY: HubSettings = {
  hermes_provider: "", hermes_model: "", hermes_api_key: "", hermes_base_url: "",
  hermes_fallback_provider: "", hermes_fallback_model: "",
  hermes_fallback_api_key: "", hermes_fallback_base_url: "",
  assistant_provider: "", assistant_model: "", assistant_api_key: "", assistant_base_url: "",
  assistant_vision_model: "",
  vision_provider: "", vision_model: "", vision_api_key: "", vision_base_url: "",
  ivyea_agent_url: "http://127.0.0.1:8765", ivyea_agent_token: "", ivyea_agent_auto_start: true,
  ivyea_agent_provider: "", ivyea_agent_model: "", ivyea_agent_api_key: "", ivyea_agent_base_url: "",
  image_model: "", image_api_key: "", image_base_url: "",
  apimart_key: "", apimart_base: "https://api.apimart.ai/v1",
  text_ai_providers: "ivyea-agent,assistant,deepseek,codex,claude",
  vision_ai_providers: "openai,assistant", deepseek_api_key: "", news_feeds: "",
  sorftime_key: "", sif_key: "", sellersprite_key: "",
  imgflow_url: "http://127.0.0.1:3001",
  brain_root: "", openai_api_key: "",
  alert_webhook: "", alert_app_id: "", alert_app_secret: "", alert_chat_id: "",
  alert_feishu_domain: "feishu",
  alert_threshold: 80, alert_sustain: 5, alert_cooldown: 30,
  dashboard_url: "", terminal_url: "",
  hermes_bin: "", codex_bin: "", claude_bin: "", kiro_cli_bin: "",
  hermes_db: "", codex_db: "", feishu_codex_db: "",
  kiro_gateway_db: "", kiro_cli_db: "", kiro_cli_sessions_dir: "",
  claude_projects_dir: "", hermes_node_bin: "", bun_bin: "",
  autofix_enabled: false,
  skill_market_enabled: false,
  skill_market_url: "",
  skill_market_pubkey: "",
  skill_market_allow_class_b: false,
  notify_webhook: "",
  notify_events: "",
  ai_budget_monthly_usd: 0,
};

// ── 外观 / 显示：字体族 + 全局字号（即时生效 + localStorage，无后端）───────────────
/** 通知渠道与 AI 预算。管理员专属；非管理员拿到 403 就整块不渲染。 */
/** 亚马逊官方 API（SP-API + Ads API）。
 *
 *  为什么在"这台机器没有卖家账号"的情况下也要有这一块：用这套系统的人有账号。
 *  凭据能填、数据源能接、规则能吃到官方数据，不该等某台机器恰好有账号才开始做。
 *
 *  凭据只存 IvyeaAgent 一侧（~/.ivyea/.env），ops 不留副本 —— 与飞书那组不同，
 *  这里没有"agent 挂了也要能用"的场景，取数本来就是 agent 干的活。
 */
function AmazonSection() {
  const [st, setSt] = useState<AmazonStatus | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [steps, setSteps] = useState<AmazonVerifyResp["steps"]>(undefined);
  const [profiles, setProfiles] = useState<AmazonVerifyResp["profiles"]>(undefined);
  const [cred, setCred] = useState({ client_id: "", client_secret: "", refresh_token: "" });
  const [adsCred, setAdsCred] = useState({ ads_client_id: "", ads_client_secret: "", ads_refresh_token: "" });
  const [sellerId, setSellerId] = useState("");
  const [rows, setRows] = useState<AmazonMarketplace[]>([]);
  const [adsOwnApp, setAdsOwnApp] = useState(false);

  const reload = useCallback(async () => {
    try {
      const s = await getAmazonStatus();
      setSt(s);
      setRows(s.marketplaces || []);
      setSellerId(s.seller_id || "");
      setAdsOwnApp(!!s.ads_uses_own_app);
    } catch (e: any) {
      setSt({ ok: false, error: errText(e, "读取失败") });
    }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  const run = async (name: string, fn: () => Promise<void>) => {
    setBusy(name); setMsg(null);
    try { await fn(); } catch (e: any) { setMsg({ ok: false, text: errText(e, "操作失败") }); }
    finally { setBusy(""); }
  };
  const flash = (ok: boolean, text: string) => {
    setMsg({ ok, text });
    if (ok) setTimeout(() => setMsg(null), 8000);
  };

  const save = () => run("save", async () => {
    // 密钥留空 = 不改（agent 侧同样按"空 = 不动"处理）：
    // 打开配置页什么都没干、保存一下就把凭据清空，是最不能容忍的一种"顺手"。
    const body: Record<string, unknown> = {
      ...Object.fromEntries(Object.entries(cred).filter(([, v]) => v)),
      ...Object.fromEntries(Object.entries(adsCred).filter(([, v]) => v)),
      seller_id: sellerId,
      marketplaces: rows.filter((r) => r.marketplace_id),
    };
    const r = await saveAmazonConfig(body);
    if (r.ok === false) { flash(false, r.error || "保存失败"); return; }
    setCred({ client_id: "", client_secret: "", refresh_token: "" });
    setAdsCred({ ads_client_id: "", ads_client_secret: "", ads_refresh_token: "" });
    flash(true, "已保存（密钥已加密存入 IvyeaAgent，输入框按惯例清空）");
    await reload();
  });

  const verify = () => run("verify", async () => {
    const r = await amazonAction("verify");
    setSteps(r.steps || []);
    flash(!!r.ok, r.ok ? "全部通过" : "有步骤没通过，看下面逐步结果");
    await reload();
  });

  const loadProfiles = () => run("profiles", async () => {
    const r = await amazonAction("profiles");
    setProfiles(r.profiles || []);
    if (!r.ok) flash(false, r.error || "取广告档案失败");
  });

  const setRow = (i: number, patch: Partial<AmazonMarketplace>) =>
    setRows(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows([...rows, { sid: "", marketplace_id: "", name: "", ads_profile_id: "" }]);
  const dropRow = (i: number) => setRows(rows.filter((_, idx) => idx !== i));

  const catalog = (st?.catalog || []).map((c) => ({
    value: c.marketplace_id, label: `${c.country}（${c.region.toUpperCase()}）`,
  }));

  return (
    <Section
      title="亚马逊官方 API"
      desc="SP-API（库存 / 订单 / 价格）+ Ads API（广告）。填完即用：巡检规则会自动改吃官方数据，官方优先、领星兜底。"
      keys={[]} vals={{}} onSave={async () => { await save(); }}
    >
      <div className="hs-caps">
        <div className={"hs-cap" + (st?.configured ? " hs-cap-ok" : "")}>
          <Dot ok={!!st?.configured} /><span>SP-API 凭据</span>
        </div>
        <div className={"hs-cap" + (st?.ads_configured ? " hs-cap-ok" : "")}>
          <Dot ok={!!st?.ads_configured} /><span>广告 API 凭据</span>
        </div>
        <div className={"hs-cap" + ((st?.marketplace_count || 0) > 0 ? " hs-cap-ok" : "")}>
          <Dot ok={(st?.marketplace_count || 0) > 0} />
          <span>站点 {st?.marketplace_count || 0} 个</span>
          {(st?.with_ads_profile || 0) > 0 && <em>{st?.with_ads_profile} 个带广告档案</em>}
        </div>
        {st?.region && <div className="hs-cap"><span>区域 {st.region.toUpperCase()}</span></div>}
      </div>
      {st && st.ok === false && (
        <div className="hs-hint" style={{ color: "var(--red)" }}>
          {st.error}{st.hint ? ` —— ${st.hint}` : ""}
        </div>
      )}

      <div className="hs-field-group-title">LWA 凭据（开发者中心 → 应用与授权）</div>
      <div className="hs-hint">
        三样都来自你自己的 SP-API 应用：<code>client_id</code>、<code>client_secret</code> 在应用详情里，
        <code>refresh_token</code> 是卖家授权回调后拿到的那串。
        <b>需要先有亚马逊开发者账号并通过 SP-API 应用审批</b>（周期可能数周，越早申请越好）。
        保存后密钥不回显 —— 留空表示不改。
      </div>
      <div className="hs-row3">
        <Field label={<><Tag kind="req">必填</Tag>Client ID</>} hint={st?.configured ? "已配置，留空不改" : "未配置"}>
          <TxtInput value={cred.client_id} onChange={(v) => setCred({ ...cred, client_id: v })}
            placeholder="amzn1.application-oa2-client..." />
        </Field>
        <Field label={<><Tag kind="req">必填</Tag>Client Secret</>} hint={st?.configured ? "已配置，留空不改" : "未配置"}>
          <SecretInput value={cred.client_secret} onChange={(v) => setCred({ ...cred, client_secret: v })}
            placeholder={st?.configured ? "••••••••（已保存）" : "amzn1.oa2-cs..."} />
        </Field>
        <Field label={<><Tag kind="req">必填</Tag>Refresh Token</>} hint={st?.configured ? "已配置，留空不改" : "未配置"}>
          <SecretInput value={cred.refresh_token} onChange={(v) => setCred({ ...cred, refresh_token: v })}
            placeholder={st?.configured ? "••••••••（已保存）" : "Atzr|..."} />
        </Field>
      </div>
      <Field label={<><Tag kind="opt">可选</Tag>Seller ID</>} hint="卖家编号（Merchant Token）。判断 Buy Box 归属时要用它认出「自己」。">
        <TxtInput value={sellerId} onChange={setSellerId} placeholder="A23SU2M9XL8R0O" />
      </Field>

      <label className="hs-toggle-line">
        <input type="checkbox" checked={adsOwnApp} onChange={(e) => setAdsOwnApp(e.target.checked)} />
        <span>广告 API 用另一套应用</span>
      </label>
      <div className="hs-hint">
        不勾就与上面共用 —— 大多数卖家两边是同一个应用，强迫把同一串东西填两遍只会填错一遍。
        广告 API 通常<b>单独审批</b>，没批下来也不影响库存那部分先跑起来。
      </div>
      {adsOwnApp && (
        <div className="hs-row3">
          <Field label="广告 Client ID">
            <TxtInput value={adsCred.ads_client_id} onChange={(v) => setAdsCred({ ...adsCred, ads_client_id: v })} placeholder="amzn1.application-oa2-client..." />
          </Field>
          <Field label="广告 Client Secret">
            <SecretInput value={adsCred.ads_client_secret} onChange={(v) => setAdsCred({ ...adsCred, ads_client_secret: v })} placeholder="留空不改" />
          </Field>
          <Field label="广告 Refresh Token">
            <SecretInput value={adsCred.ads_refresh_token} onChange={(v) => setAdsCred({ ...adsCred, ads_refresh_token: v })} placeholder="留空不改" />
          </Field>
        </div>
      )}

      <div className="hs-field-group-title">站点</div>
      <div className="hs-hint">
        一行一个站点。<b>SID 是与领星共用的连接键</b>：两边都用的话，同一个站点填同一个 SID，
        规则拿到的就是同一家店（官方优先、领星兜底）；只用亚马逊就随便给个稳定值。
        区域按站点自动推，不用自己选。
      </div>
      {rows.map((r, i) => (
        <div key={i} className="hs-mkt-row">
          <TxtInput value={r.sid} onChange={(v) => setRow(i, { sid: v })} placeholder="SID" />
          <SheetSelect value={r.marketplace_id} onChange={(v) => setRow(i, { marketplace_id: v })}
            options={catalog} placeholder="选站点" />
          <TxtInput value={r.name} onChange={(v) => setRow(i, { name: v })} placeholder="显示名（如 欧洲-UK）" />
          <TxtInput value={r.ads_profile_id} onChange={(v) => setRow(i, { ads_profile_id: v })}
            placeholder="广告档案 ID（可选）" />
          <button className="hs-test-btn" type="button" onClick={() => dropRow(i)}>删除</button>
        </div>
      ))}
      <div className="hs-test-row" style={{ gap: 8 }}>
        <button className="hs-test-btn" type="button" onClick={addRow}>+ 加一个站点</button>
        <button className="hs-test-btn" type="button" onClick={loadProfiles} disabled={!!busy}>
          {busy === "profiles" ? "查询中…" : "列出广告档案"}
        </button>
        <button className="hs-test-btn" type="button" onClick={save} disabled={!!busy}>
          {busy === "save" ? "保存中…" : "保存凭据与站点"}
        </button>
        <button className="hs-test-btn" type="button" onClick={verify} disabled={!!busy}>
          {busy === "verify" ? "自检中…" : "自检（真打一次接口）"}
        </button>
        {msg && <span className={"hs-test-result " + (msg.ok ? "ok" : "err")}>
          {msg.ok ? "✓" : "✗"} {msg.text}</span>}
      </div>

      {profiles && (
        <div className="hs-pick">
          {profiles.length === 0 && <span className="hs-hint">没有广告档案。多半是这套凭据还没开广告权限。</span>}
          {profiles.map((p) => (
            <div key={p.profile_id} className="hs-pick-item">
              {p.country} · {p.name || p.type}<em>profileId {p.profile_id}</em>
            </div>
          ))}
        </div>
      )}

      {steps && steps.length > 0 && (
        <div className="hs-steps">
          {steps.map((s) => (
            <div key={s.step} className={"hs-step" + (s.ok ? " hs-step-done" : "")}>
              <span className="hs-step-no">{s.ok ? "✓" : "✗"}</span>
              <div className="hs-step-body">
                <div className="hs-step-title">{s.step}</div>
                <div className="hs-step-detail">{s.detail}{s.hint ? ` —— ${s.hint}` : ""}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

/** 飞书 / Lark —— 一处配置，四条链路。
 *
 *  以前这里叫「飞书通知」，实际只喂 CPU 告警一条链路；店铺巡检卡片、审批按钮、
 *  飞书对话那三条各自读 IvyeaAgent 和 relay 的配置文件，界面上完全看不见。
 *  结果是同一个飞书应用要在三个地方各填一遍，任何一处漏填的表现都是
 *  「保存成功，但就是收不到消息」——没有任何报错。
 *
 *  现在凭据只填一次：存进 hub settings（服务器告警那条链路自己用，agent 挂了
 *  它照样报警），保存时后端顺手下推给 IvyeaAgent（巡检 / 审批 / 对话共用）。
 *  白名单和巡检任务只存 agent 一份，这里直接读写，不在 ops 侧留副本。
 */
/** 这一档用分钟还是小时做单位：按默认值定，不随当前值变。 */
function unitOf(defaultMinutes: number): number {
  return defaultMinutes >= 120 ? 60 : 1;
}

/** 间隔的人话。60→「每小时」、720→「每 12 小时」、10080→「每周」。 */
function fmtEvery(minutes: number): string {
  if (minutes % 43200 === 0) return minutes === 43200 ? "每月" : `每 ${minutes / 43200} 个月`;
  if (minutes % 10080 === 0) return minutes === 10080 ? "每周" : `每 ${minutes / 10080} 周`;
  if (minutes % 1440 === 0) return minutes === 1440 ? "每天" : `每 ${minutes / 1440} 天`;
  if (minutes % 60 === 0) return minutes === 60 ? "每小时" : `每 ${minutes / 60} 小时`;
  return `每 ${minutes} 分钟`;
}

function FeishuSection({ vals, set, save }: {
  vals: Partial<HubSettings>;
  set: <K extends keyof HubSettings>(k: K, v: HubSettings[K]) => void;
  save: (keys: (keyof HubSettings)[], vals: Partial<HubSettings>) => Promise<void>;
}) {
  const [st, setSt] = useState<FeishuStatus | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [chats, setChats] = useState<{ chat_id: string; name: string }[] | null>(null);
  const [members, setMembers] = useState<{ open_id: string; name: string }[] | null>(null);
  const [senders, setSenders] = useState<string[]>([]);
  // 档位不再写死三个：agent 的 patrol.defaults 给出有哪些档、默认多久一次、
  // 以及每一档在管什么。前端再写一份默认值的话，实际生效的永远是小的那个。
  const [patrol, setPatrol] = useState<{
    tiers: Record<string, { enabled: boolean; minutes: number }>;
    scope: string; sids: string;
  }>({ tiers: {}, scope: "all", sids: "" });

  const reload = useCallback(async (probe = false) => {
    try {
      const s = await getFeishuStatus(probe);
      setSt(s);
      setSenders(s.gates?.allowed_senders || []);
      const jobs = s.patrol?.jobs || [];
      const defaults = s.patrol?.defaults || {};
      const tiers: Record<string, { enabled: boolean; minutes: number }> = {};
      let any: FeishuPatrolJob | undefined;
      for (const [key, def] of Object.entries(defaults)) {
        const job = jobs.find((j) => j.task === def.task);
        if (job) any = any || job;
        tiers[key] = {
          enabled: !!job?.enabled,
          minutes: Math.round(job?.every_minutes || def.every_minutes),
        };
      }
      setPatrol({
        tiers,
        scope: any?.scope === "all" ? "all" : (any ? "sids" : "all"),
        // 旧的单店任务用的是 sid 单数，界面统一按列表展示
        sids: (any?.scope === "all" ? [] : [...(any?.sids || []), any?.sid || ""])
          .filter(Boolean).join(","),
      });
    } catch (e: any) {
      setSt({ ok: false, error: errText(e, "读取失败") });
    }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  const run = async (name: string, fn: () => Promise<void>) => {
    setBusy(name); setMsg(null);
    try { await fn(); } catch (e: any) { setMsg({ ok: false, text: errText(e, "操作失败") }); }
    finally { setBusy(""); }
  };

  const flash = (ok: boolean, text: string) => {
    setMsg({ ok, text });
    if (ok) setTimeout(() => setMsg(null), 8000);
  };

  const doChats = () => run("chats", async () => {
    const r = await feishuAction({ action: "chats" });
    setChats(r.chats || []);
    if (!r.ok) flash(false, r.error || "列群失败");
    else if (r.note) flash(true, r.note);
  });

  const doMembers = () => run("members", async () => {
    const r = await feishuAction({ action: "members", chat_id: vals.alert_chat_id || "" });
    setMembers(r.members || []);
    if (!r.ok) flash(false, r.error || "列成员失败（多半是缺 im:chat:readonly 权限）");
  });

  const doWhitelist = () => run("whitelist", async () => {
    const r = await feishuAction({ action: "whitelist", allowed_senders: senders });
    flash(!!r.ok, r.ok ? `审批白名单已更新：${senders.length} 人` : (r.error || "保存失败"));
    await reload();
  });

  const doPatrol = () => run("patrol", async () => {
    const sids = patrol.sids.split(/[,\s]+/).filter(Boolean);
    const tiers: Record<string, unknown> = {};
    for (const [key, t] of Object.entries(patrol.tiers)) {
      tiers[key] = { enabled: t.enabled, every_minutes: t.minutes };
    }
    const r = await feishuAction({
      action: "patrol",
      scope: patrol.scope, sids,
      channel: "feishu_app",
      ...tiers,
    });
    if (!r.ok) { flash(false, r.error || "巡检设置失败"); return; }
    const replaced = (r.replaced || []).length
      ? `；已收编旧任务 ${(r.replaced || []).join("、")}` : "";
    flash(true, ((r.created || []).length
      ? `已启用 ${(r.created || []).length} 条巡检任务` : "已关闭全部巡检任务") + replaced);
    await reload();
  });

  const doTest = () => run("test", async () => {
    // 先把输入框里的凭据存下来再测：不然改完直接点测试，测的还是旧凭据，
    // 结果对不上会让人以为是飞书坏了。
    await save(["alert_app_id", "alert_app_secret", "alert_chat_id",
      "alert_feishu_domain", "alert_webhook"], vals);
    const r = await feishuAction({ action: "test", chat_id: vals.alert_chat_id || "" });
    flash(!!r.ok, r.ok ? "已发出，去飞书里看看收到没有" : (r.error || "发送失败"));
    await reload();
  });

  const toggleSender = (id: string) =>
    setSenders((cur) => cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]);

  const CH_LABEL: [string, string][] = [
    ["text_alert", "文本告警"], ["cards", "交互卡片"],
    ["approval", "点按钮改领星"], ["chat", "在飞书里对话"],
    ["patrol_push", "店铺巡检推送"],
  ];

  return (
    <Section
      title="飞书 / Lark"
      desc="一处配置，四件事共用：服务器告警 · 店铺巡检卡片 · 点按钮直接改领星 · 在飞书里和 AI 对话。"
      keys={["alert_webhook", "alert_app_id", "alert_app_secret", "alert_chat_id",
        "alert_feishu_domain", "alert_threshold", "alert_sustain", "alert_cooldown"]}
      vals={vals} onSave={save}
    >
      {/* 向导：每一步的 ✓ 都由真实状态决定，不由「点过下一步」决定 */}
      {st?.steps && (
        <div className="hs-steps">
          {st.steps.map((s, i) => (
            <div key={s.key} className={"hs-step" + (s.done ? " hs-step-done" : "")}>
              <span className="hs-step-no">{s.done ? "✓" : i + 1}</span>
              <div className="hs-step-body">
                <div className="hs-step-title">{s.title}</div>
                <div className="hs-step-detail">{s.detail || s.hint}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      {st && st.ok === false && (
        <div className="hs-hint" style={{ color: "var(--red)" }}>
          {st.error}{st.hint ? ` —— ${st.hint}` : ""}
        </div>
      )}

      {/* 能力矩阵：webhook 能发文本但永远点不了按钮，这条差别必须写明 */}
      {st?.channels && (
        <div className="hs-caps">
          {CH_LABEL.map(([k, label]) => {
            const c = st.channels?.[k];
            if (!c) return null;
            return (
              <div key={k} className={"hs-cap" + (c.ready ? " hs-cap-ok" : "")}
                title={c.blockers.join("；") || c.note}>
                <Dot ok={c.ready} />
                <span>{label}</span>
                {!c.ready && <em>{c.blockers[0]}</em>}
              </div>
            );
          })}
        </div>
      )}

      <div className="hs-field-group-title">应用凭据（发卡片、收按钮、对话都靠它）</div>
      {/* 两处各存一份是有意的（看门狗不能依赖 agent），但"agent 那边配了、这边空着"
          必须说出来 —— 否则用户看着满屏 ✓，却收不到任何 CPU 告警，且毫无线索。 */}
      {st?.app?.configured && !vals.alert_app_id && (
        <div className="hs-hint" style={{ color: "var(--red)" }}>
          IvyeaAgent 侧已有凭据 <code>{st.app.app_id_masked}</code>，但这里是空的。
          巡检卡片和审批照常工作，<b>服务器 CPU 告警发不出去</b> —— 它是独立进程跑的看门狗，
          只读这一份（agent 挂了它还要能报警）。把同一个应用填进来即可。
          <button className="hs-test-btn" type="button" style={{ marginLeft: 8 }}
            onClick={() => set("alert_app_id", st.app?.app_id || "")}>
            带入 App ID
          </button>
        </div>
      )}
      <div className="hs-row3">
        <Field label={<><Tag kind="req">必填</Tag>App ID</>} hint="cli_ 开头">
          <TxtInput value={vals.alert_app_id || ""} onChange={(v) => set("alert_app_id", v)} placeholder="cli_xxx" />
        </Field>
        <Field label={<><Tag kind="req">必填</Tag>App Secret</>} hint="换应用后旧 token 会自动作废">
          <SecretInput value={vals.alert_app_secret || ""} onChange={(v) => set("alert_app_secret", v)} placeholder="App Secret" />
        </Field>
        <Field label="域名" hint="国内飞书 / 国际 Lark">
          <SheetSelect value={vals.alert_feishu_domain || "feishu"}
            onChange={(v) => set("alert_feishu_domain", v)}
            options={[{ value: "feishu", label: "飞书 open.feishu.cn" },
            { value: "lark", label: "Lark open.larksuite.com" }]} />
        </Field>
      </div>
      <div className="hs-hint">
        飞书开放平台 → 创建企业自建应用 → 「凭证与基础信息」拿这两个值。权限至少要
        <code>im:message</code>、<code>im:message:send_as_bot</code>、<code>im:chat:readonly</code>，
        <b>改完权限记得发布版本</b>，否则调用会一直报 99991672。
      </div>

      <div className="hs-field-group-title">接收会话</div>
      <Field label="Chat ID" hint={<>
        把机器人拉进群后，群设置里能看到会话 ID（<code>oc_</code> 开头）。
        下面的「列出机器人所在的群」<b>列不出来是正常的</b> —— 飞书只列应用可管理的群，
        直接粘 ID 一样能用。
      </>}>
        <div className="hs-test-row" style={{ gap: 8 }}>
          <TxtInput value={vals.alert_chat_id || ""} onChange={(v) => set("alert_chat_id", v)} placeholder="oc_..." />
          <button className="hs-test-btn" type="button" onClick={doChats} disabled={!!busy}>
            {busy === "chats" ? "查询中…" : "列出机器人所在的群"}
          </button>
        </div>
        {chats && chats.length > 0 && (
          <div className="hs-pick">
            {chats.map((c) => (
              <button key={c.chat_id} type="button" className="hs-pick-item"
                onClick={() => set("alert_chat_id", c.chat_id)}>
                {c.name || "(未命名群)"}<em>{c.chat_id}</em>
              </button>
            ))}
          </div>
        )}
      </Field>

      <div className="hs-field-group-title">谁能点审批按钮</div>
      <Field label="审批白名单" hint={<>
        卡片上的「批准执行」会<b>真的去改领星</b>。<b>留空不是所有人都能点，是所有人都不能点</b> ——
        改钱的权限不设默认放行。这一项存在 IvyeaAgent 侧，保存后 relay 立即生效，不用重启。
      </>}>
        <div className="hs-test-row" style={{ gap: 8 }}>
          <button className="hs-test-btn" type="button" onClick={doMembers}
            disabled={!!busy || !vals.alert_chat_id}>
            {busy === "members" ? "查询中…" : "从群里选人"}
          </button>
          <button className="hs-test-btn" type="button" onClick={doWhitelist} disabled={!!busy}>
            {busy === "whitelist" ? "保存中…" : `保存白名单（${senders.length} 人）`}
          </button>
        </div>
        {members && (
          <div className="hs-pick">
            {members.length === 0 && <span className="hs-hint">没列到成员，多半是缺 im:chat:readonly 权限。</span>}
            {members.map((m) => (
              <label key={m.open_id} className="hs-toggle-line" style={{ marginRight: 12 }}>
                <input type="checkbox" checked={senders.includes(m.open_id)}
                  onChange={() => toggleSender(m.open_id)} />
                <span>{m.name || m.open_id}</span>
              </label>
            ))}
          </div>
        )}
        {senders.length > 0 && (
          <div className="hs-hint">当前放行：{senders.map((s) => s.slice(0, 12) + "…").join("、")}</div>
        )}
      </Field>

      <div className="hs-field-group-title">店铺巡检推送</div>
      <Field label="定时巡检" hint={<>
        库存断货、活动被暂停、预算被外部改动、ACOS 超标… 异常推成卡片发到上面那个群。
        {st?.patrol?.timer?.running === false && <b style={{ color: "var(--red)" }}>
          {" "}触发器 ivyea-schedule.timer 没启用，任务注册了也不会跑。</b>}
      </>}>
        <div className="hs-tiers">
          {Object.entries(st?.patrol?.defaults || {}).map(([key, def]) => {
            const tier = patrol.tiers[key] || { enabled: false, minutes: def.every_minutes };
            const setTier = (patch: Partial<typeof tier>) => setPatrol({
              ...patrol, tiers: { ...patrol.tiers, [key]: { ...tier, ...patch } },
            });
            return (
              <div key={key} className={"hs-tier" + (tier.enabled ? " hs-tier-on" : "")}>
                <label className="hs-toggle-line">
                  <input type="checkbox" checked={tier.enabled}
                    onChange={(e) => setTier({ enabled: e.target.checked })} />
                  <span>{def.label}</span>
                </label>
                <div className="hs-tier-desc">{def.desc}</div>
                <div className="hs-tier-every">
                  {/* 分钟级的两档才给输入框改；日报/周报/月报的周期改起来没意义，
                      写成文字反而一眼看清各档节奏差多少。
                      单位按**这一档的默认值**定死，不看当前值 —— 看当前值的话，
                      把 12 小时改成 1 小时的瞬间输入框会跳成「60 分钟」。 */}
                  {def.every_minutes < 1440 ? (
                    <NumInput value={Math.round(tier.minutes / unitOf(def.every_minutes))}
                      onChange={(v) => setTier({ minutes: v * unitOf(def.every_minutes) })}
                      min={1} max={999}
                      unit={unitOf(def.every_minutes) === 60 ? "小时一次" : "分钟一次"} />
                  ) : (
                    <span className="hs-hint">{fmtEvery(tier.minutes)}一次</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", marginTop: 6 }}>
          <SheetSelect value={patrol.scope} onChange={(v) => setPatrol({ ...patrol, scope: v })}
            options={[{ value: "all", label: "全部店铺" }, { value: "sids", label: "指定店铺" }]} />
          {patrol.scope === "sids" && (
            <TxtInput value={patrol.sids} onChange={(v) => setPatrol({ ...patrol, sids: v })}
              placeholder="店铺 SID，逗号分隔，如 1863,1872" />
          )}
          <button className="hs-test-btn" type="button" onClick={doPatrol} disabled={!!busy}>
            {busy === "patrol" ? "应用中…" : "应用巡检设置"}
          </button>
        </div>
      </Field>

      <div className="hs-field-group-title">兜底与自检</div>
      <Field label={<><Tag kind="opt">可选</Tag>群机器人 Webhook</>} hint={<>
        应用发不出去时的兜底通道（纯文本）。<b>它没有回调，永远点不了按钮</b> ——
        只配它的话，卡片和审批都不会有。群机器人要设关键词 “IvyeaOps” 或 “CPU”。
      </>}>
        <SecretInput value={vals.alert_webhook || ""} onChange={(v) => set("alert_webhook", v)}
          placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
        <TestButton settingKey="alert_webhook" value={vals.alert_webhook} label="发测试消息" />
      </Field>
      <div className="hs-test-row" style={{ gap: 8 }}>
        <button className="hs-test-btn" type="button" onClick={doTest} disabled={!!busy}>
          {busy === "test" ? "发送中…" : "保存并发一张测试卡片"}
        </button>
        <button className="hs-test-btn" type="button" onClick={() => run("probe", () => reload(true))}
          disabled={!!busy}>
          {busy === "probe" ? "检测中…" : "重新检测"}
        </button>
        {msg && (
          <span className={"hs-test-result " + (msg.ok ? "ok" : "err")}>
            {msg.ok ? "✓" : "✗"} {msg.text}
          </span>
        )}
      </div>

      <div className="hs-field-group-title">服务器 CPU 告警阈值</div>
      <div className="hs-hint">
        这三项只管本机 CPU 看门狗（<code>scripts/cpu_alert.py</code>，独立进程跑）。
        它<b>不经过 IvyeaAgent</b> —— agent 挂了、8765 不通了，它还得能把消息发出来。
      </div>
      <div className="hs-row3">
        <Field label="触发阈值">
          <NumInput value={vals.alert_threshold ?? 80} onChange={(v) => set("alert_threshold", v)} min={10} max={9999} unit="%" />
        </Field>
        <Field label="持续时长">
          <NumInput value={vals.alert_sustain ?? 5} onChange={(v) => set("alert_sustain", v)} min={1} max={60} unit="分钟" />
        </Field>
        <Field label="冷却时间">
          <NumInput value={vals.alert_cooldown ?? 30} onChange={(v) => set("alert_cooldown", v)} min={1} max={1440} unit="分钟" />
        </Field>
      </div>
    </Section>
  );
}

function NotifySection({
  vals, set, save,
}: {
  vals: Partial<HubSettings>;
  set: <K extends keyof HubSettings>(k: K, v: HubSettings[K]) => void;
  save: (keys: (keyof HubSettings)[], vals: Partial<HubSettings>) => Promise<void>;
}) {
  const [cfg, setCfg] = useState<NotifyConfig | null>(null);
  const [budget, setBudget] = useState<BudgetStatus | null>(null);
  const [denied, setDenied] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  const reload = useCallback(async () => {
    try {
      const [c, b] = await Promise.all([getNotifyConfig(), getBudget()]);
      setCfg(c);
      setBudget(b);
    } catch {
      setDenied(true);
    }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  if (denied) return null;

  const picked: string[] = (() => {
    const raw = (vals.notify_events || "").trim();
    if (!raw) return cfg?.enabled_events || [];
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.map(String) : [];
    } catch { return []; }
  })();

  const toggle = (key: string) => {
    const next = picked.includes(key) ? picked.filter((k) => k !== key) : [...picked, key];
    set("notify_events", JSON.stringify(next));
  };

  const runTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      // 先把地址存下来再测 —— 否则用户改了输入框直接点测试，测的还是旧地址，
      // 结果对不上会让人以为是通知功能坏了。
      await save(["notify_webhook"], vals);
      setResult(await testNotify());
      await reload();
    } catch {
      setResult({ ok: false, detail: "保存或发送失败" });
    } finally { setTesting(false); }
  };

  const CHANNEL_LABEL: Record<string, string> = {
    feishu: "飞书", dingtalk: "钉钉", wecom: "企业微信",
    slack: "Slack", generic: "自定义接收端",
  };

  return (
    <Section
      title="通知与 AI 预算"
      desc="机器在服务器上跑，人在别处。任务挂了、这个月花超了，直接推到你手机上。"
      keys={["notify_webhook", "notify_events", "ai_budget_monthly_usd"]}
      vals={vals} onSave={save}
    >
      <Field
        label="通知地址（Webhook）"
        hint={<>
          支持飞书、钉钉、企业微信、Slack 和自建接收端 —— <b>粘进来就行，是哪一家我们自己认</b>。
          {cfg?.webhook_set && cfg.channel && <>当前识别为「{CHANNEL_LABEL[cfg.channel] || cfg.channel}」。</>}
          {" "}报文里只有事件类型、任务名和时间，<b>不含任何店铺数据或密钥</b>。
        </>}
      >
        <div className="hs-test-row" style={{ gap: 8 }}>
          <TxtInput value={vals.notify_webhook || ""} onChange={(v) => set("notify_webhook", v)}
            placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/…" />
          <button className="hs-test-btn" onClick={runTest} disabled={testing || !vals.notify_webhook}>
            {testing ? "发送中…" : "发条测试消息"}
          </button>
        </div>
        {result && (
          <div className="ms" style={{ marginTop: 6, color: result.ok ? "var(--acc)" : "var(--red)" }}>
            {result.ok ? "✓ " : "× "}{result.detail}
          </div>
        )}
      </Field>

      {cfg && (
        <Field label="发哪些事" hint="默认只发需要你动手的三类。每跑完一个任务都响一次的机器人，三天就会被静音。">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            {Object.entries(cfg.events).map(([key, label]) => (
              <label key={key} className="hs-toggle-line" style={{ margin: 0 }}>
                <input type="checkbox" checked={picked.includes(key)} onChange={() => toggle(key)} />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </Field>
      )}

      <Field
        label="每月 AI 预算（美元）"
        hint={<>
          填 0 表示不设预算。超了会推一条通知，<b>每月只提醒一次</b>，
          且<b>不会掐掉正在跑的任务</b> —— 这是按公开价目表对 token 的本地估算，不是账单，
          拿估算值去停用户的活儿，错一次就是事故。
        </>}
      >
        <TxtInput value={String(vals.ai_budget_monthly_usd ?? 0)}
          onChange={(v) => set("ai_budget_monthly_usd", Number(v) || 0)} placeholder="0" />
        {budget?.enabled && (
          <div className="ms" style={{ marginTop: 6 }}>
            {budget.month} 已用 <b>${budget.spend_usd.toFixed(2)}</b> / ${budget.limit_usd.toFixed(2)}
            （{Math.round(budget.ratio * 100)}%）
            {budget.exceeded && <span style={{ color: "var(--red)" }}> · 已超</span>}
          </div>
        )}
      </Field>
    </Section>
  );
}

/** 对外 MCP：把这台机器的亚马逊能力开放给 Claude Desktop / Cursor 等客户端。
 *
 *  管理员专属。非管理员拿到 403，这一整块就不渲染 —— 与其给他一个点了报错的
 *  面板，不如干脆不出现。 */
function McpSection() {
  const [tokens, setTokens] = useState<McpToken[] | null>(null);
  const [denied, setDenied] = useState(false);
  const [endpoint, setEndpoint] = useState("");
  const [name, setName] = useState("");
  const [allowWrite, setAllowWrite] = useState(false);
  const [ttl, setTtl] = useState(0);
  const [fresh, setFresh] = useState<IssuedToken | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState("");

  const reload = useCallback(async () => {
    try {
      const [list, cfg] = await Promise.all([listMcpTokens(), getMcpClientConfig()]);
      setTokens(list.tokens);
      setEndpoint(cfg.endpoint);
    } catch {
      setDenied(true);
    }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  if (denied) return null;

  const issue = async () => {
    setBusy(true);
    try {
      setFresh(await issueMcpToken(name || "未命名", allowWrite ? ["read", "write"] : ["read"], ttl));
      setName("");
      await reload();
    } finally { setBusy(false); }
  };

  const revoke = async (t: McpToken) => {
    if (!window.confirm(`撤销「${t.name}」？用它连着的客户端会立刻断开，且无法恢复。`)) return;
    await revokeMcpToken(t.id);
    if (fresh?.id === t.id) setFresh(null);
    await reload();
  };

  const snippet = JSON.stringify({
    mcpServers: {
      "ivyea-ops": {
        type: "http",
        url: endpoint || "http://<你的 IvyeaOps 地址>/api/mcp",
        headers: { Authorization: `Bearer ${fresh?.token || "<粘贴你的令牌>"}` },
      },
    },
  }, null, 2);

  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(what);
      setTimeout(() => setCopied(""), 2000);
    } catch { /* 剪贴板在非 HTTPS 下不可用，用户可以手动选中复制 */ }
  };

  const when = (ts: number | null) =>
    ts ? new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false }) : "—";

  return (
    <div className="hs-section">
      <div className="hs-section-hd">
        <div>
          <div className="hs-section-title">对外 MCP（让别的 AI 用上你的数据）</div>
          <div className="hs-section-desc">
            生成一个令牌，Claude Desktop、Cursor 或任何支持 MCP 的客户端就能调用这台机器上的广告结论、
            知识库和关键词调研。<b>令牌指向的是你自己的服务器</b> —— 数据不经过我们，也不经过任何第三方云。
          </div>
        </div>
      </div>
      <div className="hs-fields">
        <div className="hs-agent-card hs-agent-card-wide">
          <div className="hs-agent-card-title">新建令牌</div>
          <div className="hs-agent-card-desc">
            明文<b>只显示这一次</b>，关掉就再也拿不到（服务端只存哈希）。丢了就撤销重发。
          </div>
          <div className="hs-test-row" style={{ marginTop: 8, gap: 8, flexWrap: "wrap" }}>
            <TxtInput value={name} onChange={setName} placeholder="用途备注，如「我的 MacBook 上的 Claude」" />
            <SheetSelect className="hs-input" value={String(ttl)} onChange={(v) => setTtl(Number(v))}
              title="有效期" options={[
                { value: "0", label: "永久有效" },
                { value: "30", label: "30 天后过期" },
                { value: "90", label: "90 天后过期" },
                { value: "365", label: "一年后过期" },
              ]} />
            <button className="hs-save-btn" onClick={issue} disabled={busy}>
              {busy ? "生成中…" : "生成令牌"}
            </button>
          </div>
          <label className="hs-toggle-line">
            <input type="checkbox" checked={allowWrite} onChange={(e) => setAllowWrite(e.target.checked)} />
            <span>
              允许写操作（改广告投放）——
              <b>默认不给</b>。做分析的令牌不该顺带具备改你真实投放的能力。
            </span>
          </label>
          {fresh && (
            <div className="hs-agent-card" style={{ marginTop: 10, borderColor: "var(--acc)" }}>
              <div className="hs-agent-card-title">令牌已生成 —— 现在复制，之后看不到了</div>
              <code style={{ display: "block", wordBreak: "break-all", padding: "8px 0" }}>{fresh.token}</code>
              <button className="hs-test-btn" onClick={() => copy(fresh.token, "token")}>
                {copied === "token" ? "✓ 已复制" : "复制令牌"}
              </button>
            </div>
          )}
        </div>

        <div className="hs-agent-card hs-agent-card-wide">
          <div className="hs-agent-card-title">客户端配置</div>
          <div className="hs-agent-card-desc">
            粘进 Claude Desktop 的 <code>claude_desktop_config.json</code> 或 Cursor 的 <code>mcp.json</code>，重启客户端即可。
            对方机器要能访问到这个地址；<b>暴露到公网时务必套 HTTPS</b> —— 令牌是明文放在请求头里的。
          </div>
          <pre style={{ overflowX: "auto", fontSize: "var(--fs-12)", margin: "8px 0" }}>{snippet}</pre>
          <button className="hs-test-btn" onClick={() => copy(snippet, "cfg")}>
            {copied === "cfg" ? "✓ 已复制" : "复制配置"}
          </button>
        </div>

        <div className="hs-agent-card hs-agent-card-wide">
          <div className="hs-agent-card-title">已发出的令牌</div>
          {tokens === null ? (
            <div className="hs-agent-card-desc">加载中…</div>
          ) : tokens.length === 0 ? (
            <div className="hs-agent-card-desc">还没有发过令牌。</div>
          ) : (
            <table className="hs-table" style={{ width: "100%", fontSize: "var(--fs-13)" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>备注</th>
                  <th style={{ textAlign: "left" }}>权限</th>
                  <th style={{ textAlign: "left" }}>最后使用</th>
                  <th style={{ textAlign: "left" }}>过期</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tokens.map((t) => (
                  <tr key={t.id} style={t.revoked ? { opacity: 0.45 } : undefined}>
                    <td>{t.name}</td>
                    <td>{t.scopes.includes("write") ? "读 + 写" : "只读"}</td>
                    {/* 显示最后使用时间，用户才判断得出哪个令牌早就该撤销了 */}
                    <td>{t.revoked ? "已撤销" : when(t.last_used_at)}</td>
                    <td>{t.expires_at ? when(t.expires_at) : "永久"}</td>
                    <td>
                      {!t.revoked && (
                        <button className="hs-test-btn" onClick={() => revoke(t)}>撤销</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

function AppearanceSection() {
  // 用 router 的 hash 而不是 window.location.hash + hashchange：应用内 navigate 走的是
  // history.pushState，**pushState 不派发 hashchange**，只听那个事件在站内点根本不响。
  const { hash } = useLocation();
  const [fontId, setFontId] = useState(getFontId());
  const [zoom, setZoom] = useState(getZoom());
  const [weight, setWeight] = useState(getWeight());

  // 账户菜单里的「字体与字号」深链到这里（/hub-settings#appearance）。
  //
  // 这里踩过两次，两次的表现都是"点了没反应，停在设置首页"：
  //
  // 1. **滚一次是不够的。** 这一页的内容是异步来的（设置值、健康检查、MCP 列表…），
  //    挂载那一帧页面还很短，滚过去之后上面的内容陆续到达、把目标一路往下推，
  //    最终停在的位置和目标毫无关系。所以要**滚到位置稳定为止**。
  // 2. **已经在这一页时不会重新挂载。** 用户在设置页里点账户菜单的「字体与字号」，
  //    路由只是加了个 hash，组件不重挂，useEffect 不再跑 —— 所以依赖里要有 hash。
  //    **别用 window 的 hashchange**：应用内 navigate 走 pushState，不派发那个事件。
  useEffect(() => {
    let raf = 0;
    let timer = 0;
    const scrollToSelf = () => {
      window.clearTimeout(timer);
      cancelAnimationFrame(raf);
      let lastTop = Number.NaN;
      let stableFor = 0;
      const deadline = Date.now() + 3000;   // 兜底：再长也不能一直滚下去
      const step = () => {
        const el = document.getElementById("appearance");
        if (!el) return;
        const top = el.getBoundingClientRect().top;
        el.scrollIntoView({ block: "start", behavior: "smooth" });
        // 连续两次量到的位置一致（±2px）才算稳了 —— 说明上面的内容不再增高。
        stableFor = Math.abs(top - lastTop) < 2 ? stableFor + 1 : 0;
        lastTop = top;
        if (stableFor >= 2 || Date.now() > deadline) {
          el.classList.add("hs-section-hit");         // 到了要能看出来
          window.setTimeout(() => el.classList.remove("hs-section-hit"), 1600);
          return;
        }
        timer = window.setTimeout(() => { raf = requestAnimationFrame(step); }, 160);
      };
      raf = requestAnimationFrame(step);
    };

    if (hash === "#appearance") scrollToSelf();
    return () => {
      window.clearTimeout(timer);
      cancelAnimationFrame(raf);
    };
    // hash 进依赖：从别的页面跳进来、以及已经在这一页时再点一次，都要滚。
  }, [hash]);

  const onFont = (id: string) => { setFontId(id); applyFont(id); };
  const onZoom = (v: number) => { setZoom(v); applyZoom(v); };
  const onWeight = (v: number) => { setWeight(v); applyWeight(v); };
  return (
    <div className="hs-section" id="appearance">
      <div className="hs-section-hd">
        <div>
          <div className="hs-section-title">外观 / 显示</div>
          <div className="hs-section-desc">
            调整全局字体和字号，让界面更清晰。改动即时生效，仅影响当前设备（不上传、不影响他人）。
          </div>
        </div>
      </div>
      <div className="hs-fields">
        <div className="hs-agent-grid">
          <div className="hs-agent-card">
            <div className="hs-agent-card-title">字体</div>
            <div className="hs-agent-card-desc">
              默认「跟随主题」。想更好看就选「Inter + 系统中文 · 推荐」——
              数字和英文换成 Inter，汉字仍用系统里最好的那支（苹方 / 雅黑），只多下 48KB。
              想让每台机器长得一模一样，选「思源黑体 · 内置字库」（自带字库，首次约 2MB）。
            </div>
            <div style={{ marginTop: 8 }}>
              <SheetSelect className="hs-input" value={fontId} onChange={onFont} title="选择字体"
                options={FONT_OPTIONS.map((o) => ({ value: o.id, label: o.label }))} />
            </div>
          </div>

          <div className="hs-agent-card">
            <div className="hs-agent-card-title">字号</div>
            <div className="hs-agent-card-desc">整体缩放界面（含图标与间距），等同浏览器的放大/缩小。</div>
            <div className="hs-test-row" style={{ marginTop: 8, gap: 6, flexWrap: "wrap" }}>
              {ZOOM_OPTIONS.map((o) => {
                const active = Math.abs(zoom - o.value) < 1e-6;
                return (
                  <button key={o.id} type="button" className="hs-test-btn" onClick={() => onZoom(o.value)}
                    style={active ? { borderColor: "var(--acc)", color: "var(--acc)" } : undefined}>
                    {o.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="hs-agent-card">
            <div className="hs-agent-card-title">字重（加粗）</div>
            <div className="hs-agent-card-desc">觉得字太细可调粗。只加粗正文，标题不受影响；手机、桌面都生效。</div>
            <div className="hs-test-row" style={{ marginTop: 8, gap: 6, flexWrap: "wrap" }}>
              {WEIGHT_OPTIONS.map((o) => {
                const active = weight === o.value;
                return (
                  <button key={o.id} type="button" className="hs-test-btn" onClick={() => onWeight(o.value)}
                    style={active ? { borderColor: "var(--acc)", color: "var(--acc)" } : undefined}>
                    {o.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="hs-agent-card" style={{ gridColumn: "1 / -1" }}>
            <div className="hs-agent-card-title">预览</div>
            <div style={{ marginTop: 6, fontSize: "var(--fs-14)", lineHeight: 1.8, color: "var(--t)" }}>
              IvyeaOps 广告优化 · Listing 诊断 · 知识库检索 — The quick brown fox 0123456789
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * @param focusSection 深链要落到的分区 id（对话框传进来）。**它可能被折叠在
 *   「系统状态与更多设置」里** —— 那种情况下必须先把折叠块展开，否则目标元素
 *   根本不在 DOM 里，滚动无从谈起。
 *
 *   这正是「字体与字号」点了没反应的真正原因：不是滚错了位置，是外观区压根
 *   还没渲染出来，看起来就像"跳到了系统设置主页面"。
 */
const COLLAPSED_SECTIONS = new Set(["appearance"]);

/** 说明文字的显隐开关。
 *
 *  **默认必须是"显示"。** 这些说明装的不是废话，是操作指引 ——「登录 sorftime.com
 *  → 账户设置 → API」「留空 = 不带 Token」「保存后自动注册为 MCP 数据源」。默认藏
 *  起来，第一次配置的人打开就是一排空输入框，不知道 key 从哪儿来。所以只记住
 *  "用户主动关过"这件事：嫌烦的人点一次永久清爽，新用户第一次进来照样有指引。 */
const HS_HELP_KEY = "ivyea-ops.hs.help";

function useHelpVisible(): [boolean, (v: boolean) => void] {
  const [on, setOn] = useState<boolean>(() => {
    try { return localStorage.getItem(HS_HELP_KEY) !== "off"; } catch { return true; }
  });
  const set = (v: boolean) => {
    setOn(v);
    try { localStorage.setItem(HS_HELP_KEY, v ? "on" : "off"); } catch { /* 隐私模式下存不了，不影响本次会话 */ }
  };
  return [on, set];
}

export default function HubSettings({ focusSection = "" }: { focusSection?: string } = {}) {
  const [helpOn, setHelpOn] = useHelpVisible();
  // 订阅登录那一段只给管理员看：凭据存在服务器上、由 agent 全局共用，
  // 谁登录全站就烧谁的额度。后端那几个端点也是 require_admin，这里只是别把
  // 一个按下去必然 403 的按钮摆在普通用户面前。
  const { role } = useAuth();
  const isAdmin = role === "admin";
  const [vals, setVals] = useState<HubSettings>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState("");

  useEffect(() => {
    getSettings()
      .then(r => { setVals({ ...EMPTY, ...r.settings }); setLoading(false); })
      .catch(e => { setLoadErr(String(errText(e, "加载失败"))); setLoading(false); });
  }, []);

  const set = useCallback(<K extends keyof HubSettings>(k: K, v: HubSettings[K]) => {
    setVals(prev => ({ ...prev, [k]: v }));
  }, []);

  const save = useCallback(async (keys: (keyof HubSettings)[], current: Partial<HubSettings>) => {
    const patch: Partial<HubSettings> = {};
    for (const k of keys) (patch as Record<string, unknown>)[k] = current[k];
    await patchSettings(patch);
  }, []);

  const applySuggestions = useCallback(async (sug: Partial<Record<keyof HubSettings, string>>) => {
    const patch: Partial<HubSettings> = {};
    for (const [k, v] of Object.entries(sug)) {
      if (v) (patch as Record<string, unknown>)[k] = v;
    }
    if (Object.keys(patch).length === 0) return;
    const r = await patchSettings(patch);
    setVals({ ...EMPTY, ...r.settings });
  }, []);

  const [compatPathsOpen, setCompatPathsOpen] = useState(false);
  // 深链指向折叠块里的分区时，直接以展开态渲染 —— 让用户自己再点一次"更多设置"
  // 才看得到目标，等于这个深链没做。
  const [sysOpen, setSysOpen] = useState(() => COLLAPSED_SECTIONS.has(focusSection));

  if (loading) return (
    <div aria-busy="true" aria-live="polite" style={{ display: "grid", gap: 12, maxWidth: 720 }}>
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="card" style={{ padding: 14 }}>
          <div className="skeleton line sm" />
          <div className="skeleton" style={{ height: 30, marginTop: 8, borderRadius: 4 }} />
        </div>
      ))}
    </div>
  );
  if (loadErr) return <div className="hs-error">加载失败：{loadErr}</div>;

  return (
    <div className={"hs-page" + (helpOn ? "" : " hs-quiet")}>

      {/* ── Header ── */}
      <div className="hs-header">
        <span className="hs-header-icon">⊙</span>
        <div className="hs-header-main">
          <div className="hs-header-title">系统配置</div>
          <div className="hs-header-sub">优先配置 IvyeaAgent、数据源和全局兜底大模型；低频项已放到页面下方。</div>
        </div>
        <button
          type="button"
          className={"hs-help-toggle" + (helpOn ? " on" : "")}
          onClick={() => setHelpOn(!helpOn)}
          title={helpOn ? "隐藏每一项的说明文字，只留标签和输入框" : "显示每一项的说明文字（含取 key 的步骤）"}
        >
          {helpOn ? "◉ 说明已显示" : "○ 说明已隐藏"}
        </button>
      </div>

      <AutodetectPanel onApply={applySuggestions} />

      <SelfCheckPanel />

      {/* -- 核心 1: IvyeaAgent -- */}
      <Section
        title="IvyeaAgent"
        desc={<>系统主智能体。右下角 Agent 对话、知识库推理，以及通过对话操作 IvyeaOps 各板块，都优先走这里。</>}
        keys={[
          "ivyea_agent_url", "ivyea_agent_token", "ivyea_agent_auto_start",
          "ivyea_agent_provider", "ivyea_agent_model", "ivyea_agent_api_key", "ivyea_agent_base_url",
        ]}
        vals={vals} onSave={save}
      >
        <div className="hs-agent-grid">
          <div className="hs-agent-card hs-agent-card-main">
            <div className="hs-agent-card-title"><Tag kind="rec">推荐</Tag>内置 IvyeaAgent</div>
            <div className="hs-agent-card-desc">本机服务默认地址即可；远程部署时再修改。</div>
            <Field label="服务地址">
              <TxtInput value={vals.ivyea_agent_url} onChange={v => set("ivyea_agent_url", v)} placeholder="http://127.0.0.1:8765" />
              <TestButton settingKey="ivyea_agent_url" value={vals.ivyea_agent_url} label="测试 IvyeaAgent" />
            </Field>
          </div>

          <AgentUpdateRow />

          <div className="hs-agent-card">
            <div className="hs-agent-card-title"><Tag kind="rec">推荐</Tag>运行方式</div>
            <div className="hs-agent-card-desc">服务未启动时，IvyeaOps 自动拉起本机 IvyeaAgent。</div>
            <label className="hs-toggle-line">
              <input type="checkbox" checked={!!vals.ivyea_agent_auto_start}
                onChange={e => set("ivyea_agent_auto_start", e.target.checked)} />
              <span>{vals.ivyea_agent_auto_start ? "自动启动已开启" : "自动启动已关闭"}</span>
            </label>
          </div>

          <div className="hs-agent-card">
            <div className="hs-agent-card-title"><Tag kind="opt">可选</Tag>访问认证</div>
            <div className="hs-agent-card-desc">本机 127.0.0.1 默认不需要；远程部署或开启认证时填写。</div>
            <Field label="IvyeaAgent Token">
              <SecretInput value={vals.ivyea_agent_token} onChange={v => set("ivyea_agent_token", v)} placeholder="留空 = 不带 Token" />
            </Field>
          </div>
        </div>

        <div className="hs-agent-tools">
          <div className="hs-agent-tools-hd">
            <span>IvyeaAgent 主脑大模型</span>
            <em>保存后同步到本机 IvyeaAgent；留空则使用 Agent 自身默认配置。</em>
          </div>
          <LLMModelBlock
            title="Agent 模型"
            hint="可与全局兜底大模型不同。"
            providerKey="ivyea_agent_provider" modelKey="ivyea_agent_model"
            apiKeyKey="ivyea_agent_api_key" baseUrlKey="ivyea_agent_base_url"
            slot="agent"
            vals={vals} set={set}
          />
        </div>
      </Section>

      {/* -- 核心 1.5: 订阅制模型登录（仅管理员）-- */}
      {isAdmin && (
        <div className="hs-section">
          <div className="hs-section-hd">
            <div>
              <div className="hs-section-title">订阅登录</div>
              <div className="hs-section-desc">
                Claude 订阅、OpenAI Codex、Gemini Code Assist、Qwen、GitHub Copilot 这几家不是填 API Key，
                而是要走一次授权登录。以前只能去 IvyeaAgent 的命令行做，现在在这里点几下就行。
                登录完成后，它们会出现在任务台模型选择器的「已配置」分组里。
              </div>
            </div>
          </div>
          <div className="hs-fields">
            <SubscriptionLogin />
          </div>
        </div>
      )}

      {/* -- 核心 2: 数据源 -- */}
      <Section
        title="数据源"
        desc={<>运营数据接口。填 key 保存后自动生效，有哪个用哪个，都填则按场景择优调用。</>}
        keys={["sorftime_key", "sif_key", "sellersprite_key"]}
        vals={vals} onSave={save}
      >
        <Field
          label={<><Tag kind="rec">推荐</Tag>Sorftime Key</>}
          hint={<>市场调研、关键词趋势。登录 sorftime.com → 账户设置 → API。</>}
        >
          <SecretInput value={vals.sorftime_key} onChange={v => set("sorftime_key", v)} placeholder="你的 Sorftime key" />
          <TestButton settingKey="sorftime_key" value={vals.sorftime_key} label="测试" />
        </Field>

        <Field
          label={<><Tag kind="rec">推荐</Tag>SIF Key</>}
          hint={<>深度分析工具箱（关键词竞争、竞品信号、流量异常）。登录 sif.com → 获取 API Key。</>}
        >
          <SecretInput value={vals.sif_key} onChange={v => set("sif_key", v)} placeholder="你的 SIF key" />
          <TestButton settingKey="sif_key" value={vals.sif_key} label="测试" />
        </Field>

        <Field
          label={<><Tag kind="opt">可选</Tag>卖家精灵 Secret Key</>}
          hint={<>竞品关键词分析。保存后自动注册为 MCP 数据源，IvyeaAgent 对话中即可调用。登录 sellersprite.com → 账户 → API Key。</>}
        >
          <SecretInput value={vals.sellersprite_key} onChange={v => set("sellersprite_key", v)} placeholder="你的卖家精灵 Secret Key" />
          <TestButton settingKey="sellersprite_key" value={vals.sellersprite_key} label="测试" />
        </Field>

      </Section>

      {/* -- 核心 3: 全局兜底大模型 -- */}
      <Section
        title="全局兜底大模型"
        dataTour="settings-fallback"
        desc={<>所有板块的统一文本出口，也是 IvyeaAgent 掉线时任务台纯聊的兜底模型。建议配置一个稳定的文本大模型；IvyeaAgent 主脑模型可在最上方单独指定。</>}
        keys={["assistant_provider", "assistant_model", "assistant_api_key", "assistant_base_url"]}
        vals={vals} onSave={save}
      >
        <LLMModelBlock
          title="文本大模型"
          hint="市场调研、打法推荐、广告分析，以及 IvyeaAgent 不可用时的任务台对话会使用它。"
          providerKey="assistant_provider" modelKey="assistant_model"
          apiKeyKey="assistant_api_key" baseUrlKey="assistant_base_url"
          slot="assistant"
          inherit={[{
            label: "沿用主脑账号",
            title: "把 IvyeaAgent 主脑那套 Provider / 密钥 / 地址抄过来，只需再挑一个模型",
            run: () => {
              set("assistant_provider", vals.ivyea_agent_provider);
              set("assistant_api_key", vals.ivyea_agent_api_key);
              set("assistant_base_url", vals.ivyea_agent_base_url);
            },
          }]}
          vals={vals} set={set}
        />
      </Section>

      {/* -- 核心 3.5: 视觉复核模型（与全局兜底独立） -- */}
      <Section
        title="视觉复核模型"
        desc={<>看图任务专用：Listing 成图质检 + 按质检意见自动重画、图片视觉分析、竞品套图版式学习。
          与上面的全局兜底<b>完全独立</b>——可以文本用一家、看图用另一家。需选支持图片输入的模型
          （推荐硅基流动免费档 <code>Qwen/Qwen3-VL-30B-A3B-Instruct</code>，0 余额可用）。
          不配置时成图质检自动降级为人工复核（勾选「已核对」即可交付），流程不会卡住。</>}
        keys={["vision_provider", "vision_model", "vision_api_key", "vision_base_url"]}
        vals={vals} onSave={save}
      >
        <LLMModelBlock
          title="视觉大模型"
          hint="模型必须支持图片输入（多模态）。"
          providerKey="vision_provider" modelKey="vision_model"
          apiKeyKey="vision_api_key" baseUrlKey="vision_base_url"
          slot="vision" hintKind="vision"
          inherit={[
            {
              label: "沿用主脑账号",
              title: "抄 IvyeaAgent 主脑那套账号。注意仍要挑一个**支持图片输入**的模型",
              run: () => {
                set("vision_provider", vals.ivyea_agent_provider);
                set("vision_api_key", vals.ivyea_agent_api_key);
                set("vision_base_url", vals.ivyea_agent_base_url);
              },
            },
            {
              label: "沿用兜底账号",
              title: "抄上面「全局兜底大模型」那套账号",
              run: () => {
                set("vision_provider", vals.assistant_provider);
                set("vision_api_key", vals.assistant_api_key);
                set("vision_base_url", vals.assistant_base_url);
              },
            },
          ]}
          vals={vals} set={set}
        />
      </Section>

      {/* -- 核心 4: 图片生成服务 -- */}
      <Section
        title="图片生成服务"
        desc={<>默认走 Apimart。Apimart 不稳定/用不了时，在下方「自定义生图接口」填任意兼容 OpenAI <code>/images/generations</code> 的平台（地址 + Key + 模型名）即可切换——Listing 图片、图片翻译、任务台作图都会改用它。</>}
        keys={["apimart_key", "apimart_base", "image_model", "image_api_key", "image_base_url"]}
        vals={vals} onSave={save}
      >
        <Field
          label={<><Tag kind="rec">默认</Tag>Apimart API Key</>}
          hint={<>不接自定义平台时用它；Listing 图片生成、图片翻译和任务台作图共用。</>}
        >
          <div className="hs-key-inline">
            <SecretInput value={vals.apimart_key} onChange={v => set("apimart_key", v)} placeholder="sk-..." />
            <TestButton settingKey="apimart_key" value={vals.apimart_key} label="测试" />
          </div>
        </Field>

        <div className="hs-row2">
          <Field label="模型名称" hint="Apimart 用 gpt-image-2；自定义平台填它的模型名（如 dall-e-3）。">
            {/* 清单按**生效的那套账号**取：填了自定义地址就问自定义那家，没填就问
                Apimart —— 和真生成时走的端点完全同一套优先级（见后端 _SLOT_KEYS）。
                取不到（Apimart 余额不足时会返回 402）就还是手输，不挡人。 */}
            <ModelNameInput
              slot="image"
              provider="" baseUrl={vals.image_base_url} apiKey={vals.image_api_key}
              value={vals.image_model} onChange={v => set("image_model", v)}
              placeholder="gpt-image-2" hintKind="image"
              fallbackModels={["gpt-image-2", "gpt-image-1", "dall-e-3", "flux-1.1-pro",
                               "flux-kontext-pro", "seedream-4.0", "Tongyi-MAI/Z-Image-Turbo"]}
            />
          </Field>
          <Field label="Apimart 地址" hint="非官方网关才需改，否则保持默认。">
            <TxtInput value={vals.apimart_base} onChange={v => set("apimart_base", v)} placeholder="https://api.apimart.ai/v1" />
          </Field>
        </div>

        <div style={{ borderTop: "1px solid var(--b)", margin: "6px 0 2px", paddingTop: 10 }}>
          <div style={{ fontSize: "var(--fs-11)", color: "var(--t2)", fontWeight: 600, marginBottom: 2 }}>
            自定义生图接口（填了就用它，不再走 Apimart）
          </div>
          <div className="hs-inline-hint" style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 8 }}>
            任意兼容 OpenAI <code>/images/generations</code> 的平台均可：同步返回（b64/url）或 Apimart 式异步任务都支持。换平台时记得把上面的「模型名称」也改成该平台的模型名。
          </div>
        </div>
        <div className="hs-row2">
          <Field label={<><Tag kind="opt">可选</Tag>接口地址 Base URL</>}
            hint="到 /v1 为止，如 https://api.openai.com/v1。留空 = 用 Apimart。">
            <TxtInput value={vals.image_base_url} onChange={v => set("image_base_url", v)} placeholder="https://api.openai.com/v1" />
          </Field>
          <Field label={<><Tag kind="opt">可选</Tag>API Key</>}
            hint="该平台的 Key。留空 = 复用 Apimart Key。">
            <SecretInput value={vals.image_api_key} onChange={v => set("image_api_key", v)} placeholder="sk-..." />
          </Field>
        </div>
        <TestButton settingKey="image_base_url" value={vals.image_base_url} label="测试自定义生图接口" />
      </Section>

      {/* 飞书放在折叠线**之上**：它现在同时管服务器告警、店铺巡检卡片、审批按钮
          和飞书对话四条链路，是第一次部署就要配的东西。塞进「系统状态与更多设置」
          里等于没有——没人会为了找配置去点开一个叫「系统状态」的折叠块。 */}
      <FeishuSection vals={vals} set={set} save={save} />

      {/* 亚马逊官方 API 紧跟数据源：它和领星是同一类东西（数据从哪来），
          只是一个是第一手、一个是转手。 */}
      <AmazonSection />

      {/* ── 系统状态及以下：默认折叠，点开查看 ── */}
      <div className="hs-advanced">
        <button type="button" className="hs-advanced-toggle" onClick={() => setSysOpen(o => !o)}>
          <span style={{ display: "inline-block", transition: "transform .15s", transform: sysOpen ? "rotate(90deg)" : "none" }}>▶</span>
          <span className="hs-advanced-toggle-label">系统状态与更多设置</span>
          <span className="hs-advanced-toggle-sub">
            {sysOpen ? "点击收起" : "系统状态 · 可选 AI 能力 · 兼容旧链路 · 高级选项 · 修改密码 · Skill Studio"}
          </span>
        </button>
        {sysOpen && (
        <div className="hs-advanced-body">

      <HealthPanel />

      {/* -- 可选能力：AI 降级、视觉、Embedding -- */}
      <Section
        title="可选 AI 能力"
        desc="低频或增强项：AI 降级链、图片分析和自动修复。默认值已可覆盖大多数场景。"
        keys={["text_ai_providers", "autofix_enabled",
          "skill_market_enabled", "skill_market_url", "skill_market_pubkey",
          "vision_ai_providers", "openai_api_key", "deepseek_api_key"]}
        vals={vals} onSave={save}
      >
        <Field label={<><Tag kind="opt">可选</Tag>AI 提供商顺序（全局降级链）</>}
          hint={<>逗号分隔，按顺序尝试：<code>ivyea-agent</code> <code>deepseek</code> <code>assistant</code>（全局兜底大模型）<code>codex</code> <code>claude</code>。<code>hermes</code> 已从降级链移除，填了也会被忽略。</>}>
          <TxtInput value={vals.text_ai_providers} onChange={v => set("text_ai_providers", v)} placeholder="ivyea-agent,deepseek,assistant,codex,claude" />
        </Field>
        <Field label={<><Tag kind="opt">可选</Tag>视觉识别顺序（图片分析）</>}
          hint={<>Listing「AI 图片分析」走这条链。Apimart 只生图、无视觉，不在此列。</>}>
          <TxtInput value={vals.vision_ai_providers} onChange={v => set("vision_ai_providers", v)} placeholder="openai,assistant" />
        </Field>
        <Field label={<><Tag kind="opt">可选</Tag>OpenAI API Key（视觉识别）</>}
          hint={<>用于「AI 图片分析」的视觉模型（GPT-4o 系）。</>}>
          <SecretInput value={vals.openai_api_key} onChange={v => set("openai_api_key", v)} placeholder="sk-..." />
        </Field>
        <Field label={<><Tag kind="opt">可选 · 进阶</Tag>DeepSeek 专用 Key</>}
          hint={<>仅当把 <code>deepseek</code> 单独加进上面降级链时才需要；多数场景用「全局兜底大模型」选 DeepSeek 即可。</>}>
          <SecretInput value={vals.deepseek_api_key} onChange={v => set("deepseek_api_key", v)} placeholder="sk-..." />
        </Field>


        <div className="hs-agent-card hs-agent-card-wide">
          <div className="hs-agent-card-title"><Tag kind="opt">功能</Tag>能力市场（门道社区）</div>
          <div className="hs-agent-card-desc">
            从门道社区浏览并安装 Skill。<b>默认关闭</b>：它会向社区发起请求，而 IvyeaOps 的默认立场是数据不出你的机器。
            开启后也只在你主动浏览或安装时联网 —— 请求匿名、不带机器标识、不回传任何使用统计；装过的 Skill 落在本地，断网照常用。
            安装前会先给你看这个 Skill 的能力清单，确认后才落盘。
          </div>
          <label className="hs-toggle-line">
            <input type="checkbox" checked={vals.skill_market_enabled}
              onChange={e => set("skill_market_enabled", e.target.checked)} />
            <span>{vals.skill_market_enabled ? "能力市场已开启（Skill 中心 → 社区市场）" : "能力市场已关闭"}</span>
          </label>
          {vals.skill_market_enabled && (
            <>
              <Field label="市场地址" hint={<>留空用默认的门道社区。可换成自建镜像 —— 换源之后仍会校验安装包的校验和与签名。</>}>
                <TxtInput value={vals.skill_market_url} onChange={v => set("skill_market_url", v)}
                  placeholder="https://mendao.ivyea.com/api/market" />
              </Field>
              <label className="hs-toggle-line">
                <input type="checkbox" checked={!!vals.skill_market_allow_class_b}
                  onChange={e => set("skill_market_allow_class_b", e.target.checked)} />
                <span>
                  允许一键安装<b>含可执行脚本</b>的技能（B 类）——
                  {vals.skill_market_allow_class_b ? "已开启" : "默认关闭"}
                </span>
              </label>
              <div className="hs-hint" style={{ marginTop: -4, marginBottom: 10 }}>
                关着的时候<b>不是不能用</b>：安装包随时可以下载下来自己审、自己放进技能库。
                打开只是省掉手动那步 —— 每次安装仍会把脚本清单逐条列出来让你确认。
                这类技能里的代码会在 Agent 用到它时在你机器上运行，
                而社区内容<b>未经官方审计</b>。
              </div>
              <Field label={<><Tag kind="opt">可选</Tag>市场公钥</>}
                hint={<>用于校验安装包签名。留空则只校验 sha256（能证明"没传坏"，但证明不了"是那边发布的那份"）。</>}>
                <TxtInput value={vals.skill_market_pubkey} onChange={v => set("skill_market_pubkey", v)}
                  placeholder="base64 编码的 Ed25519 公钥" />
              </Field>
            </>
          )}
        </div>

        <div className="hs-agent-card hs-agent-card-wide">
          <div className="hs-agent-card-title"><Tag kind="opt">功能</Tag>自动修复 Bug</div>
          <div className="hs-agent-card-desc">开启后，功能报错时弹窗询问是否 AI 修复；在隔离副本中排查，你审核 diff 后应用。默认关闭。</div>
          <label className="hs-toggle-line">
            <input type="checkbox" checked={vals.autofix_enabled}
              onChange={e => set("autofix_enabled", e.target.checked)} />
            <span>{vals.autofix_enabled ? "自动修复已开启" : "自动修复已关闭"}</span>
          </label>
        </div>
      </Section>

      {/* -- 低优先级：兼容与旧链路 -- */}
      <Section
        title="兼容与旧链路"
        desc="Hermes/Codex/Claude 仅作为兼容或增强链路。新部署通常不需要配置。"
        keys={[
          "hermes_provider", "hermes_model", "hermes_api_key", "hermes_base_url",
          "hermes_fallback_provider", "hermes_fallback_model",
          "hermes_fallback_api_key", "hermes_fallback_base_url",
          "hermes_bin", "codex_bin", "claude_bin", "brain_root",
        ]}
        vals={vals} onSave={save}
      >
        <LLMModelBlock
          title="Hermes 主模型（旧兼容）"
          providerKey="hermes_provider" modelKey="hermes_model"
          apiKeyKey="hermes_api_key" baseUrlKey="hermes_base_url"
          vals={vals} set={set}
        />
        <div style={{ borderTop: "1px solid var(--b)", margin: "12px 0" }} />
        <LLMModelBlock
          title="Hermes Fallback 模型（可选）"
          hint="旧 Hermes 链路主模型限流或报错时使用。"
          providerKey="hermes_fallback_provider" modelKey="hermes_fallback_model"
          apiKeyKey="hermes_fallback_api_key" baseUrlKey="hermes_fallback_base_url"
          vals={vals} set={set}
        />

        <div className="hs-agent-tools">
          <div className="hs-agent-tools-hd">
            <span>外部 CLI 检测</span>
            <em>绿色即代表可用；通常无需手动配置路径。</em>
          </div>
          <div className="hs-agent-tools-list">
            {(["hermes", "codex", "claude"] as const).map(name => {
              const key = `${name}_bin` as keyof HubSettings;
              const val = (vals[key] as string) || "";
              return (
                <TestButton key={name} settingKey={key} value={val}
                  label={`${name === "hermes" ? "Hermes" : name === "codex" ? "Codex" : "Claude"} 检测`} />
              );
            })}
          </div>
        </div>

        <button
          type="button"
          onClick={() => setCompatPathsOpen(o => !o)}
          style={{
            display: "flex", alignItems: "center", gap: 6, marginBottom: 4,
            background: "transparent", border: "1px solid var(--b)", borderRadius: 4,
            padding: "5px 12px", color: "var(--t3)", fontSize: "var(--fs-11)",
            cursor: "pointer", fontFamily: "var(--font)",
          }}
        >
          <span style={{ display: "inline-block", transition: "transform .15s", transform: compatPathsOpen ? "rotate(90deg)" : "none" }}>▶</span>
          手动指定路径（自动发现失败时才需要）
        </button>

        {compatPathsOpen && (
          <div style={{ paddingLeft: 10, borderLeft: "2px solid var(--b)" }}>
            <Field label={<><Tag kind="opt">可选</Tag>Hermes 路径</>} hint="留空 = PATH 自动发现">
              <TxtInput value={vals.hermes_bin} onChange={v => set("hermes_bin", v)} placeholder="留空 = PATH 自动发现" />
            </Field>
            <Field label={<><Tag kind="opt">可选</Tag>Codex 路径</>} hint="留空 = PATH 自动发现">
              <TxtInput value={vals.codex_bin} onChange={v => set("codex_bin", v)} placeholder="留空 = PATH 自动发现" />
            </Field>
            <Field label={<><Tag kind="opt">可选</Tag>Claude 路径</>} hint={<>留空 = PATH 自动发现。<code>npm i -g @anthropic-ai/claude-code</code></>}>
              <TxtInput value={vals.claude_bin} onChange={v => set("claude_bin", v)} placeholder="留空 = PATH 自动发现" />
            </Field>
            <Field label={<><Tag kind="opt">可选 · 旧兼容</Tag>知识库根目录</>} hint={<>旧笔记目录，留空 = <code>~/brain</code>。新知识库文件由 IvyeaAgent 保存在 <code>~/.ivyea/knowledge</code>。</>}>
              <TxtInput value={vals.brain_root} onChange={v => set("brain_root", v)} placeholder="~/brain" />
            </Field>
          </div>
        )}
      </Section>

      {/* -- 区块 3 & 4: 通知 + 高级（折叠） -- */}
      <AdvancedBlock>

        {/* 高级 / 运维 */}
        <Section
          title="高级 / 运维"
          desc="Listing 图片后端、嵌入服务 URL、Token 监控 DB 路径、Kiro 集成等。通常无需改动。"
          keys={["imgflow_url", "dashboard_url", "terminal_url", "hermes_db", "codex_db", "claude_projects_dir",
            "kiro_cli_bin", "kiro_gateway_db", "kiro_cli_db", "kiro_cli_sessions_dir",
            "feishu_codex_db", "hermes_node_bin", "bun_bin", "news_feeds"]}
          vals={vals} onSave={save}
        >
          <div className="hs-field-group-title">资讯 RSS 源</div>
          <Field label={<><Tag kind="opt">可选</Tag>资讯 RSS 源</>}
            hint={<>「资讯」板块的抓取源，每行一条 <code>url | 来源名 | 分类</code>（分类 = <code>ai_industry</code> 或 <code>amazon_seller</code>）。留空 = 用内置默认源。</>}>
            <AreaInput value={vals.news_feeds} onChange={v => set("news_feeds", v)} rows={4}
              placeholder={"https://example.com/feed.xml | 来源名 | ai_industry\nhttps://.../rss | 卖家资讯 | amazon_seller"} />
          </Field>
          <div className="hs-field-group-title">图片处理后端</div>
          <Field label={<><Tag kind="opt">可选</Tag>Imgflow 地址</>} hint={<>Listing 图片处理后端，默认 <code>http://127.0.0.1:3001</code>。</>}>
            <TxtInput value={vals.imgflow_url} onChange={v => set("imgflow_url", v)} placeholder="http://127.0.0.1:3001" />
            <TestButton settingKey="imgflow_url" value={vals.imgflow_url} label="测试" />
          </Field>
          <div className="hs-row3">
            <Field label="仪表盘地址">
              <TxtInput value={vals.dashboard_url} onChange={v => set("dashboard_url", v)} placeholder="https://..." />
            </Field>
            <Field label="外部终端地址">
              <TxtInput value={vals.terminal_url} onChange={v => set("terminal_url", v)} placeholder="https://..." />
            </Field>
          </div>

          <div className="hs-field-group-title" style={{ marginTop: 12 }}>Token 用量监控（DB 路径）</div>
          <div className="hs-row3">
            <Field label="Hermes state.db">
              <TxtInput value={vals.hermes_db} onChange={v => set("hermes_db", v)} placeholder="~/.hermes/state.db" />
            </Field>
            <Field label="Codex state DB">
              <TxtInput value={vals.codex_db} onChange={v => set("codex_db", v)} placeholder="~/.codex/state_5.sqlite" />
            </Field>
            <Field label="Claude projects 目录">
              <TxtInput value={vals.claude_projects_dir} onChange={v => set("claude_projects_dir", v)} placeholder="~/.claude/projects" />
            </Field>
          </div>

          <div className="hs-field-group-title" style={{ marginTop: 12 }}>Kiro · 飞书-Codex · PATH</div>
          <div className="hs-row3">
            <Field label="kiro-cli 路径">
              <TxtInput value={vals.kiro_cli_bin as string} onChange={v => set("kiro_cli_bin", v)} placeholder="PATH 自动发现" />
            </Field>
            <Field label="Hermes Node 目录">
              <TxtInput value={vals.hermes_node_bin as string} onChange={v => set("hermes_node_bin", v)} placeholder="~/.hermes/node/bin" />
            </Field>
            <Field label="Bun 目录">
              <TxtInput value={vals.bun_bin as string} onChange={v => set("bun_bin", v)} placeholder="~/.bun/bin" />
            </Field>
          </div>
          <div className="hs-row3">
            <Field label="Kiro Gateway DB">
              <TxtInput value={vals.kiro_gateway_db as string} onChange={v => set("kiro_gateway_db", v)} placeholder="~/kiro-gateway/usage.db" />
            </Field>
            <Field label="Kiro CLI DB">
              <TxtInput value={vals.kiro_cli_db as string} onChange={v => set("kiro_cli_db", v)} placeholder="~/.local/share/kiro-cli/data.sqlite3" />
            </Field>
            <Field label="飞书-Codex 中继 DB">
              <TxtInput value={vals.feishu_codex_db as string} onChange={v => set("feishu_codex_db", v)} placeholder="~/feishu-codex-relay/..." />
            </Field>
          </div>
        </Section>

      </AdvancedBlock>

      {/* ── 通知与预算（管理员专属）── */}
      <NotifySection vals={vals} set={set} save={save} />

      {/* ── 对外 MCP（管理员专属，非管理员自动不渲染）── */}
      <McpSection />

      {/* ── 外观 / 显示（低频显示项，放页面下方）── */}
      <AppearanceSection />

      {/* ── 账号安全 ── */}
      <ChangePassword />

      {/* ── Skill Studio 跳转 ── */}
      <div className="hs-section hs-section-link">
        <div className="hs-section-hd">
          <div>
            <div className="hs-section-title">Skill Studio 配置</div>
            <div className="hs-section-desc">快照保留策略、编辑器主题、Git 导入设置等。</div>
          </div>
          <Link to="/skill/settings" className="hs-save-btn" style={{ textDecoration: "none" }}>
            前往配置 →
          </Link>
        </div>
      </div>

        </div>
        )}
      </div>

    </div>
  );
}
