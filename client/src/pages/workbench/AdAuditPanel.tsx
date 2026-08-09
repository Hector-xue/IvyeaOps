import { useCallback, useEffect, useRef, useState } from "react";
import { useConfirm } from "../../components/ConfirmDialog";
import SheetSelect from "../../components/SheetSelect";
import DeepAnalysisPanel from "../../components/DeepAnalysisPanel";
import FindingCards from "../../components/FindingCards";
import { marketplaceOptions } from "../../lib/marketplaces";
import {
  adAuditClearFailed,
  adAuditDelete,
  adAuditDownloadUrl,
  adAuditGet,
  adAuditList,
  adAuditRemoveSource,
  adAuditRunners,
  adAuditStart,
  adAuditUpdateSource,
  adAuditUpload,
  type AdAuditFull,
  type AdAuditGoal,
  type AdAuditJobMeta,
  type AdAuditOutputMode,
  type AdAuditStructured,
  type AdAuditUploadResp,
  type AdCrossCampaignInsight,
  type AdSourceInfo,
  type RunnerName,
  type RunnerStatus,
} from "../../api/client";
import { errText } from "../../lib/errText";

const MARKETPLACES = ["US", "UK", "DE", "FR", "CA", "JP", "ES", "IT", "MX", "AU", "AE", "BR", "SA"];

const GOAL_OPTIONS: { value: AdAuditGoal; label: string; hint: string }[] = [
  { value: "profit",     label: "盈利 · 稳扎稳打",  hint: "ACOS 目标严格，低效词立即砍" },
  { value: "new_launch", label: "新品 · 冲量期",    hint: "放宽 ACOS，重视曝光位置" },
  { value: "relaunch",   label: "老品 · 重推期",    hint: "中等宽松，关注 CVR 拉升" },
  { value: "clearance",  label: "清货 · 只看订单",  hint: "最激进，ACOS 放最后" },
];

type PollState =
  | { kind: "idle" }
  | { kind: "uploaded"; preview: AdAuditUploadResp }
  | { kind: "starting"; jobId: string }
  | { kind: "polling";  jobId: string; data: AdAuditFull | null }
  | { kind: "done";     jobId: string; data: AdAuditFull }
  | { kind: "failed";   jobId: string; data: AdAuditFull; error: string };

