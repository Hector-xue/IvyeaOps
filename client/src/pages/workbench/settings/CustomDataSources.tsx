import { useEffect, useState } from "react";
import {
  listDataSources, saveDataSource, deleteDataSource, probeDataSource, testDataSource,
  autoconfigDataSource, remapCapability,
  type CustomDataSource, type CapabilitySpec, type ProbeResult, type TestResult,
  type AutoReport, type CapabilityOutcome,
} from "../../../api/dataSources";
import { loadCustomDataSources } from "../../../lib/dataSource";
import { errText } from "../../../lib/errText";

// 自定义 MCP 数据源的配置界面。
//
// 默认动线只有两步：**填端点和密钥 → 点「自动配置」**。
//
// 默认视图里没有的东西，都是因为它们本来就不该问：
//   · 鉴权方式 / 参数名 —— 挨个试一遍就知道了（custom_source_mcp.detect_auth）
//   · 在哪些板块出现 —— 配了什么能力就在什么板块出现，是结果不是输入
//   · 名称 / 标识 id —— 从端点域名自动填，改不改随意
//   · 工具名 / 字段映射 —— 探测 + 真调一次样例推断出来
//
// 这几样上一版全在默认界面上，还因为"勾了板块却没配对应能力"的校验把「自动配置」
// 按钮本身给拦死了。手工映射整个收进「高级设置」，它是自动推断出错时的补救口，
// 不是常规路径。

type CapKind = "record" | "rows" | "series" | "pipeline";

const CAPS: {
  id: string; label: string; kind: CapKind; hint: string; testHint: string;
  fields?: string[]; rowFields?: string[]; summaryFields?: string[];
}[] = [
  {
    id: "keyword_pipeline", kind: "pipeline", label: "关键词采集（市场调研 / 打法推荐）",
    hint: "按顺序调用若干工具，结果整包交给 AI 生成报告。不需要字段映射。",
    testHint: "填一个关键词",
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
    hint: "关键词的搜索量 / 竞价 / 竞争度。", testHint: "填一个关键词",
    fields: ["monthly_search_volume", "recommended_cpc_bid", "purchase_rate", "competition_index"],
  },
  {
    id: "home_keyword_extends", kind: "rows", label: "拓展词（首页）",
    hint: "相关词列表，用于机会词打分。", testHint: "填一个关键词",
    rowFields: ["keyword", "monthly_search", "cpc", "seasonality", "evidence_sales"],
  },
  {
    id: "home_keyword_purchase_evidence", kind: "record", label: "关键词购买佐证（首页）",
    hint: "拓展词的月购买量。只需映射一个 value。",
    testHint: "填一个关键词", fields: ["value"],
  },
  {
    id: "home_category", kind: "rows", label: "类目大盘（首页）",
    hint: "类目 Top 商品列表；价格带和汇总由工作台自己算。",
    testHint: "填类目词 / nodeId / ASIN",
    rowFields: ["asin", "title", "brand", "image", "price", "bsr", "est_sales", "rating", "review_count"],
    summaryFields: ["category_name", "node_id", "avg_price", "total_sales"],
  },
  {
    id: "home_market_metrics", kind: "record", label: "大盘指标（首页趋势记录）",
    hint: "每日记录一次的搜索量 / 销量 / 均价。", testHint: "填一个关键词",
    fields: ["search_volume", "total_sales", "avg_price", "node_id", "node_id_path", "category_name"],
  },
  {
    id: "home_keyword_trend_series", kind: "series", label: "关键词趋势",
    hint: "时间序列。day 支持 2024-05-07 / 202405 / 2024年05月。",
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
    auth: { mode: "auto", name: "", value: "" },
    headers: {}, handshake: true, envelope: "", timeout: 40,
    surfaces: [], capabilities: {}, note: "",
  };
}

function jsonText(value: unknown): string {
  if (value === undefined || value === null) return "";
  try { return JSON.stringify(value, null, 2); } catch { return ""; }
}

/** 能力名去掉括号里的板块注解：「关键词采集（市场调研 / 打法推荐）」→「关键词采集」 */
function shortLabel(capId: string): string {
  const full = CAPS.find((c) => c.id === capId)?.label || capId;
  return full.replace(/（[^）]*）/g, "").trim();
}

/** 从端点域名推一个标识：https://mcp.example.com/mcp → example */
function slugFromUrl(url: string): string {
  try {
    const host = new URL(url.trim()).hostname;
    const parts = host.split(".").filter((x) => !["www", "mcp", "api", "open", "gateway"].includes(x));
    return (parts[0] || host).toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 32);
  } catch {
    return "";
  }
}

