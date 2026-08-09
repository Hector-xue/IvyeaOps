import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BookOpenCheck,
  CheckCircle2,
  Clock3,
  Database,
  ExternalLink,
  FileCheck2,
  FlaskConical,
  GitCompareArrows,
  Grid3X3,
  Loader2,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useAuth } from "../../App";
import { useConfirm } from "../../components/ConfirmDialog";
import {
  ivyeaKnowledgeChangeApply,
  ivyeaKnowledgeChangeDraft,
  ivyeaKnowledgeChangePacket,
  ivyeaKnowledgeChanges,
  ivyeaKnowledgeGovernance,
  ivyeaKnowledgeEvidence,
  ivyeaKnowledgeEvidenceApply,
  ivyeaKnowledgeEvidenceDraft,
  ivyeaKnowledgeQuality,
  ivyeaKnowledgeReviewChange,
  ivyeaKnowledgeSync,
  type KnowledgeChange,
  type KnowledgeChangePacket,
  type KnowledgeCoverageRequirement,
  type KnowledgeGovernance,
  type KnowledgeEvidencePayload,
  type KnowledgeQuality,
  type KnowledgeReviewStatus,
} from "../../api/ivyeaAgent";
import "../../styles/knowledge-governance.css";
import { errText } from "../../lib/errText";

type View = "overview" | "changes" | "coverage" | "freshness" | "quality" | "evidence" | "conflicts";

const VIEWS: Array<{ key: View; label: string; icon: typeof ShieldCheck }> = [
  { key: "overview", label: "总览", icon: ShieldCheck },
  { key: "changes", label: "变更审核", icon: GitCompareArrows },
  { key: "coverage", label: "覆盖矩阵", icon: Grid3X3 },
  { key: "freshness", label: "时效监控", icon: Clock3 },
  { key: "quality", label: "质量评测", icon: FlaskConical },
  { key: "evidence", label: "账户证据", icon: Database },
  { key: "conflicts", label: "冲突风险", icon: AlertTriangle },
];

const EVIDENCE_KINDS = [
  ["performance_notification", "账户健康/绩效通知"],
  ["listing_issue", "Listing 报错"],
  ["registration_notice", "注册验证通知"],
  ["compliance_notice", "商品合规通知"],
  ["fee_record", "费用记录"],
  ["tax_report", "税务报告"],
  ["settlement_report", "结算对账"],
  ["returns_report", "退货/SAFE-T 索赔"],
  ["brand_notice", "品牌保护通知"],
  ["support_case", "Seller Support 回复"],
  ["api_notification", "SP-API 通知"],
] as const;

const MARKETPLACES = ["US", "CA", "UK", "DE", "FR", "IT", "ES", "JP", "AU", "SG", "IN", "AE", "GLOBAL"];

const EMPTY_EVIDENCE: KnowledgeEvidencePayload = {
  authorized: false,
  rights_confirmed: false,
  kind: "performance_notification",
  marketplace: "US",
  title: "",
  source_url: "",
  content: "",
  exact_message: "",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "待审核",
  approved: "已批准",
  rejected: "已拒绝",
  superseded: "已替代",
  strong: "强覆盖",
  governed: "治理规则",
  review_due: "待复核",
  synthesis_only: "仅综合",
  gap: "缺口",
  current: "当前",
  unseen: "未初始化",
  overdue: "已逾期",
  error: "错误",
};

function errorMessage(error: any, fallback = "操作失败") {
  return errText(error, fallback);
}

function pct(value: number | undefined) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function Badge({ status, children }: { status: string; children?: React.ReactNode }) {
  return <span className={`kg-badge kg-${status}`}>{children || STATUS_LABEL[status] || status}</span>;
}

function MetricCard({
  label,
  value,
  hint,
  tone = "neutral",
  onClick,
}: {
  label: string;
  value: string | number;
  hint: string;
  tone?: "good" | "warn" | "bad" | "info" | "neutral";
  onClick?: () => void;
}) {
  return (
    <button className={`kg-metric kg-metric-${tone}`} onClick={onClick} disabled={!onClick}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{hint}</small>
    </button>
  );
}

