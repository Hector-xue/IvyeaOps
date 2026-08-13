import { useCallback, useEffect, useRef, useState } from "react";
import { useConfirm } from "../../components/ConfirmDialog";
import SheetSelect from "../../components/SheetSelect";
import { marketplaceOptions } from "../../lib/marketplaces";
import {
  auditDownloadUrl,
  auditGet,
  auditList,
  auditRunners,
  auditStart,
  auditClearFailed,
  auditDelete,
  type AuditFull,
  type AuditJobMeta,
  type AuditStructured,
  type CosmoNode,
  type EvidenceGroup,
  type RufusQA,
  type RunnerName,
  type RunnerStatus,
} from "../../api/client";
import AdAuditPanel from "./AdAuditPanel";
import DeepAnalysis from "./DeepAnalysis";
import DeepAnalysisPanel from "../../components/DeepAnalysisPanel";
import { errText } from "../../lib/errText";

const MARKETPLACES = ["US", "UK", "DE", "FR", "CA", "JP", "ES", "IT", "MX", "AU", "AE", "BR", "SA"];

type PollState =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "polling"; jobId: string; data: AuditFull | null }
  | { kind: "done"; jobId: string; data: AuditFull }
  | { kind: "failed"; jobId: string; data: AuditFull; error: string };

export default function Tools() {
  const confirm = useConfirm();
  return (
    <div>
      <div className="ptitle">/ 分析工具</div>

      {/* Primary tool: full-width ASIN audit */}
      <AsinAuditPanel />

      {/* Secondary: ad search-term report audit */}
      <AdAuditPanel />

      {/* Deep Analysis Tools */}
      <div style={{ marginTop: 18 }}>
        <DeepAnalysis />
      </div>
    </div>
  );
}

/* ===================== ASIN Audit Panel ===================== */

