import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import SheetSelect from "../../components/SheetSelect";
import {
  getSettings, patchSettings, getHealth, changePassword,
  testSetting, autodetectSettings,
  type HubSettings, type HealthResp, type TestResult,
} from "../../api/settings";
import { installAgentStreamUrl } from "../../api/setup";
import { lockBodyScroll } from "../../lib/scrollLock";

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
    catch (e: any) { setResult({ ok: false, detail: e?.response?.data?.detail || e?.message || "请求失败" }); }
    finally { setBusy(false); setTimeout(() => setResult(null), 12000); }
  };
  return (
    <div className="hs-test-row">
      <button className="hs-test-btn" onClick={run} disabled={busy} type="button">
        {busy ? "测试中…" : `🔌 ${label}`}
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
    } catch (e: any) { setErr(e?.response?.data?.detail || e?.message || "检测失败"); }
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

  type InstallableComponent = "hermes" | "gbrain" | "ollama" | "codex" | "claude" | "all";

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
    { label: "AI · 文本链可用",           key: "ai_chain", nested: "text" },
    { label: "AI · 全局兜底大模型",       key: "ai_chain", nested: "global_fallback" },
    { label: "AI · 视觉识别",             key: "ai_chain", nested: "vision" },
    { label: "Apimart · 图片 / AI 服务", key: "apimart" },
    { label: "Sorftime · 市场数据",       key: "sorftime" },
    { label: "GBrain · 知识库 CLI",       key: "gbrain_bin", install: "gbrain" },
    { label: "Ollama · 本地 Embedding",   key: "ollama", install: "ollama" },
    { label: "Agent · hermes",            key: "runners", nested: "hermes", install: "hermes" },
    { label: "Agent · codex",             key: "runners", nested: "codex", install: "codex" },
    { label: "Agent · claude",            key: "runners", nested: "claude", install: "claude" },
    { label: "Agent · kiro",              key: "runners", nested: "kiro" },
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
      setStatus("error"); setMsg(e?.response?.data?.detail || "修改失败");
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

// ── Main page ─────────────────────────────────────────────────────────────────

const EMPTY: HubSettings = {
  hermes_provider: "", hermes_model: "", hermes_api_key: "", hermes_base_url: "",
  hermes_fallback_provider: "", hermes_fallback_model: "",
  hermes_fallback_api_key: "", hermes_fallback_base_url: "",
  assistant_provider: "", assistant_model: "", assistant_api_key: "", assistant_base_url: "",
  image_model: "", image_api_key: "", image_base_url: "",
  gbrain_embed_provider: "", gbrain_embed_model: "", gbrain_embed_api_key: "",
  apimart_key: "", apimart_base: "https://api.apimart.ai/v1",
  text_ai_providers: "hermes,assistant,codex,claude",
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
};