export default function AdAuditPanel() {
  const confirm = useConfirm();
  const [state, setState] = useState<PollState>({ kind: "idle" });
  const [history, setHistory] = useState<AdAuditJobMeta[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [runners, setRunners] = useState<RunnerStatus[]>([]);
  const [marketplace, setMarketplace] = useState("US");
  const [runner, setRunner] = useState<RunnerName>("auto");
  const [goal, setGoal] = useState<AdAuditGoal>("profit");
  const [outputMode, setOutputMode] = useState<AdAuditOutputMode>("report");
  const [asin, setAsin] = useState("");
  const [productNotes, setProductNotes] = useState("");
  const [protectedRaw, setProtectedRaw] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const pollTimer = useRef<number | null>(null);

  const loadHistory = useCallback(async () => {
    try {
      const r = await adAuditList(20);
      setHistory(r.items);
    } catch { /* ignore */ }
  }, []);

  const loadRunners = useCallback(async () => {
    try {
      const rs = await adAuditRunners();
      setRunners(rs);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    loadHistory();
    loadRunners();
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    };
  }, [loadHistory, loadRunners]);

  const startPolling = useCallback((jobId: string) => {
    const tick = async () => {
      try {
        const data = await adAuditGet(jobId);
        if (data.status === "done") {
          setState({ kind: "done", jobId, data });
          loadHistory();
          return;
        }
        if (data.status === "failed" || data.status === "cancelled") {
          setState({ kind: "failed", jobId, data, error: data.error || data.status });
          loadHistory();
          return;
        }
        setState({ kind: "polling", jobId, data });
        pollTimer.current = window.setTimeout(tick, 3000);
      } catch (e: any) {
        setState({
          kind: "failed",
          jobId,
          data: { job_id: jobId } as AdAuditFull,
          error: errText(e, "轮询失败"),
        });
      }
    };
    tick();
  }, [loadHistory]);

  const handleFile = async (file: File) => {
    setUploading(true);
    try {
      // Append to existing job if we're in the "uploaded" stage; otherwise create new.
      const existingId =
        state.kind === "uploaded" ? state.preview.job_id : undefined;
      const preview = await adAuditUpload(file, marketplace, existingId);
      setState({ kind: "uploaded", preview });
    } catch (e: any) {
      alert(errText(e, "上传失败"));
    } finally {
      setUploading(false);
    }
  };

  const onRemoveSource = async (sourceId: string) => {
    if (state.kind !== "uploaded") return;
    try {
      const preview = await adAuditRemoveSource(
        state.preview.job_id,
        sourceId,
      );
      setState({ kind: "uploaded", preview });
    } catch (e: any) {
      alert(errText(e, "删除失败"));
    }
  };

  const onUpdateSource = async (
    sourceId: string,
    payload: { campaign_name?: string; daily_budget_usd?: number; clear_daily_budget?: boolean },
  ) => {
    if (state.kind !== "uploaded") return;
    try {
      const preview = await adAuditUpdateSource(
        state.preview.job_id,
        sourceId,
        payload,
      );
      setState({ kind: "uploaded", preview });
    } catch (e: any) {
      alert(errText(e, "更新失败"));
    }
  };

  const onStart = async () => {
    if (state.kind !== "uploaded") return;
    const protectedKeywords = protectedRaw
      .split(/[,，\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
    const trimmedAsin = asin.trim().toUpperCase();
    const sources = state.preview.sources || [];
    const multiSource = sources.length > 1;
    // Multi-source requires ASIN (backend enforces, but warn early).
    if (multiSource && !trimmedAsin) {
      alert("上传多份报告时必须填写目标 ASIN（用于关联同一产品的多个活动）");
      return;
    }
    if (trimmedAsin && !/^[A-Z0-9]{10}$/.test(trimmedAsin)) {
      alert("ASIN 必须留空或 10 位字母数字");
      return;
    }
    // Collect daily_budgets from source entries (only those with values).
    const daily_budgets: Record<string, number> = {};
    for (const src of sources) {
      if (src.daily_budget_usd != null && src.campaign_name) {
        daily_budgets[src.campaign_name] = src.daily_budget_usd;
      }
    }
    setState({ kind: "starting", jobId: state.preview.job_id });
    try {
      await adAuditStart({
        job_id: state.preview.job_id,
        goal,
        output_mode: outputMode,
        asin: trimmedAsin,
        product_notes: productNotes.trim(),
        protected_keywords: protectedKeywords,
        runner,
        daily_budgets: Object.keys(daily_budgets).length > 0 ? daily_budgets : undefined,
      });
      startPolling(state.preview.job_id);
    } catch (e: any) {
      alert(errText(e, "启动失败"));
      setState({ kind: "idle" });
    }
  };

  const openHistoryItem = async (jobId: string) => {
    if (pollTimer.current) window.clearTimeout(pollTimer.current);
    setHistoryOpen(false);
    try {
      const data = await adAuditGet(jobId);
      if (data.status === "running" || data.status === "queued") {
        setState({ kind: "polling", jobId, data });
        startPolling(jobId);
      } else if (data.status === "done") {
        setState({ kind: "done", jobId, data });
      } else if (data.status === "uploaded") {
        // resuming an un-started upload — treat as new preview
        setState({
          kind: "uploaded",
          preview: {
            ...data,
            columns: data.preview_columns || [],
          } as AdAuditUploadResp,
        });
      } else {
        setState({ kind: "failed", jobId, data, error: data.error || data.status });
      }
    } catch {
      alert("加载任务失败");
    }
  };

  const reset = () => {
    if (pollTimer.current) window.clearTimeout(pollTimer.current);
    setState({ kind: "idle" });
    setAsin("");
    setProductNotes("");
    setProtectedRaw("");
    setGoal("profit");
  };

  const isWorking =
    state.kind === "starting" ||
    state.kind === "polling" ||
    uploading;

  const currentData =
    state.kind === "polling" || state.kind === "done" || state.kind === "failed"
      ? state.data
      : null;

  return (
    <div className="card" style={{ padding: "14px 16px", marginTop: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <span className="tag tb-tag">主力工具</span>
        <span style={{ fontSize: 13, color: "var(--t)" }}>广告搜索词诊断</span>
        <span style={{ fontSize: 10, color: "var(--t3)" }}>
          · SP / SB / SD search term report 根因分析
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

      {/* Stage 1: upload */}
      {state.kind === "idle" && (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const f = e.dataTransfer.files?.[0];
            if (f) handleFile(f);
          }}
          style={{
            border: `2px dashed ${dragOver ? "var(--acc)" : "var(--b)"}`,
            borderRadius: "var(--r)",
            padding: 24,
            textAlign: "center",
            background: dragOver ? "rgba(34,197,94,.05)" : "var(--bg3)",
            cursor: uploading ? "wait" : "pointer",
          }}
          onClick={() => !uploading && fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls,.csv,.tsv"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFile(f);
              e.target.value = "";
            }}
          />
          {uploading ? (
            <>
              <span className="spin" style={{ marginRight: 6 }} />
              <span style={{ fontSize: 11, color: "var(--t2)" }}>解析中，自动识别广告类型…</span>
            </>
          ) : (
            <>
              <div style={{ fontSize: 20, marginBottom: 6 }}>📊</div>
              <div style={{ fontSize: 12, color: "var(--t)", marginBottom: 4 }}>
                拖拽 SP / SB / SD search term report 到此处，或点击选择文件
              </div>
              <div style={{ fontSize: 10, color: "var(--t3)" }}>
                支持 .xlsx / .xls / .csv / .tsv，≤ 20MB · 广告类型自动识别
              </div>
              <div style={{ marginTop: 10 }}>
                <SheetSelect
                  className="inp"
                  value={marketplace}
                  onChange={setMarketplace}
                  style={{ width: 90 }}
                  flags
                  title="报告所属站点"
                  options={marketplaceOptions(MARKETPLACES)}
                />
              </div>
            </>
          )}
        </div>
      )}

      {/* Stage 2: upload preview + context form */}
      {state.kind === "uploaded" && (
        <UploadedForm
          preview={state.preview}
          uploading={uploading}
          marketplace={marketplace}
          goal={goal} setGoal={setGoal}
          outputMode={outputMode} setOutputMode={setOutputMode}
          asin={asin} setAsin={setAsin}
          productNotes={productNotes} setProductNotes={setProductNotes}
          protectedRaw={protectedRaw} setProtectedRaw={setProtectedRaw}
          runner={runner} setRunner={setRunner}
          runners={runners}
          onAddFile={handleFile}
          onRemoveSource={onRemoveSource}
          onUpdateSource={onUpdateSource}
          onStart={onStart}
          onCancel={reset}
        />
      )}

      {/* Stage 3: running status */}
      {(state.kind === "starting" || state.kind === "polling") && (
        <div
          style={{
            marginTop: 6,
            padding: 10,
            background: "var(--bg3)",
            border: "1px solid var(--b)",
            borderRadius: "var(--r)",
          }}
        >
          <div style={{ fontSize: 11, color: "var(--t2)", marginBottom: 4 }}>
            <span className="spin" style={{ marginRight: 6 }} />
            {state.kind === "starting"
              ? "正在启动…"
              : state.data?.progress || "分析中…（预计 3-8 分钟）"}
          </div>
          {state.kind === "polling" && state.data?.started_at && (
            <div style={{ fontSize: 10, color: "var(--t3)" }}>
              启动时间：{new Date(state.data.started_at).toLocaleTimeString("zh-CN")}
            </div>
          )}
        </div>
      )}

      {/* History */}
      {historyOpen && (
        <HistoryList
          items={history}
          onOpen={openHistoryItem}
          onClearFailed={async () => {
            if (!await confirm({ title: "清空记录", message: "清空所有失败/已取消的任务记录？此操作不可撤销。", confirmText: "清空", danger: true })) return;
            try {
              const r = await adAuditClearFailed();
              await loadHistory();
              alert(`已清除 ${r.removed} 条失败记录`);
            } catch (e: any) {
              alert(errText(e, "清除失败"));
            }
          }}
        />
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
            fontSize: 11,
          }}
        >
          ✗ {state.error}
          <button className="tbtn" style={{ marginLeft: 10 }} onClick={reset}>重新上传</button>
        </div>
      )}

      {/* Result */}
      {state.kind === "done" && <AdResultPanel data={state.data} onReset={reset} />}
      {state.kind === "done" && state.data.raw_md && (
        <AdDeepAnalysisPanel data={state.data} />
      )}

      {/* Disable global actions while working */}
      <div style={{ display: "none" }}>{isWorking ? "" : ""}</div>
    </div>
  );
}

