import { useEffect, useState } from "react";
import {
  listDataSources, saveDataSource, deleteDataSource, probeDataSource, testDataSource,
  type CustomDataSource, type CapabilitySpec, type ProbeResult, type TestResult,
} from "../../../api/dataSources";
import { loadCustomDataSources } from "../../../lib/dataSource";

// 自定义 MCP 数据源的配置界面。
//
// 配一个源就是三步，界面顺序刻意和这三步一致：
//   ① 连上（端点 + 鉴权 → 探测出对方有哪些工具）
//   ② 翻译（把对方的工具和字段映射成工作台内部那套字段名）
//   ③ 验证（真调一次，看翻译出来的结果对不对）
// 第②步是全部工作量所在，所以每个能力都直接把「工作台要什么字段」列出来让人
// 对着填，而不是丢一个空的 JSON 编辑器让人猜。

type CapKind = "record" | "rows" | "series" | "pipeline";

const CAPS: {
  id: string; label: string; kind: CapKind; hint: string; testHint: string;
  fields?: string[]; rowFields?: string[]; summaryFields?: string[];
}[] = [
  {
    id: "keyword_pipeline", kind: "pipeline", label: "关键词采集（市场调研 / 打法推荐）",
    hint: "按顺序调用若干工具，结果整包交给 AI 生成报告。不需要字段映射 —— AI 直接读原始 JSON。",
    testHint: "填一个关键词，如 ipad case",
  },
  {
    id: "asin_pipeline", kind: "pipeline", label: "ASIN 采集（市场调研 / 打法推荐）",
    hint: "同上，输入是 ASIN。", testHint: "填一个 ASIN",
  },
  {
    id: "home_asin_pulse", kind: "record", label: "ASIN 监控卡片（首页）",
    hint: "首页竞品/自营监控的每张卡片。", testHint: "填一个 ASIN",
    fields: ["title", "brand", "image", "price", "bsr", "bsr_category", "sub_rank",
             "sub_category", "est_sales", "rating", "review_count", "variations",
             "coupon", "deal", "inventory"],
  },
  {
    id: "home_keyword_pulse", kind: "record", label: "关键词监控卡片（首页）",
    hint: "关键词的搜索量 / 竞价 / 竞争度。趋势线走下面的「关键词趋势」能力。",
    testHint: "填一个关键词",
    fields: ["monthly_search_volume", "recommended_cpc_bid", "purchase_rate", "competition_index"],
  },
  {
    id: "home_keyword_extends", kind: "rows", label: "拓展词（首页）",
    hint: "相关词列表，用于机会词打分。", testHint: "填一个关键词",
    rowFields: ["keyword", "monthly_search", "cpc", "seasonality", "evidence_sales"],
  },
  {
    id: "home_keyword_purchase_evidence", kind: "record", label: "关键词购买佐证（首页）",
    hint: "拓展词的月购买量，用来给机会词补一条真实成交佐证。只需映射一个 value。",
    testHint: "填一个关键词", fields: ["value"],
  },
  {
    id: "home_category", kind: "rows", label: "类目大盘（首页）",
    hint: "类目 Top 商品列表；价格带和汇总由工作台按这些商品自己算。",
    testHint: "填类目词 / nodeId / ASIN",
    rowFields: ["asin", "title", "brand", "image", "price", "bsr", "est_sales", "rating", "review_count"],
    summaryFields: ["category_name", "node_id", "avg_price", "total_sales"],
  },
  {
    id: "home_market_metrics", kind: "record", label: "大盘指标（首页趋势记录）",
    hint: "每日记录一次的搜索量 / 销量 / 均价，是首页趋势图的数据来源。",
    testHint: "填一个关键词",
    fields: ["search_volume", "total_sales", "avg_price", "node_id", "node_id_path", "category_name"],
  },
  {
    id: "home_keyword_trend_series", kind: "series", label: "关键词趋势",
    hint: "时间序列。day 支持 2024-05-07 / 202405 / 2024年05月，只到月份会补成 1 号。",
    testHint: "填一个关键词", rowFields: ["day", "value"],
  },
  {
    id: "home_product_trend_series", kind: "series", label: "ASIN 销量趋势",
    hint: "时间序列，同上。", testHint: "填一个 ASIN", rowFields: ["day", "value"],
  },
];

