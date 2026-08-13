import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { sidCurrencyMap, fmtBudget, type Cur } from "./lingxingCurrency";
import SheetSelect from "../../components/SheetSelect";
import { useToast } from "../../components/toast";
import { Btn, L, fmtTs, humanErr, inputStyle } from "./lingxingUi";

const WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

/* AI 建议 → 受控写工单 payload（活动预算/启停）；不可落地的返回 null */
function proposalPayload(p: any): any | null {
  if (!p || p.action === "keep" || !p.campaign_id) return null;
  const payload: any = {
    op_type: "campaign_budget", sid: p.sid, target_id: String(p.campaign_id),
    target_name: p.campaign_name || String(p.campaign_id),
    rationale: p.rationale || "(AI 分析建议)",
  };
  if ((p.action === "increase_budget" || p.action === "decrease_budget") && p.proposed?.daily_budget != null) {
    payload.new_value = p.proposed.daily_budget;
  }
  if (p.action === "pause") payload.new_state = "paused";
  if (p.action === "enable") payload.new_state = "enabled";
  if (p.current?.daily_budget != null) payload.cur_value = p.current.daily_budget;
  if (p.current?.state) payload.cur_state = p.current.state;
  return payload.new_value != null || payload.new_state ? payload : null;
}

export default function LingXingAutomation({ onGoTickets }: { onGoTickets?: (firstId?: string) => void }) {
  const [cfg, setCfg] = useState<Record<string, any>>({});
  const [runs, setRuns] = useState<any[]>([]);
  const [sel, setSel] = useState<any | null>(null);
  const [picked, setPicked] = useState<Record<number, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [sellers, setSellers] = useState<any[]>([]);
  const toast = useToast();
  const stopRef = useRef(false);
  const curMap = useMemo(() => sidCurrencyMap(sellers), [sellers]);
  const curOf = (sid: any): Cur | undefined => curMap[String(sid)];

  useEffect(() => { void load(); return () => { stopRef.current = true; }; }, []);
  async function load() {
    try {
      const [c, r, sl] = await Promise.all([
        api.get("/lingxing/auto/config"), api.get("/lingxing/auto/runs"),
        api.post("/lingxing/read/sellers", { params: {} }).catch(() => ({ data: { rows: [] } })),
      ]);
      setCfg(c.data.config || {}); setRuns(r.data.runs || []); setSellers(sl.data.rows || []);
    } catch (e: any) { toast("error", humanErr(e)); }
  }
  async function saveCfg() {
    try { const r = await api.patch("/lingxing/auto/config", { config: cfg }); setCfg(r.data.config); toast("success", "配置已保存"); }
    catch (e: any) { toast("error", humanErr(e)); }
  }
  async function runNow() {
    setRunning(true);
    try {
      const start = await api.post("/lingxing/auto/run", {});
      const rid = start.data.run_id;
      toast("info", "AI 分析已在后台运行…");
      for (let i = 0; i < 60 && !stopRef.current; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const r = await api.get("/lingxing/auto/runs");
        setRuns(r.data.runs || []);
        const mine = (r.data.runs || []).find((x: any) => x.id === rid) || (r.data.runs || [])[0];
        if (mine && mine.status !== "collecting" && mine.status !== "analyzing") {
          if (mine.status === "failed") toast("error", `分析失败：${mine.error || ""}`);
          else { toast("success", "AI 分析完成"); void openRun(mine.id); }
          break;
        }
      }
    } catch (e: any) { toast("error", humanErr(e)); }
    finally { setRunning(false); }
  }
  async function openRun(id: string) {
    try { const r = await api.get(`/lingxing/auto/runs/${id}`); setSel(r.data); setPicked({}); }
    catch (e: any) { toast("error", humanErr(e)); }
  }

  /* 就地批量生成工单（不用再去「工单」tab 找运行记录） */
  const proposals: any[] = sel?.proposals || [];
  const actionable = proposals.map((p, i) => ({ p, i })).filter(({ p }) => proposalPayload(p) != null).map(({ i }) => i);
  const chosen = actionable.filter((i) => picked[i]);
  async function makeTickets() {
    if (!chosen.length) return;
    setBusy(true);
    try {
      const r = await api.post("/lingxing/operate/batch-tickets", { payloads: chosen.map((i) => proposalPayload(proposals[i])) });
      const ids: string[] = r.data.tickets || [];
      for (const e of r.data.errors || []) toast("warn", `「${e.target || "?"}」创建失败：${e.error}`);
      if (ids.length) {
        toast("success", `已创建 ${ids.length} 张工单，三重复核后台进行中`);
        setPicked({});
        onGoTickets?.(ids[0]);
      }
    } catch (e: any) { toast("error", humanErr(e)); }
    finally { setBusy(false); }
  }

  return (
    <div>
      {/* config */}
      <div className="card" style={{ padding: 12, marginBottom: 10 }}>
        <div style={{ fontSize: "var(--fs-11)", fontWeight: 600, marginBottom: 8 }}>定时建议（仅分析+建议，不写入领星）</div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <L t="启用定时">
            <SheetSelect value={String(!!cfg.lingxing_auto_enabled)} onChange={(v) => setCfg((c) => ({ ...c, lingxing_auto_enabled: v === "true" }))} title="启用定时" style={{ ...inputStyle, width: 90 }}
              options={[{ value: "false", label: "关" }, { value: "true", label: "开" }]} /></L>
          <L t="星期">
            <SheetSelect value={String(cfg.lingxing_auto_weekday ?? 0)} onChange={(v) => setCfg((c) => ({ ...c, lingxing_auto_weekday: Number(v) }))} title="星期" style={{ ...inputStyle, width: 90 }}
              options={WEEKDAYS.map((w, i) => ({ value: String(i), label: w }))} /></L>
          <L t="小时">
            <input value={cfg.lingxing_auto_hour ?? 9} onChange={(e) => setCfg((c) => ({ ...c, lingxing_auto_hour: Number(e.target.value) }))} style={{ ...inputStyle, width: 70 }} /></L>
          <L t="分析天数">
            <input value={cfg.lingxing_auto_report_days ?? 7} onChange={(e) => setCfg((c) => ({ ...c, lingxing_auto_report_days: Number(e.target.value) }))} style={{ ...inputStyle, width: 70 }} /></L>
          <L t="幅度上限%">
            <input value={cfg.lingxing_max_change_pct ?? 20} onChange={(e) => setCfg((c) => ({ ...c, lingxing_max_change_pct: Number(e.target.value) }))} style={{ ...inputStyle, width: 80 }} /></L>
          <L t="店铺SID(空=全部)">
            <input value={cfg.lingxing_auto_stores ?? ""} onChange={(e) => setCfg((c) => ({ ...c, lingxing_auto_stores: e.target.value }))} style={{ ...inputStyle, width: 160 }} /></L>
          <Btn onClick={saveCfg}>保存配置</Btn>
          <Btn primary onClick={runNow} disabled={running}>{running ? "分析中…" : "立即运行一次"}</Btn>
        </div>
      </div>

      <div className="lx-split">
        {/* runs list */}
        <div style={{ width: 220 }} className="card lx-side">
          <div style={{ padding: "8px 10px", fontSize: "var(--fs-10)", color: "var(--t3)", borderBottom: "1px solid var(--b)" }}>运行记录</div>
          {runs.length === 0 && <div style={{ padding: 16, fontSize: "var(--fs-11)", color: "var(--t3)" }}>暂无</div>}
          {runs.map((r) => (
            <div key={r.id} onClick={() => openRun(r.id)} style={{
              padding: "7px 10px", cursor: "pointer", borderBottom: "1px solid var(--b)",
              background: sel?.id === r.id ? "var(--bg2)" : "transparent",
            }}>
              <div style={{ fontSize: "var(--fs-11)", display: "flex", justifyContent: "space-between" }}>
                <span>{fmtTs(r.started_at)}</span><StatusTag s={r.status} />
              </div>
              <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {r.trigger === "scheduled" ? "定时" : "手动"} · {r.summary || r.error || "—"}
              </div>
            </div>
          ))}
        </div>

        {/* run detail */}
        <div className="card lx-main">
          {!sel ? (
            <div style={{ padding: 30, textAlign: "center", color: "var(--t3)", fontSize: "var(--fs-11)" }}>选择左侧运行记录查看建议</div>
          ) : (
            <div style={{ padding: 12 }} className="wb-enter" key={sel.id}>
              <div style={{ fontSize: "var(--fs-12)", marginBottom: 8, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span>{sel.summary || "（无总结）"}</span>
                {actionable.length > 0 && (
                  <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center" }}>
                    <label style={{ display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer", fontSize: "var(--fs-10)", color: "var(--t3)" }}>
                      <input type="checkbox" checked={chosen.length === actionable.length && actionable.length > 0}
                        onChange={(e) => { const n: Record<number, boolean> = {}; if (e.target.checked) actionable.forEach((i) => { n[i] = true; }); setPicked(n); }} />
                      全选可落地建议
                    </label>
                    <Btn primary onClick={makeTickets} disabled={busy || !chosen.length}>生成所选工单（{chosen.length}）</Btn>
                  </span>
                )}
              </div>
              {sel.error && <div style={{ color: "var(--red)", fontSize: "var(--fs-11)", marginBottom: 8 }}>错误：{sel.error}</div>}
              {(!proposals || proposals.length === 0) ? (
                <div style={{ color: "var(--t3)", fontSize: "var(--fs-11)" }}>无建议（数据不足或无明显信号）。</div>
              ) : (
                <div style={{ overflowX: "auto" }}>
                  <table className="lx-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--fs-11)" }}>
                    <thead><tr>{["", "活动", "动作", "当前", "建议", "幅度", "依据", "预期", "置信", "风险"].map((h) => (
                      <th key={h} style={th}>{h}</th>))}</tr></thead>
                    <tbody>
                      {proposals.map((p: any, i: number) => (
                        <tr key={i} style={{ borderBottom: "1px solid var(--b)", opacity: p.action === "keep" ? 0.6 : 1 }}>
                          <td style={td}>{proposalPayload(p) != null && (
                            <input type="checkbox" checked={!!picked[i]} onChange={(e) => setPicked((s) => ({ ...s, [i]: e.target.checked }))} />
                          )}</td>
                          <td style={td}>{p.campaign_name || p.campaign_id}</td>
                          <td style={td}><b>{p.action}</b></td>
                          <td style={td}>{fmtState(p.current, curOf(p.sid))}</td>
                          <td style={td}>{fmtState(p.proposed, curOf(p.sid))}</td>
                          <td style={td}>{p.change_pct != null ? `${p.change_pct}%` : "—"}{p.guardrail_flag && <span title={p.guardrail_flag} style={{ color: "var(--amber)" }}> ⚠</span>}</td>
                          <td style={{ ...td, maxWidth: 240, whiteSpace: "normal" }}>{p.rationale}</td>
                          <td style={{ ...td, maxWidth: 200, whiteSpace: "normal" }}>{p.expected_impact}</td>
                          <td style={td}>{p.confidence != null ? Math.round(p.confidence * 100) + "%" : "—"}</td>
                          <td style={td}><RiskTag r={p.risk} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ marginTop: 8, fontSize: "var(--fs-10)", color: "var(--t3)" }}>
                    ⓘ 勾选建议 → 生成工单：进入 三重复核 + 护栏 + 人工确认，确认前不会写入领星。
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const th: React.CSSProperties = { textAlign: "left", padding: "6px 8px", color: "var(--t3)", borderBottom: "1px solid var(--b)", whiteSpace: "nowrap" };
const td: React.CSSProperties = { padding: "6px 8px", color: "var(--t2)", verticalAlign: "top" };

function StatusTag({ s }: { s: string }) {
  const c = s === "done" ? "var(--acc)" : s === "failed" ? "var(--red)" : "var(--amber)";
  return <span style={{ color: c, fontSize: "var(--fs-10)" }}>{s}</span>;
}
function RiskTag({ r }: { r: string }) {
  const c = r === "high" ? "var(--red)" : r === "medium" ? "var(--amber)" : "var(--acc)";
  return <span style={{ color: c }}>{r || "—"}</span>;
}
function fmtState(o: any, cur?: Cur) {
  if (!o || typeof o !== "object") return "—";
  const b = o.daily_budget, s = o.state;
  return [s, b != null ? fmtBudget(b, cur) : null].filter(Boolean).join(" / ") || "—";
}
