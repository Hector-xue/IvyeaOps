import { useEffect, useRef, useState } from "react";
import {
  streamPlaybook,
  fetchHistory,
  saveHistoryEntry,
  deleteHistoryEntry,
  clearHistory as apiClearHistory,
  type PlaybookMode,
  type SseEvent,
  type HistoryEntry,
} from "../../api/playbook";
import {
  MarkdownReport,
  triggerDownload,
  markdownToCsv,
  markdownToHtmlPage,
  extractCsvBlock,
  relativeTime,
} from "../../lib/reportFormat";
import { getDataSource, setDataSource, dataSourceMeta, type DataSourceId } from "../../lib/dataSource";
import DataSourcePicker from "../../components/DataSourcePicker";
import { FLAG_URL } from "../../lib/marketplaces";
import DeepAnalysisPanel, { type DeepAnalysisType } from "../../components/DeepAnalysisPanel";

interface LocalHistoryEntry {
  id: string;
  mode: PlaybookMode;
  query: string;
  marketplace: string;
  price: string;
  cost: string;
  provider: string;
  data_source: string;
  elapsedS: number;
  ts: number;
  report: string;
}

function toLocal(e: HistoryEntry): LocalHistoryEntry {
  return { ...e, elapsedS: e.elapsed_s };
}

const MARKETPLACES: { code: string; name: string }[] = [
  { code: "US", name: "美国" },
  { code: "UK", name: "英国" },
  { code: "DE", name: "德国" },
  { code: "FR", name: "法国" },
  { code: "CA", name: "加拿大" },
  { code: "JP", name: "日本" },
  { code: "ES", name: "西班牙" },
  { code: "IT", name: "意大利" },
  { code: "MX", name: "墨西哥" },
  { code: "AU", name: "澳大利亚" },
];

const EXAMPLE_QUERIES: Record<PlaybookMode, string[]> = {
  keyword: ["wireless earbuds", "yoga mat", "air fryer", "led desk lamp"],
  asin: ["B08N5WRWNW", "B09G9FPHY6", "B07ZPKN6YR"],
};

// Deep-analysis presets tailored to a 打法手册 (vs. market research).
const PLAYBOOK_ANALYSIS_TYPES: readonly DeepAnalysisType[] = [
  {
    id: "execute",
    icon: "▶",
    label: "落地执行",
    promptFn: (query, mkt, report) =>
      `以下是一份关于「${query}」（${mkt} 站）的亚马逊打法手册，请把它拆解成可直接落地的执行方案：\n1. 分阶段排期（备货/上架/广告开启/爬排名/旺季收割的时间线）\n2. 每阶段的具体动作清单与负责事项\n3. 关键检查点与达标指标（KPI）\n4. 预算与库存的分批投入建议\n\n---\n${report}`,
  },
  {
    id: "ads",
    icon: "▦",
    label: "广告细化",
    promptFn: (query, mkt, report) =>
      `以下是一份关于「${query}」（${mkt} 站）的亚马逊打法手册，请基于其广告策略进一步细化：\n1. 广告活动结构（SP/SB/SD 分层与命名）\n2. 关键词分组与匹配方式、初始竞价与每日预算\n3. 竞品 ASIN 精准拦截清单\n4. 否词与竞价的迭代规则（按 ACOS/出单表现）\n\n---\n${report}`,
  },
  {
    id: "risk",
    icon: "⚠",
    label: "风险推演",
    promptFn: (query, mkt, report) =>
      `以下是一份关于「${query}」（${mkt} 站）的亚马逊打法手册，请推演执行过程中的风险并给出应对：\n1. 主要风险点（库存/合规/竞争/广告成本失控等）\n2. 每个风险的预警信号与触发阈值\n3. 应对预案与止损动作\n4. 最坏情况下的退出/调整策略\n\n---\n${report}`,
  },
];

type Phase = "idle" | "collecting" | "synthesizing" | "done" | "error";

