import { useEffect, useState } from "react";
import { api } from "../../api/client";
import SheetSelect from "../../components/SheetSelect";
import LingXingHelp from "./LingXingHelp";
import { errText } from "../../lib/errText";

const inputStyle: React.CSSProperties = {
  background: "var(--bg1)", border: "1px solid var(--b)", borderRadius: 3,
  padding: "6px 8px", fontSize: 11, color: "var(--t)", outline: "none", fontFamily: "inherit", boxSizing: "border-box",
};
function Btn({ onClick, children, primary, disabled }: any) {
  return <button onClick={onClick} disabled={disabled} style={{ background: primary ? "var(--acc)" : "var(--bg2)", color: primary ? "#000" : "var(--t)", border: primary ? "none" : "1px solid var(--b)", borderRadius: 4, padding: "5px 12px", fontSize: 11, cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.55 : 1 }}>{children}</button>;
}
function Card({ title, children }: any) {
  return <div className="card" style={{ padding: 12, marginBottom: 10 }}><div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{title}</div>{children}</div>;
}
function Field({ label, children }: any) {
  return <div style={{ display: "grid", gap: 3, fontSize: 10, color: "var(--t3)" }}><span>{label}</span>{children}</div>;
}

export default function LingXingConfig() {
  const [st, setStatus] = useState<any>(null);
  const [s, setS] = useState<Record<string, any>>({});      // non-secret settings
  const [secrets, setSecrets] = useState<string[]>([]);
  const [host, setHost] = useState(""); const [appid, setAppid] = useState("");
  const [secret, setSecret] = useState(""); const [mcp, setMcp] = useState("");
  const [probe, setProbe] = useState<any>(null);
  const [msg, setMsg] = useState(""); const [busy, setBusy] = useState(false);
  const [avail, setAvail] = useState<any[]>([]); const [personas, setPersonas] = useState<string[]>([]);
  const [provs, setProvs] = useState<string[]>(["ivyea-agent", "deepseek", "assistant"]);
  const [analysisProv, setAnalysisProv] = useState("ivyea-agent");
  const [models, setModels] = useState<any[]>([]); const [cm, setCm] = useState<any>({});
  const [rules, setRules] = useState(""); const [rulesDefault, setRulesDefault] = useState("");
  const [showHelp, setShowHelp] = useState(false);

  useEffect(() => { void load(); }, []);
  async function load() {
    try {
      const [stat, set, rp] = await Promise.all([api.get("/lingxing/status"), api.get("/settings"), api.get("/lingxing/review/providers")]);
      setStatus(stat.data); setSecrets(set.data.secret_keys || []);
      const cfg = set.data.settings || {}; setS(cfg);
      setHost(cfg.lingxing_openapi_host || ""); setAppid(cfg.lingxing_openapi_appid || "");
      setAvail(rp.data.available || []); setPersonas(rp.data.personas || []);
      setProvs(String(rp.data.review_providers || "ivyea-agent,deepseek,assistant").split(",").map((x: string) => x.trim()));
      setAnalysisProv(rp.data.analysis_provider || "ivyea-agent");
      try { setModels(JSON.parse(cfg.lingxing_custom_models || "[]")); } catch { setModels([]); }
      setRules(rp.data.rules_doc || ""); setRulesDefault(rp.data.rules_doc_default || "");
    } catch (e: any) { setMsg(humanErr(e)); }
  }
  async function saveProvs(next: string[]) { setProvs(next); await patch({ lingxing_review_providers: next.join(",") }, "复核模型已保存"); }
  async function saveModels(next: any[]) { setModels(next); await patch({ lingxing_custom_models: JSON.stringify(next) }, "自定义模型已保存"); }
  async function patch(updates: Record<string, any>, okMsg = "已保存") {
    setBusy(true); setMsg("");
    try { await api.patch("/settings", { settings: updates }); setMsg(okMsg); await load(); }
    catch (e: any) { setMsg(humanErr(e)); } finally { setBusy(false); }
  }
  async function saveCreds() {
    const u: Record<string, any> = { lingxing_openapi_host: host, lingxing_openapi_appid: appid };
    if (secret) u.lingxing_openapi_secret = secret;
    if (mcp) u.lingxing_mcp_key = mcp;
    await patch(u, "凭证已保存"); setSecret(""); setMcp("");
  }
  async function test() {
    setBusy(true); setMsg(""); setProbe(null);
    try { setProbe((await api.post("/lingxing/probe")).data); setMsg("测试完成"); }
    catch (e: any) { setMsg(humanErr(e)); } finally { setBusy(false); }
  }
  const setN = (k: string, v: any) => setS((o) => ({ ...o, [k]: v }));

  return (
    <div>
      <div className="card" style={{ padding: "8px 12px", marginBottom: 10, fontSize: 11, color: "var(--t3)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span>开箱即用：① 填 OpenAPI 凭证 → ② 测试连接 → ③ 打开总开关。之后即可在「大盘/数据浏览/优化建议」浏览分析；写操作另有独立「操作开关」+ 三重复核。</span>
        <span style={{ marginLeft: "auto" }}><Btn onClick={() => setShowHelp((v) => !v)}>{showHelp ? "收起帮助文档" : "📖 帮助文档"}</Btn></span>
      </div>

      {showHelp && <div className="wb-enter" style={{ marginBottom: 10 }}><LingXingHelp /></div>}

      {/* ① credentials */}
      <Card title="① 领星 OpenAPI 凭证（领星 ERP → 开放接口）">
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <Field label="API 域名"><input value={host} onChange={(e) => setHost(e.target.value)} style={{ ...inputStyle, width: 280 }} placeholder="https://openapi.lingxing.com" /></Field>
          <Field label="AppID"><input value={appid} onChange={(e) => setAppid(e.target.value)} style={{ ...inputStyle, width: 200 }} /></Field>
          <Field label={`AppSecret ${s.lingxing_openapi_secret ? "（已配置，留空不改）" : "（未配置）"}`}>
            <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} style={{ ...inputStyle, width: 220 }} placeholder={s.lingxing_openapi_secret ? "••••••••" : "填入"} /></Field>
          <Field label={`MCP key（可选）${s.lingxing_mcp_key ? "（已配置）" : ""}`}>
            <input type="password" value={mcp} onChange={(e) => setMcp(e.target.value)} style={{ ...inputStyle, width: 160 }} placeholder="可不填" /></Field>
          <Btn primary onClick={saveCreds} disabled={busy}>保存凭证</Btn>
        </div>
      </Card>

      {/* ② test */}
      <Card title="② 测试连接">
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Btn onClick={test} disabled={busy}>测试连接（probe）</Btn>
          {probe?.openapi && <span style={{ fontSize: 11, color: probe.openapi.ok ? "var(--acc)" : "var(--red)" }}>
            OpenAPI：{probe.openapi.ok ? `✓ 已连通，店铺 ${probe.openapi.probe_seller_count ?? "?"} 个` : `✗ ${probe.openapi.error}`}</span>}
          {probe?.mcp && <span style={{ fontSize: 11, color: "var(--t3)" }}>MCP：{probe.mcp.ok === false ? "未连通" : `工具 ${probe.mcp.tool_count ?? "?"} 个`}</span>}
        </div>
      </Card>

      {/* ③ switches */}
      <Card title="③ 总开关">
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, color: st?.master_enabled ? "var(--acc)" : "var(--t3)" }}>数据总开关：{st?.master_enabled ? "已启用" : "关闭"}</span>
          {st?.master_enabled
            ? <Btn onClick={() => patch({ lingxing_enabled: false }, "已关闭")} disabled={busy}>关闭</Btn>
            : <Btn primary onClick={() => patch({ lingxing_enabled: true }, "已启用")} disabled={busy}>启用数据（只读）</Btn>}
          <span style={{ fontSize: 10, color: "var(--t3)" }}>写操作的「操作开关」在「操作执行」tab，默认关、带自动失效。</span>
        </div>
      </Card>

      {/* ④ optimization params */}
      <Card title="④ 优化参数（保守默认；目标 ACOS 自动按毛利推）">
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          {([
            ["lingxing_target_acos_factor", "目标ACOS系数(×毛利)", 90],
            ["lingxing_max_change_pct", "单步幅度上限%", 90],
            ["lingxing_bid_step_pct", "bid步长%", 80],
            ["lingxing_neg_min_clicks", "否词最小点击", 90],
            ["lingxing_cooldown_days", "冷却天数", 80],
            ["lingxing_opt_window_days", "分析窗口天", 90],
          ] as const).map(([k, lbl, w]) => (
            <Field key={k} label={lbl}><input value={s[k] ?? ""} onChange={(e) => setN(k, e.target.value)} style={{ ...inputStyle, width: w }} /></Field>
          ))}
          <Field label="写白名单店铺SID(逗号)"><input value={s.lingxing_scope_stores ?? ""} onChange={(e) => setN("lingxing_scope_stores", e.target.value)} style={{ ...inputStyle, width: 180 }} placeholder="空=禁止所有写" /></Field>
          <Btn primary onClick={() => patch({
            lingxing_target_acos_factor: Number(s.lingxing_target_acos_factor),
            lingxing_max_change_pct: Number(s.lingxing_max_change_pct), lingxing_bid_step_pct: Number(s.lingxing_bid_step_pct),
            lingxing_neg_min_clicks: Number(s.lingxing_neg_min_clicks), lingxing_cooldown_days: Number(s.lingxing_cooldown_days),
            lingxing_opt_window_days: Number(s.lingxing_opt_window_days), lingxing_scope_stores: s.lingxing_scope_stores || "",
          }, "参数已保存")} disabled={busy}>保存参数</Btn>
        </div>
        <div style={{ fontSize: 10, color: "var(--t3)", marginTop: 6 }}>白名单为空时，任何写操作都会被护栏拦截（fail-closed）；只放你确认要自动优化的店铺。</div>
      </Card>

      {/* ⑤ review models */}
      <Card title="⑤ 复核模型（三重复核每位可用不同模型/智能体）">
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {personas.map((pn, i) => (
            <Field key={i} label={`复核${i + 1}：${pn}`}>
              <SheetSelect value={provs[i] || "ivyea-agent"} onChange={(v) => { const n = [...provs]; n[i] = v; void saveProvs(n); }} title="选择模型" style={{ ...inputStyle, minWidth: 170 }}
                options={avail.map((a) => ({ value: a.id, label: a.label + (a.ok ? "" : "（未配置/未装）"), disabled: !a.ok }))} />
            </Field>
          ))}
          <Field label="自动化建议·分析模型">
            <SheetSelect value={analysisProv} onChange={(v) => { setAnalysisProv(v); void patch({ lingxing_analysis_provider: v }, "分析模型已保存"); }} title="分析模型" style={{ ...inputStyle, minWidth: 170 }}
              options={avail.map((a) => ({ value: a.id, label: a.label + (a.ok ? "" : "（未配置/未装）"), disabled: !a.ok }))} />
          </Field>
        </div>
        <div style={{ fontSize: 10, color: "var(--t3)", marginTop: 6 }}>
          默认优先用内置 IvyeaAgent；也可选全局兜底大模型、DeepSeek/Apimart、CLI 智能体(hermes/claude/codex,较慢)或下方自定义模型。某个不可用会自动回退默认链。建议把「魔鬼代言人」设成与其它不同的模型做真异构。「分析模型」是自动化建议产出建议时用的模型（优化引擎是纯规则、不用模型）。
        </div>

        <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--b)" }}>
          <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 6 }}>自定义模型（OpenAI 兼容）</div>
          {models.map((m, i) => (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 11, marginBottom: 4 }}>
              <b>{m.label || m.id}</b><span style={{ color: "var(--t3)" }}>{m.model} @ {m.base_url}</span>
              <span style={{ color: "var(--t3)" }}>引用名 custom:{m.id}</span>
              <Btn onClick={() => saveModels(models.filter((_, j) => j !== i))}>删除</Btn>
            </div>
          ))}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", marginTop: 6 }}>
            <Field label="id"><input value={cm.id || ""} onChange={(e) => setCm({ ...cm, id: e.target.value })} style={{ ...inputStyle, width: 80 }} /></Field>
            <Field label="名称"><input value={cm.label || ""} onChange={(e) => setCm({ ...cm, label: e.target.value })} style={{ ...inputStyle, width: 110 }} /></Field>
            <Field label="base_url"><input value={cm.base_url || ""} onChange={(e) => setCm({ ...cm, base_url: e.target.value })} style={{ ...inputStyle, width: 220 }} placeholder="https://openrouter.ai/api/v1" /></Field>
            <Field label="model"><input value={cm.model || ""} onChange={(e) => setCm({ ...cm, model: e.target.value })} style={{ ...inputStyle, width: 180 }} /></Field>
            <Field label="api_key"><input type="password" value={cm.api_key || ""} onChange={(e) => setCm({ ...cm, api_key: e.target.value })} style={{ ...inputStyle, width: 150 }} /></Field>
            <Btn primary disabled={!cm.id || !cm.base_url || !cm.model} onClick={() => { void saveModels([...models.filter((x) => x.id !== cm.id), cm]); setCm({}); }}>添加</Btn>
          </div>
        </div>
      </Card>

      {/* ⑥ rules doc */}
      <Card title="⑥ 优化规则文档（展示 + 可编辑；作为 LLM 复核/分析的方法论依据注入）">
        <textarea value={rules} onChange={(e) => setRules(e.target.value)} rows={14}
          style={{ ...inputStyle, width: "100%", resize: "vertical", lineHeight: 1.5, fontFamily: "inherit" }} />
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <Btn primary onClick={() => patch({ lingxing_rules_doc: rules }, "规则文档已保存")} disabled={busy}>保存规则文档</Btn>
          <Btn onClick={() => setRules(rulesDefault)} disabled={busy}>恢复默认</Btn>
        </div>
        <div style={{ fontSize: 10, color: "var(--t3)", marginTop: 6 }}>
          确定性阈值（否词点击数/步长/冷却等）在「④ 优化参数」里调；这里改的是 LLM 复核与分析所遵循的方法论叙述。
        </div>
      </Card>

      {msg && <div style={{ fontSize: 11, color: "var(--t3)" }}>{msg}</div>}
    </div>
  );
}
function humanErr(e: any): string { return errText(e, "请求失败"); }