/* ===================== Uploaded form ===================== */

function UploadedForm({
  preview, uploading, marketplace,
  goal, setGoal, outputMode, setOutputMode, asin, setAsin, productNotes, setProductNotes,
  protectedRaw, setProtectedRaw, runner, setRunner, runners,
  onAddFile, onRemoveSource, onUpdateSource,
  onStart, onCancel,
}: {
  preview: AdAuditUploadResp;
  uploading: boolean;
  marketplace: string;
  goal: AdAuditGoal; setGoal: (g: AdAuditGoal) => void;
  outputMode: AdAuditOutputMode; setOutputMode: (m: AdAuditOutputMode) => void;
  asin: string; setAsin: (s: string) => void;
  productNotes: string; setProductNotes: (s: string) => void;
  protectedRaw: string; setProtectedRaw: (s: string) => void;
  runner: RunnerName; setRunner: (r: RunnerName) => void;
  runners: RunnerStatus[];
  onAddFile: (f: File) => void;
  onRemoveSource: (sourceId: string) => void;
  onUpdateSource: (
    sourceId: string,
    payload: { campaign_name?: string; daily_budget_usd?: number; clear_daily_budget?: boolean },
  ) => void;
  onStart: () => void; onCancel: () => void;
}) {
  const sources = preview.sources || [];
  const hasBudgets = sources.some((s) => s.daily_budget_usd != null);
  const multiSource = sources.length > 1;

  return (
    <>
      <SourcesPanel
        sources={sources}
        marketplace={marketplace}
        uploading={uploading}
        onAddFile={onAddFile}
        onRemoveSource={onRemoveSource}
        onUpdateSource={onUpdateSource}
        onCancel={onCancel}
      />

      {multiSource && (
        <div
          style={{
            padding: 8,
            marginBottom: 10,
            background: "rgba(59,130,246,.08)",
            border: "1px solid rgba(59,130,246,.25)",
            borderRadius: "var(--r)",
            fontSize: 10,
            color: "var(--t2)",
          }}
        >
          🔗 多报告模式：已启用跨活动分析（预算重分配 / 黑洞活动识别 / 关键词迁移）。
          请在下方填写 <b>目标 ASIN</b>（必填）以关联同一产品的多个活动。
          {!hasBudgets && "建议为每个活动填写每日预算，便于给出预算重分配建议。"}
        </div>
      )}

      <div style={{ fontSize: 11, color: "var(--t2)", marginBottom: 6 }}>📝 任务上下文</div>

      {/* Goal selector */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 8, marginBottom: 10 }}>
        {GOAL_OPTIONS.map((g) => (
          <label
            key={g.value}
            style={{
              padding: 8,
              border: `1px solid ${goal === g.value ? "var(--acc)" : "var(--b)"}`,
              background: goal === g.value ? "rgba(34,197,94,.06)" : "var(--bg3)",
              borderRadius: "var(--r)",
              cursor: "pointer",
              display: "flex",
              flexDirection: "column",
              gap: 2,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <input
                type="radio"
                name="ad-goal"
                checked={goal === g.value}
                onChange={() => setGoal(g.value)}
              />
              <span style={{ fontSize: 11, color: "var(--t)" }}>{g.label}</span>
            </div>
            <span style={{ fontSize: 10, color: "var(--t3)", paddingLeft: 20 }}>{g.hint}</span>
          </label>
        ))}
      </div>

      {/* Output mode selector */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <span style={{ fontSize: 10, color: "var(--t3)" }}>输出模式</span>
        <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
          <input type="radio" name="ad-output-mode" checked={outputMode === "report"} onChange={() => setOutputMode("report")} />
          <span style={{ fontSize: 11, color: "var(--t)" }}>分析报告</span>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
          <input type="radio" name="ad-output-mode" checked={outputMode === "xlsx_plan"} onChange={() => setOutputMode("xlsx_plan")} />
          <span style={{ fontSize: 11, color: "var(--t)" }}>📊 8-Sheet 优化方案 xlsx</span>
          <span className="tag" style={{ fontSize: 8, background: "var(--acc)", color: "#fff", marginLeft: 4 }}>NEW</span>
        </label>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: 8, marginBottom: 8 }}>
        <input
          className="inp"
          placeholder="目标 ASIN（可选）"
          value={asin}
          maxLength={10}
          onChange={(e) => setAsin(e.target.value.toUpperCase())}
          style={{ fontFamily: "monospace", letterSpacing: "0.05em" }}
        />
        <input
          className="inp"
          placeholder="产品备注（可选，如：太阳能 4G 蜂窝野外相机，支持夜视）"
          value={productNotes}
          onChange={(e) => setProductNotes(e.target.value)}
        />
      </div>

      <textarea
        className="inp"
        rows={2}
        placeholder="守护关键词（可选，多个用逗号或换行分隔。留空则完全按数据判断，核心词也可按数据否/降）"
        value={protectedRaw}
        onChange={(e) => setProtectedRaw(e.target.value)}
        style={{ width: "100%", fontFamily: "inherit", resize: "vertical", marginBottom: 10 }}
      />

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <SheetSelect
          className="inp"
          value={runner}
          onChange={(v) => setRunner(v as RunnerName)}
          disabled={runners.length === 0}
          title="选择执行分析的智能体 CLI"
          style={{ width: 180 }}
          options={
            runners.length === 0
              ? [{ value: "auto", label: "自动" }]
              : runners.map((r) => ({
                  value: r.name,
                  label: `${r.available ? "🤖 " : "⊘ "}${r.label}${!r.available && r.reason ? `（${r.reason}）` : ""}`,
                  disabled: !r.available,
                }))
          }
        />
        <button className="tbtn" onClick={onStart}>🚀 开始分析</button>
        <span style={{ fontSize: 10, color: "var(--t3)" }}>预计 3-8 分钟</span>
      </div>
    </>
  );
}

