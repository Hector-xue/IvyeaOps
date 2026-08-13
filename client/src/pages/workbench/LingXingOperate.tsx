import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import { sidCurrencyMap, fmtBudget, type Cur } from "./lingxingCurrency";
import { useConfirm } from "../../components/ConfirmDialog";
import SheetSelect from "../../components/SheetSelect";
import { useToast } from "../../components/toast";
import {
  Btn, L, Section, TicketStatus, TICKET_STATUS_ZH, fmtDur, fmtTs, humanErr, inputStyle,
} from "./lingxingUi";

const STATUS_FILTERS: [string, string][] = [
  ["awaiting_human", "待确认"], ["reviewing", "复核中"], ["executed", "已执行"],
  ["review_rejected", "复核否决"], ["guardrail_blocked", "护栏拦截"], ["failed", "失败"],
];

export default function LingXingOperate({ focusTicket, onFocusConsumed }: {
  focusTicket?: string; onFocusConsumed?: () => void;
}) {
  const [status, setStatus] = useState<any>(null);
  const [tickets, setTickets] = useState<any[]>([]);
  const [sel, setSel] = useState<any | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [runs, setRuns] = useState<any[]>([]);
  const [runId, setRunId] = useState("");
  const [sellers, setSellers] = useState<any[]>([]);
  const [opTypes, setOpTypes] = useState<any[]>([]);
  const [mForm, setMForm] = useState<any>({ op_type: "keyword_bid" });
  const [showManual, setShowManual] = useState(false);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const curMap = useMemo(() => sidCurrencyMap(sellers), [sellers]);
  const curOf = (sid: any): Cur | undefined => curMap[String(sid)];
  const confirm = useConfirm();

  useEffect(() => {
    void load();
    const t = setInterval(() => { void refreshStatus(); void refreshTickets(); }, 5000);
    return () => clearInterval(t);
  }, []);
  async function load() {
    try {
      const [s, t, r, sl, ot] = await Promise.all([
        api.get("/lingxing/status"), api.get("/lingxing/operate/tickets"), api.get("/lingxing/auto/runs"),
        api.post("/lingxing/read/sellers", { params: {} }).catch(() => ({ data: { rows: [] } })),
        api.get("/lingxing/operate/op-types").catch(() => ({ data: { op_types: [] } })),
      ]);
      setStatus(s.data); setTickets(t.data.tickets || []); setRuns(r.data.runs || []);
      setSellers(sl.data.rows || []); setOpTypes(ot.data.op_types || []);
      if (!mForm.sid && sl.data.rows?.[0]) setMForm((f: any) => ({ ...f, sid: sl.data.rows[0].sid }));
      if (!runId && r.data.runs?.[0]) setRunId(r.data.runs[0].id);
    } catch (e: any) { toast("error", humanErr(e)); }
  }
  async function refreshStatus() { try { setStatus((await api.get("/lingxing/status")).data); } catch { /* */ } }
  async function refreshTickets() {
    try {
      const t = (await api.get("/lingxing/operate/tickets")).data.tickets || [];
      setTickets(t);
      // keep the open ticket in sync while its review pipeline finishes
      if (sel && ["reviewing", "executing"].includes(sel.status)) {
        const fresh = t.find((x: any) => x.id === sel.id);
        if (fresh && fresh.status !== sel.status) void openTicket(sel.id, true);
      }
    } catch { /* */ }
  }

  /* 从「优化建议」跳转过来时，自动打开刚生成的工单 */
  useEffect(() => {
    if (focusTicket) { void openTicket(focusTicket); onFocusConsumed?.(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusTicket]);

  async function toggleOperate(on: boolean) {
    setBusy(true);
    try {
      const r = await api.post(`/lingxing/operate/${on ? "enable" : "disable"}`);
      setStatus(r.data.status);
      toast(on ? "warn" : "success", on ? "操作开关已开启（可写态）" : "操作开关已关闭（恢复只读）");
    } catch (e: any) { toast("error", humanErr(e)); } finally { setBusy(false); }
  }
  async function genFromRun() {
    if (!runId) return;
    setBusy(true);
    try { const r = await api.post(`/lingxing/operate/from-run/${runId}`); toast("success", `已生成 ${r.data.created} 张工单（后台复核中）`); await refreshTickets(); }
    catch (e: any) { toast("error", humanErr(e)); } finally { setBusy(false); }
  }
  async function submitManual() {
    setBusy(true);
    try {
      const r = await api.post("/lingxing/operate/manual", mForm);
      toast("success", `工单 ${r.data.id} 已进入后台复核`);
      setShowManual(false); await refreshTickets(); setSel(r.data);
    } catch (e: any) { toast("error", humanErr(e)); } finally { setBusy(false); }
  }
  const mSet = (k: string, v: any) => setMForm((f: any) => ({ ...f, [k]: v }));
  async function report(tid: string, download: boolean) {
    try {
      const r = await api.get(`/lingxing/operate/tickets/${tid}/report?download=${download ? 1 : 0}`, { responseType: "blob" });
      const url = URL.createObjectURL(r.data as Blob);
      if (download) {
        const a = document.createElement("a"); a.href = url; a.download = `lingxing-op-${tid}.html`; a.click();
      } else { window.open(url, "_blank"); }
      setTimeout(() => URL.revokeObjectURL(url), 15000);
    } catch (e: any) { toast("error", humanErr(e)); }
  }
  async function openTicket(id: string, silent = false) {
    try { setSel((await api.get(`/lingxing/operate/tickets/${id}`)).data); }
    catch (e: any) { if (!silent) toast("error", humanErr(e)); }
  }
  async function act(id: string, action: string, body: any = {}, okMsg?: string) {
    setBusy(true);
    try {
      const r = await api.post(`/lingxing/operate/tickets/${id}/${action}`, body);
      setSel(r.data); await refreshTickets(); await refreshStatus();
      if (okMsg) toast("success", okMsg);
    } catch (e: any) { toast("error", humanErr(e)); } finally { setBusy(false); }
  }

  const active = !!status?.operate_active;
  const remain = status?.operate_remaining_seconds || 0;
  const shown = filter ? tickets.filter((t) => t.status === filter) : tickets;
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const t of tickets) c[t.status] = (c[t.status] || 0) + 1;
    return c;
  }, [tickets]);

  /* 批量确认/驳回：只对 awaiting_human 的选中项 */
  const selectable = shown.filter((t) => t.status === "awaiting_human");
  const chosenIds = selectable.filter((t) => checked[t.id]).map((t) => t.id);
  async function batch(action: "confirm" | "reject") {
    if (!chosenIds.length) return;
    if (action === "confirm") {
      if (!active) { toast("warn", "请先开启操作开关"); return; }
      const lines = selectable.filter((t) => checked[t.id]).map((t) =>
        `· ${t.intent?.target_name || t.intent?.target_id}：${fmtChange(t.intent?.change, curOf(t.intent?.sid)) || (t.intent?.keyword_text ? `「${t.intent.keyword_text}」${t.intent.match_type || ""}` : "")}`).join("\n");
      const ok = await confirm({
        title: `批量确认执行 ${chosenIds.length} 张工单`,
        message: `将真实写入领星以下改动（均已过三重复核 + 护栏）：\n${lines}\n\n任一执行失败会立即停止并熔断。确定执行？`,
        confirmText: "确认执行", danger: true, icon: "⚠",
      });
      if (!ok) return;
    }
    setBusy(true);
    try {
      const r = await api.post("/lingxing/operate/tickets/batch", { action, ids: chosenIds });
      const fails = (r.data.results || []).filter((x: any) => !x.ok);
      toast(fails.length ? "warn" : "success", `批量${action === "confirm" ? "执行" : "驳回"}：成功 ${r.data.done}/${r.data.total}`);
      for (const f of fails) toast("error", `${f.id}：${f.error}`);
      setChecked({}); await refreshTickets(); await refreshStatus();
      if (sel) void openTicket(sel.id, true);
    } catch (e: any) { toast("error", humanErr(e)); } finally { setBusy(false); }
  }

  return (
    <div>
      {/* circuit breaker tripped */}
      {status?.circuit_reason && (
        <div className="card" style={{ padding: "10px 12px", marginBottom: 10, border: "1px solid var(--red)", background: "color-mix(in srgb, var(--red) 8%, transparent)" }}>
          <span style={{ fontSize: "var(--fs-11)", color: "var(--red)", fontWeight: 600 }}>⚠ 熔断已触发：</span>
          <span style={{ fontSize: "var(--fs-11)", color: "var(--t2)" }}> {status.circuit_reason}</span>
          <span style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>（重新开启操作开关即确认并清除）</span>
        </div>
      )}

      {/* operate switch (danger) */}
      <div className="card" style={{ padding: 12, marginBottom: 10, border: active ? "1px solid var(--red)" : "1px solid var(--b)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <div style={{ fontSize: "var(--fs-12)", fontWeight: 600, color: active ? "var(--red)" : "var(--t)" }}>
            操作开关：{active ? "已开启（可写）" : "关闭（只读）"}
          </div>
          {active && <span style={{ fontSize: "var(--fs-11)", color: "var(--amber)" }}>剩余 {fmtDur(remain)} 后自动关闭</span>}
          <span style={{ marginLeft: "auto" }}>
            {active
              ? <Btn onClick={() => toggleOperate(false)} disabled={busy}>关闭操作</Btn>
              : <Btn danger onClick={async () => { if (await confirm({ title: "开启操作开关", message: "开启后进入可写态（写操作仍需三重复核 + 护栏 + 人工确认）。确定开启？", confirmText: "开启", danger: true, icon: "⚠" })) toggleOperate(true); }} disabled={busy}>开启操作领星</Btn>}
          </span>
        </div>
        <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginTop: 8 }}>
          每一笔写操作都必须：① 三重独立复核全过 → ② 确定性护栏（白名单/幅度上限）→ ③ 你人工点确认 → 才执行；执行前抓回滚快照，失败自动熔断。{!status?.master_enabled && " （注意：总开关未开启，写操作仍会被拦截）"}
        </div>
      </div>

      {/* generate tickets from a run + new manual */}
      <div className="card" style={{ padding: 12, marginBottom: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>从分析运行生成工单</span>
        <SheetSelect value={runId} onChange={setRunId} title="选择分析运行" placeholder="（无运行记录）" style={{ ...inputStyle, minWidth: 220 }}
          options={runs.map((r) => ({ value: String(r.id), label: `${fmtTs(r.started_at)} · ${r.summary?.slice(0, 20) || r.status}` }))} />
        <Btn onClick={genFromRun} disabled={busy || !runId}>生成工单（进入复核）</Btn>
        <span style={{ marginLeft: "auto" }}><Btn onClick={() => setShowManual((v) => !v)}>{showManual ? "收起" : "＋ 新建工单"}</Btn></span>
      </div>

      {/* manual ticket — dynamic fields per op type */}
      {showManual && (() => {
        const op = opTypes.find((o) => o.key === mForm.op_type);
        return (
          <div className="card wb-enter" style={{ padding: 12, marginBottom: 10 }}>
            <div style={{ fontSize: "var(--fs-11)", fontWeight: 600, marginBottom: 8 }}>新建写操作工单（走 三复核 + 护栏 + 人工确认）</div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
              <L t="操作类型"><SheetSelect value={mForm.op_type} onChange={(v) => setMForm({ op_type: v, sid: mForm.sid })} title="操作类型" style={{ ...inputStyle, minWidth: 170 }}
                options={opTypes.map((o) => ({ value: o.key, label: o.label }))} /></L>
              <L t="店铺"><SheetSelect value={String(mForm.sid ?? "")} onChange={(v) => mSet("sid", Number(v))} title="选择店铺" style={{ ...inputStyle, minWidth: 140 }}
                options={sellers.map((s) => ({ value: String(s.sid), label: String(s.name || s.sid) }))} /></L>
              {(op?.fields || []).map((f: any) => (
                <L key={f.name} t={f.label + (f.required ? " *" : "")}>
                  {f.type === "select"
                    ? <SheetSelect value={String(mForm[f.name] ?? "")} onChange={(v) => mSet(f.name, v)} title={f.label} placeholder="不改" style={{ ...inputStyle, minWidth: 110 }}
                        options={f.options.map((o: string) => ({ value: o, label: o || "不改" }))} />
                    : <input value={mForm[f.name] ?? ""} onChange={(e) => mSet(f.name, e.target.value)}
                        style={{ ...inputStyle, width: f.type === "number" ? 100 : 150 }} placeholder={f.type === "number" ? "数字" : ""} />}
                </L>
              ))}
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "flex-end" }}>
              <L t="依据/理由"><input value={mForm.rationale ?? ""} onChange={(e) => mSet("rationale", e.target.value)} style={{ ...inputStyle, width: 420 }} placeholder="为什么这么做（复核会读）" /></L>
              <Btn primary onClick={submitManual} disabled={busy || !mForm.sid}>提交进复核</Btn>
            </div>
            <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginTop: 6 }}>
              {op?.category === "add"
                ? `加词/否词为新增操作${op?.reversible ? "（回滚=归档该否定词）" : "（不可一键回滚，撤销请到领星暂停/归档）"}；活动/广告组ID 可在「数据浏览」对应数据集查到。`
                : "改竞价/预算：当前值自动读取真实值算幅度护栏；目标ID 在「数据浏览」SP关键词/定向/广告组里查；回滚以执行前真实值为快照。"}
            </div>
          </div>
        );
      })()}

      {/* status filter + batch actions */}
      <div className="card" style={{ padding: "8px 12px", marginBottom: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span onClick={() => setFilter("")} style={chipStyle(filter === "")}>全部 {tickets.length}</span>
        {STATUS_FILTERS.map(([s, l]) => (
          <span key={s} onClick={() => setFilter(filter === s ? "" : s)} style={chipStyle(filter === s)}>{l} {counts[s] || 0}</span>
        ))}
        {selectable.length > 0 && (
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, alignItems: "center" }}>
            <label style={{ display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer", fontSize: "var(--fs-10)", color: "var(--t3)" }}>
              <input type="checkbox" checked={chosenIds.length === selectable.length && selectable.length > 0}
                onChange={(e) => { const n: Record<string, boolean> = {}; if (e.target.checked) selectable.forEach((t) => { n[t.id] = true; }); setChecked(n); }} />
              全选待确认
            </label>
            <Btn danger onClick={() => batch("confirm")} disabled={busy || !chosenIds.length || !active}>批量确认执行（{chosenIds.length}）</Btn>
            <Btn onClick={() => batch("reject")} disabled={busy || !chosenIds.length}>批量驳回</Btn>
          </span>
        )}
      </div>

      <div className="lx-split">
        {/* tickets list */}
        <div style={{ width: 240 }} className="card lx-side">
          <div style={{ padding: "8px 10px", fontSize: "var(--fs-10)", color: "var(--t3)", borderBottom: "1px solid var(--b)" }}>工单 {filter ? `· ${TICKET_STATUS_ZH[filter] || filter}` : ""}</div>
          {shown.length === 0 && <div style={{ padding: 16, fontSize: "var(--fs-11)", color: "var(--t3)" }}>暂无</div>}
          {shown.map((t) => (
            <div key={t.id} style={{
              display: "flex", alignItems: "center", gap: 6, padding: "7px 10px", cursor: "pointer",
              borderBottom: "1px solid var(--b)", background: sel?.id === t.id ? "var(--bg2)" : "transparent",
            }}>
              {t.status === "awaiting_human" && (
                <input type="checkbox" checked={!!checked[t.id]} onClick={(e) => e.stopPropagation()}
                  onChange={(e) => setChecked((c) => ({ ...c, [t.id]: e.target.checked }))} />
              )}
              <div style={{ flex: 1, minWidth: 0 }} onClick={() => openTicket(t.id)}>
                <div style={{ fontSize: "var(--fs-11)", display: "flex", justifyContent: "space-between", gap: 6 }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.intent?.target_name || t.intent?.campaign_name || t.intent?.target_id || t.intent?.campaign_id}</span>
                  <TicketStatus s={t.status} />
                </div>
                <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>{t.intent?.keyword_text ? `「${t.intent.keyword_text}」${t.intent.match_type || ""}` : fmtChange(t.intent?.change, curOf(t.intent?.sid))}</div>
              </div>
            </div>
          ))}
        </div>

        {/* ticket detail */}
        <div className="card lx-main">
          {!sel ? <div style={{ padding: 30, textAlign: "center", color: "var(--t3)", fontSize: "var(--fs-11)" }}>选择左侧工单</div> : (
            <div style={{ padding: 12 }} className="wb-enter" key={sel.id}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <b style={{ fontSize: "var(--fs-12)" }}>{sel.intent?.target_name || sel.intent?.campaign_name || sel.intent?.target_id || sel.intent?.campaign_id}</b>
                {sel.intent?.op_label && <span style={{ fontSize: "var(--fs-10)", color: "var(--t3)", border: "1px solid var(--b)", borderRadius: 3, padding: "1px 5px" }}>{sel.intent.op_label}</span>}
                <TicketStatus s={sel.status} />
                <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>店铺 {sel.intent?.sid}</span>
              </div>

              {sel.status === "reviewing" ? (
                <div style={{ fontSize: "var(--fs-11)", color: "var(--amber)", padding: "10px 0" }}>
                  <span className="lx-spin-dot" />三重复核后台进行中，稍候自动刷新…
                </div>
              ) : (
                <div style={{ fontSize: "var(--fs-11)", marginBottom: 8 }}>
                  {sel.intent?.keyword_text
                    ? <>新增：<b>「{sel.intent.keyword_text}」（{sel.intent.match_type}）</b> 活动 {sel.intent.campaign_id}{sel.intent.ad_group_id ? ` / 组 ${sel.intent.ad_group_id}` : ""}{sel.intent.bid != null ? ` / 竞价 ${fmtBudget(sel.intent.bid, curOf(sel.intent.sid))}` : ""}<br /></>
                    : <>改动：<b>{fmtChange(sel.intent?.change, curOf(sel.intent?.sid))}</b>（当前 {fmtState(sel.intent?.before, curOf(sel.intent?.sid))}）<br /></>}
                  依据：<span style={{ color: "var(--t2)" }}>{sel.intent?.rationale || "—"}</span>
                </div>
              )}

              {/* guardrails */}
              {sel.guardrail && (
                <Section title="确定性护栏">
                  {(sel.guardrail?.checks || []).map((c: any, i: number) => (
                    <div key={i} style={{ fontSize: "var(--fs-11)", color: c.ok ? "var(--acc)" : "var(--red)" }}>
                      {c.ok ? "✓" : "✗"} {c.name} <span style={{ color: "var(--t3)" }}>{c.detail}</span>
                    </div>
                  ))}
                </Section>
              )}

              {/* reviews */}
              {sel.reviews && (
                <Section title={`三重复核 ${sel.reviews?.approved ? "（全过）" : "（未通过）"}`}>
                  {(sel.reviews?.reviews || []).map((r: any, i: number) => (
                    <div key={i} style={{ fontSize: "var(--fs-11)", marginBottom: 4 }}>
                      <span style={{ color: r.approve ? "var(--acc)" : "var(--red)" }}>{r.approve ? "批准" : "否决"}</span>
                      {" · "}<b>{r.reviewer}</b>{r.provider && <span style={{ color: "var(--t3)" }}> [{({ "ivyea-agent": "IvyeaAgent", assistant: "全局兜底", deepseek: "DeepSeek", apimart: "Claude", fallback: "兜底", none: "不可用" } as any)[r.provider] || r.provider}]</span>}{" · 风险 "}{Math.round((r.risk_score ?? 1) * 100)}%
                      <div style={{ color: "var(--t3)" }}>{r.reasons}</div>
                    </div>
                  ))}
                </Section>
              )}

              {sel.result?.dry_run && (
                <Section title="预览（将发送的请求）">
                  <pre style={{ fontSize: "var(--fs-10)", color: "var(--t2)", whiteSpace: "pre-wrap" }}>{JSON.stringify(sel.result.body, null, 1)}</pre>
                </Section>
              )}
              {sel.error && <div style={{ color: "var(--red)", fontSize: "var(--fs-11)", margin: "6px 0" }}>错误：{sel.error}</div>}

              {/* actions */}
              <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                <Btn onClick={() => report(sel.id, false)}>预览报告</Btn>
                <Btn onClick={() => report(sel.id, true)}>下载报告</Btn>
                {sel.status === "awaiting_human" && <>
                  <Btn onClick={() => act(sel.id, "confirm", { dry_run: true })} disabled={busy}>预览请求</Btn>
                  <Btn danger onClick={async () => { if (await confirm({ title: "确认执行写操作", message: `将真实写入领星：${sel.intent?.target_name || sel.intent?.target_id}。已通过三重复核 + 护栏，确定执行？`, confirmText: "确认执行", danger: true, icon: "⚠" })) act(sel.id, "confirm", { dry_run: false }, "已执行"); }} disabled={busy || !active}>确认执行</Btn>
                  <Btn onClick={() => act(sel.id, "reject", {}, "已驳回")} disabled={busy}>驳回</Btn>
                  {!active && <span style={{ fontSize: "var(--fs-10)", color: "var(--amber)", alignSelf: "center" }}>需先开启操作开关</span>}
                </>}
                {sel.status === "executed" && <Btn onClick={async () => { if (await confirm({ title: "回滚操作", message: "回滚到执行前的快照状态？", confirmText: "回滚", danger: true, icon: "⚠" })) act(sel.id, "rollback", {}, "已回滚"); }} disabled={busy || !active}>回滚</Btn>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function chipStyle(on: boolean): React.CSSProperties {
  return {
    fontSize: "var(--fs-11)", cursor: "pointer", padding: "2px 8px", borderRadius: 10,
    background: on ? "var(--bg2)" : "transparent", border: "1px solid var(--b)",
    color: on ? "var(--t)" : "var(--t2)",
  };
}
function numField(o: any): [string, any] {
  if (o?.daily_budget != null) return ["预算", o.daily_budget];
  if (o?.bid != null) return ["竞价", o.bid];
  if (o?.defaultBid != null) return ["默认竞价", o.defaultBid];
  return ["", null];
}
function fmtChange(c: any, cur?: Cur) {
  if (!c) return "—";
  const a = []; const [lbl, v] = numField(c);
  if (v != null) a.push(`${lbl}→${fmtBudget(v, cur)}`);
  if (c.state) a.push(`状态→${c.state}`);
  return a.join(" / ") || "—";
}
function fmtState(o: any, cur?: Cur) { if (!o) return "—"; const a = []; if (o.state) a.push(o.state); const [, v] = numField(o); if (v != null) a.push(fmtBudget(v, cur)); return a.join(" / ") || "—"; }