export default function HubSettings() {
  const [vals, setVals] = useState<HubSettings>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState("");

  useEffect(() => {
    getSettings()
      .then(r => { setVals({ ...EMPTY, ...r.settings }); setLoading(false); })
      .catch(e => { setLoadErr(String(e?.response?.data?.detail || e?.message || "加载失败")); setLoading(false); });
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

  const [gbrainPathsOpen, setGbrainPathsOpen] = useState(false);

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
          <div className="hs-header-sub">填写下方密钥后点保存即可直接使用，大多数场景只需填「数据源」一栏。</div>
        </div>
      </div>

      <AutodetectPanel onApply={applySuggestions} />
      <HealthPanel />

      {/* -- 区块 1: 数据源 -- */}
      <Section
        title="数据源"
        desc={<>填 key 保存后<strong>自动配置到 Hermes MCP</strong>，无需手动操作。有哪个用哪个，都填则按情况择优调用。</>}
        keys={["sorftime_key", "sif_key", "sellersprite_key", "apimart_key", "apimart_base"]}
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
          hint={<>竞品关键词分析。保存后自动注册 Hermes MCP，hermes 对话中即可调用。登录 sellersprite.com → 账户 → API Key。</>}
        >
          <SecretInput value={vals.sellersprite_key} onChange={v => set("sellersprite_key", v)} placeholder="你的卖家精灵 Secret Key" />
          <TestButton settingKey="sellersprite_key" value={vals.sellersprite_key} label="测试" />
        </Field>

        <Field
          label={<><Tag kind="opt">可选</Tag>Apimart API Key</>}
          hint={<>仅 Listing 图片生成需要。登录 apimart.ai → 控制台 → API Keys。</>}
        >
          <SecretInput value={vals.apimart_key} onChange={v => set("apimart_key", v)} placeholder="sk-..." />
          <TestButton settingKey="apimart_key" value={vals.apimart_key} label="测试" />
        </Field>
      </Section>

      {/* -- 区块 2: 大模型 -- */}
      <Section
        title="大模型"
        desc="配置 Hermes 使用的主模型和 fallback 模型。保存后立即生效，下次调用 hermes 时自动使用新配置。"
        keys={[
          "hermes_provider", "hermes_model", "hermes_api_key", "hermes_base_url",
          "hermes_fallback_provider", "hermes_fallback_model",
          "hermes_fallback_api_key", "hermes_fallback_base_url",
        ]}
        vals={vals} onSave={save}
      >
        <LLMModelBlock
          title="主模型"
          providerKey="hermes_provider" modelKey="hermes_model"
          apiKeyKey="hermes_api_key" baseUrlKey="hermes_base_url"
          vals={vals} set={set}
        />
        <div style={{ borderTop: "1px solid var(--b)", margin: "12px 0" }} />
        <LLMModelBlock
          title="Fallback 模型（可选）"
          hint="主模型限流或报错时自动切换到这里"
          providerKey="hermes_fallback_provider" modelKey="hermes_fallback_model"
          apiKeyKey="hermes_fallback_api_key" baseUrlKey="hermes_fallback_base_url"
          vals={vals} set={set}
        />
      </Section>

      {/* -- 应用模型：AI 问答 / AI 生图（直连大模型，不走智能体）-- */}
      <Section
        title="应用模型"
        dataTour="settings-fallback"
        desc={<>「全局兜底大模型」是所有板块的<strong>统一降级出口</strong>：当 Hermes/Codex/Claude 都不可用时各板块都会调它，同时它也是「AI 问答」用的模型。「AI 生图」直接调生图 API。均<strong>不经过智能体</strong>；留空则回退默认链路（DeepSeek→Apimart）。</>}
        keys={[
          "assistant_provider", "assistant_model", "assistant_api_key", "assistant_base_url",
          "image_model", "image_api_key", "image_base_url",
        ]}
        vals={vals} onSave={save}
      >
        <LLMModelBlock
          title="全局兜底大模型（兼 AI 问答）"
          hint="所有板块的智能体（Hermes/Codex/Claude）都不可用时，统一降级调用这个模型；同时也是「AI 问答」使用的模型。建议配一个稳定的文本大模型。"
          providerKey="assistant_provider" modelKey="assistant_model"
          apiKeyKey="assistant_api_key" baseUrlKey="assistant_base_url"
          vals={vals} set={set}
        />
        <div style={{ borderTop: "1px solid var(--b)", margin: "12px 0" }} />
        <div style={{ fontSize: 11, color: "var(--t2)", fontWeight: 600, marginBottom: 10 }}>
          AI 生图
          <span style={{ fontWeight: 400, color: "var(--t3)", marginLeft: 8 }}>留空用默认 Apimart gpt-image-2</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, alignItems: "end" }}>
          <Field label="模型名称" hint="默认 gpt-image-2">
            <TxtInput value={vals.image_model} onChange={v => set("image_model", v)} placeholder="gpt-image-2" />
          </Field>
          <Field label="API Key" hint="留空复用 Apimart key">
            <SecretInput value={vals.image_api_key} onChange={v => set("image_api_key", v)} placeholder="留空 = 复用 Apimart key" />
          </Field>
          <Field label="Base URL" hint="留空复用 Apimart 地址">
            <TxtInput value={vals.image_base_url} onChange={v => set("image_base_url", v)} placeholder="留空 = 复用 Apimart 地址" />
          </Field>
        </div>
      </Section>

      {/* -- 区块 3: 智能体 -- */}
      <Section
        title="智能体"
        desc={<>Hermes、Claude、GBrain 均从系统 PATH <strong>自动发现</strong>，绿色即代表可用，无需手动配置路径。</>}
        keys={["hermes_bin", "codex_bin", "claude_bin", "text_ai_providers", "autofix_enabled",
          "vision_ai_providers", "openai_api_key", "deepseek_api_key",
          "gbrain_bin", "brain_root",
          "gbrain_embed_provider", "gbrain_embed_model", "gbrain_embed_api_key"]}
        vals={vals} onSave={save}
      >
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          {(["hermes", "codex", "claude"] as const).map(name => {
            const key = `${name}_bin` as keyof HubSettings;
            const val = (vals[key] as string) || "";
            return (
              <TestButton key={name} settingKey={key} value={val}
                label={`${name === "hermes" ? "Hermes" : name === "codex" ? "Codex" : "Claude"} 检测`} />
            );
          })}
          <TestButton settingKey="kiro_cli_bin" value={(vals.kiro_cli_bin as string) || ""} label="Kiro 检测" />
        </div>

        <button
          type="button"
          onClick={() => setGbrainPathsOpen(o => !o)}
          style={{
            display: "flex", alignItems: "center", gap: 6, marginBottom: 4,
            background: "transparent", border: "1px solid var(--b)", borderRadius: 4,
            padding: "5px 12px", color: "var(--t3)", fontSize: 11,
            cursor: "pointer", fontFamily: "var(--font)",
          }}
        >
          <span style={{ display: "inline-block", transition: "transform .15s", transform: gbrainPathsOpen ? "rotate(90deg)" : "none" }}>▶</span>
          手动指定路径（自动发现失败时才需要）
        </button>

        {gbrainPathsOpen && (
          <div style={{ paddingLeft: 10, borderLeft: "2px solid var(--b)" }}>
            <Field label={<><Tag kind="opt">可选</Tag>AI 提供商顺序（全局降级链）</>}
              hint={<>逗号分隔，按顺序尝试：<code>hermes</code> <code>assistant</code>（全局兜底大模型）<code>codex</code> <code>claude</code> <code>apimart</code> <code>deepseek</code>。所有板块的文本 AI 都走这条链。</>}>
              <TxtInput value={vals.text_ai_providers} onChange={v => set("text_ai_providers", v)} placeholder="hermes,assistant,codex,claude" />
            </Field>
            <Field label={<><Tag kind="opt">可选</Tag>视觉识别顺序（图片分析）</>}
              hint={<>逗号分隔，按顺序尝试：<code>openai</code> <code>assistant</code>（支持视觉的全局兜底）。Listing「AI 图片分析」走这条链。apimart 只生图、无视觉，不在此列。</>}>
              <TxtInput value={vals.vision_ai_providers} onChange={v => set("vision_ai_providers", v)} placeholder="openai,assistant" />
            </Field>
            <Field label={<><Tag kind="opt">可选</Tag>OpenAI API Key（视觉识别）</>}
              hint={<>用于「AI 图片分析」的视觉模型（GPT‑4o 系）。登录 platform.openai.com → API Keys。</>}>
              <SecretInput value={vals.openai_api_key} onChange={v => set("openai_api_key", v)} placeholder="sk-..." />
            </Field>
            <Field label={<><Tag kind="opt">可选 · 进阶</Tag>DeepSeek 专用 Key</>}
              hint={<>仅当把 <code>deepseek</code> 单独加进上面「AI 提供商顺序」时才需要；多数人用上面的「全局兜底大模型」选 DeepSeek 即可，无需在此填。</>}>
              <SecretInput value={vals.deepseek_api_key} onChange={v => set("deepseek_api_key", v)} placeholder="sk-..." />
            </Field>
            <Field label={<><Tag kind="opt">可选</Tag>Hermes 路径</>} hint="留空 = PATH 自动发现">
              <TxtInput value={vals.hermes_bin} onChange={v => set("hermes_bin", v)} placeholder="留空 = PATH 自动发现" />
            </Field>
            <Field label={<><Tag kind="opt">可选</Tag>Claude 路径</>} hint={<>留空 = PATH 自动发现。<code>npm i -g @anthropic-ai/claude-code</code></>}>
              <TxtInput value={vals.claude_bin} onChange={v => set("claude_bin", v)} placeholder="留空 = PATH 自动发现" />
            </Field>
            <Field label={<><Tag kind="opt">可选</Tag>GBrain 路径</>} hint={<>留空 = PATH 自动发现（通常 <code>~/.bun/bin/gbrain</code>）。</>}>
              <TxtInput value={vals.gbrain_bin} onChange={v => set("gbrain_bin", v)} placeholder="留空 = PATH 自动发现" />
            </Field>
            <Field label={<><Tag kind="opt">可选</Tag>知识库根目录</>} hint={<>GBrain 笔记目录，留空 = <code>~/brain</code>。</>}>
              <TxtInput value={vals.brain_root} onChange={v => set("brain_root", v)} placeholder="~/brain" />
            </Field>
            <div style={{ gridColumn: "1 / -1", borderTop: "1px solid var(--b)", margin: "4px 0 2px", paddingTop: 10 }}>
              <div style={{ fontSize: 11, color: "var(--t2)", fontWeight: 600, marginBottom: 2 }}>
                知识库语义检索（Embedding）
              </div>
              <div style={{ fontSize: 10, color: "var(--t3)", marginBottom: 8 }}>
                配置后知识库支持语义检索；留空则仅关键词检索（不影响对话）。注意：deepseek 等纯对话模型不提供 embedding。
              </div>
            </div>
            <Field label={<><Tag kind="opt">可选</Tag>Embedding 服务商</>}
              hint={<>支持 embedding 的服务商。<code>ollama</code> 本地免费（需先 pull 模型），其余需对应 API Key。</>}>
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
              <Field label="Embedding API Key" hint="保存后写入 Hermes 环境，GBrain 自动读取">
                <SecretInput value={vals.gbrain_embed_api_key} onChange={v => set("gbrain_embed_api_key", v)} placeholder="对应服务商的 API Key" />
              </Field>
            )}
          </div>
        )}

        <Field label={<><Tag kind="opt">功能</Tag>自动修复 Bug</>}
          hint={<>开启后，功能报错时弹窗询问是否 AI 修复（hermes 在隔离副本中排查，你审核 diff 后应用）。默认关闭。</>}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--t2)", cursor: "pointer" }}>
            <input type="checkbox" checked={vals.autofix_enabled}
              onChange={e => set("autofix_enabled", e.target.checked)} />
            {vals.autofix_enabled ? "已开启" : "已关闭"}
          </label>
        </Field>
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
            "apimart_base", "kiro_cli_bin", "kiro_gateway_db", "kiro_cli_db", "kiro_cli_sessions_dir",
            "feishu_codex_db", "hermes_node_bin", "bun_bin", "news_feeds"]}
          vals={vals} onSave={save}
        >
          <div className="hs-field-group-title">资讯 RSS 源</div>
          <Field label={<><Tag kind="opt">可选</Tag>资讯 RSS 源</>}
            hint={<>「资讯」板块的抓取源，每行一条 <code>url | 来源名 | 分类</code>（分类 = <code>ai_industry</code> 或 <code>amazon_seller</code>）。留空 = 用内置默认源。</>}>
            <AreaInput value={vals.news_feeds} onChange={v => set("news_feeds", v)} rows={4}
              placeholder={"https://example.com/feed.xml | 来源名 | ai_industry\nhttps://.../rss | 卖家资讯 | amazon_seller"} />
          </Field>
          <div className="hs-field-group-title">图片生成 · 嵌入服务</div>
          <Field label={<><Tag kind="opt">可选</Tag>Imgflow 地址</>} hint={<>Listing 图片处理后端，默认 <code>http://127.0.0.1:3001</code>。</>}>
            <TxtInput value={vals.imgflow_url} onChange={v => set("imgflow_url", v)} placeholder="http://127.0.0.1:3001" />
            <TestButton settingKey="imgflow_url" value={vals.imgflow_url} label="测试" />
          </Field>
          <Field label={<><Tag kind="opt">可选</Tag>Apimart 地址</>} hint="非官方网关才需改，否则保持默认。">
            <TxtInput value={vals.apimart_base} onChange={v => set("apimart_base", v)} placeholder="https://api.apimart.ai/v1" />
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
  );
}
