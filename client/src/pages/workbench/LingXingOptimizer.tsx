import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { sidCurrencyMap, fmtBudget, type Cur } from "./lingxingCurrency";
import SheetSelect from "../../components/SheetSelect";
import { useToast } from "../../components/toast";
import { Btn, LEVER_COLOR, LxProgress, LxTableSkeleton, fmtTs, humanErr, inputStyle, pct0 } from "./lingxingUi";

export default function LingXingOptimizer({ storeSid, onGoTickets }: {
  storeSid?: string; onGoTickets?: (firstId?: string) => void;
}) {
  const sid = storeSid || "";   // store is driven by the page-level selector
  const [sellers, setSellers] = useState<any[]>([]);
  const [days, setDays] = useState(30);
  const [runs, setRuns] = useState<any[]>([]);
  const [runId, setRunId] = useState("");
  const [run, setRun] = useState<any>(null);        // detail incl. progress + result
  const [sel, setSel] = useState<Record<number, boolean>>({});
  const [done, setDone] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [adgroups, setAdgroups] = useState<any[]>([]);
  const [hForm, setHForm] = useState<Record<number, any>>({});
  const toast = useToast();
  const stopRef = useRef(false);
  const setH = (i: number, k: string, v: any) => setHForm((f) => ({ ...f, [i]: { ...f[i], [k]: v } }));
  const cur: Cur | undefined = sidCurrencyMap(sellers)[sid];

  useEffect(() => {
    api.post("/lingxing/read/sellers", { params: {} })
      .then((r) => setSellers(r.data.rows || [])).catch(() => { /* */ });
  }, []);

  /* 店铺切换：拉该店历史运行，默认打开最近一次（结果持久化，刷新不丢） */
  useEffect(() => {
    setRun(null); setRunId(""); setSel({}); setDone({}); setErr("");
    if (!sid) return;
    api.get(`/lingxing/optimizer/runs?sid=${sid}&limit=10`).then((r) => {
      const list = r.data.runs || [];
      setRuns(list);
      if (list[0]) setRunId(list[0].id);
    }).catch(() => setRuns([]));
  }, [storeSid]);

  /* 追踪当前 run：running 时轮询进度，done 后拿到结果 */
  useEffect(() => {
    setRun(null); setSel({}); setDone({});
    if (!runId) return;
    stopRef.current = false;
    async function tick() {
      try {
        const r = (await api.get(`/lingxing/optimizer/runs/${runId}`)).data;
        if (stopRef.current) return;
        setRun(r);
        if (r.status === "running") { setTimeout(tick, 2000); return; }
        if (r.status === "done" && (r.result?.candidates || []).some((c: any) => c.harvest)) void loadDest();
      } catch (e: any) { if (!stopRef.current) setErr(humanErr(e)); }
    }
    void tick();
    return () => { stopRef.current = true; };
  }, [runId]);

  async function start() {
    if (!sid) return;
    setBusy(true); setErr("");
    try {
      const r = await api.post(`/lingxing/optimizer/run?sid=${sid}&days=${days}`);
      toast("info", "优化引擎已在后台运行，可随时切走");
      setRuns((l) => [r.data, ...l]);
      setRunId(r.data.id);
    } catch (e: any) { toast("error", humanErr(e)); }
    finally { setBusy(false); }
  }

  async function loadDest() {
    try {
      const [cp, ag] = await Promise.all([
        api.post("/lingxing/read/sp_campaigns", { params: { sid: Number(sid), length: 300 } }),
        api.post("/lingxing/read/sp_adgroups", { params: { sid: Number(sid), length: 300 } }),
      ]);
      setCampaigns((cp.data.rows || []).filter((c: any) => c.targeting_type === "manual"));
      setAdgroups(ag.data.rows || []);
    } catch { /* */ }
  }

  const cands: any[] = run?.status === "done" ? (run.result?.candidates || []) : [];
  const batchable = (i: number) => !!cands[i]?.payload && !done[i];
  const selected = Object.keys(sel).filter((k) => sel[Number(k)] && batchable(Number(k))).map(Number);
  const allBatchable = cands.map((_, i) => i).filter(batchable);

  async function makeBatch() {
    if (!selected.length) return;
    setBusy(true);
    try {
      const r = await api.post("/lingxing/operate/batch-tickets", { payloads: selected.map((i) => cands[i].payload) });
      const ids: string[] = r.data.tickets || [];
      setDone((d) => { const n = { ...d }; selected.forEach((i, j) => { if (ids[j]) n[i] = ids[j]; }); return n; });
      setSel({});
      for (const e of r.data.errors || []) toast("warn", `「${e.target || "?"}」创建失败：${e.error}`);
      if (ids.length) {
        toast("success", `已创建 ${ids.length} 张工单，三重复核后台进行中`);
        onGoTickets?.(ids[0]);
      }
    } catch (e: any) { toast("error", humanErr(e)); }
    finally { setBusy(false); }
  }

  async function makeOne(c: any, i: number) {
    try {
      const r = await api.post("/lingxing/operate/manual", c.payload);
      setDone((d) => ({ ...d, [i]: r.data.id }));
      toast("success", `工单 ${r.data.id} 已进入后台复核`);
    } catch (e: any) { toast("error", humanErr(e)); }
  }
  async function makeHarvest(c: any, i: number) {
    const h = hForm[i] || {};
    if (!h.campaign_id || !h.ad_group_id) return;
    try {
      const r = await api.post("/lingxing/operate/manual", {
        op_type: "add_keyword", sid: Number(sid), campaign_id: h.campaign_id, ad_group_id: h.ad_group_id,
        keyword_text: c.harvest.query, match_type: "EXACT", bid: Number(h.bid ?? c.harvest.suggested_bid),
        rationale: `收割：搜索词「${c.harvest.query}」已 ${c.metrics?.orders} 单，加入精准活动，建议bid ${c.harvest.suggested_bid}`,
        opt: { lever: "收割", rule: c.rule, significance: c.significance, metrics: c.metrics, target_acos: c.opt_target, breakeven_acos: c.opt_breakeven },
      });
      setDone((d) => ({ ...d, [i]: r.data.id }));
      toast("success", `收割工单 ${r.data.id} 已进入后台复核`);
    } catch (e: any) { toast("error", humanErr(e)); }
  }

  const running = run?.status === "running";
  const data = run?.status === "done" ? run.result : null;

  return (
    <div>
      {/* control bar */}
      <div className="card" style={{ padding: 12, marginBottom: 10, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>店铺：{sellers.find((s) => String(s.sid) === sid)?.name || sid || "（上方选择）"}</span>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>窗口</span>
        <SheetSelect value={String(days)} onChange={(v) => setDays(Number(v))} title="时间窗口" style={{ ...inputStyle, width: 100 }}
          options={[14, 30, 60].map((d) => ({ value: String(d), label: `近 ${d} 天` }))} />
        <Btn primary onClick={start} disabled={busy || running || !sid}>{running ? "运行中…" : "运行优化引擎"}</Btn>
        {runs.length > 0 && (
          <SheetSelect value={runId} onChange={setRunId} title="历史运行" placeholder="历史运行" style={{ ...inputStyle, minWidth: 190 }}
            options={runs.map((r) => ({ value: String(r.id), label: `${fmtTs(r.started_at)} · ${r.status === "done" ? (r.summary?.split("·")[0] || "完成") : r.status === "failed" ? "失败" : "运行中"}` }))} />
        )}
        {running && <LxProgress phase={run.phase} done={run.done} total={run.total} />}
        {err && <span style={{ fontSize: "var(--fs-11)", color: "var(--red)" }}>{err}</span>}
      </div>

      {run?.status === "failed" && (
        <div className="card" style={{ padding: 12, marginBottom: 10, fontSize: "var(--fs-11)", color: "var(--red)" }}>运行失败：{run.error}</div>
      )}

      {running && <div className="card" style={{ marginBottom: 10 }}><LxTableSkeleton lines={6} /></div>}

      {data && (
        <div className="card wb-enter" style={{ padding: "8px 12px", marginBottom: 10, fontSize: "var(--fs-11)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span><b>{data.note}</b> · 候选 <b>{data.count}</b> 条 · 窗口 {data.window_days} 天（已剔除近 2 天）</span>
          {allBatchable.length > 0 && (
            <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8, alignItems: "center" }}>
              <label style={{ display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer", fontSize: "var(--fs-10)", color: "var(--t3)" }}>
                <input type="checkbox" checked={allBatchable.length > 0 && selected.length === allBatchable.length}
                  onChange={(e) => { const n: Record<number, boolean> = {}; if (e.target.checked) allBatchable.forEach((i) => { n[i] = true; }); setSel(n); }} />
                全选（收割除外）
              </label>
              <Btn primary onClick={makeBatch} disabled={busy || !selected.length}>生成所选工单（{selected.length}）</Btn>
            </span>
          )}
        </div>
      )}

      {data && cands.length === 0 && (
        <div className="card wb-enter" style={{ padding: 30, textAlign: "center", color: "var(--t3)", fontSize: "var(--fs-11)" }}>窗口内无达阈值的优化候选（数据不足或表现平稳）。</div>
      )}

      {data && cands.map((c: any, i: number) => (
        <div key={i} className="card wb-enter" style={{ padding: 10, marginBottom: 8, borderLeft: `3px solid ${LEVER_COLOR[c.lever] || "var(--b)"}` }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            {batchable(i) && (
              <input type="checkbox" checked={!!sel[i]} onChange={(e) => setSel((s) => ({ ...s, [i]: e.target.checked }))} title="加入批量生成" />
            )}
            <span style={{ fontSize: "var(--fs-11)", fontWeight: 600, color: LEVER_COLOR[c.lever] }}>{c.lever}</span>
            <b style={{ fontSize: "var(--fs-12)" }}>{c.target_name}</b>
            {c.current && c.proposed && (
              <span style={{ fontSize: "var(--fs-11)", color: "var(--t2)" }}>
                {fmtVal(c.current, cur)} → <b>{fmtVal(c.proposed, cur)}</b>
                {c.change_pct != null && <span style={{ color: "var(--t3)" }}> ({c.change_pct}%)</span>}
              </span>
            )}
            <span style={{ marginLeft: "auto" }}>
              {c.harvest
                ? (done[i]
                  ? <span style={{ fontSize: "var(--fs-10)", color: "var(--acc)" }}>✓ 工单 {done[i]}</span>
                  : <span style={{ display: "inline-flex", gap: 5, alignItems: "center", flexWrap: "wrap" }}>
                      <SheetSelect value={String(hForm[i]?.campaign_id || "")} onChange={(v) => setH(i, "campaign_id", v)} title="目标活动(manual)" placeholder="目标活动" style={{ ...inputStyle, maxWidth: 140 }}
                        options={campaigns.map((cp) => ({ value: String(cp.campaign_id), label: String(cp.name || cp.campaign_id) }))} />
                      <SheetSelect value={String(hForm[i]?.ad_group_id || "")} onChange={(v) => setH(i, "ad_group_id", v)} title="广告组" placeholder="广告组" style={{ ...inputStyle, maxWidth: 120 }}
                        options={adgroups.filter((a) => String(a.campaign_id) === String(hForm[i]?.campaign_id)).map((a) => ({ value: String(a.ad_group_id), label: String(a.name || a.ad_group_id) }))} />
                      <input value={hForm[i]?.bid ?? c.harvest.suggested_bid} onChange={(e) => setH(i, "bid", e.target.value)} style={{ ...inputStyle, width: 64 }} title="精准词bid" />
                      <Btn onClick={() => makeHarvest(c, i)} disabled={!hForm[i]?.campaign_id || !hForm[i]?.ad_group_id}>生成工单</Btn>
                    </span>)
                : done[i]
                  ? <span style={{ fontSize: "var(--fs-10)", color: "var(--acc)" }}>✓ 工单 {done[i]}</span>
                  : <Btn onClick={() => makeOne(c, i)}>生成工单</Btn>}
            </span>
          </div>
          <div style={{ fontSize: "var(--fs-11)", color: "var(--t2)", marginTop: 4 }}>{c.rule}</div>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginTop: 2 }}>
            显著性：{c.significance} · 花费 {fmtBudget(c.metrics?.spend, cur)} · 销售 {fmtBudget(c.metrics?.sales, cur)} · ACOS {pct0(c.metrics?.acos)} · 订单 {c.metrics?.orders} · 点击 {c.metrics?.clicks}
          </div>
        </div>
      ))}
    </div>
  );
}

function fmtVal(o: any, cur?: Cur) {
  if (o?.bid != null) return fmtBudget(o.bid, cur);
  if (o?.daily_budget != null) return fmtBudget(o.daily_budget, cur);
  if (o?.defaultBid != null) return fmtBudget(o.defaultBid, cur);
  return "—";
}