interface ProgressItem {
  step: string;
  done: number;
  total: number;
}

export default function Playbook() {
  const [mode, setMode] = useState<PlaybookMode>("keyword");
  const [query, setQuery] = useState("");
  const [price, setPrice] = useState("");
  const [cost, setCost] = useState("");
  const [marketplace, setMarketplace] = useState("US");
  const [dataSource, setDataSourceState] = useState<DataSourceId>(getDataSource);
  const changeDataSource = (id: DataSourceId) => { setDataSource(id); setDataSourceState(id); };
  const dsReady = dataSourceMeta(dataSource, "playbook").ready;

  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<ProgressItem | null>(null);
  const [provider, setProvider] = useState("");
  const [actualDataSource, setActualDataSource] = useState<DataSourceId>(dataSource);
  const [actualSourceLabel, setActualSourceLabel] = useState(dataSourceMeta(dataSource, "playbook").name);
  const [attemptingProvider, setAttemptingProvider] = useState("");
  const [report, setReport] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [elapsedS, setElapsedS] = useState<number | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [copyLabel, setCopyLabel] = useState("复制");
  const [dlMenuOpen, setDlMenuOpen] = useState(false);
  const [liveTimer, setLiveTimer] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<LocalHistoryEntry[]>([]);

  const abortRef = useRef<AbortController | null>(null);
  const reportRef = useRef<HTMLDivElement>(null);
  const pickerRef = useRef<HTMLDivElement>(null);
  const dlMenuRef = useRef<HTMLDivElement>(null);
  const startTimeRef = useRef<number>(0);

  const hasCsvBlock = !!extractCsvBlock(report);

  useEffect(() => {
    if (reportRef.current && phase === "synthesizing") {
      reportRef.current.scrollTop = reportRef.current.scrollHeight;
    }
  }, [report, phase]);

  useEffect(() => {
    if (phase !== "collecting" && phase !== "synthesizing") return;
    startTimeRef.current = Date.now();
    setLiveTimer(0);
    const id = setInterval(() => {
      setLiveTimer(Math.round((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [phase]);

  useEffect(() => {
    if (!pickerOpen) return;
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [pickerOpen]);

  useEffect(() => {
    if (!dlMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (dlMenuRef.current && !dlMenuRef.current.contains(e.target as Node)) setDlMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [dlMenuOpen]);

  const handleSubmit = async () => {
    if (!query.trim() || !price.trim() || phase === "collecting" || phase === "synthesizing") return;
    if (!dsReady) return;  // selected data source not wired yet — button is disabled
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setPhase("collecting");
    setProgress(null);
    setReport("");
    setWarnings([]);
    setProvider("");
    setActualDataSource(dataSource);
    setActualSourceLabel(dataSourceMeta(dataSource, "playbook").name);
    setAttemptingProvider("");
    setElapsedS(null);
    setErrorMsg("");

    try {
      await streamPlaybook(
        { mode, query: query.trim(), marketplace, price: price.trim(), cost: cost.trim(), data_source: dataSource },
        (evt: SseEvent) => {
          if (evt.type === "phase") {
            setPhase(evt.phase as Phase);
          } else if (evt.type === "progress") {
            setProgress({ step: evt.step, done: evt.done, total: evt.total });
          } else if (evt.type === "attempt") {
            setAttemptingProvider(evt.provider);
          } else if (evt.type === "source") {
            setActualDataSource(evt.actual as DataSourceId);
            setActualSourceLabel(evt.label);
          } else if (evt.type === "token") {
            setProvider(evt.provider);
            setReport((r) => r + evt.text);
          } else if (evt.type === "warn") {
            setWarnings((w) => [...w, evt.detail]);
          } else if (evt.type === "error") {
            setErrorMsg(evt.detail);
            setPhase("error");
          } else if (evt.type === "done") {
            setProvider(evt.provider);
            if (evt.data_source) setActualDataSource(evt.data_source as DataSourceId);
            if (evt.data_source_label) setActualSourceLabel(evt.data_source_label);
            setElapsedS(evt.elapsed_s);
            setPhase("done");
          }
        },
        ctrl.signal,
      );
    } catch (err: any) {
      if (err?.name !== "AbortError") {
        const raw = String(err?.message || err);
        const friendly = /network error|Failed to fetch|TypeError/i.test(raw)
          ? `与服务器的连接中断。常见原因：(1) AI 合成耗时过长被反代掐断；(2) ${actualSourceLabel} 暂不可用导致采集失败；(3) 服务端 502/503。请检查 IvyeaOps 服务日志，或稍后重试。`
          : raw;
        setErrorMsg(friendly);
        setPhase("error");
      }
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setPhase("idle");
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(report).then(() => {
      setCopyLabel("已复制");
      setTimeout(() => setCopyLabel("复制"), 2000);
    }).catch(() => {});
  };

  const stem = `playbook-${(query.trim() || "report").replace(/\s+/g, "-")}-${marketplace}`;

  const handleDownloadMd = () => {
    triggerDownload(report, `${stem}.md`, "text/markdown");
    setDlMenuOpen(false);
  };

  const handleDownloadCsv = () => {
    // Prefer the AI-emitted ```csv ad-bulksheet block; fall back to all tables.
    const csv = extractCsvBlock(report) || markdownToCsv(report);
    triggerDownload(csv, `${stem}-ads.csv`, "text/csv;charset=utf-8");
    setDlMenuOpen(false);
  };

  const handleDownloadHtml = () => {
    const html = markdownToHtmlPage(report, {
      title: `亚马逊打法手册：${query} (${marketplace})`,
      icon: "◎",
      meta: [
        `🛠 ${query}`,
        `🌍 ${marketplace}`,
        `📊 ${actualSourceLabel}`,
        `💲 目标价 ${price}`,
        ...(provider ? [`🤖 ${provider}`] : []),
        ...(elapsedS !== null ? [`⏱ ${elapsedS}s`] : []),
      ],
    });
    triggerDownload(html, `${stem}.html`, "text/html;charset=utf-8");
    setDlMenuOpen(false);
  };

  // ── History ───────────────────────────────────────────────────────────────
  useEffect(() => {
    fetchHistory().then((entries) => setHistory(entries.map(toLocal))).catch(() => {});
  }, []);

  useEffect(() => {
    if (phase !== "done" || !report) return;
    const ts = Date.now();
    const apiEntry: HistoryEntry = {
      id: ts.toString(),
      mode, query, marketplace, price, cost,
      provider, data_source: actualDataSource, elapsed_s: elapsedS ?? 0,
      ts, report,
    };
    saveHistoryEntry(apiEntry).catch(() => {});
    setHistory((prev) => [toLocal(apiEntry), ...prev].slice(0, 60));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  const handleLoadHistory = (entry: LocalHistoryEntry) => {
    setMode(entry.mode);
    setQuery(entry.query);
    setMarketplace(entry.marketplace);
    setPrice(entry.price);
    setCost(entry.cost);
    setProvider(entry.provider);
    const loadedSource = (entry.data_source || "sorftime") as DataSourceId;
    setDataSource(loadedSource);
    setDataSourceState(loadedSource);
    setActualDataSource(loadedSource);
    setActualSourceLabel(dataSourceMeta(loadedSource, "playbook").name);
    setElapsedS(entry.elapsedS);
    setReport(entry.report);
    setPhase("done");
    setWarnings([]);
    setErrorMsg("");
    setHistoryOpen(false);
  };

  const handleDeleteHistory = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    deleteHistoryEntry(id).catch(() => {});
    setHistory((prev) => prev.filter((h) => h.id !== id));
  };

  const handleClearHistory = () => {
    apiClearHistory().catch(() => {});
    setHistory([]);
  };

  const isRunning = phase === "collecting" || phase === "synthesizing";
  const currentMkt = MARKETPLACES.find((m) => m.code === marketplace)!;
  const progressPct = progress
    ? Math.round((progress.done / progress.total) * 100)
    : phase === "synthesizing" ? 100 : 15;

  return (
    <div className="market-page">
      {/* Header row */}
      <div className="market-header">
        <span className="market-title">
          亚马逊打法推荐
        </span>

        <div className="market-mode-toggle">
          {(["keyword", "asin"] as PlaybookMode[]).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setQuery(""); }}
              disabled={isRunning}
              className={"market-mode-btn" + (mode === m ? " active" : "")}
            >
              {m === "keyword" ? "产品名/类目词" : "竞品ASIN"}
            </button>
          ))}
        </div>

        {isRunning && (
          <span className="market-live-badge">
            <span className="market-live-dot" />
            {liveTimer}s
          </span>
        )}

        <button
          className={"market-history-btn" + (historyOpen ? " active" : "")}
          onClick={() => setHistoryOpen((o) => !o)}
          title="历史记录"
        >
          历史
          {history.length > 0 && <span className="market-history-count">{history.length}</span>}
        </button>
      </div>

      {/* Input row */}
      <div className="market-input-row playbook-input-row">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          placeholder={mode === "keyword" ? "产品名或类目词，如 wireless earbuds" : "竞品 ASIN，如 B08N5WRWNW"}
          disabled={isRunning}
          className="market-query-input"
        />
        <input
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          placeholder="目标售价 $"
          inputMode="decimal"
          disabled={isRunning}
          className="market-query-input playbook-num-input"
          title="目标售价（必填，USD）"
        />
        <input
          value={cost}
          onChange={(e) => setCost(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          placeholder="成本(选填)"
          inputMode="decimal"
          disabled={isRunning}
          className="market-query-input playbook-num-input"
          title="单件成本估算（选填：采购+头程+FBA，用于利润/ACOS 测算）"
        />

        {/* Data source picker — request carries the selected source end to end. */}
        <DataSourcePicker value={dataSource} onChange={changeDataSource} disabled={isRunning} surface="playbook" />

        {/* Marketplace picker */}
        <div className="market-mkt-wrap" ref={pickerRef}>
          <button
            className="market-mkt-btn"
            disabled={isRunning}
            onClick={() => setPickerOpen((o) => !o)}
            title="选择站点"
          >
            <span className="market-mkt-flag"><img src={FLAG_URL(currentMkt.code)} alt={currentMkt.code} style={{width:16,height:12,verticalAlign:"middle"}} /></span>
            <span className="market-mkt-code">{currentMkt.code}</span>
            <span className="market-mkt-arrow">{pickerOpen ? "▴" : "▾"}</span>
          </button>
          {pickerOpen && (
            <div className="market-mkt-dropdown hide-mobile-picker">
              {MARKETPLACES.map((m) => (
                <button
                  key={m.code}
                  className={"market-mkt-option" + (marketplace === m.code ? " active" : "")}
                  onClick={() => { setMarketplace(m.code); setPickerOpen(false); }}
                >
                  <span><img src={FLAG_URL(m.code)} alt={m.code} style={{width:16,height:12,verticalAlign:"middle"}} /></span>
                  <span className="market-mkt-option-code">{m.code}</span>
                  <span className="market-mkt-option-name">{m.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {isRunning ? (
          <button onClick={handleStop} className="market-btn market-btn-stop">停止</button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={!query.trim() || !price.trim() || !dsReady}
            title={!dsReady ? `数据源「${dataSourceMeta(dataSource, "playbook").name}」当前不可用` : undefined}
            className="market-btn market-btn-submit"
          >
            生成打法
          </button>
        )}
      </div>

      {!dsReady && (
        <div className="market-error" style={{ marginTop: 8 }}>
          数据源「{dataSourceMeta(dataSource, "playbook").name}」当前不可用于打法推荐：{dataSourceMeta(dataSource, "playbook").note || "请切换数据源"}。
        </div>
      )}

      {/* Mobile bottom sheet picker */}
      {pickerOpen && (
        <div className="show-mobile-picker">
          <div className="market-sheet-backdrop" onClick={() => setPickerOpen(false)} />
          <div className="market-sheet">
            <div className="market-sheet-handle" />
            <div className="market-sheet-title">选择站点</div>
            <div className="market-sheet-grid">
              {MARKETPLACES.map((m) => (
                <button
                  key={m.code}
                  className={"market-sheet-item" + (marketplace === m.code ? " active" : "")}
                  onClick={() => { setMarketplace(m.code); setPickerOpen(false); }}
                >
                  <span className="market-sheet-flag"><img src={FLAG_URL(m.code)} alt={m.code} style={{width:16,height:12,verticalAlign:"middle"}} /></span>
                  <span className="market-sheet-code">{m.code}</span>
                  <span className="market-sheet-name">{m.name}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Progress */}
      {isRunning && (
        <div className="market-progress-wrap">
          <div className="market-progress-label">
            <span>
              {phase === "collecting"
                ? progress
                  ? `采集中 · ${progress.step}（${progress.done}/${progress.total}）`
                  : `连接 ${actualSourceLabel}…`
                : `AI 合成中${provider ? `（${provider}）` : ""}…`}
            </span>
            <span className="market-progress-pct">
              {progress ? `${progressPct}%` : phase === "synthesizing" ? "合成中" : ""}
            </span>
          </div>
          <div className="market-progress-bar">
            <div
              className={"market-progress-fill" + (phase === "synthesizing" ? " shimmer" : "")}
              style={{ width: `${progressPct}%` }}
            />
          </div>
          {phase === "synthesizing" && !report && attemptingProvider && liveTimer >= 20 && (
            <div className="market-progress-hint">
              ⏳ 正在调用 <code>{attemptingProvider}</code>
              {attemptingProvider === "apimart"
                ? "（API 流式，正常 5-15s 内会有首字）"
                : "（本机 CLI，整段缓冲，单次通常 1-3 分钟；超时会自动回退下一个提供商）"}
              ，已等待 {liveTimer}s …
            </div>
          )}
        </div>
      )}

      {/* Warnings */}
      {warnings.length > 0 && (
        <div className="market-warnings">
          {warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
        </div>
      )}

      {/* Error */}
      {phase === "error" && (
        <div className="market-error">{errorMsg || "发生未知错误"}</div>
      )}

      {/* Report */}
      {(report || phase === "synthesizing") && (
        <div className="market-report-wrap wb-enter">
          <div className="market-report-toolbar">
            <span className="market-report-meta">
              {phase === "done" && elapsedS !== null
                ? `数据：${actualSourceLabel} · AI：${provider} · ${elapsedS}s · ${query} · $${price}`
                : phase === "synthesizing"
                  ? `${provider || "AI"} 生成中…`
                  : ""}
            </span>
            {report && (
              <div className="market-report-actions">
                <button onClick={handleCopy} className="market-btn market-btn-copy">
                  {copyLabel}
                </button>
                <div className="market-dl-wrap" ref={dlMenuRef}>
                  <button
                    className="market-btn market-btn-copy market-btn-dl"
                    onClick={() => setDlMenuOpen((o) => !o)}
                  >
                    下载 <span className="market-dl-arrow">{dlMenuOpen ? "▴" : "▾"}</span>
                  </button>
                  {dlMenuOpen && (
                    <div className="market-dl-menu">
                      <button className="market-dl-item" onClick={handleDownloadMd}>
                        <span className="market-dl-ext md">.md</span>
                        <span className="market-dl-label">手册原文</span>
                      </button>
                      <button className="market-dl-item" onClick={handleDownloadCsv}>
                        <span className="market-dl-ext csv">.csv</span>
                        <span className="market-dl-label">{hasCsvBlock ? "广告批量表" : "表格数据"}</span>
                      </button>
                      <button className="market-dl-item" onClick={handleDownloadHtml}>
                        <span className="market-dl-ext html">.html</span>
                        <span className="market-dl-label">网页手册</span>
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
          <div ref={reportRef} className="market-report-body">
            {!report && phase === "synthesizing" ? (
              <div aria-busy="true" aria-live="polite">
                <div className="skeleton line lg" />
                <div className="skeleton line md" />
                <div className="skeleton line lg" />
                <div className="skeleton line sm" />
                <div className="skeleton line md" />
                <div className="skeleton line lg" />
              </div>
            ) : (
              <>
                <MarkdownReport text={report} />
                {phase === "synthesizing" && <span className="cursor-blink">▋</span>}
              </>
            )}
          </div>
        </div>
      )}

      {/* Deep-analysis hand-off (report → native agents session) */}
      {phase === "done" && report && (
        <DeepAnalysisPanel
          types={PLAYBOOK_ANALYSIS_TYPES}
          query={query}
          marketplace={marketplace}
          report={report}
          slug={query}
        />
      )}

      {/* Empty state */}
      {phase === "idle" && !report && (
        <div className="market-empty">
          <div className="market-empty-icon">◎</div>
          <div className="market-empty-title">
            输入{mode === "keyword" ? "产品名/类目词" : "竞品 ASIN"}与目标售价，生成纯白帽站内打法手册
          </div>
          <div className="market-empty-chips">
            {EXAMPLE_QUERIES[mode].map((ex) => (
              <button key={ex} className="market-example-chip" onClick={() => setQuery(ex)}>
                {ex}
              </button>
            ))}
          </div>
          <div className="market-empty-hint">
            数据来源：{dataSourceMeta(dataSource, "playbook").name} MCP &nbsp;·&nbsp; AI：应用模型优先 &nbsp;·&nbsp; 仅站内流量 · 纯白帽
          </div>
        </div>
      )}

      {/* History drawer backdrop */}
      {historyOpen && (
        <div className="market-history-backdrop" onClick={() => setHistoryOpen(false)} />
      )}

      {/* History drawer */}
      <div className={"market-history-drawer" + (historyOpen ? " open" : "")}>
        <div className="market-history-hd">
          <span className="market-history-hd-title">历史记录</span>
          {history.length > 0 && (
            <button className="market-history-hd-clear" onClick={handleClearHistory}>清空</button>
          )}
          <button className="market-history-hd-close" onClick={() => setHistoryOpen(false)}>✕</button>
        </div>
        <div className="market-history-list">
          {history.length === 0 ? (
            <div className="market-history-empty">暂无历史记录</div>
          ) : (
            history.map((entry) => {
              const mkt = MARKETPLACES.find((m) => m.code === entry.marketplace);
              return (
                <div key={entry.id} className="market-history-item" onClick={() => handleLoadHistory(entry)}>
                  <div className="market-history-item-top">
                    <span className={"market-history-mode " + entry.mode}>
                      {entry.mode === "keyword" ? "词" : "ASIN"}
                    </span>
                    <span className="market-history-item-query" title={entry.query}>{entry.query}</span>
                    <button
                      className="market-history-item-del"
                      onClick={(e) => handleDeleteHistory(entry.id, e)}
                      title="删除"
                    >
                      ✕
                    </button>
                  </div>
                  <div className="market-history-item-meta">
                    <span><img src={FLAG_URL(mkt?.code || "US")} alt="" style={{width:16,height:12,verticalAlign:"middle"}} /> {entry.marketplace}</span>
                    {entry.price && <span>${entry.price}</span>}
                    {entry.provider && <span>{entry.provider}</span>}
                    <span>数据：{dataSourceMeta((entry.data_source || "sorftime") as DataSourceId, "playbook").name}</span>
                    <span className="market-history-item-time">{relativeTime(entry.ts)}</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