const SURFACES: { id: string; label: string; requires: string }[] = [
  { id: "home", label: "首页驾驶舱", requires: "home_asin_pulse" },
  { id: "market", label: "市场调研", requires: "keyword_pipeline" },
  { id: "playbook", label: "打法推荐", requires: "keyword_pipeline" },
];

const PLACEHOLDERS = "{keyword} {query} {asin} {marketplace} {month} {month_dash} {today} {top_n}";

function blank(): CustomDataSource {
  return {
    id: "", name: "", enabled: true, transport: "http", url: "",
    auth: { mode: "query", name: "key", value: "" },
    headers: {}, handshake: true, envelope: "", timeout: 40,
    surfaces: [], capabilities: {}, note: "",
  };
}

function jsonText(value: unknown): string {
  if (value === undefined || value === null) return "";
  try { return JSON.stringify(value, null, 2); } catch { return ""; }
}

export default function CustomDataSources() {
  const [sources, setSources] = useState<CustomDataSource[]>([]);
  const [draft, setDraft] = useState<CustomDataSource | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    try {
      const body = await listDataSources();
      setSources(body.sources || []);
      await loadCustomDataSources(true);   // 让各板块的数据源下拉立刻跟上
    } catch {
      setError("读取自定义数据源失败");
    }
  };

  useEffect(() => { void refresh(); }, []);

  const remove = async (id: string) => {
    if (!window.confirm(`删除数据源「${id}」？已经用它记录的历史数据不会被删除，但会失去来源。`)) return;
    setBusy(true);
    try { await deleteDataSource(id); await refresh(); if (draft?.id === id) setDraft(null); }
    finally { setBusy(false); }
  };

  return (
    <div className="hs-section cds">
      <div className="hs-section-hd">
        <div>
          <div className="hs-section-title">自定义数据源</div>
          <div className="hs-section-desc">
            把任意 MCP 数据源接进首页 / 市场调研 / 打法推荐。填一次「工具名 + 字段映射」，
            它就会出现在各板块的数据源下拉里，和内置的三家并列。
            内置的 Sorftime / SIF / 卖家精灵不受这里影响。
          </div>
        </div>
        <button className="hs-save-btn" disabled={busy}
          onClick={() => setDraft(draft ? null : blank())}>
          {draft ? "收起" : "+ 添加数据源"}
        </button>
      </div>

      <div className="hs-fields">
        {error && <div className="cds-err">{error}</div>}

        {sources.length === 0 && !draft && (
          <div className="cds-empty">还没有自定义数据源。点右上角「+ 添加数据源」开始。</div>
        )}

        {sources.map((s) => (
          <div key={s.id} className="cds-row">
            <div className="cds-row-main">
              <div className="cds-row-name">
                {s.name}
                <code className="cds-id">custom:{s.id}</code>
                {!s.enabled && <span className="cds-badge cds-badge-off">已停用</span>}
              </div>
              <div className="cds-row-sub">
                {s.url}
                {" · "}
                {s.surfaces.length
                  ? s.surfaces.map((x) => SURFACES.find((y) => y.id === x)?.label || x).join(" / ")
                  : "未启用任何板块"}
                {" · "}
                {Object.keys(s.capabilities || {}).length} 项能力
              </div>
            </div>
            <button className="cds-btn" onClick={() => setDraft({ ...s })}>编辑</button>
            <button className="cds-btn cds-btn-danger" disabled={busy}
              onClick={() => void remove(s.id)}>删除</button>
          </div>
        ))}

        {draft && (
          <Editor
            key={draft.id || "__new__"}
            value={draft}
            onChange={setDraft}
            onSaved={async () => { await refresh(); setDraft(null); }}
            onCancel={() => setDraft(null)}
          />
        )}
      </div>
    </div>
  );
}