/* ===================== Sources panel ===================== */

function SourcesPanel({
  sources, marketplace, uploading,
  onAddFile, onRemoveSource, onUpdateSource, onCancel,
}: {
  sources: AdSourceInfo[];
  marketplace: string;
  uploading: boolean;
  onAddFile: (f: File) => void;
  onRemoveSource: (id: string) => void;
  onUpdateSource: (
    id: string,
    payload: { campaign_name?: string; daily_budget_usd?: number; clear_daily_budget?: boolean },
  ) => void;
  onCancel: () => void;
}) {
  const addRef = useRef<HTMLInputElement | null>(null);
  return (
    <div
      style={{
        padding: 10,
        background: "var(--bg3)",
        border: "1px solid var(--b)",
        borderRadius: "var(--r)",
        marginBottom: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <span className="tag tg">已上传 {sources.length} 份</span>
        <span style={{ fontSize: 10, color: "var(--t3)" }}>
          站点 {marketplace} · 同一 ASIN 的多个活动可分别上传（Auto / Exact / Phrase / SP / SB / SD）
        </span>
        <button
          className="tbtn"
          style={{ marginLeft: "auto" }}
          onClick={onCancel}
          title="清空所有已上传报告，回到初始状态"
        >
          ✗ 全部取消
        </button>
      </div>

      <table className="tbl" style={{ marginBottom: 8 }}>
        <thead>
          <tr>
            <th style={{ width: 28 }}>#</th>
            <th>文件</th>
            <th style={{ width: 40 }}>类型</th>
            <th style={{ width: 60 }}>行数</th>
            <th>活动名（可编辑）</th>
            <th style={{ width: 100 }} title="本活动的每日预算，用于诊断预算利用率">
              每日预算($)
            </th>
            <th style={{ width: 40 }}></th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s, i) => (
            <SourceRow
              key={s.source_id}
              idx={i + 1}
              src={s}
              canDelete={sources.length > 1}
              onRemove={() => onRemoveSource(s.source_id)}
              onRename={(name) => onUpdateSource(s.source_id, { campaign_name: name })}
              onBudget={(v) => {
                if (v == null) {
                  onUpdateSource(s.source_id, { clear_daily_budget: true });
                } else {
                  onUpdateSource(s.source_id, { daily_budget_usd: v });
                }
              }}
            />
          ))}
        </tbody>
      </table>

      <div>
        <input
          ref={addRef}
          type="file"
          accept=".xlsx,.xls,.csv,.tsv"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onAddFile(f);
            e.target.value = "";
          }}
        />
        <button
          className="tbtn"
          disabled={uploading}
          onClick={() => addRef.current?.click()}
          title="上传同一 ASIN 的其他活动报告（最多 8 份）"
        >
          {uploading ? (
            <><span className="spin" style={{ marginRight: 4 }} />追加中…</>
          ) : (
            <>➕ 添加另一份报告</>
          )}
        </button>
        <span style={{ marginLeft: 8, fontSize: 10, color: "var(--t3)" }}>
          留空活动名时默认使用文件名；建议填写为 "SP-Exact-Core" / "SP-Auto" 等便于对比
        </span>
      </div>
    </div>
  );
}