/** 后端错误归一成一句话。
 *
 *  **必须走 errText**：FastAPI 的 422 里 `detail` 是对象数组，直接塞进 JSX 会让
 *  React 整页崩成「渲染失败」，而真正的原因（哪个参数传错了）一个字都不会露出来。
 *  仓库有门禁卡这条（scripts/check-errtext.mjs）。 */
function detail(e: unknown, fallback = "操作失败"): string {
  return errText(e, fallback);
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
            接自己的 MCP 数据源：填端点和密钥，点「自动配置」，剩下的交给它自己探测。
            配好后就出现在首页 / 市场调研 / 打法推荐的数据源下拉里，和内置三家并列
            —— 内置的 Sorftime / SIF / 卖家精灵不受影响。
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
  const [report, setReport] = useState<AutoReport | null>(null);
  const [autoBusy, setAutoBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [sampleOpen, setSampleOpen] = useState(false);
  const [sampleKeyword, setSampleKeyword] = useState("wireless earbuds");
  const [sampleAsin, setSampleAsin] = useState("B08N5WRWNW");
  const [err, setErr] = useState("");
  // 名称和 id 从端点域名自动填。用户一旦自己改过就不再覆盖 —— 边打字边被改掉
  // 是最烦人的一种"智能"。
  const [nameTouched, setNameTouched] = useState(!!value.name);
  const [idTouched, setIdTouched] = useState(!!value.id);

  const set = <K extends keyof CustomDataSource>(key: K, v: CustomDataSource[K]) =>
    onChange({ ...value, [key]: v });

  const changeUrl = (url: string) => {
    const slug = slugFromUrl(url);
    onChange({
      ...value, url,
      id: idTouched ? value.id : slug,
      name: nameTouched ? value.name : slug,
    });
  };

  const setCap = (id: string, spec: CapabilitySpec | null) => {
    const next = { ...(value.capabilities || {}) };
    if (spec === null) delete next[id]; else next[id] = spec;
    onChange({ ...value, capabilities: next });
  };

  const configured = Object.keys(value.capabilities || {}).length;

  const runAuto = async () => {
    setErr(""); setReport(null); setAutoBusy(true);
    try {
      const out = await autoconfigDataSource(value, sampleKeyword, sampleAsin, "US");
      setReport(out.report);
      if (out.source) onChange({ ...out.source, auth: value.auth });
    } catch (e: unknown) {
      setErr(detail(e) || "自动配置失败");
    } finally {
      setAutoBusy(false);
    }
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
  // 只认端点。id 是自动填的，别再拿它挡住这个按钮。
  const canAuto = !!value.url.trim();

  return (
    <div className="cds-editor">
      <div className="cds-grid">
        <label className="cds-f cds-f-wide">
          <span>MCP 端点</span>
          <input className="hs-input" value={value.url} spellCheck={false}
            placeholder="https://mcp.example.com/mcp"
            onChange={(e) => changeUrl(e.target.value)} />
        </label>
        <label className="cds-f">
          <span>密钥<i>{value.auth.value_set && !value.auth.value ? "（已保存，留空不改）" : "（没有就留空）"}</i></span>
          <input className="hs-input" type="password" autoComplete="new-password"
            value={value.auth.value} placeholder={value.auth.value_set ? "••••••" : "粘贴密钥"}
            onChange={(e) => set("auth", { ...value.auth, value: e.target.value })} />
        </label>
        <label className="cds-f">
          <span>显示名称<i>（自动填，可改）</i></span>
          <input className="hs-input" value={value.name} placeholder="从端点自动填"
            onChange={(e) => { setNameTouched(true); set("name", e.target.value); }} />
        </label>
      </div>

      <div className="cds-actions">
        <button className="cds-primary" disabled={autoBusy || !canAuto}
          onClick={() => void runAuto()}>
          {autoBusy ? "正在探测并试调…" : configured ? "重新自动配置" : "自动配置"}
        </button>
        <button className="cds-link" onClick={() => setSampleOpen((o) => !o)}>
          {sampleOpen ? "收起样例" : "换个样例试"}
        </button>
        {!canAuto && <span className="cds-note">填上 MCP 端点就能点</span>}
        {err && <span className="cds-err">{err}</span>}
      </div>

      {sampleOpen && (
        <div className="cds-sample">
          <div className="cds-note">
            自动配置要拿真实数据试调一次才认得出字段。换成你自己的品类词和在售 ASIN 会更准。
          </div>
          <div className="cds-grid">
            <label className="cds-f">
              <span>样例关键词</span>
              <input className="hs-input" value={sampleKeyword}
                onChange={(e) => setSampleKeyword(e.target.value)} />
            </label>
            <label className="cds-f">
              <span>样例 ASIN</span>
              <input className="hs-input" value={sampleAsin}
                onChange={(e) => setSampleAsin(e.target.value)} />
            </label>
          </div>
        </div>
      )}

      {report && <AutoResult report={report} />}

      <div className="cds-adv-hd">
        <button className="cds-link" onClick={() => setAdvanced((a) => !a)}>
          {advanced ? "▾ 收起高级设置" : "▸ 高级设置（自动配置认错了才需要动）"}
        </button>
        {configured > 0 && !advanced && (
          <span className="cds-note">已配置 {configured} 项能力</span>
        )}
      </div>

      {advanced && (
        <div className="cds-adv">
          <div className="cds-step"><span className="cds-step-t">连接细节</span></div>
          <div className="cds-grid">
            <label className="cds-f">
              <span>标识 id<i>（存历史数据用，保存后别改）</i></span>
              <input className="hs-input" value={value.id} placeholder="从端点自动填"
                onChange={(e) => { setIdTouched(true); set("id", e.target.value); }} />
            </label>
            <label className="cds-f">
              <span>鉴权方式<i>（自动配置会自己试出来）</i></span>
              <select className="hs-input" value={value.auth.mode}
                onChange={(e) => set("auth", { ...value.auth, mode: e.target.value as never })}>
                <option value="auto">自动探测</option>
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
            <label className="cds-f">
              <span>数据信封路径<i>（可选）</i></span>
              <input className="hs-input" value={value.envelope} placeholder="留空自动识别，如 data"
                onChange={(e) => set("envelope", e.target.value)} />
            </label>
            <label className="cds-f">
              <span>单次调用超时（秒）</span>
              <input className="hs-input" type="number" min={5} max={300} value={value.timeout}
                onChange={(e) => set("timeout", Number(e.target.value) || 40)} />
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
            <button className="cds-btn" onClick={() => void runProbe()}>只看工具清单</button>
            {probe && !probe.ok && <span className="cds-err">{probe.error}</span>}
            {probe?.ok && <span className="cds-ok">读到 {probe.count} 个工具</span>}
          </div>
          {probe?.ok && <div className="cds-note">{probe.note}</div>}
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

          <div className="cds-step">
            <span className="cds-step-t">在哪些板块出现</span>
            <i>由下面配了哪些能力决定，不用手动勾</i>
          </div>
          <div className="cds-surfaces">
            {SURFACES.map((s) => {
              const on = !!value.capabilities?.[s.requires];
              return (
                <span key={s.id} className={"cds-surface cds-surface-ro" + (on ? " on" : "")}>
                  {on ? "✓" : "—"} {s.label}
                  {/* 这一行本身就是"市场调研"，能力名里的括号注解再写一遍纯属噪音 */}
                  {!on && <em>缺「{shortLabel(s.requires)}」</em>}
                </span>
              );
            })}
          </div>

          <div className="cds-step">
            <span className="cds-step-t">能力映射</span><i>占位符：{PLACEHOLDERS}</i>
          </div>
          {CAPS.map((cap) => (
            <CapabilityBlock
              key={cap.id} cap={cap} tools={toolNames}
              spec={value.capabilities?.[cap.id]}
              outcome={report?.capabilities.find((c) => c.id === cap.id)}
              sample={{ keyword: sampleKeyword, asin: sampleAsin }}
              onChange={(spec) => setCap(cap.id, spec)}
              source={value}
            />
          ))}
        </div>
      )}

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

function AutoResult({ report }: { report: AutoReport }) {
  const labels: Record<string, string> = {
    home: "首页驾驶舱", market: "市场调研", playbook: "打法推荐",
  };
  const ok = report.capabilities.filter((c) => c.ok);
  const failed = report.capabilities.filter((c) => !c.ok);

  if (!report.ok) {
    return (
      <div className="cds-report cds-report-bad">
        <div className="cds-report-hd">没能自动配好</div>
        <div className="cds-note">{report.error || "这台服务器的工具都没能返回可用数据。"}</div>
        <div className="cds-note">
          可以展开下面的「高级设置」看看它到底有哪些工具，手动指定一次。
        </div>
      </div>
    );
  }

  return (
    <div className="cds-report">
      <div className="cds-report-hd">
        读到 {report.tools} 个工具，配好 {ok.length} 项能力
        {report.surfaces.length > 0 && (
          <> · 可用板块：{report.surfaces.map((s) => labels[s] || s).join("、")}</>
        )}
      </div>
      {report.auth && <div className="cds-note">鉴权方式已自动认出：{report.auth}</div>}
      {report.surfaces.length === 0 && (
        <div className="cds-note">
          没有板块能点亮 —— 首页要 ASIN 监控卡片，市场调研/打法推荐要关键词采集。
        </div>
      )}
      <ul className="cds-report-list">
        {ok.map((c) => (
          <li key={c.id}>
            <span className="cds-ok">✓</span> {c.label}
            <span className="cds-report-detail">
              用 <code>{c.tool}</code>
              {typeof c.matched === "number" && <>，映射 {c.matched} 个字段</>}
              {c.missing && c.missing.length > 0 && <>（缺 {c.missing.join("、")}）</>}
            </span>
          </li>
        ))}
        {failed.map((c) => (
          <li key={c.id}>
            <span className="cds-skip">—</span> {c.label}
            <span className="cds-report-detail">{c.error}</span>
          </li>
        ))}
      </ul>
      {failed.length > 0 && (
        <div className="cds-note">
          没配上的这几项，对应的功能就不出数据（其余照常用）。要补就展开「高级设置」手动指定。
        </div>
      )}
      <div className="cds-note">确认无误后点右下角「保存数据源」。</div>
    </div>
  );
}

function CapabilityBlock({ cap, spec, tools, outcome, sample, onChange, source }: {
  cap: (typeof CAPS)[number];
  spec?: CapabilitySpec;
  tools: string[];
  outcome?: CapabilityOutcome;
  sample: { keyword: string; asin: string };
  onChange: (spec: CapabilitySpec | null) => void;
  source: CustomDataSource;
}) {
  const on = !!spec;
  const [picking, setPicking] = useState("");
  const [pickErr, setPickErr] = useState("");
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
            <input className="hs-input" spellCheck={false} placeholder="如 price 或 a||b"
              value={(spec?.[key] as Record<string, string> | undefined)?.[name] || ""}
              onChange={(e) => patch({
                [key]: { ...(spec?.[key] || {}), [name]: e.target.value },
              } as Partial<CapabilitySpec>)} />
          </label>
        ))}
      </div>
    </div>
  );

  // 用户从候选里指一个工具 → 字段映射仍然由后端按真实返回推断。
  // 让人挑工具是合理的，让人逐个填字段路径不是。
  const pick = async (tool: string) => {
    setPicking(tool); setPickErr("");
    try {
      const out = await remapCapability(source, cap.id, tool, sample.keyword, sample.asin);
      if (out.spec) {
        onChange(out.spec);
        setArgsText(jsonText(out.spec.args) || "{}");
      }
      if (!out.ok) setPickErr(out.error || "这个工具的返回里认不出需要的字段");
    } catch (e: unknown) {
      setPickErr(detail(e) || "试这个工具失败");
    } finally {
      setPicking("");
    }
  };

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

      {!on && outcome && !outcome.ok && (
        <div className="cds-cap-why">
          <div className="cds-note">未自动配置：{outcome.error}</div>
          {outcome.candidates && outcome.candidates.length > 0 && (
            <div className="cds-pick">
              <span className="cds-note">从这几个里指一个试试（字段映射仍然自动推断）：</span>
              {outcome.candidates.map((c) => (
                <button key={c.tool} className="cds-btn" disabled={!!picking}
                  title={c.description} onClick={() => void pick(c.tool)}>
                  {picking === c.tool ? "试跑中…" : c.tool}
                </button>
              ))}
            </div>
          )}
          {pickErr && <div className="cds-err">{pickErr}</div>}
        </div>
      )}

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
                placeholder="工具名"
                onChange={(e) => patch({ tool: e.target.value })} />
              <datalist id={`cds-tools-${cap.id}`}>
                {tools.map((t) => <option key={t} value={t} />)}
              </datalist>
            </label>
            <label className="cds-f">
              <span>{cap.kind === "record" ? "记录路径" : "列表路径"}<i>（可选）</i></span>
              <input className="hs-input"
                placeholder={cap.kind === "record" ? "留空自动，如 data" : "留空自动，如 data.items"}
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
          <input className="hs-input" placeholder="这一步叫什么（显示在采集进度里）"
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