function Editor({ value, onChange, onSaved, onCancel }: {
  value: CustomDataSource;
  onChange: (v: CustomDataSource) => void;
  onSaved: () => Promise<void>;
  onCancel: () => void;
}) {
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const set = <K extends keyof CustomDataSource>(key: K, v: CustomDataSource[K]) =>
    onChange({ ...value, [key]: v });

  const setCap = (id: string, spec: CapabilitySpec | null) => {
    const next = { ...(value.capabilities || {}) };
    if (spec === null) delete next[id]; else next[id] = spec;
    onChange({ ...value, capabilities: next });
  };

  const runProbe = async () => {
    setErr(""); setProbe(null);
    try { setProbe(await probeDataSource(value)); }
    catch (e: unknown) { setErr(detail(e) || "探测失败"); }
  };

  const save = async () => {
    setErr(""); setSaving(true);
    try { await saveDataSource(value); await onSaved(); }
    catch (e: unknown) { setErr(detail(e) || "保存失败"); }
    finally { setSaving(false); }
  };

  const toolNames = (probe?.tools || []).map((t) => t.name);

  return (
    <div className="cds-editor">
      {/* ① 连接 */}
      <div className="cds-step"><span className="cds-step-t">① 连接</span></div>
      <div className="cds-grid">
        <label className="cds-f">
          <span>显示名称</span>
          <input className="hs-input" value={value.name} placeholder="例如：我的 ERP"
            onChange={(e) => set("name", e.target.value)} />
        </label>
        <label className="cds-f">
          <span>标识 id</span>
          <input className="hs-input" value={value.id} placeholder="myerp（小写字母/数字/-/_）"
            onChange={(e) => set("id", e.target.value)} />
        </label>
        <label className="cds-f cds-f-wide">
          <span>MCP 端点</span>
          <input className="hs-input" value={value.url} spellCheck={false}
            placeholder="https://mcp.example.com/mcp"
            onChange={(e) => set("url", e.target.value)} />
        </label>
        <label className="cds-f">
          <span>鉴权方式</span>
          <select className="hs-input" value={value.auth.mode}
            onChange={(e) => set("auth", { ...value.auth, mode: e.target.value as never })}>
            <option value="query">URL 参数（?key=…）</option>
            <option value="header">自定义 Header</option>
            <option value="bearer">Authorization: Bearer</option>
            <option value="none">不需要鉴权</option>
          </select>
        </label>
        {(value.auth.mode === "query" || value.auth.mode === "header") && (
          <label className="cds-f">
            <span>{value.auth.mode === "query" ? "参数名" : "Header 名"}</span>
            <input className="hs-input" value={value.auth.name}
              placeholder={value.auth.mode === "query" ? "key" : "X-API-Key"}
              onChange={(e) => set("auth", { ...value.auth, name: e.target.value })} />
          </label>
        )}
        {value.auth.mode !== "none" && (
          <label className="cds-f">
            <span>密钥{value.auth.value_set && !value.auth.value ? "（已保存，留空即不改）" : ""}</span>
            <input className="hs-input" type="password" autoComplete="new-password"
              value={value.auth.value} placeholder={value.auth.value_set ? "••••••" : "粘贴密钥"}
              onChange={(e) => set("auth", { ...value.auth, value: e.target.value })} />
          </label>
        )}
        <label className="cds-f">
          <span>数据信封路径<i>（可选）</i></span>
          <input className="hs-input" value={value.envelope} placeholder="留空自动识别，如 data"
            onChange={(e) => set("envelope", e.target.value)} />
        </label>
        <label className="cds-f cds-f-check">
          <input type="checkbox" checked={value.handshake}
            onChange={(e) => set("handshake", e.target.checked)} />
          <span>调用前先发 initialize 握手（多数服务器需要，个别不需要）</span>
        </label>
        <label className="cds-f cds-f-check">
          <input type="checkbox" checked={value.enabled}
            onChange={(e) => set("enabled", e.target.checked)} />
          <span>启用（停用后各板块下拉里不再出现）</span>
        </label>
      </div>

      <div className="cds-actions">
        <button className="cds-btn" onClick={() => void runProbe()}>探测可用工具</button>
        {probe && !probe.ok && <span className="cds-err">{probe.error}</span>}
        {probe && probe.ok && (
          <span className="cds-ok">读到 {probe.count} 个工具</span>
        )}
      </div>
      {probe?.ok && (
        <div className="cds-note">{probe.note}</div>
      )}
      {probe?.ok && (
        <div className="cds-tools">
          {probe.tools.map((t) => (
            <div key={t.name} className="cds-tool">
              <code>{t.name}</code>
              <span className="cds-tool-params">
                {t.params.length ? t.params.join(", ") : "无参数"}
              </span>
              {t.description && <div className="cds-tool-desc">{t.description}</div>}
            </div>
          ))}
        </div>
      )}

      {/* ② 板块 */}
      <div className="cds-step"><span className="cds-step-t">② 在哪些板块出现</span></div>
      <div className="cds-surfaces">
        {SURFACES.map((s) => {
          const on = value.surfaces.includes(s.id);
          const ok = !!value.capabilities?.[s.requires];
          return (
            <label key={s.id} className={"cds-surface" + (on ? " on" : "")}>
              <input type="checkbox" checked={on}
                onChange={(e) => set("surfaces",
                  e.target.checked ? [...value.surfaces, s.id] : value.surfaces.filter((x) => x !== s.id))} />
              <span>{s.label}</span>
              {!ok && <em>需先配置「{CAPS.find((c) => c.id === s.requires)?.label}」</em>}
            </label>
          );
        })}
      </div>

      {/* ③ 能力映射 */}
      <div className="cds-step"><span className="cds-step-t">③ 能力映射</span><i>占位符：{PLACEHOLDERS}</i></div>
      {CAPS.map((cap) => (
        <CapabilityBlock
          key={cap.id} cap={cap} tools={toolNames}
          spec={value.capabilities?.[cap.id]}
          onChange={(spec) => setCap(cap.id, spec)}
          source={value}
        />
      ))}

      <div className="cds-actions cds-actions-end">
        {err && <span className="cds-err">{err}</span>}
        <button className="cds-btn" onClick={onCancel}>取消</button>
        <button className="hs-save-btn" disabled={saving} onClick={() => void save()}>
          {saving ? "保存中…" : "保存数据源"}
        </button>
      </div>
    </div>
  );
}