function SourceRow({
  idx, src, canDelete, onRemove, onRename, onBudget,
}: {
  idx: number;
  src: AdSourceInfo;
  canDelete: boolean;
  onRemove: () => void;
  onRename: (v: string) => void;
  onBudget: (v: number | null) => void;
}) {
  const [nameDraft, setNameDraft] = useState(src.campaign_name);
  const [budgetDraft, setBudgetDraft] = useState(
    src.daily_budget_usd != null ? String(src.daily_budget_usd) : "",
  );
  // Sync when server sends a fresh snapshot.
  useEffect(() => { setNameDraft(src.campaign_name); }, [src.campaign_name]);
  useEffect(() => {
    setBudgetDraft(src.daily_budget_usd != null ? String(src.daily_budget_usd) : "");
  }, [src.daily_budget_usd]);

  return (
    <tr>
      <td style={{ fontSize: 10, color: "var(--t3)" }}>{idx}</td>
      <td style={{ fontSize: 10 }}>
        <div>{src.file_name}</div>
        <div style={{ fontSize: 9, color: "var(--t3)" }}>
          {src.date_range || "日期未识别"}
        </div>
      </td>
      <td>
        <span className="tag ta">{src.ad_type || "?"}</span>
      </td>
      <td style={{ fontSize: 10 }}>{src.row_count}</td>
      <td>
        <input
          className="inp"
          style={{ width: "100%", fontSize: 10 }}
          value={nameDraft}
          onChange={(e) => setNameDraft(e.target.value)}
          onBlur={() => {
            const trimmed = nameDraft.trim();
            if (trimmed && trimmed !== src.campaign_name) onRename(trimmed);
          }}
          placeholder="例: SP-Exact-Core"
        />
      </td>
      <td>
        <input
          className="inp"
          type="number"
          min={0}
          step={1}
          style={{ width: "100%", fontSize: 10 }}
          value={budgetDraft}
          onChange={(e) => setBudgetDraft(e.target.value)}
          onBlur={() => {
            const raw = budgetDraft.trim();
            if (raw === "") {
              if (src.daily_budget_usd != null) onBudget(null);
              return;
            }
            const v = Number(raw);
            if (!isNaN(v) && v >= 0 && v !== src.daily_budget_usd) onBudget(v);
          }}
          placeholder="可选"
        />
      </td>
      <td>
        <button
          className="tbtn"
          onClick={onRemove}
          disabled={!canDelete}
          title={canDelete ? "移除此报告" : "至少保留一份报告"}
        >✗</button>
      </td>
    </tr>
  );
}

/* ===================== History ===================== */

