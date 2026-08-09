import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import SheetSelect from "../../components/SheetSelect";
import {
  getSettings, patchSettings, getHealth, changePassword,
  testSetting, autodetectSettings, selfCheckSettings, getAgentVersion,
  startAgentUpgrade, getAgentUpgradeProgress,
  type HubSettings, type HealthResp, type TestResult, type SelfCheckResp,
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
          fontSize: 12.5, fontFamily: "var(--font)", cursor: "pointer",
          outline: "none", transition: "border .12s",
        }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={{ fontWeight: selected ? 500 : 400 }}>
            {selected ? selected.label : "选择 Provider"}
          </span>
          {selected?.hint && (
            <span style={{ color: "var(--t3)", fontSize: 11, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {selected.hint}
            </span>
          )}
        </span>
        <span style={{ color: "var(--t3)", fontSize: 9, marginLeft: 8, flexShrink: 0 }}>▼</span>
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
              <span style={{ fontSize: 13, fontWeight: 600, color: "var(--t)" }}>选择模型 Provider</span>
              <span onClick={() => setOpen(false)} style={{ cursor: "pointer", color: "var(--t3)", fontSize: 16, lineHeight: 1 }}>✕</span>
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
                      fontSize: 13, fontFamily: "var(--font)", cursor: "pointer",
                      userSelect: "none", transition: "background .1s",
                    }}
                  >
                    <span style={{ flex: 1, fontWeight: isSel ? 500 : 400 }}>{p.label}</span>
                    {p.hint && (
                      <span style={{ color: isSel ? "color-mix(in srgb, var(--acc) 70%, var(--t3))" : "var(--t3)", fontSize: 11 }}>
                        {p.hint}
                      </span>
                    )}
                    <span style={{ width: 12, textAlign: "center", color: "var(--acc)", fontSize: 12, flexShrink: 0 }}>
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
  vals, set,
}: {
  title: string; hint?: string;
  providerKey: keyof HubSettings; modelKey: keyof HubSettings;
  apiKeyKey: keyof HubSettings; baseUrlKey: keyof HubSettings;
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
      <div style={{ fontSize: 11, color: "var(--t2)", fontWeight: 600, marginBottom: 10 }}>
        {title}
        {hint && <span style={{ fontWeight: 400, color: "var(--t3)", marginLeft: 8 }}>{hint}</span>}
      </div>

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
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 10, alignItems: "end" }}>
          <Field label="模型名称" hint={info?.examples ? `可用：${info.examples}` : (info?.defaultModel ? `推荐：${info.defaultModel}` : undefined)}>
            <TxtInput
              value={model}
              onChange={v => set(modelKey, v as HubSettings[typeof modelKey])}
              placeholder={info?.defaultModel || "模型名称"}
            />
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

  type InstallableComponent = "ivyea-agent" | "legacy" | "hermes" | "gbrain" | "ollama" | "codex" | "claude" | "all";

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
    { label: "兼容 · GBrain CLI",         key: "gbrain_bin", install: "gbrain" },
    { label: "兼容 · Ollama Embedding",   key: "ollama", install: "ollama" },
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
              <span className="hs-health-detail" title={full}>{shortDetail(full)}</span>
              {row.install && (
                <button
                  className="hs-refresh-btn"
                  style={{ padding: "2px 8px", fontSize: 10 }}
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
          {open ? "点击收起" : "Token 监控 · Imgflow · 飞书通知 · CPU 告警 · 内嵌服务 · Kiro"}
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
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--t)" }}>一键全部自检</div>
          <div style={{ fontSize: 11, color: "var(--t3)", marginTop: 2 }}>
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
          <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 6 }}>
            通过 {res.ok} · 失败 {res.err} · 未配置 {res.skip} · 共 {res.total}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {res.results.map((r) => (
              <div key={r.key} style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 11 }}>
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
        {hasUpd && <span style={{ marginLeft: 8, fontSize: 10, color: "#fff", background: "#dc2626", borderRadius: 8, padding: "1px 7px" }}>有新版</span>}
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
          <div style={{ fontSize: 10, color: "var(--t3)", marginTop: 4 }}>{_PHASE_LABEL[phase] || "更新中…"}（{percent}%）</div>
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
  gbrain_embed_provider: "", gbrain_embed_model: "", gbrain_embed_api_key: "",
  apimart_key: "", apimart_base: "https://api.apimart.ai/v1",
  text_ai_providers: "ivyea-agent,assistant,deepseek,codex,claude",
  vision_ai_providers: "openai,assistant", deepseek_api_key: "", news_feeds: "",
  sorftime_key: "", sif_key: "", sellersprite_key: "",
  imgflow_url: "http://127.0.0.1:3001",
  gbrain_bin: "", brain_root: "", openai_api_key: "",
  alert_webhook: "", alert_app_id: "", alert_app_secret: "", alert_chat_id: "",
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
          <pre style={{ overflowX: "auto", fontSize: 12, margin: "8px 0" }}>{snippet}</pre>
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
            <table className="hs-table" style={{ width: "100%", fontSize: 13 }}>
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
  const [fontId, setFontId] = useState(getFontId());
  const [zoom, setZoom] = useState(getZoom());
  const [weight, setWeight] = useState(getWeight());
  const onFont = (id: string) => { setFontId(id); applyFont(id); };
  const onZoom = (v: number) => { setZoom(v); applyZoom(v); };
  const onWeight = (v: number) => { setWeight(v); applyWeight(v); };
  return (
    <div className="hs-section">
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
              默认「跟随主题」。觉得字太细/不清晰，换「系统默认 · 清晰」或「微软雅黑」通常最舒服。
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
            <div style={{ marginTop: 6, fontSize: 14, lineHeight: 1.8, color: "var(--t)" }}>
              IvyeaOps 广告优化 · Listing 诊断 · 知识库检索 — The quick brown fox 0123456789
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function HubSettings() {
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
  const [sysOpen, setSysOpen] = useState(false);

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
    <div className="hs-page">

      {/* ── Header ── */}
      <div className="hs-header">
        <span className="hs-header-icon">⊙</span>
        <div>
          <div className="hs-header-title">系统配置</div>
          <div className="hs-header-sub">优先配置 IvyeaAgent、数据源和全局兜底大模型；低频项已放到页面下方。</div>
        </div>
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
            vals={vals} set={set}
          />
        </div>
      </Section>

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
        desc={<>所有板块的统一文本出口，也是 AI 问答的默认模型。建议配置一个稳定的文本大模型；IvyeaAgent 主脑模型可在最上方单独指定。</>}
        keys={["assistant_provider", "assistant_model", "assistant_api_key", "assistant_base_url"]}
        vals={vals} onSave={save}
      >
        <LLMModelBlock
          title="文本大模型"
          hint="市场调研、打法推荐、广告分析、AI 问答等文本任务会使用它。"
          providerKey="assistant_provider" modelKey="assistant_model"
          apiKeyKey="assistant_api_key" baseUrlKey="assistant_base_url"
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
          vals={vals} set={set}
        />
      </Section>

      {/* -- 核心 4: 图片生成服务 -- */}
      <Section
        title="图片生成服务"
        desc={<>默认走 Apimart。Apimart 不稳定/用不了时，在下方「自定义生图接口」填任意兼容 OpenAI <code>/images/generations</code> 的平台（地址 + Key + 模型名）即可切换——Listing 图片、图片翻译、AI 生图都会改用它。</>}
        keys={["apimart_key", "apimart_base", "image_model", "image_api_key", "image_base_url"]}
        vals={vals} onSave={save}
      >
        <Field
          label={<><Tag kind="rec">默认</Tag>Apimart API Key</>}
          hint={<>不接自定义平台时用它；Listing 图片生成、图片翻译和 AI 生图共用。</>}
        >
          <div className="hs-key-inline">
            <SecretInput value={vals.apimart_key} onChange={v => set("apimart_key", v)} placeholder="sk-..." />
            <TestButton settingKey="apimart_key" value={vals.apimart_key} label="测试" />
          </div>
        </Field>

        <div className="hs-row2">
          <Field label="模型名称" hint="Apimart 用 gpt-image-2；自定义平台填它的模型名（如 dall-e-3）。">
            <TxtInput value={vals.image_model} onChange={v => set("image_model", v)} placeholder="gpt-image-2" />
          </Field>
          <Field label="Apimart 地址" hint="非官方网关才需改，否则保持默认。">
            <TxtInput value={vals.apimart_base} onChange={v => set("apimart_base", v)} placeholder="https://api.apimart.ai/v1" />
          </Field>
        </div>

        <div style={{ borderTop: "1px solid var(--b)", margin: "6px 0 2px", paddingTop: 10 }}>
          <div style={{ fontSize: 11, color: "var(--t2)", fontWeight: 600, marginBottom: 2 }}>
            自定义生图接口（填了就用它，不再走 Apimart）
          </div>
          <div style={{ fontSize: 10, color: "var(--t3)", marginBottom: 8 }}>
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
        desc="低频或增强项：AI 降级链、图片分析、知识库语义检索和自动修复。默认值已可覆盖大多数场景。"
        keys={["text_ai_providers", "autofix_enabled",
          "skill_market_enabled", "skill_market_url", "skill_market_pubkey",
          "vision_ai_providers", "openai_api_key", "deepseek_api_key",
          "gbrain_embed_provider", "gbrain_embed_model", "gbrain_embed_api_key"]}
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

        <div style={{ borderTop: "1px solid var(--b)", margin: "4px 0 2px", paddingTop: 10 }}>
          <div style={{ fontSize: 11, color: "var(--t2)", fontWeight: 600, marginBottom: 2 }}>
            知识库语义检索（Embedding）
          </div>
          <div style={{ fontSize: 10, color: "var(--t3)", marginBottom: 8 }}>
            配置后知识库支持语义检索；留空则仅关键词检索。
          </div>
        </div>
        <Field label={<><Tag kind="opt">可选</Tag>Embedding 服务商</>}
          hint={<>支持 embedding 的服务商。<code>ollama</code> 本地免费，其余需对应 API Key。</>}>
          <SheetSelect className="hs-input" value={vals.gbrain_embed_provider} title="Embedding 服务商"
            onChange={p => {
              set("gbrain_embed_provider", p);
              if (p && !vals.gbrain_embed_model) {
                const dm: Record<string, string> = {
                  openai: "text-embedding-3-large", zhipu: "embedding-3",
                  dashscope: "text-embedding-v3", minimax: "embo-01",
                  voyage: "voyage-3", google: "text-embedding-004",
                  ollama: "nomic-embed-text",
                };
                if (dm[p]) set("gbrain_embed_model", dm[p]);
              }
            }}
            options={[
              { value: "", label: "未配置（关键词检索）" },
              { value: "ollama", label: "Ollama（本地免费）" },
              { value: "zhipu", label: "智谱 Zhipu" },
              { value: "dashscope", label: "阿里 DashScope" },
              { value: "minimax", label: "MiniMax" },
              { value: "openai", label: "OpenAI" },
              { value: "voyage", label: "Voyage" },
              { value: "google", label: "Google" },
            ]} />
        </Field>
        {vals.gbrain_embed_provider && (
          <Field label="Embedding 模型" hint="已按服务商预填默认值，可改">
            <TxtInput value={vals.gbrain_embed_model} onChange={v => set("gbrain_embed_model", v)} placeholder="模型名" />
          </Field>
        )}
        {vals.gbrain_embed_provider && vals.gbrain_embed_provider !== "ollama" && (
          <Field label="Embedding API Key">
            <SecretInput value={vals.gbrain_embed_api_key} onChange={v => set("gbrain_embed_api_key", v)} placeholder="对应服务商的 API Key" />
          </Field>
        )}

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
        desc="Hermes/Codex/Claude/GBrain 仅作为兼容或增强链路。新部署通常不需要配置。"
        keys={[
          "hermes_provider", "hermes_model", "hermes_api_key", "hermes_base_url",
          "hermes_fallback_provider", "hermes_fallback_model",
          "hermes_fallback_api_key", "hermes_fallback_base_url",
          "hermes_bin", "codex_bin", "claude_bin", "gbrain_bin", "brain_root",
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
            padding: "5px 12px", color: "var(--t3)", fontSize: 11,
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
            <Field label={<><Tag kind="opt">可选</Tag>GBrain 路径</>} hint={<>留空 = PATH 自动发现（通常 <code>~/.bun/bin/gbrain</code>）。</>}>
              <TxtInput value={vals.gbrain_bin} onChange={v => set("gbrain_bin", v)} placeholder="留空 = PATH 自动发现" />
            </Field>
            <Field label={<><Tag kind="opt">可选 · 旧兼容</Tag>知识库根目录</>} hint={<>GBrain 笔记目录，留空 = <code>~/brain</code>。新知识库文件由 IvyeaAgent 保存在 <code>~/.ivyea/knowledge</code>。</>}>
              <TxtInput value={vals.brain_root} onChange={v => set("brain_root", v)} placeholder="~/brain" />
            </Field>
          </div>
        )}
      </Section>

      {/* -- 区块 3 & 4: 通知 + 高级（折叠） -- */}
      <AdvancedBlock>

        {/* 飞书通知 */}
        <Section
          title="飞书 / Lark 通知"
          desc="CPU 告警推送到飞书群。Webhook（渠道 A）和自建应用（渠道 B）任选其一。"
          keys={["alert_webhook", "alert_app_id", "alert_app_secret", "alert_chat_id", "alert_threshold", "alert_sustain", "alert_cooldown"]}
          vals={vals} onSave={save}
        >
          <Field label="Webhook 地址" hint={<>群机器人 → 添加自定义机器人，复制 URL。设关键词 "IvyeaOps" 或 "CPU"。</>}>
            <SecretInput value={vals.alert_webhook} onChange={v => set("alert_webhook", v)}
              placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
            <TestButton settingKey="alert_webhook" value={vals.alert_webhook} label="发测试消息" />
          </Field>
          <div className="hs-row3">
            <Field label="App ID" hint="cli_ 开头">
              <TxtInput value={vals.alert_app_id} onChange={v => set("alert_app_id", v)} placeholder="cli_xxx" />
            </Field>
            <Field label="App Secret">
              <SecretInput value={vals.alert_app_secret} onChange={v => set("alert_app_secret", v)} placeholder="App Secret" />
            </Field>
            <Field label="Chat ID" hint="oc_ 开头">
              <TxtInput value={vals.alert_chat_id} onChange={v => set("alert_chat_id", v)} placeholder="oc_..." />
            </Field>
          </div>
          <div className="hs-row3" style={{ marginTop: 8 }}>
            <Field label="触发阈值">
              <NumInput value={vals.alert_threshold} onChange={v => set("alert_threshold", v)} min={10} max={9999} unit="%" />
            </Field>
            <Field label="持续时长">
              <NumInput value={vals.alert_sustain} onChange={v => set("alert_sustain", v)} min={1} max={60} unit="分钟" />
            </Field>
            <Field label="冷却时间">
              <NumInput value={vals.alert_cooldown} onChange={v => set("alert_cooldown", v)} min={1} max={1440} unit="分钟" />
            </Field>
          </div>
        </Section>

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