function CapabilityBlock({ cap, spec, tools, onChange, source }: {
  cap: (typeof CAPS)[number];
  spec?: CapabilitySpec;
  tools: string[];
  onChange: (spec: CapabilitySpec | null) => void;
  source: CustomDataSource;
}) {
  const on = !!spec;
  const [argsText, setArgsText] = useState(() => jsonText(spec?.args) || "{}");
  const [argsBad, setArgsBad] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [testQuery, setTestQuery] = useState("");
  const [testing, setTesting] = useState(false);

  const patch = (over: Partial<CapabilitySpec>) => onChange({ ...(spec || {}), ...over });

  const commitArgs = (text: string) => {
    setArgsText(text);
    try { patch({ args: JSON.parse(text || "{}") }); setArgsBad(false); }
    catch { setArgsBad(true); }        // 保留输入不回滚，只标红 —— 边打字边解析必然中途非法
  };

  const mapEditor = (
    label: string,
    key: "fields" | "row_fields" | "summary_fields",
    names: string[],
  ) => (
    <div className="cds-map">
      <div className="cds-map-hd">{label}</div>
      <div className="cds-map-grid">
        {names.map((name) => (
          <label key={name} className="cds-map-row">
            <code>{name}</code>
            <input className="hs-input" spellCheck={false}
              placeholder="如 price 或 a||b"
              value={(spec?.[key] as Record<string, string> | undefined)?.[name] || ""}
              onChange={(e) => patch({
                [key]: { ...(spec?.[key] || {}), [name]: e.target.value },
              } as Partial<CapabilitySpec>)} />
          </label>
        ))}
      </div>
    </div>
  );

  const runTest = async () => {
    setTesting(true); setTest(null);
    try { setTest(await testDataSource(source, cap.id, testQuery, "US")); }
    catch (e: unknown) { setTest({ ok: false, errors: [detail(e) || "试跑失败"], result: null }); }
    finally { setTesting(false); }
  };

  return (
    <div className={"cds-cap" + (on ? " on" : "")}>
      <label className="cds-cap-hd">
        <input type="checkbox" checked={on}
          onChange={(e) => onChange(e.target.checked
            ? { tool: "", args: {}, ...(cap.kind === "pipeline" ? { steps: [{ label: "", tool: "", args: {} }] } : {}) }
            : null)} />
        <span className="cds-cap-name">{cap.label}</span>
        <span className="cds-cap-hint">{cap.hint}</span>
      </label>

      {on && cap.kind === "pipeline" && (
        <StepsEditor steps={spec?.steps || []} tools={tools}
          onChange={(steps) => patch({ steps })} />
      )}

      {on && cap.kind !== "pipeline" && (
        <div className="cds-cap-body">
          <div className="cds-grid">
            <label className="cds-f">
              <span>调用工具</span>
              <input className="hs-input" list={`cds-tools-${cap.id}`} value={spec?.tool || ""}
                placeholder="工具名（先探测可自动补全）"
                onChange={(e) => patch({ tool: e.target.value })} />
              <datalist id={`cds-tools-${cap.id}`}>
                {tools.map((t) => <option key={t} value={t} />)}
              </datalist>
            </label>
            <label className="cds-f">
              <span>{cap.kind === "record" ? "记录路径" : "列表路径"}<i>（可选）</i></span>
              <input className="hs-input"
                placeholder={cap.kind === "record" ? "留空自动，如 data" : "留空自动，如 data.top100_products"}
                value={(cap.kind === "record" ? spec?.record : spec?.rows) || ""}
                onChange={(e) => patch(cap.kind === "record"
                  ? { record: e.target.value } : { rows: e.target.value })} />
            </label>
          </div>
          <label className="cds-f cds-f-wide">
            <span>入参模板（JSON）{argsBad && <em className="cds-err">JSON 格式不对</em>}</span>
            <textarea className={"hs-input cds-json" + (argsBad ? " bad" : "")} rows={4}
              spellCheck={false} value={argsText}
              onChange={(e) => commitArgs(e.target.value)} />
          </label>

          {cap.fields && mapEditor("字段映射", "fields", cap.fields)}
          {cap.rowFields && mapEditor(
            cap.kind === "series" ? "每个数据点的字段" : "每行的字段", "row_fields", cap.rowFields)}
          {cap.summaryFields && mapEditor("汇总字段（可选）", "summary_fields", cap.summaryFields)}

          <div className="cds-actions">
            <input className="hs-input cds-test-input" value={testQuery}
              placeholder={cap.testHint} onChange={(e) => setTestQuery(e.target.value)} />
            <button className="cds-btn" disabled={testing || !testQuery.trim()}
              onClick={() => void runTest()}>{testing ? "试跑中…" : "试跑"}</button>
            {test && (
              <span className={test.ok ? "cds-ok" : "cds-err"}>
                {test.ok
                  ? `通过${test.filled_fields ? `（映射出 ${test.filled_fields} 个字段）` : ""}`
                  : test.errors.join("；") || "没拿到数据"}
              </span>
            )}
          </div>
          {test?.result != null && (
            <pre className="cds-result">{jsonText(test.result)}</pre>
          )}
        </div>
      )}
    </div>
  );
}