function AsinAuditPanel() {
  const confirm = useConfirm();
  const [asin, setAsin] = useState("");
  const [marketplace, setMarketplace] = useState("US");
  const [mode, setMode] = useState<"full" | "rewrite_only">("full");
  const [runner, setRunner] = useState<RunnerName>("auto");
  const [runners, setRunners] = useState<RunnerStatus[]>([]);
  const [state, setState] = useState<PollState>({ kind: "idle" });
  const [history, setHistory] = useState<AuditJobMeta[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [viewingJobId, setViewingJobId] = useState<string | null>(null);
  const pollTimer = useRef<number | null>(null);

  const loadHistory = useCallback(async () => {
    try {
      const r = await auditList(20);
      setHistory(r.items);
    } catch {
      /* ignore */
    }
  }, []);

  const loadRunners = useCallback(async () => {
    try {
      const rs = await auditRunners();
      setRunners(rs);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadHistory();
    loadRunners();
  }, [loadHistory, loadRunners]);

  // Polling loop
  const startPolling = useCallback((jobId: string) => {
    const tick = async () => {
      try {
        const data = await auditGet(jobId);
        if (data.status === "done") {
          setState({ kind: "done", jobId, data });
          loadHistory();
          return;
        }
        if (data.status === "failed" || data.status === "cancelled") {
          setState({
            kind: "failed",
            jobId,
            data,
            error: data.error || data.status,
          });
          loadHistory();
          return;
        }
        setState({ kind: "polling", jobId, data });
        pollTimer.current = window.setTimeout(tick, 3000);
      } catch (e: any) {
        setState({
          kind: "failed",
          jobId,
          data: { job_id: jobId } as AuditFull,
          error: errText(e, "轮询失败"),
        });
      }
    };
    tick();
  }, [loadHistory]);

  useEffect(() => () => {
    if (pollTimer.current) window.clearTimeout(pollTimer.current);
  }, []);

  const onStart = async () => {
    const val = asin.trim().toUpperCase();
    if (!/^[A-Z0-9]{10}$/.test(val)) {
      alert("ASIN 必须是 10 位字母数字");
      return;
    }
    setState({ kind: "starting" });
    try {
      const r = await auditStart(val, marketplace, mode, runner);
      setViewingJobId(r.job_id);
      startPolling(r.job_id);
    } catch (e: any) {
      alert(errText(e, "启动失败"));
      setState({ kind: "idle" });
    }
  };

  const openHistoryItem = async (jobId: string) => {
    if (pollTimer.current) window.clearTimeout(pollTimer.current);
    setViewingJobId(jobId);
    setHistoryOpen(false);
    try {
      const data = await auditGet(jobId);
      if (data.status === "running" || data.status === "queued") {
        setState({ kind: "polling", jobId, data });
        startPolling(jobId);
      } else if (data.status === "done") {
        setState({ kind: "done", jobId, data });
      } else {
        setState({ kind: "failed", jobId, data, error: data.error || data.status });
      }
    } catch (e: any) {
      alert("加载任务失败");
    }
  };

  const isRunning = state.kind === "starting" || state.kind === "polling";
  const currentData = state.kind !== "idle" && state.kind !== "starting" ? state.data : null;

  return (
    <div className="card" style={{ padding: "14px 16px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <span className="tag tg">主力工具</span>
        <span style={{ fontSize: "var(--fs-13)", color: "var(--t)" }}>ASIN 深度审计</span>
        <span style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>
          · 多维度分析 + 广告方案
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button
            className="tbtn"
            onClick={() => {
              setHistoryOpen((v) => !v);
              if (!historyOpen) loadHistory();
            }}
          >
            📜 历史 ({history.length})
          </button>
        </div>
      </div>

      {/* Input row */}
      <div data-tour="tools-asin" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          className="inp"
          placeholder="B0XXXXXXXX"
          value={asin}
          maxLength={10}
          style={{ width: 140, fontFamily: "monospace", letterSpacing: "0.05em" }}
          onChange={(e) => setAsin(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && !isRunning && onStart()}
          disabled={isRunning}
        />
        <SheetSelect
          className="inp"
          value={marketplace}
          onChange={setMarketplace}
          disabled={isRunning}
          style={{ width: 74 }}
          flags
          title="选择站点"
          options={marketplaceOptions(MARKETPLACES)}
        />
        <SheetSelect
          className="inp"
          value={mode}
          onChange={(v) => setMode(v as "full" | "rewrite_only")}
          disabled={isRunning}
          style={{ width: 140 }}
          title="分析模式"
          options={[
            { value: "full", label: "完整 11 板块" },
            { value: "rewrite_only", label: "精简 + 改写稿" },
          ]}
        />
        <SheetSelect
          className="inp"
          value={runner}
          onChange={(v) => setRunner(v as RunnerName)}
          disabled={isRunning || runners.length === 0}
          title="选择执行审计的智能体"
          style={{ width: 180 }}
          options={
            runners.length === 0
              ? [{ value: "auto", label: "自动" }]
              : runners.map((r) => ({
                  value: r.name,
                  label: `${r.available ? "" : "不可用 · "}${r.label}${!r.available && r.reason ? `（${r.reason}）` : ""}`,
                  disabled: !r.available,
                }))
          }
        />
        {(() => {
          const r = runners.find((x) => x.name === runner);
          if (r && r.available && r.data_source_ready === false && r.data_source_hint) {
            return <span style={{ fontSize: "var(--fs-10)", color: "var(--amber)", maxWidth: 260, lineHeight: 1.4 }}>⚠ {r.data_source_hint}</span>;
          }
          return null;
        })()}
        <button className="tbtn" onClick={onStart} disabled={isRunning}>
          {isRunning ? (
            <><span className="spin" /> 分析中…</>
          ) : (
            "🚀 启动审计"
          )}
        </button>
        <span style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>预计 5–15 分钟</span>
      </div>

      {/* History dropdown */}
      {historyOpen && (
        <div
          style={{
            marginTop: 10,
            border: "1px solid var(--b)",
            borderRadius: "var(--r)",
            maxHeight: 240,
            overflowY: "auto",
          }}
        >
          {history.length === 0 ? (
            <div style={{ padding: 12, fontSize: "var(--fs-10)", color: "var(--t3)" }}>暂无历史任务</div>
          ) : (
            <>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                padding: "6px 8px",
                borderBottom: "1px solid var(--b)",
                background: "var(--bg3)",
                position: "sticky",
                top: 0,
                zIndex: 1,
              }}
            >
              <span style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>
                共 {history.length} 条，失败 {history.filter((h) => h.status === "failed" || h.status === "cancelled").length} 条
              </span>
              <button
                className="tbtn"
                style={{ marginLeft: "auto" }}
                disabled={!history.some((h) => h.status === "failed" || h.status === "cancelled")}
                onClick={async () => {
                  if (!await confirm({ title: "清空记录", message: "确定清空所有失败/已取消的任务记录？此操作不可撤销。", confirmText: "清空", danger: true })) return;
                  try {
                    const r = await auditClearFailed();
                    await loadHistory();
                    alert(`已清除 ${r.removed} 条失败记录`);
                  } catch (e: any) {
                    alert(errText(e, "清除失败"));
                  }
                }}
              >
                🗑 清空失败
              </button>
            </div>
            <table className="tbl">
              <thead>
                <tr>
                  <th>ASIN</th>
                  <th>站点</th>
                  <th>状态</th>
                  <th>时间</th>
                  <th style={{ width: 60 }}></th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.job_id}>
                    <td style={{ fontFamily: "monospace" }}>{h.asin}</td>
                    <td>{h.marketplace}</td>
                    <td><StatusTag status={h.status} /></td>
                    <td style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>
                      {new Date(h.created_at).toLocaleString("zh-CN")}
                    </td>
                    <td>
                      <button className="tbtn" onClick={() => openHistoryItem(h.job_id)}>
                        查看
                      </button>
                      <button className="tbtn" style={{ marginLeft: 4, color: "var(--red)" }} onClick={async () => {
                        if (!await confirm({ title: "删除记录", message: `删除 ${h.asin} 的审计记录？`, confirmText: "删除", danger: true })) return;
                        try { await auditDelete(h.job_id); loadHistory(); } catch { alert("删除失败"); }
                      }}>✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </>
          )}
        </div>
      )}

      {/* Status / progress */}
      {isRunning && (
        <div
          style={{
            marginTop: 12,
            padding: 10,
            background: "var(--bg3)",
            border: "1px solid var(--b)",
            borderRadius: "var(--r)",
          }}
        >
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 4 }}>
            <span className="spin" style={{ marginRight: 6 }} />
            {state.kind === "starting" ? "正在启动…" : state.data?.progress || "分析中…"}
          </div>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>
            {state.kind === "polling" && state.data?.started_at && (
              <>启动时间：{new Date(state.data.started_at).toLocaleTimeString("zh-CN")}</>
            )}
          </div>
        </div>
      )}

      {/* Failure */}
      {state.kind === "failed" && (
        <div
          style={{
            marginTop: 12,
            padding: 10,
            background: "rgba(248,113,113,.08)",
            border: "1px solid rgba(248,113,113,.25)",
            borderRadius: "var(--r)",
            color: "var(--red)",
            fontSize: "var(--fs-11)",
          }}
        >
          ✗ {state.error}
        </div>
      )}

      {/* Result panel */}
      {currentData && state.kind === "done" && (
        <ResultPanel data={currentData} onCollapse={() => setState({ kind: "idle" })} />
      )}
      {state.kind === "done" && state.data.raw_md && (
        <AsinDeepAnalysisPanel data={state.data} />
      )}
      {currentData && state.kind === "polling" && currentData.raw_md && (
        <div style={{ marginTop: 12 }}>
          <div className="ct">实时输出片段</div>
          <pre
            style={{
              maxHeight: 200,
              overflow: "auto",
              fontSize: "var(--fs-10)",
              padding: 10,
              background: "var(--bg3)",
              border: "1px solid var(--b)",
              borderRadius: "var(--r)",
              whiteSpace: "pre-wrap",
            }}
          >{currentData.raw_md.slice(-3000)}</pre>
        </div>
      )}
    </div>
  );
}