export default function KnowledgeGovernancePanel() {
  const confirm = useConfirm();
  const { role, username } = useAuth();
  const isAdmin = role === "admin";
  const [view, setView] = useState<View>("overview");
  const [governance, setGovernance] = useState<KnowledgeGovernance | null>(null);
  const [changes, setChanges] = useState<KnowledgeChange[]>([]);
  const [changeSummary, setChangeSummary] = useState<Record<string, number>>({});
  const [statusFilter, setStatusFilter] = useState<"" | KnowledgeReviewStatus>("");
  const [selected, setSelected] = useState<KnowledgeChange | null>(null);
  const [quality, setQuality] = useState<KnowledgeQuality | null>(null);
  const [evidenceRows, setEvidenceRows] = useState<any[]>([]);
  const [evidenceForm, setEvidenceForm] = useState<KnowledgeEvidencePayload>({ ...EMPTY_EVIDENCE });
  const [evidenceDraft, setEvidenceDraft] = useState<any>(null);
  const [packet, setPacket] = useState<KnowledgeChangePacket | null>(null);
  const [targetCardId, setTargetCardId] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [draftTitle, setDraftTitle] = useState("");
  const [newCardId, setNewCardId] = useState("");
  const [draftPreview, setDraftPreview] = useState<any>(null);
  const [reviewNote, setReviewNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");

  const loadChanges = useCallback(async (status = statusFilter) => {
    const result = await ivyeaKnowledgeChanges(status, 200);
    setChanges(result.changes || []);
    setChangeSummary(result.summary || {});
    setSelected((current) => {
      if (!current) return null;
      return result.changes.find((row) => row.event_id === current.event_id) || null;
    });
  }, [statusFilter]);

  const loadGovernance = useCallback(async () => {
    const result = await ivyeaKnowledgeGovernance();
    setGovernance(result);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      await Promise.all([loadGovernance(), loadChanges()]);
    } catch (error: any) {
      setError(errorMessage(error, "知识治理服务加载失败"));
    } finally {
      setLoading(false);
    }
  }, [loadChanges, loadGovernance]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (view !== "quality" || quality) return;
    setBusy("quality");
    ivyeaKnowledgeQuality()
      .then(setQuality)
      .catch((error) => setError(errorMessage(error, "质量评测失败")))
      .finally(() => setBusy(""));
  }, [quality, view]);

  useEffect(() => {
    if (view !== "evidence") return;
    ivyeaKnowledgeEvidence(100)
      .then((result) => setEvidenceRows(result.evidence || []))
      .catch((error) => setError(errorMessage(error, "账户证据列表加载失败")));
  }, [view]);

  const setEvidenceField = (key: keyof KnowledgeEvidencePayload, value: any) => {
    setEvidenceForm((current) => ({ ...current, [key]: value }));
    setEvidenceDraft(null);
  };

  const evidencePayload = (confirmApply = false): KnowledgeEvidencePayload => {
    const payload: KnowledgeEvidencePayload = {
      ...evidenceForm,
      confirm: confirmApply,
      rebuild: true,
    };
    Object.keys(payload).forEach((key) => {
      const value = payload[key as keyof KnowledgeEvidencePayload];
      if (typeof value === "string" && !value.trim()) delete (payload as any)[key];
    });
    return payload;
  };

  const previewEvidence = async () => {
    if (!isAdmin) return;
    setBusy("evidence-preview");
    setError("");
    try {
      const result = await ivyeaKnowledgeEvidenceDraft(evidencePayload(false));
      setEvidenceDraft(result);
      setFlash("账户证据已在本机完成脱敏并生成草案；尚未写入知识库。");
    } catch (error: any) {
      setError(errorMessage(error, "账户证据草案生成失败"));
    } finally {
      setBusy("");
    }
  };

  const applyEvidence = async () => {
    if (!isAdmin || !evidenceDraft) return;
    const accepted = await confirm({
      title: "写入脱敏账户证据",
      message: "只保存脱敏后的知识卡和哈希引用，不保留原始文档或明文账户标识。确认写入并重建索引？",
      confirmText: "确认写入",
      icon: "✓",
    });
    if (!accepted) return;
    setBusy("evidence-apply");
    setError("");
    try {
      const result = await ivyeaKnowledgeEvidenceApply(evidencePayload(true));
      if (!result.ok) throw new Error(result.error || result.result?.error || "写入失败");
      setFlash(`已写入脱敏账户证据 ${result.evidence?.id || ""}。`);
      setEvidenceForm({ ...EMPTY_EVIDENCE });
      setEvidenceDraft(null);
      const listed = await ivyeaKnowledgeEvidence(100);
      setEvidenceRows(listed.evidence || []);
    } catch (error: any) {
      setError(errorMessage(error, "账户证据写入失败"));
    } finally {
      setBusy("");
    }
  };

  const selectChange = (change: KnowledgeChange) => {
    setSelected(change);
    setPacket(null);
    setDraftPreview(null);
    setReviewNote(change.review_note || "");
    setTargetCardId("");
    setDraftBody("");
    setDraftTitle("");
    setNewCardId("");
  };

  const review = async (decision: Exclude<KnowledgeReviewStatus, "pending">) => {
    if (!selected || !isAdmin) return;
    const verb = decision === "approved" ? "批准" : decision === "rejected" ? "拒绝" : "标记为已替代";
    const accepted = await confirm({
      title: `${verb}官方来源变更`,
      message: decision === "approved"
        ? "批准只允许进入知识草案编辑，不会自动发布。系统会再次校验来源快照哈希。"
        : `确定${verb} ${selected.event_id}？该决定会写入不可变审核历史。`,
      confirmText: verb,
      danger: decision !== "approved",
      icon: decision === "approved" ? "✓" : "!",
    });
    if (!accepted) return;
    setBusy("review");
    setError("");
    try {
      await ivyeaKnowledgeReviewChange({
        eventId: selected.event_id,
        decision,
        reviewer: username || "local-operator",
        note: reviewNote,
        confirm: true,
      });
      setFlash(`${selected.event_id} 已${verb}；知识尚未发布。`);
      await Promise.all([loadGovernance(), loadChanges()]);
    } catch (error: any) {
      setError(errorMessage(error, "审核失败"));
    } finally {
      setBusy("");
    }
  };

  const loadPacket = async (cardId = targetCardId) => {
    if (!selected) return;
    setDetailLoading(true);
    setError("");
    try {
      const result = await ivyeaKnowledgeChangePacket(selected.event_id, cardId);
      const next = result.packet;
      setPacket(next);
      const resolvedCard = next.target?.id || cardId;
      setTargetCardId(resolvedCard || "");
      setDraftTitle(next.target?.title || selected.title || selected.id);
      setDraftBody(next.target?.body || `# ${selected.title || selected.id}\n\n## Reviewed change\n\n`);
      setDraftPreview(null);
    } catch (error: any) {
      setError(errorMessage(error, "审核包加载失败"));
    } finally {
      setDetailLoading(false);
    }
  };

  const changeTarget = async (cardId: string) => {
    setTargetCardId(cardId);
    await loadPacket(cardId);
  };

  const previewDraft = async () => {
    if (!selected || !draftBody.trim()) return;
    setBusy("preview");
    setError("");
    try {
      const result = await ivyeaKnowledgeChangeDraft({
        eventId: selected.event_id,
        cardId: targetCardId,
        newCardId,
        title: draftTitle,
        body: draftBody,
      });
      setDraftPreview(result.draft || null);
      setFlash("草案 diff 已生成；仍未发布。请复核后再确认应用。");
    } catch (error: any) {
      setError(errorMessage(error, "草案生成失败"));
    } finally {
      setBusy("");
    }
  };

  const publishDraft = async () => {
    if (!selected || !draftBody.trim() || !isAdmin) return;
    const accepted = await confirm({
      title: "发布运行时审核知识卡",
      message: "这会创建一张带 change-event 和 source-hash 的运行时官方更新卡，并重建本地索引；不会修改安装包中的内置卡。是否继续？",
      confirmText: "确认发布",
      danger: false,
      icon: "✓",
    });
    if (!accepted) return;
    setBusy("publish");
    setError("");
    try {
      const result = await ivyeaKnowledgeChangeApply({
        eventId: selected.event_id,
        cardId: targetCardId,
        newCardId,
        title: draftTitle,
        body: draftBody,
        confirm: true,
        rebuild: true,
      });
      if (!result.ok) throw new Error(result.error || result.result?.error || "发布失败");
      setFlash(`已发布 ${result.result?.card?.id || "审核知识卡"}，来源事件 ${selected.event_id} 已登记。`);
      setDraftPreview(null);
      setPacket(null);
      await Promise.all([loadGovernance(), loadChanges()]);
    } catch (error: any) {
      setError(errorMessage(error, "发布失败"));
    } finally {
      setBusy("");
    }
  };

  const syncSources = async () => {
    if (!isAdmin) return;
    const accepted = await confirm({
      title: "检查官方来源更新", message: "只检查到期的公开官方源。变更进入审核队列，不会自动发布。", confirmText: "开始检查",
    });
    if (!accepted) return;
    setBusy("sync");
    setError("");
    try {
      const result = await ivyeaKnowledgeSync([], false);
      setFlash(`来源检查完成：selected=${result.summary?.selected || 0}，error=${result.summary?.error || 0}。`);
      await refresh();
    } catch (error: any) {
      setError(errorMessage(error, "官方来源同步失败"));
    } finally {
      setBusy("");
    }
  };

  const coverageMatrix = useMemo(() => {
    const rows = governance?.coverage.requirements || [];
    const domains = Array.from(new Set(rows.map((row) => row.domain)));
    const markets = Array.from(new Set(rows.map((row) => row.marketplace)));
    const map = new Map(rows.map((row) => [`${row.domain}:${row.marketplace}`, row]));
    return { domains, markets, map };
  }, [governance]);

  if (loading && !governance) {
    return <div className="kg-loading"><Loader2 className="spin" size={18} />正在加载知识治理数据…</div>;
  }

  if (!governance) {
    // 瞬时网络失败（Network Error / timeout）≠ 能力缺失：给重试出口，别把用户
    // 引去系统配置死路。仅接口明确 404（旧版 serve 没有治理接口）才提示升级。
    const transient = /network|timeout|超时/i.test(error || "");
    return (
      <div className="kg-unavailable">
        <AlertTriangle size={28} />
        <strong>{transient ? "知识治理数据加载失败" : "知识治理能力不可用"}</strong>
        <p>{transient
          ? `连接失败（${error}），可能是网络瞬断或服务正在重启，重试即可。`
          : error || "当前 IvyeaAgent 版本没有治理接口。请先升级并重启本地服务。"}</p>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="tbtn" onClick={() => refresh()} disabled={loading}>
            {loading ? <Loader2 className="spin" size={13} /> : null}重试
          </button>
          {!transient && <a className="tbtn" href="/hub-settings">前往系统配置</a>}
        </div>
      </div>
    );
  }

  const summary = governance.summary;
  const cover = governance.coverage.summary;
  const qualitySummary = quality?.quality.summary;

  return (
    <div className="kg-root">
      <div className="kg-head">
        <div>
          <div className="kg-title"><ShieldCheck size={18} />IvyeaAgent 知识治理中心</div>
          <div className="kg-subtitle">官方来源审核、知识覆盖、时效、质量与冲突证据统一管理</div>
        </div>
        <div className="kg-head-actions">
          {!isAdmin && <Badge status="review_due">只读用户</Badge>}
          <button className="tbtn" onClick={refresh} disabled={loading || !!busy}>
            <RefreshCw size={13} className={loading ? "spin" : ""} />刷新
          </button>
        </div>
      </div>

      {error && <div className="kg-alert kg-alert-bad"><XCircle size={15} />{error}</div>}
      {flash && <div className="kg-alert kg-alert-info"><CheckCircle2 size={15} />{flash}<button onClick={() => setFlash("")}>×</button></div>}

      <div className="kg-nav">
        {VIEWS.map(({ key, label, icon: Icon }) => (
          <button key={key} data-testid={`knowledge-view-${key}`} className={view === key ? "active" : ""} onClick={() => setView(key)}>
            <Icon size={14} />{label}
            {key === "changes" && summary.pending_reviews > 0 && <em>{summary.pending_reviews}</em>}
            {key === "conflicts" && summary.conflicts > 0 && <em>{summary.conflicts}</em>}
          </button>
        ))}
      </div>

      {view === "overview" && (
        <div className="kg-section-stack">
          <div className="kg-metrics">
            <MetricCard label="治理状态" value={governance.healthy ? "健康" : "需处理"} hint="综合审核、覆盖、时效与冲突" tone={governance.healthy ? "good" : "warn"} />
            <MetricCard label="待审核" value={summary.pending_reviews} hint="官方来源变更" tone={summary.pending_reviews ? "warn" : "good"} onClick={() => setView("changes")} />
            <MetricCard label="已批准未发布" value={summary.approved_not_published} hint="等待知识草案" tone={summary.approved_not_published ? "info" : "neutral"} onClick={() => setView("changes")} />
            <MetricCard label="关键覆盖" value={`${cover.covered}/${cover.requirements}`} hint={`覆盖率 ${pct(cover.coverage_rate)}`} tone={cover.gaps ? "warn" : "good"} onClick={() => setView("coverage")} />
            <MetricCard label="过期知识" value={summary.stale_cards} hint={`监控错误 ${summary.monitor_errors}`} tone={summary.stale_cards || summary.monitor_errors ? "bad" : "good"} onClick={() => setView("freshness")} />
            <MetricCard label="冲突风险" value={summary.conflicts} hint="算法/绝对数值/来源边界" tone={summary.conflicts ? "bad" : "good"} onClick={() => setView("conflicts")} />
          </div>
          <div className="kg-grid-2">
            <section className="kg-card">
              <h3><AlertTriangle size={15} />优先补齐的覆盖缺口</h3>
              <div className="kg-list">
                {governance.coverage.requirements.filter((row) => row.status === "gap").slice(0, 10).map((row) => (
                  <button key={`${row.domain}:${row.marketplace}`} onClick={() => setView("coverage")}>
                    <Badge status="gap" />
                    <span>{row.domain}</span>
                    <b>{row.marketplace}</b>
                  </button>
                ))}
              </div>
            </section>
            <section className="kg-card">
              <h3><FileCheck2 size={15} />治理规则</h3>
              <ul className="kg-rules">
                <li>官方变更必须先审核，批准不等于发布。</li>
                <li>发布前重新校验来源快照哈希并生成正文 diff。</li>
                <li>运行时更新卡保留 change-event、source-hash 和目标卡关联。</li>
                <li>过期证据自动降权，高风险回答要求复核当前官方来源。</li>
              </ul>
            </section>
          </div>
        </div>
      )}

      {view === "changes" && (
        <div className="kg-changes-layout">
          <section className="kg-card kg-change-list-card">
            <div className="kg-card-head">
              <h3><GitCompareArrows size={15} />官方来源变更</h3>
              <select value={statusFilter} onChange={async (event) => {
                const status = event.target.value as "" | KnowledgeReviewStatus;
                setStatusFilter(status);
                setLoading(true);
                try { await loadChanges(status); } catch (error: any) { setError(errorMessage(error)); } finally { setLoading(false); }
              }}>
                <option value="">全部状态</option>
                <option value="pending">待审核</option>
                <option value="approved">已批准</option>
                <option value="rejected">已拒绝</option>
                <option value="superseded">已替代</option>
              </select>
            </div>
            <div className="kg-change-summary">
              <span>总计 {changeSummary.changes || 0}</span><span>待审 {changeSummary.pending || 0}</span>
              <span>已发布 {changeSummary.published || 0}</span>
            </div>
            <div className="kg-change-list">
              {!changes.length && <div className="kg-empty">当前筛选条件下没有变更。</div>}
              {changes.map((change) => (
                <button key={change.event_id} className={selected?.event_id === change.event_id ? "active" : ""} onClick={() => selectChange(change)}>
                  <div><Badge status={change.review_status} />{change.published && <Badge status="strong">已发布</Badge>}</div>
                  <strong>{change.title || change.id}</strong>
                  <small>{change.event_id} · {change.checked_at || "-"}</small>
                </button>
              ))}
            </div>
          </section>

          <section className="kg-card kg-change-detail">
            {!selected ? <div className="kg-empty">选择一条变更查看来源 diff 和审核操作。</div> : (
              <>
                <div className="kg-card-head">
                  <div>
                    <h3>{selected.title || selected.id}</h3>
                    <div className="kg-meta">{selected.event_id} · {selected.evidence_class || "-"}</div>
                  </div>
                  <Badge status={selected.review_status} />
                </div>
                <div className="kg-evidence-meta">
                  <span>站点：{selected.marketplaces?.join(", ") || "GLOBAL"}</span>
                  <span>语言：{selected.locales?.join(", ") || "-"}</span>
                  <span>hash：{selected.content_hash?.slice(0, 12) || "-"}</span>
                  {selected.url && <a href={selected.url} target="_blank" rel="noreferrer">官方来源 <ExternalLink size={11} /></a>}
                </div>
                <div className="kg-diff"><div>官方来源变化</div><pre>{selected.diff || "（事件未携带文本 diff，请打开官方来源或审核包查看快照。）"}</pre></div>

                {selected.review_status === "pending" && (
                  <div className="kg-review-box">
                    <label>审核备注<textarea value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} maxLength={1000} placeholder="记录核对范围、适用站点和决定依据" /></label>
                    {isAdmin ? <div className="kg-actions">
                      <button className="kg-primary" onClick={() => review("approved")} disabled={!!busy}>{busy === "review" ? <Loader2 className="spin" size={13} /> : <CheckCircle2 size={13} />}批准进入草案</button>
                      <button className="kg-danger" onClick={() => review("rejected")} disabled={!!busy}><XCircle size={13} />拒绝</button>
                      <button className="tbtn" onClick={() => review("superseded")} disabled={!!busy}>标记已替代</button>
                    </div> : <div className="kg-readonly">只有管理员可以提交审核决定。</div>}
                  </div>
                )}

                {selected.review_status === "approved" && !selected.published && (
                  <div className="kg-draft-zone">
                    {!packet ? (
                      <button className="kg-primary" onClick={() => loadPacket()} disabled={detailLoading || !isAdmin}>
                        {detailLoading ? <Loader2 className="spin" size={13} /> : <BookOpenCheck size={13} />}加载审核包并生成草案
                      </button>
                    ) : (
                      <>
                        <div className="kg-alert kg-alert-info"><ShieldCheck size={14} />审核包只是证据，不会把网页快照直接写入知识库。</div>
                        <div className="kg-draft-fields">
                          <label>关联的现有知识卡<select value={targetCardId} onChange={(event) => changeTarget(event.target.value)}>
                            <option value="">请选择知识卡</option>
                            {packet.candidates.map((card) => <option key={card.id} value={card.id}>{card.title} · {card.id}</option>)}
                          </select></label>
                          <label>新运行时卡 ID<input value={newCardId} onChange={(event) => setNewCardId(event.target.value)} placeholder="留空则根据来源和 hash 自动生成" /></label>
                          <label className="kg-wide">标题<input value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} /></label>
                          <label className="kg-wide">审核后的知识正文<textarea className="kg-editor" value={draftBody} onChange={(event) => { setDraftBody(event.target.value); setDraftPreview(null); }} /></label>
                        </div>
                        <details className="kg-snapshot"><summary>查看官方快照摘录（{packet.snapshot_chars.toLocaleString()} 字符）</summary><pre>{packet.snapshot_excerpt}</pre></details>
                        {draftPreview?.diff && <div className="kg-diff kg-proposed-diff"><div>待发布知识 diff</div><pre>{draftPreview.diff}</pre></div>}
                        <div className="kg-actions">
                          <button className="tbtn" onClick={previewDraft} disabled={!draftBody.trim() || busy === "preview"}>{busy === "preview" ? <Loader2 className="spin" size={13} /> : <GitCompareArrows size={13} />}生成 diff</button>
                          <button className="kg-primary" onClick={publishDraft} disabled={!draftPreview || busy === "publish"}>{busy === "publish" ? <Loader2 className="spin" size={13} /> : <FileCheck2 size={13} />}二次确认发布</button>
                        </div>
                      </>
                    )}
                  </div>
                )}
                {selected.published && <div className="kg-alert kg-alert-good"><CheckCircle2 size={14} />已发布为 {selected.published_card_id || "审核知识卡"}，时间 {selected.published_at || "-"}。</div>}
              </>
            )}
          </section>
        </div>
      )}

      {view === "coverage" && (
        <section className="kg-card">
          <div className="kg-card-head"><h3><Grid3X3 size={15} />关键知识域 × Marketplace</h3><span>{cover.covered}/{cover.requirements} · {pct(cover.coverage_rate)}</span></div>
          <p className="kg-policy">{governance.coverage.policy}</p>
          <div className="kg-matrix-wrap"><table className="kg-matrix"><thead><tr><th>知识域</th>{coverageMatrix.markets.map((market) => <th key={market}>{market}</th>)}</tr></thead><tbody>
            {coverageMatrix.domains.map((domain) => <tr key={domain}><th>{domain}</th>{coverageMatrix.markets.map((market) => {
              const row = coverageMatrix.map.get(`${domain}:${market}`) as KnowledgeCoverageRequirement | undefined;
              return <td key={market}>{row ? <div className={`kg-cell kg-cell-${row.status}`} title={row.card_ids.join("\n") || "未覆盖"}><Badge status={row.status} /><small>{row.card_ids.length ? `${row.card_ids.length} 卡` : "需补充"}</small></div> : <span className="kg-na">—</span>}</td>;
            })}</tr>)}
          </tbody></table></div>
        </section>
      )}

      {view === "freshness" && (
        <div className="kg-section-stack">
          <div className="kg-card-head"><h3><Clock3 size={15} />知识卡与官方来源时效</h3>{isAdmin && <button className="tbtn" onClick={syncSources} disabled={busy === "sync"}>{busy === "sync" ? <Loader2 className="spin" size={13} /> : <RefreshCw size={13} />}检查到期来源</button>}</div>
          <div className="kg-metrics kg-metrics-small">
            {Object.entries(governance.freshness.summary.card_freshness).map(([key, value]) => <MetricCard key={key} label={STATUS_LABEL[key] || key} value={value} hint="知识卡" tone={key.includes("stale") ? "bad" : key.includes("aging") || key === "undated" ? "warn" : "good"} />)}
            {Object.entries(governance.freshness.summary.monitor_status).map(([key, value]) => <MetricCard key={`source-${key}`} label={`来源${STATUS_LABEL[key] || key}`} value={value} hint="监控源" tone={key === "error" ? "bad" : key === "overdue" ? "warn" : "neutral"} />)}
          </div>
          <section className="kg-card"><div className="kg-table-wrap"><table className="kg-table"><thead><tr><th>官方来源</th><th>状态</th><th>周期</th><th>最近检查</th><th>最近变化</th></tr></thead><tbody>
            {governance.freshness.sources.map((source) => <tr key={source.id}><td>{source.id}{source.last_error && <small>{source.last_error}</small>}</td><td><Badge status={source.status} /></td><td>{source.cadence_hours}h</td><td>{source.last_checked || "-"}</td><td>{source.last_change || "-"}</td></tr>)}
          </tbody></table></div></section>
        </div>
      )}

      {view === "quality" && (
        <section className="kg-card">
          <div className="kg-card-head"><h3><FlaskConical size={15} />持续质量评测</h3><button className="tbtn" onClick={async () => { setBusy("quality"); try { setQuality(await ivyeaKnowledgeQuality()); } catch (error: any) { setError(errorMessage(error)); } finally { setBusy(""); } }} disabled={busy === "quality"}>{busy === "quality" ? <Loader2 className="spin" size={13} /> : <RefreshCw size={13} />}重新运行</button></div>
          {qualitySummary ? <>
            <div className="kg-quality-hero"><div className={quality?.quality.ok ? "pass" : "fail"}>{quality?.quality.ok ? "PASS" : "FAIL"}</div><strong>{qualitySummary.passed}/{qualitySummary.cases}</strong><span>通过率 {pct(qualitySummary.pass_rate)}</span></div>
            <div className="kg-table-wrap"><table className="kg-table"><thead><tr><th>用例</th><th>知识域</th><th>风险</th><th>命中排名</th><th>结果</th></tr></thead><tbody>
              {quality?.quality.results.map((row) => <tr key={row.id}><td>{row.id}<small>{row.query}</small></td><td>{row.domain}</td><td>{row.risk}</td><td>{Object.entries(row.matched_ranks).map(([id, rank]) => `${id}:${rank || "-"}`).join(" · ")}</td><td>{row.ok ? <Badge status="strong">通过</Badge> : <Badge status="gap">失败</Badge>}</td></tr>)}
            </tbody></table></div>
          </> : <div className="kg-empty">{busy === "quality" ? "正在运行评测…" : "尚未运行评测。"}</div>}
        </section>
      )}

      {view === "evidence" && (
        <div className="kg-section-stack" data-testid="knowledge-evidence-view">
          <section className="kg-card">
            <div className="kg-card-head">
              <div><h3><Database size={15} />授权账户证据导入</h3><small>粘贴 Seller Central 或官方报表中的必要文本；不要上传身份证、银行卡或完整原始文件。</small></div>
              {!isAdmin && <Badge status="review_due">管理员可写</Badge>}
            </div>
            <div className="kg-alert kg-alert-info"><ShieldCheck size={14} />数据只发送到本机 IvyeaAgent。账户号、订单号、case、notification、结算和交易标识只保存哈希引用；邮箱、电话、地址、证件、银行和税号会专项脱敏。</div>
            <div className="kg-draft-fields kg-evidence-fields">
              <label>证据类型<select data-testid="evidence-kind" value={evidenceForm.kind} onChange={(event) => setEvidenceField("kind", event.target.value)}>
                {EVIDENCE_KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select></label>
              <label>Marketplace<select data-testid="evidence-marketplace" value={evidenceForm.marketplace} onChange={(event) => setEvidenceField("marketplace", event.target.value)}>
                {MARKETPLACES.map((value) => <option key={value} value={value}>{value}</option>)}
              </select></label>
              <label className="kg-wide">标题<input data-testid="evidence-title" value={evidenceForm.title || ""} onChange={(event) => setEvidenceField("title", event.target.value)} placeholder="例如：US 站账户健康通知 2026-07" /></label>
              <label className="kg-wide">官方来源 URL<input data-testid="evidence-source-url" value={evidenceForm.source_url || ""} onChange={(event) => setEvidenceField("source_url", event.target.value)} placeholder="https://sellercentral.amazon.com/...；留空则生成本地 export 引用" /></label>
              <label className="kg-wide">原文错误/通知信息<textarea data-testid="evidence-message" value={evidenceForm.exact_message || ""} onChange={(event) => setEvidenceField("exact_message", event.target.value)} placeholder="粘贴需要诊断的准确通知或错误文本" /></label>
              <label className="kg-wide">必要上下文<textarea data-testid="evidence-content" className="kg-editor" value={evidenceForm.content || ""} onChange={(event) => setEvidenceField("content", event.target.value)} placeholder="只粘贴诊断所需的政策、交易、报表或处理上下文" /></label>
            </div>
            <details className="kg-snapshot"><summary>补充结构化字段（用于提高诊断就绪度）</summary>
              <div className="kg-draft-fields kg-evidence-fields">
                {([[
                  "account_id", "账户标识"], ["case_id", "Case ID"], ["notification_id", "Notification ID"],
                  ["order_id", "Order ID"], ["claim_id", "Claim / SAFE-T ID"], ["settlement_id", "Settlement ID"],
                  ["transaction_id", "Transaction ID"], ["asin", "ASIN"], ["sku", "SKU"],
                  ["product_type", "Product type"], ["error_code", "错误码"], ["policy", "政策/状态"],
                  ["program", "品牌项目"], ["report_type", "报告类型"], ["currency", "币种"],
                  ["registration_stage", "注册阶段"], ["document_request", "要求的资料"],
                ] as Array<[keyof KnowledgeEvidencePayload, string]>).map(([key, label]) => (
                  <label key={key}>{label}<input value={String(evidenceForm[key] || "")} onChange={(event) => setEvidenceField(key, event.target.value)} /></label>
                ))}
                <label>账户状态<select value={evidenceForm.account_status || ""} onChange={(event) => setEvidenceField("account_status", event.target.value)}><option value="">未指定</option><option value="NORMAL">NORMAL</option><option value="AT_RISK">AT_RISK</option><option value="DEACTIVATED">DEACTIVATED</option></select></label>
                <label>记录类型<select value={evidenceForm.record_type || ""} onChange={(event) => setEvidenceField("record_type", event.target.value)}><option value="">未指定</option><option value="observed">observed</option><option value="estimate">estimate</option></select></label>
              </div>
            </details>
            <div className="kg-consents">
              <label><input data-testid="evidence-authorized" type="checkbox" checked={evidenceForm.authorized} onChange={(event) => setEvidenceField("authorized", event.target.checked)} />我明确授权在本机处理这些数据</label>
              <label><input data-testid="evidence-rights" type="checkbox" checked={evidenceForm.rights_confirmed} onChange={(event) => setEvidenceField("rights_confirmed", event.target.checked)} />我确认有权处理和保存这份账户证据</label>
            </div>
            {evidenceDraft && <div className="kg-evidence-preview" data-testid="evidence-preview">
              <div className="kg-alert kg-alert-good"><CheckCircle2 size={14} />草案已生成：{evidenceDraft.evidence?.id} · ready={String(evidenceDraft.evidence?.diagnostic?.ready_for_diagnosis)}</div>
              <div className="kg-evidence-preview-grid"><span>缺少字段：{evidenceDraft.evidence?.diagnostic?.missing_inputs?.join(", ") || "无"}</span><span>脱敏：{JSON.stringify(evidenceDraft.evidence?.redactions || {})}</span><span>原始文件保留：否</span></div>
              {evidenceDraft.draft?.diff && <div className="kg-diff"><div>待写入知识卡 diff</div><pre>{evidenceDraft.draft.diff}</pre></div>}
            </div>}
            <div className="kg-actions">
              <button data-testid="evidence-preview-button" className="tbtn" onClick={previewEvidence} disabled={!isAdmin || busy === "evidence-preview" || !evidenceForm.authorized || !evidenceForm.rights_confirmed}>{busy === "evidence-preview" ? <Loader2 className="spin" size={13} /> : <GitCompareArrows size={13} />}生成脱敏草案</button>
              <button data-testid="evidence-apply-button" className="kg-primary" onClick={applyEvidence} disabled={!evidenceDraft || busy === "evidence-apply"}>{busy === "evidence-apply" ? <Loader2 className="spin" size={13} /> : <FileCheck2 size={13} />}确认写入知识库</button>
            </div>
          </section>
          <section className="kg-card">
            <div className="kg-card-head"><h3><FileCheck2 size={15} />已应用的脱敏账户证据</h3><span>{evidenceRows.length} 条</span></div>
            {!evidenceRows.length ? <div className="kg-empty">暂无账户证据。</div> : <div className="kg-table-wrap"><table className="kg-table"><thead><tr><th>证据</th><th>类型</th><th>站点</th><th>诊断就绪</th><th>知识卡</th></tr></thead><tbody>
              {evidenceRows.map((row) => <tr key={row.id}><td>{row.title}<small>{row.id}</small></td><td>{row.kind}</td><td>{row.marketplace}</td><td>{row.diagnostic?.ready_for_diagnosis ? <Badge status="strong">就绪</Badge> : <Badge status="review_due">待补字段</Badge>}</td><td>{row.card_id}</td></tr>)}
            </tbody></table></div>}
          </section>
        </div>
      )}

      {view === "conflicts" && (
        <section className="kg-card">
          <div className="kg-card-head"><h3><AlertTriangle size={15} />知识冲突与证据边界风险</h3><span>{governance.conflicts.length} 条</span></div>
          {!governance.conflicts.length ? <div className="kg-empty"><CheckCircle2 size={20} />未发现明显冲突风险。</div> : <div className="kg-conflicts">
            {governance.conflicts.map((row: any) => <article key={row.fingerprint || `${row.id}-${row.reason_code}`}><div><Badge status={row.level === "fail" ? "gap" : "review_due"}>{row.level}</Badge><strong>{row.id}</strong><code>{row.reason_code}</code></div><p>{row.reason}</p>{row.related?.length > 0 && <small>相关官方卡：{row.related.join(", ")}</small>}</article>)}
          </div>}
        </section>
      )}
    </div>
  );
}