function HistoryList({
  items, onOpen, onClearFailed,
}: {
  items: AdAuditJobMeta[];
  onOpen: (id: string) => void;
  onClearFailed: () => void;
}) {
  const confirm = useConfirm();
  return (
    <div
      style={{
        marginTop: 10,
        border: "1px solid var(--b)",
        borderRadius: "var(--r)",
        maxHeight: 240,
        overflowY: "auto",
      }}
    >
      {items.length === 0 ? (
        <div style={{ padding: 12, fontSize: 10, color: "var(--t3)" }}>暂无历史任务</div>
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
            <span style={{ fontSize: 10, color: "var(--t3)" }}>
              共 {items.length} 条，失败 {items.filter((h) => h.status === "failed" || h.status === "cancelled").length} 条
            </span>
            <button
              className="tbtn"
              style={{ marginLeft: "auto" }}
              disabled={!items.some((h) => h.status === "failed" || h.status === "cancelled")}
              onClick={onClearFailed}
            >🗑 清空失败</button>
          </div>
          <table className="tbl">
            <thead>
              <tr>
                <th>文件</th>
                <th>类型</th>
                <th>目标</th>
                <th>状态</th>
                <th>时间</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {items.map((h) => (
                <tr key={h.job_id}>
                  <td style={{ fontSize: 10 }}>{h.file_name}</td>
                  <td>{h.ad_type || "—"}</td>
                  <td style={{ fontSize: 10, color: "var(--t3)" }}>{h.goal || "—"}</td>
                  <td><AdStatusTag status={h.status} /></td>
                  <td style={{ fontSize: 10, color: "var(--t3)" }}>
                    {new Date(h.created_at).toLocaleString("zh-CN")}
                  </td>
                  <td>
                    <button className="tbtn" onClick={() => onOpen(h.job_id)}>查看</button>
                    <button className="tbtn" style={{ marginLeft: 4, color: "var(--red)" }} onClick={async () => {
                      if (!await confirm({ title: "删除记录", message: "确定删除此条记录？", confirmText: "删除", danger: true })) return;
                      try { await adAuditDelete(h.job_id); onClearFailed(); } catch { alert("删除失败"); }
                    }}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function AdStatusTag({ status }: { status: string }) {
  const cls =
    status === "done"      ? "tg" :
    status === "failed"    ? "tr" :
    status === "running"   ? "tb-tag" :
    status === "queued"    ? "tp" :
    status === "uploaded"  ? "ta" : "ta";
  const label =
    status === "done"      ? "完成" :
    status === "failed"    ? "失败" :
    status === "running"   ? "运行中" :
    status === "queued"    ? "排队" :
    status === "uploaded"  ? "待启动" :
    status === "cancelled" ? "已取消" : status;
  return <span className={"tag " + cls}>{label}</span>;
}

/* ===================== Deep analysis panel ===================== */


const AD_ANALYSIS_TYPES = [
  {
    id: "bid",
    icon: "▦",
    label: "竞价优化",
    promptFn: (ctx: string, _mkt: string, report: string) =>
      `以下是广告审计报告（${ctx}）：\n\n${report}\n\n请基于此报告深入分析竞价策略，给出：\n1. 各匹配类型建议竞价区间\n2. 高效词竞价提升方案\n3. 低效词竞价削减或暂停建议\n4. 位置竞价系数调整建议`,
  },
  {
    id: "keywords",
    icon: "⬡",
    label: "关键词策略",
    promptFn: (ctx: string, _mkt: string, report: string) =>
      `以下是广告审计报告（${ctx}）：\n\n${report}\n\n请基于此报告深入分析关键词策略，给出：\n1. 优先否词清单及否词层级建议（活动/广告组级别）\n2. 新增关键词优先级排序\n3. 匹配类型迁移建议（Auto→Exact/Phrase）\n4. 长尾词挖掘机会`,
  },
  {
    id: "structure",
    icon: "◈",
    label: "广告结构",
    promptFn: (ctx: string, _mkt: string, report: string) =>
      `以下是广告审计报告（${ctx}）：\n\n${report}\n\n请基于此报告分析广告活动结构，给出：\n1. 活动/广告组拆分优化建议\n2. 预算分配调整方案\n3. 广告位策略（搜索顶部 vs 商品页面 vs 其他）\n4. 整体账户结构优化路线图`,
  },
];

// Post-audit deep-analysis — shared panel (consistent with 市场调研/打法推荐).
// `query` carries the ad context; the analysis prompts ignore marketplace.
function AdDeepAnalysisPanel({ data }: { data: AdAuditFull }) {
  if (!data.raw_md) return null;
  const context = `${data.ad_type || "广告"} · ${data.file_name} · ${data.marketplace}`;
  return (
    <DeepAnalysisPanel
      types={AD_ANALYSIS_TYPES}
      query={context}
      marketplace={data.marketplace}
      report={data.raw_md}
      slug={data.file_name || "ad-report"}
    />
  );
}

/* ===================== Result panel ===================== */

function AdResultPanel({ data, onReset }: { data: AdAuditFull; onReset: () => void }) {
  const s: AdAuditStructured = data.structured || {};
  const hasStructured =
    s.overview || s.high_performers?.length || s.low_performers?.length || s.action_summary?.length;

  return (
    <div style={{ marginTop: 14 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 10,
          paddingBottom: 8,
          borderBottom: "1px solid var(--b)",
          flexWrap: "wrap",
        }}
      >
        <span className="tag tg">✓ 完成</span>
        <span style={{ fontSize: 11, color: "var(--t)" }}>
          {data.file_name} · {data.ad_type} · {data.marketplace}
        </span>
        {data.finished_at && (
          <span style={{ fontSize: 10, color: "var(--t3)" }}>
            · {new Date(data.finished_at).toLocaleString("zh-CN")}
          </span>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <a className="tbtn" href={adAuditDownloadUrl(data.job_id, "xlsx")} download style={{ textDecoration: "none" }}>{data.output_mode === "xlsx_plan" ? "📊 优化方案 xlsx" : "📊 Excel（带色块）"}</a>
          {/* 交付物：结论一页、证据一页（指标/数值/时间窗/来源）、说明一页。
              和上面那份原始报表的区别是「能直接发给别人」—— 被追问时翻证据页。 */}
          <a className="tbtn" href={adAuditDownloadUrl(data.job_id, "deliverable")} download
            title="结论 + 证据 + 说明，直接发给老板/客户" style={{ textDecoration: "none" }}>📑 交付物（带证据页）</a>
          <a className="tbtn" href={adAuditDownloadUrl(data.job_id, "brief")} download
            title="同样内容的 Markdown，贴进飞书文档 / Notion" style={{ textDecoration: "none" }}>📝 结论摘要</a>
          <a className="tbtn" href={adAuditDownloadUrl(data.job_id, "pptx")} download
            title="经营周报：每条结论一页，证据就在同一页上" style={{ textDecoration: "none" }}>📊 周报 PPT</a>
          {/* PDF 需要服务器上有 Chrome。没有时后端回 501 并说明可以从 HTML 里
              Ctrl+P —— 所以这个按钮照常给，点了会得到一句能看懂的话。 */}
          <a className="tbtn" href={adAuditDownloadUrl(data.job_id, "pdf")} download
            title="PDF（需服务器已安装 Chrome；没有的话可从 HTML 报告里打印）"
            style={{ textDecoration: "none" }}>📕 PDF</a>
          <a className="tbtn" href={adAuditDownloadUrl(data.job_id, "html")} download title="单文件网页版" style={{ textDecoration: "none" }}>🌐 HTML</a>
          <a className="tbtn" href={adAuditDownloadUrl(data.job_id, "md")} download style={{ textDecoration: "none" }}>📄 Markdown</a>
          {data.output_mode !== "xlsx_plan" && <a className="tbtn" href={adAuditDownloadUrl(data.job_id, "json")} download style={{ textDecoration: "none" }}>🧾 JSON</a>}
          <button className="tbtn" onClick={onReset}>↑ 收起</button>
        </div>
      </div>

      {!hasStructured ? (
        data.output_mode === "xlsx_plan" ? (
          <div
            style={{
              padding: 14,
              fontSize: 11,
              color: "var(--t2)",
              background: "rgba(34,197,94,.06)",
              border: "1px solid rgba(34,197,94,.25)",
              borderRadius: "var(--r)",
              marginBottom: 10,
            }}
          >📊 已生成 8-Sheet 广告优化方案 xlsx，请点击上方「Excel」按钮下载。</div>
        ) : (
          <div
            style={{
              padding: 10,
              fontSize: 10,
              color: "var(--t3)",
              background: "var(--bg3)",
              border: "1px solid var(--b)",
              borderRadius: "var(--r)",
              marginBottom: 10,
            }}
          >⚠ 未能解析结构化数据，仅展示原始 markdown（可下载查看）</div>
        )
      ) : (
        <>
          {s.overview && <AdOverview ov={s.overview} verdict={s.overview.one_line_verdict} />}
          {s.action_summary && s.action_summary.length > 0 && (
            <AdActionTable items={s.action_summary} />
          )}
          {/* 统一结论卡片：证据可点开核对、无证据的显式标出。
              与下面的老面板并存 —— 老结构还有它自己的表格视图，不替换。 */}
          <FindingCards data={data.findings} traceUrl={`/ad-audit/${data.job_id}/evidence`} />
          {s.cross_campaign_insights && s.cross_campaign_insights.length > 0 && (
            <AdCrossCampaignPanel items={s.cross_campaign_insights} />
          )}
          {s.protected_keywords_status && s.protected_keywords_status.length > 0 && (
            <AdProtectedTable items={s.protected_keywords_status} />
          )}
          {s.high_performers && s.high_performers.length > 0 && (
            <AdKwTable title="🚀 高效词 Top-20" items={s.high_performers} />
          )}
          {s.low_performers && s.low_performers.length > 0 && (
            <AdKwTable title="⚠ 低效词 Top-20" items={s.low_performers} />
          )}
          {s.new_keyword_candidates && s.new_keyword_candidates.length > 0 && (
            <AdNewKwTable items={s.new_keyword_candidates} />
          )}
          {s.negative_suggestions && s.negative_suggestions.length > 0 && (
            <AdNegTable items={s.negative_suggestions} />
          )}
          {s.placement_diagnosis && s.placement_diagnosis.length > 0 && (
            <AdPlacementTable items={s.placement_diagnosis} />
          )}
          {s.data_notes && (
            <div
              style={{
                marginTop: 10,
                padding: 8,
                background: "var(--bg3)",
                border: "1px solid var(--b)",
                borderRadius: "var(--r)",
                fontSize: 10,
                color: "var(--t3)",
              }}
            >
              <b style={{ color: "var(--t2)" }}>数据说明：</b>{s.data_notes}
            </div>
          )}
        </>
      )}

      {data.raw_md && (
        <details style={{ marginTop: 14 }}>
          <summary style={{ fontSize: 10, color: "var(--t3)", cursor: "pointer" }}>查看原始 Markdown</summary>
          <pre
            style={{
              marginTop: 6,
              maxHeight: 500,
              overflow: "auto",
              fontSize: 10,
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

function SectionHeader({ icon, text }: { icon: string; text: string }) {
  return (
    <div style={{ fontSize: 11, color: "var(--t)", margin: "12px 0 6px", display: "flex", gap: 6 }}>
      <span>{icon}</span><span>{text}</span>
    </div>
  );
}

function AdOverview({ ov, verdict }: {
  ov: NonNullable<AdAuditStructured["overview"]>;
  verdict?: string;
}) {
  return (
    <>
      <SectionHeader icon="📋" text="整体概览" />
      {verdict && (
        <div
          style={{
            padding: 8,
            background: "rgba(34,197,94,.06)",
            borderLeft: "3px solid var(--acc)",
            fontSize: 11,
            color: "var(--t)",
            marginBottom: 8,
            borderRadius: 3,
          }}
        >💡 {verdict}</div>
      )}
      <table className="tbl">
        <tbody>
          <tr>
            <td style={{ color: "var(--t3)", width: 80 }}>曝光 / 点击</td>
            <td>{ov.impressions ?? "—"} / {ov.clicks ?? "—"}</td>
            <td style={{ color: "var(--t3)", width: 80 }}>CTR / CVR</td>
            <td>{ov.ctr ?? "—"} / {ov.cvr ?? "—"}</td>
          </tr>
          <tr>
            <td style={{ color: "var(--t3)" }}>花费 / 销售额</td>
            <td>${ov.spend ?? "—"} / ${ov.sales ?? "—"}</td>
            <td style={{ color: "var(--t3)" }}>订单 / ACOS</td>
            <td>{ov.orders ?? "—"} / {ov.acos ?? "—"}</td>
          </tr>
        </tbody>
      </table>
    </>
  );
}

function AdCrossCampaignPanel({ items }: { items: AdCrossCampaignInsight[] }) {
  const typeLabel: Record<string, { label: string; icon: string; color: string }> = {
    black_hole_campaign: { label: "黑洞活动", icon: "🕳", color: "var(--red)" },
    budget_reallocation: { label: "预算重分配", icon: "💰", color: "var(--acc)" },
    keyword_migration:   { label: "关键词迁移", icon: "➡", color: "#3b82f6" },
    match_type_gap:      { label: "匹配类型缺口", icon: "◇", color: "#a855f7" },
    placement_shift:     { label: "位置调整", icon: "📍", color: "#eab308" },
  };
  return (
    <>
      <SectionHeader icon="🔗" text={`跨活动洞察（${items.length} 条）`} />
      <div style={{ display: "grid", gap: 6 }}>
        {items.map((it, i) => {
          const meta = typeLabel[it.insight_type] || { label: it.insight_type, icon: "•", color: "var(--t2)" };
          return (
            <div
              key={i}
              style={{
                padding: 8,
                background: "var(--bg3)",
                border: "1px solid var(--b)",
                borderLeft: `3px solid ${meta.color}`,
                borderRadius: "var(--r)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4, flexWrap: "wrap" }}>
                <span style={{ fontSize: 12 }}>{meta.icon}</span>
                <span style={{ fontSize: 10, color: meta.color, fontWeight: 600 }}>{meta.label}</span>
                {it.from_campaign && (
                  <span style={{ fontSize: 10, color: "var(--t3)" }}>
                    {it.from_campaign}
                    {it.to_campaign && ` → ${it.to_campaign}`}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 11, color: "var(--t)", marginBottom: 4 }}>{it.summary}</div>
              {it.detail && (
                <div style={{ fontSize: 10, color: "var(--t2)", marginBottom: 4 }}>{it.detail}</div>
              )}
              {it.evidence && (
                <div style={{ fontSize: 10, color: "var(--t3)" }}>
                  <b>证据：</b>{it.evidence}
                </div>
              )}
              {it.suggested_action && (
                <div style={{ fontSize: 10, color: "var(--acc)", marginTop: 3 }}>
                  <b>建议：</b>{it.suggested_action}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

function AdActionTable({ items }: { items: NonNullable<AdAuditStructured["action_summary"]> }) {
  return (
    <>
      <SectionHeader icon="🎯" text="建议汇总（按优先级）" />
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 40 }}>级别</th>
            <th>动作</th>
            <th>证据</th>
            <th style={{ width: 140 }}>预期影响</th>
          </tr>
        </thead>
        <tbody>
          {items.map((a, i) => (
            <tr key={i}>
              <td><span className={"tag prio-" + String(a.level).toLowerCase()}>{a.level}</span></td>
              <td>{a.action}</td>
              <td style={{ fontSize: 10, color: "var(--t3)" }}>{a.evidence}</td>
              <td style={{ fontSize: 10 }}>{a.expected_impact}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function AdProtectedTable({ items }: { items: NonNullable<AdAuditStructured["protected_keywords_status"]> }) {
  return (
    <>
      <SectionHeader icon="🛡" text="守护关键词状态" />
      <table className="tbl">
        <thead>
          <tr>
            <th>关键词</th>
            <th style={{ width: 60 }}>状态</th>
            <th>曝光</th>
            <th>点击</th>
            <th>花费</th>
            <th>订单</th>
            <th>ACOS</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p, i) => (
            <tr key={i}>
              <td style={{ fontFamily: "monospace", fontSize: 10 }}>{p.keyword}</td>
              <td><span className={"cell-" + p.status}>{p.status}</span></td>
              <td>{p.impressions ?? "—"}</td>
              <td>{p.clicks ?? "—"}</td>
              <td>{p.spend ?? "—"}</td>
              <td>{p.orders ?? "—"}</td>
              <td>{p.acos ?? "—"}</td>
              <td style={{ fontSize: 10, color: "var(--t3)" }}>{p.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function AdKwTable({ title, items }: {
  title: string;
  items: NonNullable<AdAuditStructured["high_performers"]>;
}) {
  return (
    <>
      <SectionHeader icon="" text={title} />
      <table className="tbl">
        <thead>
          <tr>
            <th>关键词</th>
            <th>匹配</th>
            <th>曝光</th>
            <th>点击</th>
            <th>花费</th>
            <th>订单</th>
            <th>ACOS</th>
            <th>动作</th>
            <th>建议竞价</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          {items.map((k, i) => (
            <tr key={i}>
              <td style={{ fontFamily: "monospace", fontSize: 10 }}>{k.keyword}</td>
              <td>{k.match_type}</td>
              <td>{k.impressions}</td>
              <td>{k.clicks}</td>
              <td>{k.spend}</td>
              <td>{k.orders}</td>
              <td>{k.acos}</td>
              <td><span className={"act-" + String(k.action || "")}>{k.action}</span></td>
              <td>{k.suggested_bid}</td>
              <td style={{ fontSize: 10, color: "var(--t3)" }}>{k.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function AdNewKwTable({ items }: { items: NonNullable<AdAuditStructured["new_keyword_candidates"]> }) {
  return (
    <>
      <SectionHeader icon="✨" text="新增关键词候选" />
      <table className="tbl">
        <thead>
          <tr>
            <th>候选词</th>
            <th>来源搜索词</th>
            <th>曝光</th>
            <th>订单</th>
            <th>建议竞价</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          {items.map((k, i) => (
            <tr key={i}>
              <td style={{ fontFamily: "monospace", fontSize: 10 }}>{k.keyword}</td>
              <td style={{ fontSize: 10, color: "var(--t3)" }}>{k.source_search_term}</td>
              <td>{k.impressions}</td>
              <td>{k.orders}</td>
              <td>{k.suggested_bid}</td>
              <td style={{ fontSize: 10, color: "var(--t3)" }}>{k.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function AdNegTable({ items }: { items: NonNullable<AdAuditStructured["negative_suggestions"]> }) {
  return (
    <>
      <SectionHeader icon="🚫" text="否词建议" />
      <table className="tbl">
        <thead>
          <tr>
            <th>词 / 短语</th>
            <th style={{ width: 80 }}>类型</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          {items.map((n, i) => (
            <tr key={i}>
              <td style={{ fontFamily: "monospace", fontSize: 10 }}>{n.term}</td>
              <td>
                <span className={n.type === "immediate" ? "act-cut" : "act-watch"}>
                  {n.type === "immediate" ? "立即否定" : "观察后否定"}
                </span>
              </td>
              <td style={{ fontSize: 10, color: "var(--t3)" }}>{n.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function AdPlacementTable({ items }: { items: NonNullable<AdAuditStructured["placement_diagnosis"]> }) {
  return (
    <>
      <SectionHeader icon="📍" text="位置诊断" />
      <table className="tbl">
        <thead>
          <tr>
            <th>位置</th>
            <th>曝光</th>
            <th>点击</th>
            <th>花费</th>
            <th>订单</th>
            <th>ACOS</th>
            <th>CTR</th>
            <th>CVR</th>
            <th>建议</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p, i) => (
            <tr key={i}>
              <td>{p.placement}</td>
              <td>{p.impressions}</td>
              <td>{p.clicks}</td>
              <td>{p.spend}</td>
              <td>{p.orders}</td>
              <td>{p.acos}</td>
              <td>{p.ctr}</td>
              <td>{p.cvr}</td>
              <td style={{ fontSize: 10, color: "var(--t2)" }}>{p.action}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