/* ===================== ASIN deep analysis panel ===================== */


const ASIN_ANALYSIS_TYPES = [
  {
    id: "listing",
    icon: "◈",
    label: "Listing 优化",
    promptFn: (asin: string, mkt: string, report: string) =>
      `以下是 ASIN ${asin}（${mkt} 站）的深度审计报告：\n\n${report}\n\n请基于此报告给出 Listing 优化方案：\n1. 标题优化建议（关键词布局、核心卖点排序）\n2. 五点描述改写方向\n3. Q&A / 评论痛点转化为卖点的机会\n4. 图片方案优先级建议`,
  },
  {
    id: "competition",
    icon: "⬡",
    label: "竞品策略",
    promptFn: (asin: string, mkt: string, report: string) =>
      `以下是 ASIN ${asin}（${mkt} 站）的深度审计报告：\n\n${report}\n\n请基于此报告分析竞争格局并制定差异化策略：\n1. 当前产品相对竞品的优劣势\n2. 竞品流量来源与关键词差距\n3. 差异化切入点（功能 / 价格 / 包装 / 用户群）\n4. 短中长期竞争应对路径`,
  },
  {
    id: "ads",
    icon: "▦",
    label: "广告布局",
    promptFn: (asin: string, mkt: string, report: string) =>
      `以下是 ASIN ${asin}（${mkt} 站）的深度审计报告：\n\n${report}\n\n请基于此报告制定广告投放策略：\n1. 核心词 / 长尾词 / 竞品 ASIN 投放优先级\n2. 活动结构建议（SP / SB / SD 分层）\n3. 预算分配与竞价策略建议\n4. 广告与 Listing 协同优化要点`,
  },
];

// Post-audit deep-analysis — uses the shared panel for consistency with
// 市场调研 / 打法推荐 (report handed off as a document to the agents session).
function AsinDeepAnalysisPanel({ data }: { data: AuditFull }) {
  if (!data.raw_md) return null;
  return (
    <DeepAnalysisPanel
      types={ASIN_ANALYSIS_TYPES}
      query={data.asin}
      marketplace={data.marketplace}
      report={data.raw_md}
      slug={data.asin}
    />
  );
}

/* ===================== Result tables ===================== */

function ResultPanel({ data, onCollapse }: { data: AuditFull; onCollapse: () => void }) {
  const s = data.structured || {};
  const hasStructured =
    s.overview || s.scorecard?.length || s.priorities?.length || s.ad_plan || s.rewrites
    || s.semantic_blind_spots?.length || s.cosmo_nodes?.length || s.rufus_qa?.length
    || s.behavior_signals?.length || s.competitor_diff?.length;

  return (
    <div style={{ marginTop: 14 }}>
      {/* Header + downloads */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 10,
          paddingBottom: 8,
          borderBottom: "1px solid var(--b)",
        }}
      >
        <span className="tag tg">✓ 完成</span>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--t)" }}>
          {data.asin} · {data.marketplace}
        </span>
        <span style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>
          {data.finished_at && `· ${new Date(data.finished_at).toLocaleString("zh-CN")}`}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <a
            className="tbtn"
            href={auditDownloadUrl(data.job_id, "xlsx")}
            download
            title="多 sheet Excel 表格，可直接在 Excel / WPS 打开"
            style={{ textDecoration: "none" }}
          >
            📊 Excel
          </a>
          <a
            className="tbtn"
            href={auditDownloadUrl(data.job_id, "html")}
            download
            title="单文件网页版，内嵌样式，浏览器直接打开，便于转发"
            style={{ textDecoration: "none" }}
          >
            🌐 HTML
          </a>
          <a
            className="tbtn"
            href={auditDownloadUrl(data.job_id, "md")}
            download
            style={{ textDecoration: "none" }}
          >
            📄 Markdown
          </a>
          <a
            className="tbtn"
            href={auditDownloadUrl(data.job_id, "json")}
            download
            style={{ textDecoration: "none" }}
          >
            🧾 JSON
          </a>
          <button className="tbtn" onClick={onCollapse}>↑ 收起</button>
        </div>
      </div>

      {!hasStructured ? (
        <div
          style={{
            padding: 10,
            fontSize: "var(--fs-10)",
            color: "var(--t3)",
            background: "var(--bg3)",
            border: "1px solid var(--b)",
            borderRadius: "var(--r)",
            marginBottom: 10,
          }}
        >
          ⚠ 未能解析结构化数据，仅展示原始 markdown（可下载查看）
        </div>
      ) : (
        <>
          {/* Component 1: ListingProfile — 基础画像 + 评分卡 + 文案/Q&A/Reviews/竞品 */}
          <ListingProfile
            overview={s.overview}
            scorecard={s.scorecard}
            semanticBlindSpots={s.semantic_blind_spots}
            cosmoNodes={s.cosmo_nodes}
            rufusQa={s.rufus_qa}
            behaviorSignals={s.behavior_signals}
            competitorDiff={s.competitor_diff}
            rewrites={s.rewrites}
          />

          {/* Component 2: VisualReport — 视觉与图片方案 */}
          {s.rewrites?.image_plan && <VisualReport imagePlan={s.rewrites.image_plan} />}

          {/* Component 3: TrafficStructureChart — 流量结构 */}
          {s.scorecard && s.scorecard.length > 0 && <TrafficStructureChart scorecard={s.scorecard} />}

          {/* Component 4: KeywordPositionMatrix — 关键词阵地 */}
          {s.ad_plan && <KeywordPositionMatrix adPlan={s.ad_plan} />}

          {/* Component 5: RiskSignalBoard — 风险信号 */}
          {s.priorities && s.priorities.length > 0 && <RiskSignalBoard priorities={s.priorities} />}
        </>
      )}

      {/* Raw markdown collapsed */}
      {data.raw_md && (
        <details style={{ marginTop: 14 }}>
          <summary style={{ fontSize: "var(--fs-10)", color: "var(--t3)", cursor: "pointer" }}>
            查看原始 Markdown 报告
          </summary>
          <pre
            style={{
              marginTop: 6,
              maxHeight: 500,
              overflow: "auto",
              fontSize: "var(--fs-10)",
              padding: 10,
              background: "var(--bg3)",
              border: "1px solid var(--b)",
              borderRadius: "var(--r)",
              whiteSpace: "pre-wrap",
            }}
          >{data.raw_md}</pre>
        </details>
      )}
    </div>
  );
}