function StepsEditor({ steps, tools, onChange }: {
  steps: { label: string; tool: string; args?: Record<string, unknown> }[];
  tools: string[];
  onChange: (steps: { label: string; tool: string; args?: Record<string, unknown> }[]) => void;
}) {
  const patch = (i: number, over: Partial<{ label: string; tool: string; args: Record<string, unknown> }>) =>
    onChange(steps.map((s, idx) => (idx === i ? { ...s, ...over } : s)));

  return (
    <div className="cds-cap-body">
      {steps.map((step, i) => (
        <div key={i} className="cds-step-row">
          <input className="hs-input" placeholder="这一步叫什么（会显示在采集进度里）"
            value={step.label} onChange={(e) => patch(i, { label: e.target.value })} />
          <input className="hs-input" list="cds-tools-pipeline" placeholder="工具名"
            value={step.tool} onChange={(e) => patch(i, { tool: e.target.value })} />
          <textarea className="hs-input cds-json" rows={2} spellCheck={false}
            placeholder='入参 JSON，如 {"keyword": "{keyword}"}'
            defaultValue={jsonText(step.args) || "{}"}
            onBlur={(e) => {
              try { patch(i, { args: JSON.parse(e.target.value || "{}") }); } catch { /* 保留原值 */ }
            }} />
          <button className="cds-btn cds-btn-danger"
            onClick={() => onChange(steps.filter((_, idx) => idx !== i))}>移除</button>
        </div>
      ))}
      <datalist id="cds-tools-pipeline">
        {tools.map((t) => <option key={t} value={t} />)}
      </datalist>
      <button className="cds-btn"
        onClick={() => onChange([...steps, { label: "", tool: "", args: {} }])}>+ 加一步</button>
    </div>
  );
}

function detail(e: unknown): string {
  const anyE = e as { response?: { data?: { detail?: string } }; message?: string };
  return anyE?.response?.data?.detail || anyE?.message || "";
}