function SectionTitle({ icon, children }: { icon: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: "var(--fs-11)",
        color: "var(--t)",
        margin: "12px 0 6px",
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}
    >
      <span>{icon}</span>
      <span>{children}</span>
    </div>
  );
}

function OverviewTable({ ov }: { ov: NonNullable<AuditStructured["overview"]> }) {
  const rows: [string, string | undefined][] = [
    ["类目 / 产品类型", ov.category],
    ["标题摘要", ov.title_summary],
    ["核心规格", ov.key_specs],
    ["最大风险", ov.top_risk],
  ];
  return (
    <>
      <SectionTitle icon="📋">产品概览</SectionTitle>
      <table className="tbl">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <td style={{ width: 120, color: "var(--t3)" }}>{k}</td>
              <td>{v || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Scorecard({ items }: { items: NonNullable<AuditStructured["scorecard"]> }) {
  return (
    <>
      <SectionTitle icon="📊">7 维评分卡</SectionTitle>
      <table className="tbl">
        <thead>
          <tr>
            <th>维度</th>
            <th style={{ width: 80 }}>得分 / 10</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it) => (
            <tr key={it.dimension}>
              <td>{it.dimension}</td>
              <td>
                <ScoreBar score={it.score} />
              </td>
              <td style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>{it.note || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function ScoreBar({ score }: { score: number }) {
  const s = Math.max(0, Math.min(10, Number(score) || 0));
  const pct = s * 10;
  const color =
    s >= 8 ? "var(--acc)" : s >= 5 ? "var(--amber)" : "var(--red)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div
        style={{
          width: 50,
          height: 5,
          background: "var(--bg3)",
          borderRadius: 2,
          overflow: "hidden",
        }}
      >
        <div style={{ width: `${pct}%`, height: "100%", background: color }} />
      </div>
      <span style={{ fontSize: "var(--fs-10)", color, minWidth: 22 }}>{s.toFixed(1)}</span>
    </div>
  );
}

function PriorityTable({ items }: { items: NonNullable<AuditStructured["priorities"]> }) {
  const tagCls = (lv: string) =>
    lv === "P0" ? "tr" : lv === "P1" ? "ta" : "tp";
  return (
    <>
      <SectionTitle icon="🎯">改进优先级</SectionTitle>
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 38 }}>级别</th>
            <th>问题</th>
            <th>证据</th>
            <th>动作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p, i) => (
            <tr key={i}>
              <td><span className={"tag " + tagCls(p.level)}>{p.level}</span></td>
              <td>{p.issue}</td>
              <td style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>{p.evidence}</td>
              <td style={{ color: "var(--t)" }}>{p.action}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function AdPlanSection({ ad }: { ad: NonNullable<AuditStructured["ad_plan"]> }) {
  return (
    <>
      <SectionTitle icon="📣">广告搭建建议</SectionTitle>
      {ad.objective && (
        <div style={{ fontSize: "var(--fs-11)", color: "var(--t2)", marginBottom: 6 }}>
          <span style={{ color: "var(--t3)" }}>目标：</span>
          {ad.objective}
        </div>
      )}

      {ad.campaigns && ad.campaigns.length > 0 && (
        <table className="tbl" style={{ marginBottom: 10 }}>
          <thead>
            <tr>
              <th>活动</th>
              <th>类型</th>
              <th>定向</th>
              <th>竞价区间</th>
              <th>日预算</th>
              <th>策略</th>
            </tr>
          </thead>
          <tbody>
            {ad.campaigns.map((c, i) => (
              <tr key={i}>
                <td>{c.name}</td>
                <td><span className="tag tb-tag">{c.type}</span></td>
                <td style={{ fontSize: "var(--fs-10)" }}>{c.targeting}</td>
                <td>{c.bid_range}</td>
                <td>{c.budget}</td>
                <td style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>{c.strategy}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {ad.keywords_exact && ad.keywords_exact.length > 0 && (
        <>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 4 }}>精准关键词（Exact）</div>
          <KeywordTable items={ad.keywords_exact} />
        </>
      )}

      {ad.keywords_phrase_broad && ad.keywords_phrase_broad.length > 0 && (
        <>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", margin: "8px 0 4px" }}>扩量关键词（Phrase / Broad）</div>
          <KeywordTable items={ad.keywords_phrase_broad} />
        </>
      )}

      {(ad.negatives_immediate?.length || ad.negatives_watch?.length) && (
        <div style={{ display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
          {ad.negatives_immediate && ad.negatives_immediate.length > 0 && (
            <NegBox title="立即否定" color="var(--red)" items={ad.negatives_immediate} />
          )}
          {ad.negatives_watch && ad.negatives_watch.length > 0 && (
            <NegBox title="观察后否定" color="var(--amber)" items={ad.negatives_watch} />
          )}
        </div>
      )}

      {ad.rules && (
        <div style={{ marginTop: 8, fontSize: "var(--fs-10)", color: "var(--t3)", lineHeight: 1.6 }}>
          <b style={{ color: "var(--t2)" }}>调价规则：</b>
          {ad.rules}
        </div>
      )}
    </>
  );
}

function KeywordTable({ items }: { items: { keyword: string; bid: string; reason: string }[] }) {
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th>关键词</th>
          <th style={{ width: 80 }}>建议竞价</th>
          <th>原因</th>
        </tr>
      </thead>
      <tbody>
        {items.map((k, i) => (
          <tr key={i}>
            <td style={{ fontFamily: "monospace", fontSize: "var(--fs-10)" }}>{k.keyword}</td>
            <td>{k.bid}</td>
            <td style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>{k.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function NegBox({ title, color, items }: { title: string; color: string; items: unknown[] }) {
  // Tolerate both shapes: string[] (schema) and {keyword, bid?, reason?}[] (Claude
  // sometimes mirrors the keyword-row schema). React error #31 fires if we render
  // an object directly, so we normalize before mapping.
  const normalize = (it: unknown): { label: string; tip?: string } => {
    if (typeof it === "string") return { label: it };
    if (it && typeof it === "object") {
      const o = it as Record<string, unknown>;
      const label = (o.keyword || o.term || o.text || o.label || "") as string;
      const tip = (o.reason || o.note || "") as string;
      return { label: label || JSON.stringify(o), tip: tip || undefined };
    }
    return { label: String(it ?? "") };
  };
  return (
    <div
      style={{
        flex: 1,
        minWidth: 200,
        padding: 8,
        border: "1px solid var(--b)",
        borderLeft: `2px solid ${color}`,
        borderRadius: "var(--r)",
      }}
    >
      <div style={{ fontSize: "var(--fs-10)", color, marginBottom: 4 }}>{title}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {items.map((raw, i) => {
          const { label, tip } = normalize(raw);
          return (
            <span
              key={i}
              title={tip}
              style={{
                fontSize: "var(--fs-10)",
                padding: "1px 6px",
                background: "var(--bg3)",
                borderRadius: 3,
                color: "var(--t2)",
                cursor: tip ? "help" : "default",
              }}
            >{label}</span>
          );
        })}
      </div>
    </div>
  );
}

function RewriteSection({ rw }: { rw: NonNullable<AuditStructured["rewrites"]> }) {
  const copy = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  return (
    <>
      <SectionTitle icon="✍️">改写稿</SectionTitle>

      {rw.title && (
        <div
          style={{
            padding: 8,
            background: "var(--bg3)",
            border: "1px solid var(--b)",
            borderRadius: "var(--r)",
            marginBottom: 8,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <span style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>TITLE</span>
            <button
              className="tbtn"
              style={{ marginLeft: "auto", padding: "0 6px" }}
              onClick={() => copy(rw.title || "")}
            >复制</button>
          </div>
          <div style={{ fontSize: "var(--fs-11)", color: "var(--t)", lineHeight: 1.6 }}>{rw.title}</div>
        </div>
      )}

      {rw.bullets && rw.bullets.length > 0 && (
        <div
          style={{
            padding: 8,
            background: "var(--bg3)",
            border: "1px solid var(--b)",
            borderRadius: "var(--r)",
            marginBottom: 8,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <span style={{ fontSize: "var(--fs-10)", color: "var(--t3)" }}>BULLETS</span>
            <button
              className="tbtn"
              style={{ marginLeft: "auto", padding: "0 6px" }}
              onClick={() => copy((rw.bullets || []).join("\n"))}
            >复制</button>
          </div>
          <ol style={{ margin: 0, paddingLeft: 20, fontSize: "var(--fs-11)", lineHeight: 1.7 }}>
            {rw.bullets.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ol>
        </div>
      )}

      {rw.qa && rw.qa.length > 0 && (
        <>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", margin: "8px 0 4px" }}>Q&A</div>
          <table className="tbl">
            <tbody>
              {rw.qa.map((q, i) => (
                <tr key={i}>
                  <td style={{ width: 30, color: "var(--t3)" }}>Q{i + 1}</td>
                  <td>
                    <div style={{ color: "var(--t2)" }}>{q.q}</div>
                    <div style={{ color: "var(--t3)", fontSize: "var(--fs-10)", marginTop: 2 }}>{q.a}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {rw.backend_terms && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 4 }}>BACKEND SEARCH TERMS</div>
          <div
            style={{
              padding: 8,
              background: "var(--bg3)",
              border: "1px solid var(--b)",
              borderRadius: "var(--r)",
              fontSize: "var(--fs-10)",
              fontFamily: "monospace",
              color: "var(--t2)",
              wordBreak: "break-all",
            }}
          >{rw.backend_terms}</div>
        </div>
      )}

      {rw.image_plan && (
        <ImagePlanBlock plan={rw.image_plan} />
      )}

      {rw.aplus_plan && rw.aplus_plan.length > 0 && (
        <ListBlock title="A+ 页面方案" items={rw.aplus_plan} />
      )}

      {rw.compliance_reminders && rw.compliance_reminders.length > 0 && (
        <ListBlock title="合规提醒" items={rw.compliance_reminders} accent="var(--red)" />
      )}
    </>
  );
}

function ImagePlanBlock({ plan }: { plan: NonNullable<NonNullable<AuditStructured["rewrites"]>["image_plan"]> }) {
  const groups: [string, string[] | undefined][] = [
    ["主图优化", plan.main_image],
    ["辅图卖点（≥6）", plan.aux_images],
    ["应用场景（≥3）", plan.scene_images],
  ];
  return (
    <>
      <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", margin: "10px 0 4px" }}>图片卖点</div>
      {groups.map(([title, items]) =>
        items && items.length > 0 ? (
          <ListBlock key={title} title={title} items={items} />
        ) : null,
      )}
    </>
  );
}

function ListBlock({
  title,
  items,
  accent,
}: {
  title: string;
  items: string[];
  accent?: string;
}) {
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ fontSize: "var(--fs-10)", color: accent || "var(--t3)", marginBottom: 3 }}>{title}</div>
      <ol
        style={{
          margin: 0,
          paddingLeft: 20,
          fontSize: "var(--fs-10)",
          lineHeight: 1.7,
          color: "var(--t2)",
        }}
      >
        {items.map((x, i) => (
          <li key={i}>{x}</li>
        ))}
      </ol>
    </div>
  );
}

/* ===================== Small UI atoms ===================== */

function StatusTag({ status }: { status: string }) {
  const cls =
    status === "done" ? "tg" :
    status === "failed" ? "tr" :
    status === "running" ? "tb-tag" :
    status === "queued" ? "tp" : "ta";
  const label =
    status === "done" ? "完成" :
    status === "failed" ? "失败" :
    status === "running" ? "运行中" :
    status === "queued" ? "排队" :
    status === "cancelled" ? "已取消" : status;
  return <span className={"tag " + cls}>{label}</span>;
}

/* ===================== 5 Visualization Components ===================== */

function ListingProfile({
  overview, scorecard, semanticBlindSpots, cosmoNodes, rufusQa,
  behaviorSignals, competitorDiff, rewrites,
}: {
  overview?: AuditStructured["overview"];
  scorecard?: AuditStructured["scorecard"];
  semanticBlindSpots?: EvidenceGroup[];
  cosmoNodes?: CosmoNode[];
  rufusQa?: RufusQA[];
  behaviorSignals?: EvidenceGroup[];
  competitorDiff?: EvidenceGroup[];
  rewrites?: AuditStructured["rewrites"];
}) {
  return (
    <div className="card" style={{ padding: "12px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: "var(--fs-12)", color: "var(--t)", marginBottom: 10, fontWeight: 600 }}>
        📋 Listing 全景画像
      </div>

      {/* Overview */}
      {overview && (
        <table className="tbl" style={{ marginBottom: 10 }}>
          <tbody>
            {overview.category && <tr><td style={{ width: 100, color: "var(--t3)" }}>类目</td><td>{overview.category}</td></tr>}
            {overview.title_summary && <tr><td style={{ color: "var(--t3)" }}>标题</td><td>{overview.title_summary}</td></tr>}
            {overview.key_specs && <tr><td style={{ color: "var(--t3)" }}>核心规格</td><td>{overview.key_specs}</td></tr>}
            {overview.price && <tr><td style={{ color: "var(--t3)" }}>价格</td><td>{overview.price}</td></tr>}
            {overview.top_risk && <tr><td style={{ color: "var(--t3)" }}>头号风险</td><td style={{ color: "var(--red)" }}>{overview.top_risk}</td></tr>}
          </tbody>
        </table>
      )}

      {/* Scorecard */}
      {scorecard && scorecard.length > 0 && (
        <>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t2)", marginBottom: 4 }}>评分卡</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 6, marginBottom: 10 }}>
            {scorecard.map((it) => (
              <div key={it.dimension} style={{ padding: "6px 8px", background: "var(--bg3)", borderRadius: "var(--r)", border: "1px solid var(--b)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 3 }}>
                  <span style={{ fontSize: "var(--fs-10)", color: "var(--t)" }}>{it.dimension}</span>
                  <ScoreBar score={it.score} />
                </div>
                <div style={{ fontSize: "var(--fs-9)", color: "var(--t3)", lineHeight: 1.4 }}>{it.note}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Semantic blind spots (板块③ Listing文案) */}
      {semanticBlindSpots && semanticBlindSpots.length > 0 && (
        <EvidenceSection title="🔍 语义检索盲区" groups={semanticBlindSpots} groupKey="aspect" />
      )}

      {/* COSMO nodes (板块④ A+内容) */}
      {cosmoNodes && cosmoNodes.length > 0 && (
        <>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t2)", margin: "8px 0 4px" }}>🧠 COSMO 知识图谱</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 6, marginBottom: 8 }}>
            {cosmoNodes.map((n) => (
              <div key={n.node} style={{ padding: "6px 8px", background: "var(--bg3)", borderRadius: "var(--r)", border: "1px solid var(--b)" }}>
                <div style={{ fontSize: "var(--fs-10)", color: "var(--acc)", marginBottom: 3 }}>{n.node} · {n.label_cn}</div>
                {n.bullets.map((b, i) => (
                  <div key={i} style={{ fontSize: "var(--fs-9)", color: b.label === "推断建议" ? "var(--amber)" : "var(--t3)", marginBottom: 2 }}>
                    <span style={{ opacity: 0.7 }}>[{b.label}]</span> {b.text}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </>
      )}

      {/* Rufus Q&A (板块⑤) */}
      {rufusQa && rufusQa.length > 0 && (
        <>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t2)", margin: "8px 0 4px" }}>🤖 Rufus 问答能力</div>
          <table className="tbl" style={{ marginBottom: 8 }}>
            <thead><tr><th>问题</th><th style={{ width: 50 }}>判定</th><th>证据</th></tr></thead>
            <tbody>
              {rufusQa.map((r, i) => (
                <tr key={i}>
                  <td style={{ fontSize: "var(--fs-10)" }}>{r.question}</td>
                  <td><span className={`tag ${r.verdict === "能" ? "tg" : r.verdict === "不能" ? "tr" : "ta"}`}>{r.verdict}</span></td>
                  <td style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>{r.evidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* Behavior signals (板块⑥ Reviews) */}
      {behaviorSignals && behaviorSignals.length > 0 && (
        <EvidenceSection title="👥 用户行为信号" groups={behaviorSignals} groupKey="category" />
      )}

      {/* Competitor diff (板块⑦ 价格与变体/竞品) */}
      {competitorDiff && competitorDiff.length > 0 && (
        <EvidenceSection title="⚔️ 竞品差异化" groups={competitorDiff} groupKey="topic" />
      )}

      {/* Rewrites preview */}
      {rewrites && (
        <details style={{ marginTop: 8 }}>
          <summary style={{ fontSize: "var(--fs-10)", color: "var(--t3)", cursor: "pointer" }}>✍️ 改写建议（标题 / 五点 / A+ / 后台词）</summary>
          <div style={{ marginTop: 6, padding: 8, background: "var(--bg3)", borderRadius: "var(--r)", fontSize: "var(--fs-10)" }}>
            {rewrites.title && <div style={{ marginBottom: 4 }}><b>标题：</b>{rewrites.title}</div>}
            {rewrites.bullets && rewrites.bullets.length > 0 && (
              <div style={{ marginBottom: 4 }}><b>五点：</b><ul style={{ margin: "2px 0", paddingLeft: 16 }}>{rewrites.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul></div>
            )}
            {rewrites.backend_terms && <div style={{ marginBottom: 4 }}><b>后台词：</b><span style={{ color: "var(--t3)" }}>{rewrites.backend_terms}</span></div>}
            {rewrites.aplus_plan && rewrites.aplus_plan.length > 0 && (
              <div><b>A+ 方案：</b><ul style={{ margin: "2px 0", paddingLeft: 16 }}>{rewrites.aplus_plan.map((a, i) => <li key={i}>{a}</li>)}</ul></div>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

function VisualReport({ imagePlan }: { imagePlan: NonNullable<NonNullable<AuditStructured["rewrites"]>["image_plan"]> }) {
  return (
    <div className="card" style={{ padding: "12px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: "var(--fs-12)", color: "var(--t)", marginBottom: 8, fontWeight: 600 }}>
        🖼️ 视觉与图片方案
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 10 }}>
        {imagePlan.main_image && imagePlan.main_image.length > 0 && (
          <ImageColumn title="主图" icon="📸" items={imagePlan.main_image} />
        )}
        {imagePlan.aux_images && imagePlan.aux_images.length > 0 && (
          <ImageColumn title="辅图" icon="🔲" items={imagePlan.aux_images} />
        )}
        {imagePlan.scene_images && imagePlan.scene_images.length > 0 && (
          <ImageColumn title="场景图" icon="🌄" items={imagePlan.scene_images} />
        )}
      </div>
    </div>
  );
}

function ImageColumn({ title, icon, items }: { title: string; icon: string; items: string[] }) {
  return (
    <div style={{ padding: "8px 10px", background: "var(--bg3)", borderRadius: "var(--r)", border: "1px solid var(--b)" }}>
      <div style={{ fontSize: "var(--fs-10)", color: "var(--t2)", marginBottom: 4 }}>{icon} {title}</div>
      <ol style={{ margin: 0, paddingLeft: 16 }}>
        {items.map((it, i) => (
          <li key={i} style={{ fontSize: "var(--fs-9)", color: "var(--t3)", marginBottom: 3, lineHeight: 1.4 }}>{it}</li>
        ))}
      </ol>
    </div>
  );
}

function TrafficStructureChart({ scorecard }: { scorecard: NonNullable<AuditStructured["scorecard"]> }) {
  // Radar-like horizontal bar chart for the 7 dimensions
  const max = 10;
  return (
    <div className="card" style={{ padding: "12px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: "var(--fs-12)", color: "var(--t)", marginBottom: 8, fontWeight: 600 }}>
        📈 流量结构 · 7 维雷达
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {scorecard.map((it) => {
          const pct = (Math.min(it.score, max) / max) * 100;
          const color = it.score >= 8 ? "var(--acc)" : it.score >= 5 ? "var(--amber)" : "var(--red)";
          return (
            <div key={it.dimension} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)", width: 90, textAlign: "right", flexShrink: 0 }}>{it.dimension}</span>
              <div style={{ flex: 1, height: 12, background: "var(--bg3)", borderRadius: 3, overflow: "hidden", position: "relative" }}>
                <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3, transition: "width .3s" }} />
                <span style={{ position: "absolute", right: 4, top: 0, fontSize: "var(--fs-8)", lineHeight: "12px", color: "var(--t3)" }}>{it.score.toFixed(1)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function KeywordPositionMatrix({ adPlan }: { adPlan: NonNullable<AuditStructured["ad_plan"]> }) {
  const exact = adPlan.keywords_exact || [];
  const broad = adPlan.keywords_phrase_broad || [];
  const targets = adPlan.product_targeting || [];
  const negImm = adPlan.negatives_immediate || [];
  const negWatch = adPlan.negatives_watch || [];
  if (!exact.length && !broad.length && !targets.length) return null;

  return (
    <div className="card" style={{ padding: "12px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: "var(--fs-12)", color: "var(--t)", marginBottom: 8, fontWeight: 600 }}>
        🎯 关键词阵地矩阵
      </div>
      {adPlan.objective && <div style={{ fontSize: "var(--fs-10)", color: "var(--t2)", marginBottom: 8 }}>目标：{adPlan.objective}</div>}

      {/* Campaigns */}
      {adPlan.campaigns && adPlan.campaigns.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", marginBottom: 4 }}>广告活动</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 6 }}>
            {adPlan.campaigns.map((c, i) => (
              <div key={i} style={{ padding: "6px 8px", background: "var(--bg3)", borderRadius: "var(--r)", border: "1px solid var(--b)", fontSize: "var(--fs-9)" }}>
                <div style={{ color: "var(--t)", marginBottom: 2 }}>{c.name}</div>
                <div style={{ color: "var(--t3)" }}>{c.type} · {c.targeting} · {c.bid_range} · {c.budget}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Keywords table */}
      {(exact.length > 0 || broad.length > 0) && (
        <table className="tbl" style={{ marginBottom: 8 }}>
          <thead><tr><th>关键词</th><th style={{ width: 60 }}>出价</th><th style={{ width: 50 }}>类型</th><th>理由</th></tr></thead>
          <tbody>
            {exact.map((k, i) => (
              <tr key={`e${i}`}><td style={{ fontSize: "var(--fs-10)" }}>{k.keyword}</td><td>{k.bid}</td><td><span className="tag tg" style={{ fontSize: "var(--fs-8)" }}>精准</span></td><td style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>{k.reason}</td></tr>
            ))}
            {broad.map((k, i) => (
              <tr key={`b${i}`}><td style={{ fontSize: "var(--fs-10)" }}>{k.keyword}</td><td>{k.bid}</td><td><span className="tag tp" style={{ fontSize: "var(--fs-8)" }}>扩量</span></td><td style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>{k.reason}</td></tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Negatives */}
      {(negImm.length > 0 || negWatch.length > 0) && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {negImm.length > 0 && (
            <div><span style={{ fontSize: "var(--fs-9)", color: "var(--red)" }}>🚫 立即否定：</span><span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>{negImm.join("、")}</span></div>
          )}
          {negWatch.length > 0 && (
            <div><span style={{ fontSize: "var(--fs-9)", color: "var(--amber)" }}>👁 观察否定：</span><span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>{negWatch.join("、")}</span></div>
          )}
        </div>
      )}
    </div>
  );
}

function RiskSignalBoard({ priorities }: { priorities: NonNullable<AuditStructured["priorities"]> }) {
  const p0 = priorities.filter((p) => p.level === "P0");
  const p1 = priorities.filter((p) => p.level === "P1");
  const p2 = priorities.filter((p) => p.level !== "P0" && p.level !== "P1");

  return (
    <div className="card" style={{ padding: "12px 14px", marginBottom: 12 }}>
      <div style={{ fontSize: "var(--fs-12)", color: "var(--t)", marginBottom: 8, fontWeight: 600 }}>
        ⚠️ 风险信号看板
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 8 }}>
        {p0.length > 0 && <RiskColumn level="P0" color="var(--red)" items={p0} />}
        {p1.length > 0 && <RiskColumn level="P1" color="var(--amber)" items={p1} />}
        {p2.length > 0 && <RiskColumn level="P2" color="var(--t3)" items={p2} />}
      </div>
    </div>
  );
}

function RiskColumn({ level, color, items }: { level: string; color: string; items: NonNullable<AuditStructured["priorities"]> }) {
  return (
    <div style={{ padding: "8px 10px", background: "var(--bg3)", borderRadius: "var(--r)", border: `1px solid ${color}22` }}>
      <div style={{ fontSize: "var(--fs-10)", color, fontWeight: 600, marginBottom: 6 }}>{level} · {items.length} 项</div>
      {items.map((it, i) => (
        <div key={i} style={{ marginBottom: 6, paddingBottom: 6, borderBottom: i < items.length - 1 ? "1px solid var(--b)" : "none" }}>
          <div style={{ fontSize: "var(--fs-10)", color: "var(--t)", marginBottom: 2 }}>{it.issue}</div>
          <div style={{ fontSize: "var(--fs-9)", color: "var(--t3)", marginBottom: 2 }}>{it.evidence}</div>
          <div style={{ fontSize: "var(--fs-9)", color: "var(--acc)" }}>→ {it.action}</div>
        </div>
      ))}
    </div>
  );
}

function EvidenceSection({ title, groups, groupKey }: { title: string; groups: EvidenceGroup[]; groupKey: string }) {
  return (
    <>
      <div style={{ fontSize: "var(--fs-10)", color: "var(--t2)", margin: "8px 0 4px" }}>{title}</div>
      {groups.map((g, gi) => {
        const label = (g as any)[groupKey] || `#${gi + 1}`;
        return (
          <div key={gi} style={{ marginBottom: 6, paddingLeft: 8, borderLeft: "2px solid var(--b)" }}>
            <div style={{ fontSize: "var(--fs-10)", color: "var(--t)", marginBottom: 2 }}>{label}</div>
            {g.bullets.map((b, bi) => (
              <div key={bi} style={{ fontSize: "var(--fs-9)", color: b.label === "推断建议" ? "var(--amber)" : "var(--t3)", marginBottom: 1 }}>
                <span style={{ opacity: 0.6 }}>[{b.label}]</span> {b.text}
              </div>
            ))}
          </div>
        );
      })}
    </>
  );
}

function ComingSoonCard({
  icon,
  title,
  desc,
}: {
  icon: string;
  title: string;
  desc: string;
}) {
  return (
    <div
      className="card"
      style={{
        opacity: 0.55,
        cursor: "not-allowed",
        position: "relative",
      }}
      title="即将上线"
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: "var(--fs-16)" }}>{icon}</span>
        <span style={{ fontSize: "var(--fs-11)", color: "var(--t)" }}>{title}</span>
        <span
          className="tag"
          style={{
            marginLeft: "auto",
            fontSize: "var(--fs-8)",
            background: "var(--bg3)",
            color: "var(--t3)",
            border: "1px solid var(--b)",
          }}
        >WIP</span>
      </div>
      <div style={{ fontSize: "var(--fs-10)", color: "var(--t3)", lineHeight: 1.5 }}>{desc}</div>
    </div>
  );
}
