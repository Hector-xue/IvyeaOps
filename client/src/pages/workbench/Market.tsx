import { useEffect, useRef, useState } from "react";
import {
  streamResearch,
  fetchHistory,
  saveHistoryEntry,
  deleteHistoryEntry,
  clearHistory as apiClearHistory,
  type ResearchMode,
  type SseEvent,
  type HistoryEntry,
} from "../../api/market";
import { MarkdownReport } from "../../lib/reportFormat";
import { FLAG_URL, MARKETPLACES } from "../../lib/marketplaces";
import { getDataSource, setDataSource, dataSourceMeta, type DataSourceId } from "../../lib/dataSource";
import DataSourcePicker from "../../components/DataSourcePicker";
import DeepAnalysisPanel from "../../components/DeepAnalysisPanel";

// Local display shape — uses camelCase; converted from the snake_case API type.
interface LocalHistoryEntry {
  id: string;
  mode: ResearchMode;
  query: string;
  marketplace: string;
  provider: string;
  data_source: string;
  elapsedS: number;
  ts: number;
  report: string;
}

function toLocal(e: import("../../api/market").HistoryEntry): LocalHistoryEntry {
  return { ...e, elapsedS: e.elapsed_s };
}

// ── Deep-analysis panel ───────────────────────────────────────────────────────

const ANALYSIS_TYPES = [
  {
    id: "market",
    icon: "◈",
    label: "市场诊断",
    promptFn: (query: string, mkt: string, report: string) =>
      `以下是一份关于「${query}」（${mkt} 站）的亚马逊市场调研报告，请基于内容进行深度市场诊断：\n1. 整体市场机会评估（规模、趋势）\n2. 竞争格局解读（头部玩家、集中度）\n3. 差异化切入点建议\n4. 主要风险提示\n\n---\n${report}`,
  },
  {
    id: "asin",
    icon: "⬡",
    label: "ASIN 深析",
    promptFn: (query: string, mkt: string, report: string) =>
      `以下是一份关于「${query}」（${mkt} 站）的亚马逊市场调研报告，请基于内容进行 ASIN 深度分析：\n1. 头部 ASIN 产品特征与共性\n2. 用户评价中的痛点与亮点\n3. 产品差异化改进方向\n4. 具体选品建议\n\n---\n${report}`,
  },
  {
    id: "ads",
    icon: "▦",
    label: "广告策略",
    promptFn: (query: string, mkt: string, report: string) =>
      `以下是一份关于「${query}」（${mkt} 站）的亚马逊市场调研报告，请基于内容制定广告策略：\n1. 核心关键词布局（品类词、长尾词优先级）\n2. 竞品流量拦截策略（ASIN 精准投放）\n3. 竞价策略与预算分配建议\n4. 广告结构优化方案\n\n---\n${report}`,
  },
] as const;

const EXAMPLE_QUERIES: Record<ResearchMode, string[]> = {
  keyword: ["wireless earbuds", "yoga mat", "air fryer", "led desk lamp"],
  asin: ["B08N5WRWNW", "B09G9FPHY6", "B07ZPKN6YR"],
};

type Phase = "idle" | "collecting" | "synthesizing" | "done" | "error";

interface ProgressItem {
  step: string;
  done: number;
  total: number;
}

export default function Market() {
  const [mode, setMode] = useState<ResearchMode>("keyword");
  const [query, setQuery] = useState("");
  const [marketplace, setMarketplace] = useState("US");
  const [dataSource, setDataSourceState] = useState<DataSourceId>(getDataSource);
  const changeDataSource = (id: DataSourceId) => { setDataSource(id); setDataSourceState(id); };
  const dsReady = dataSourceMeta(dataSource, "market").ready;

  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState<ProgressItem | null>(null);
  const [provider, setProvider] = useState("");
  const [actualDataSource, setActualDataSource] = useState<DataSourceId>(dataSource);
  const [actualSourceLabel, setActualSourceLabel] = useState(dataSourceMeta(dataSource, "market").name);
  // Which provider the backend is currently trying (set by the 'attempt' SSE
  // event, before any token arrives). Used to show "正在尝试 hermes…" in real time.
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

  // Auto-scroll during streaming
  useEffect(() => {
    if (reportRef.current && phase === "synthesizing") {
      reportRef.current.scrollTop = reportRef.current.scrollHeight;
    }
  }, [report, phase]);

  // Live elapsed timer while running
  useEffect(() => {
    if (phase !== "collecting" && phase !== "synthesizing") return;
    startTimeRef.current = Date.now();
    setLiveTimer(0);
    const id = setInterval(() => {
      setLiveTimer(Math.round((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [phase]);

  // Close marketplace picker on outside click
  useEffect(() => {
    if (!pickerOpen) return;
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setPickerOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [pickerOpen]);

  // Close download menu on outside click
  useEffect(() => {
    if (!dlMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (dlMenuRef.current && !dlMenuRef.current.contains(e.target as Node)) {
        setDlMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [dlMenuOpen]);

  const handleSubmit = async () => {
    if (!query.trim() || phase === "collecting" || phase === "synthesizing") return;
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
    setActualSourceLabel(dataSourceMeta(dataSource, "market").name);
    setAttemptingProvider("");
    setElapsedS(null);
    setErrorMsg("");

    try {
      await streamResearch(
        { mode, query: query.trim(), marketplace, data_source: dataSource },
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
        // Browser fetch with a streaming response surfaces transport
        // failures as the unhelpful "TypeError: network error". Translate
        // that to something the user can act on.
        const friendly = /network error|Failed to fetch|TypeError/i.test(raw)
          ? "与服务器的连接中断。常见原因：(1) AI 合成耗时过长被反代掐断；(2) Apimart 密钥失效后回退到 CLI 但 CLI 无响应；(3) 服务端 502/503。请到「系统配置 → AI 服务」点「测试密钥」验证 Apimart key，或检查 IvyeaOps 服务日志。"
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

  const stem = `market-${(query.trim() || "report").replace(/\s+/g, "-")}-${marketplace}`;

  const handleDownloadMd = () => {
    triggerDownload(report, `${stem}.md`, "text/markdown");
    setDlMenuOpen(false);
  };

  const handleDownloadCsv = () => {
    triggerDownload(markdownToCsv(report), `${stem}.csv`, "text/csv;charset=utf-8");
    setDlMenuOpen(false);
  };

  const handleDownloadHtml = () => {
    const html = markdownToHtmlPage(report, query, marketplace, provider, elapsedS, actualSourceLabel);
    triggerDownload(html, `${stem}.html`, "text/html;charset=utf-8");
    setDlMenuOpen(false);
  };

  // ── History ───────────────────────────────────────────────────────────────
  // Load on mount; migrate any leftover localStorage entries on first run
  useEffect(() => {
    const LEGACY_KEY = "ops-market-history";
    fetchHistory().then(async (serverEntries) => {
      // One-time migration: upload localStorage entries not already on the server
      try {
        const raw = localStorage.getItem(LEGACY_KEY);
        if (raw) {
          const local: any[] = JSON.parse(raw);
          const serverIds = new Set(serverEntries.map((e) => e.id));
          const toMigrate = local.filter((e) => e.id && !serverIds.has(e.id));
          for (const e of toMigrate) {
            await saveHistoryEntry({
              id: e.id,
              mode: e.mode,
              query: e.query,
              marketplace: e.marketplace,
              provider: e.provider ?? "",
              data_source: e.data_source ?? "sorftime",
              elapsed_s: e.elapsedS ?? e.elapsed_s ?? 0,
              ts: e.ts,
              report: e.report ?? "",
            }).catch(() => {});
          }
          localStorage.removeItem(LEGACY_KEY);
          if (toMigrate.length > 0) {
            // Reload merged list from server
            return fetchHistory();
          }
        }
      } catch {}
      return serverEntries;
    }).then((entries) => setHistory(entries.map(toLocal))).catch(() => {});
  }, []);

  // Save when a report finishes
  useEffect(() => {
    if (phase !== "done" || !report) return;
    const ts = Date.now();
    const apiEntry: import("../../api/market").HistoryEntry = {
      id: ts.toString(),
      mode, query, marketplace,
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
    setProvider(entry.provider);
    const loadedSource = (entry.data_source || "sorftime") as DataSourceId;
    setDataSource(loadedSource);
    setDataSourceState(loadedSource);
    setActualDataSource(loadedSource);
    setActualSourceLabel(dataSourceMeta(loadedSource, "market").name);
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
          <span className="market-title-icon">◈</span>
          亚马逊市场调研
        </span>

        <div className="market-mode-toggle">
          {(["keyword", "asin"] as ResearchMode[]).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setQuery(""); }}
              disabled={isRunning}
              className={"market-mode-btn" + (mode === m ? " active" : "")}
            >
              {m === "keyword" ? "关键词" : "ASIN"}
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
          {history.length > 0 && (
            <span className="market-history-count">{history.length}</span>
          )}
        </button>
      </div>

      {/* Input row */}
      <div className="market-input-row">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          placeholder={mode === "keyword" ? "输入关键词，如 wireless earbuds" : "输入 ASIN，如 B08N5WRWNW"}
          disabled={isRunning}
          className="market-query-input"
        />

        {/* Data source picker — request carries the selected source end to end. */}
        <DataSourcePicker value={dataSource} onChange={changeDataSource} disabled={isRunning} surface="market" />

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
            disabled={!query.trim() || !dsReady}
            title={!dsReady ? `数据源「${dataSourceMeta(dataSource, "market").name}」当前不可用` : undefined}
            className="market-btn market-btn-submit"
          >
            生成报告
          </button>
        )}
      </div>

      {!dsReady && (
        <div className="market-error" style={{ marginTop: 8 }}>
          数据源「{dataSourceMeta(dataSource, "market").name}」当前不可用于市场调研：{dataSourceMeta(dataSource, "market").note || "请切换数据源"}。
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
          {/* Long-wait hint: synthesizing 阶段超过 20s 没拿到 token 时显示。
              动态使用 backend 的 attempt 事件展示真正在跑的 provider，
              不再硬编码 "Apimart 失败". */}
          {phase === "synthesizing" && !report && attemptingProvider && liveTimer >= 20 && (
            <div className="market-progress-hint">
              ⏳ 正在调用 <code>{attemptingProvider}</code>
              {attemptingProvider === "apimart"
                ? "（API 流式，正常 5-15s 内会有首字）"
                : attemptingProvider === "ivyea-agent"
                ? "（智能体逐字生成；命中知识引证时会压住草稿、只输出终稿，整篇报告通常 2-6 分钟）"
                : "（本机 CLI，整段缓冲，单次通常 1-3 分钟；超时会自动回退下一个提供商）"}
              ，已等待 {liveTimer}s …
              {attemptingProvider !== "apimart" && (
                <>
                  <br />
                  如果迟迟没结果，请在「系统配置 → 文本 AI 提供商顺序」调整候选顺序，或在「外部集成路径」确认 CLI 已安装。
                </>
              )}
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
                ? `数据：${actualSourceLabel} · AI：${provider} · ${elapsedS}s · ${query}`
                : phase === "synthesizing"
                  ? `${provider || "AI"} 生成中…`
                  : ""}
            </span>
            {report && (
              <div className="market-report-actions">
                <button onClick={handleCopy} className="market-btn market-btn-copy">
                  {copyLabel}
                </button>
                {/* Download dropdown */}
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
                        <span className="market-dl-label">Markdown 原文</span>
                      </button>
                      <button className="market-dl-item" onClick={handleDownloadCsv}>
                        <span className="market-dl-ext csv">.csv</span>
                        <span className="market-dl-label">表格数据</span>
                      </button>
                      <button className="market-dl-item" onClick={handleDownloadHtml}>
                        <span className="market-dl-ext html">.html</span>
                        <span className="market-dl-label">网页报告</span>
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

      {/* Empty state */}
      {phase === "idle" && !report && (
        <div className="market-empty">
          <div className="market-empty-icon">◈</div>
          <div className="market-empty-title">
            输入{mode === "keyword" ? "关键词" : "ASIN"}，生成多维度市场调研报告
          </div>
          <div className="market-empty-chips">
            {EXAMPLE_QUERIES[mode].map((ex) => (
              <button
                key={ex}
                className="market-example-chip"
                onClick={() => setQuery(ex)}
              >
                {ex}
              </button>
            ))}
          </div>
          <div className="market-empty-hint">
            数据来源：{dataSourceMeta(dataSource, "market").name} MCP &nbsp;·&nbsp; AI：IvyeaAgent → 全局兜底 → DeepSeek → Codex/Claude
          </div>
        </div>
      )}

      {/* Deep analysis panel — visible after report is done */}
      {phase === "done" && report && (
        <DeepAnalysisPanel types={ANALYSIS_TYPES} query={query} marketplace={marketplace} report={report} />
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
            <button className="market-history-hd-clear" onClick={handleClearHistory}>
              清空
            </button>
          )}
          <button className="market-history-hd-close" onClick={() => setHistoryOpen(false)}>
            ✕
          </button>
        </div>
        <div className="market-history-list">
          {history.length === 0 ? (
            <div className="market-history-empty">暂无历史记录</div>
          ) : (
            history.map((entry) => {
              const mkt = MARKETPLACES.find((m) => m.code === entry.marketplace);
              return (
                <div
                  key={entry.id}
                  className="market-history-item"
                  onClick={() => handleLoadHistory(entry)}
                >
                  <div className="market-history-item-top">
                    <span className={"market-history-mode " + entry.mode}>
                      {entry.mode === "keyword" ? "词" : "ASIN"}
                    </span>
                    <span className="market-history-item-query" title={entry.query}>
                      {entry.query}
                    </span>
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
                    {entry.provider && <span>{entry.provider}</span>}
                    <span>数据：{dataSourceMeta((entry.data_source || "sorftime") as DataSourceId, "market").name}</span>
                    {entry.elapsedS > 0 && <span>{entry.elapsedS}s</span>}
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


// ─── Download Utilities ───────────────────────────────────────────────────────

function triggerDownload(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// CSV: extract all markdown tables; fall back to section-content structure if none found.
function markdownToCsv(text: string): string {
  const lines = text.split("\n");
  const sections: Array<{ heading: string; headers: string[]; rows: string[][] }> = [];
  let currentHeading = "";
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (/^#{1,3} /.test(line)) {
      currentHeading = line.replace(/^#+\s*/, "").trim();
    }
    if (line.startsWith("|") && i + 1 < lines.length && lines[i + 1].match(/^\|[\s\-|:]+\|$/)) {
      const headers = parseCells(line).map(stripMd);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        rows.push(parseCells(lines[i]).map(stripMd));
        i++;
      }
      sections.push({ heading: currentHeading, headers, rows });
      continue;
    }
    i++;
  }

  if (sections.length === 0) {
    // No tables: export report as section → content pairs
    return exportReportStructure(lines);
  }

  const out: string[] = ["﻿"]; // UTF-8 BOM for Excel
  for (const sec of sections) {
    if (sec.heading) out.push(`# ${sec.heading}`);
    out.push(sec.headers.map(csvCell).join(","));
    for (const row of sec.rows) out.push(row.map(csvCell).join(","));
    out.push("");
  }
  return out.join("\r\n");
}

function exportReportStructure(lines: string[]): string {
  const out: string[] = ["﻿章节,内容"];
  let heading = "";
  const buf: string[] = [];

  const flush = () => {
    const content = buf.join(" ").trim();
    if (heading || content) out.push(csvCell(heading) + "," + csvCell(content));
    buf.length = 0;
  };

  for (const line of lines) {
    if (/^#{1,3} /.test(line)) {
      flush();
      heading = line.replace(/^#+\s*/, "").trim();
    } else if (line.trim() && !line.startsWith("|")) {
      buf.push(stripMd(line));
    }
  }
  flush();
  return out.join("\r\n");
}

function stripMd(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/`(.+?)`/g, "$1")
    .trim();
}

function csvCell(s: string): string {
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

interface HtmlChartSpec {
  id: string;
  type: "line" | "bar" | "doughnut";
  title: string;
  labels: string[];
  datasets: Array<{ label: string; data: number[]; color: string }>;
}

// HTML: render full standalone page with Chart.js visualizations + print-friendly light theme.
function markdownToHtmlPage(
  text: string,
  query: string,
  marketplace: string,
  provider: string,
  elapsedS: number | null,
  dataSource: string,
): string {
  const chartSpecs: HtmlChartSpec[] = [];
  const body = buildHtmlWithCharts(text, chartSpecs);
  const date = new Date().toLocaleString("zh-CN");
  const hasCharts = chartSpecs.length > 0;
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>市场调研：${esc(query)} (${esc(marketplace)})</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;font-size:14px;line-height:1.8;color:#1a1a2e;background:#fff;max-width:960px;margin:0 auto;padding:36px 28px 72px}
  .rpt-header{border-bottom:3px solid #16a34a;padding-bottom:18px;margin-bottom:32px}
  .rpt-header h1{font-size:22px;color:#1a1a2e;font-weight:700;margin-bottom:8px;display:flex;align-items:center;gap:10px}
  .rpt-header h1 .ico{color:#16a34a}
  .rpt-meta{font-size:12px;color:#6b7280;display:flex;flex-wrap:wrap;gap:6px 20px}
  h1{font-size:19px;font-weight:700;color:#1a1a2e;border-bottom:2px solid #16a34a;padding-bottom:8px;margin:28px 0 14px}
  h2{font-size:16px;font-weight:600;color:#1a1a2e;margin:22px 0 10px;padding-left:10px;border-left:3px solid #16a34a}
  h3{font-size:14px;font-weight:600;color:#374151;margin:16px 0 8px}
  p{margin:6px 0;color:#374151}
  ul,ol{padding-left:22px;margin:8px 0}
  li{margin:4px 0;line-height:1.7;color:#374151}
  ul li::marker{color:#16a34a}
  blockquote{border-left:3px solid #16a34a;padding:6px 0 6px 16px;color:#6b7280;font-style:italic;margin:10px 0;background:#f0fdf4;border-radius:0 4px 4px 0}
  hr{border:none;border-top:1px solid #e5e7eb;margin:20px 0}
  code{background:#f0fdf4;padding:2px 6px;border-radius:4px;font-family:'JetBrains Mono','Fira Code',monospace;font-size:0.87em;color:#166534;border:1px solid #bbf7d0}
  pre{background:#f8fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;overflow-x:auto;margin:14px 0}
  pre .lang{font-size:10px;color:#9ca3af;letter-spacing:.06em;margin-bottom:8px;text-transform:uppercase}
  pre code{background:none;border:none;padding:0;font-size:12.5px;color:#1f2937;white-space:pre}
  table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13px;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.06)}
  thead{background:#f0fdf4}
  th{text-align:left;padding:9px 14px;border-bottom:2px solid #16a34a;font-weight:600;color:#1a1a2e;white-space:nowrap;font-size:12px;letter-spacing:.02em}
  td{padding:8px 14px;border-bottom:1px solid #f3f4f6;color:#374151;vertical-align:top}
  tr:last-child td{border-bottom:none}
  tbody tr:hover td{background:#fafff9}
  strong{font-weight:600;color:#111827}
  em{font-style:italic;color:#6b7280}
  .chart-wrap{margin:6px 0 28px;background:#fafff9;border:1px solid #dcfce7;border-radius:10px;padding:16px 20px}
  .chart-wrap canvas{max-height:300px}
  @media print{
    body{max-width:100%;padding:20px}
    .rpt-header{page-break-after:avoid}
    table,h2,h3,.chart-wrap{page-break-inside:avoid}
    pre{white-space:pre-wrap;word-break:break-word}
  }
</style>
</head>
<body>
<div class="rpt-header">
  <h1><span class="ico">◈</span> 亚马逊市场调研报告</h1>
  <div class="rpt-meta">
    <span>🔍 ${esc(query)}</span>
    <span>🌍 ${esc(marketplace)}</span>
    <span>📅 ${date}</span><span>📊 ${esc(dataSource)}</span>${provider ? `\n    <span>🤖 ${esc(provider)}</span>` : ""}${elapsedS !== null ? `\n    <span>⏱ ${elapsedS}s</span>` : ""}
  </div>
</div>
${body}
${hasCharts ? `<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>\n<script>${buildChartInitJs(chartSpecs)}</script>` : ""}
</body>
</html>`;
}

function markdownToHtml(text: string): string {
  const lines = text.split("\n");
  const out: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Code fence
    if (line.startsWith("```")) {
      const lang = esc(line.slice(3).trim());
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(esc(lines[i]));
        i++;
      }
      out.push(`<pre>${lang ? `<div class="lang">${lang}</div>` : ""}<code>${codeLines.join("\n")}</code></pre>`);
      i++;
      continue;
    }

    // Table
    if (line.startsWith("|") && i + 1 < lines.length && lines[i + 1].match(/^\|[\s\-|:]+\|$/)) {
      const headers = parseCells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        rows.push(parseCells(lines[i]));
        i++;
      }
      const ths = headers.map((h) => `<th>${inlineToHtml(h)}</th>`).join("");
      const trs = rows.map((row) => "<tr>" + row.map((c) => `<td>${inlineToHtml(c)}</td>`).join("") + "</tr>").join("\n");
      out.push(`<table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table>`);
      continue;
    }

    if (line.startsWith("# "))        out.push(`<h1>${inlineToHtml(line.slice(2))}</h1>`);
    else if (line.startsWith("## "))  out.push(`<h2>${inlineToHtml(line.slice(3))}</h2>`);
    else if (line.startsWith("### ")) out.push(`<h3>${inlineToHtml(line.slice(4))}</h3>`);
    else if (line.startsWith("> "))   out.push(`<blockquote><p>${inlineToHtml(line.slice(2))}</p></blockquote>`);
    else if (line.startsWith("- ") || line.startsWith("* ")) out.push(`<ul><li>${inlineToHtml(line.slice(2))}</li></ul>`);
    else if (/^\d+\. /.test(line))    out.push(`<ol><li>${inlineToHtml(line.replace(/^\d+\. /, ""))}</li></ol>`);
    else if (line.startsWith("---") || line.startsWith("===")) out.push("<hr>");
    else if (line.trim() === "")      out.push("");
    else                              out.push(`<p>${inlineToHtml(line)}</p>`);

    i++;
  }

  // Merge adjacent list items so they form a single <ul>/<ol>
  return out.join("\n")
    .replace(/<\/ul>\n<ul>/g, "")
    .replace(/<\/ol>\n<ol>/g, "");
}

function inlineToHtml(text: string): string {
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}

function parseNum(s: string | undefined): number {
  if (!s) return NaN;
  return parseFloat(s.replace(/[,，%％万亿\s]/g, "").trim());
}

function detectChartForTable(
  heading: string,
  headers: string[],
  rows: string[][],
  chartId: string,
): HtmlChartSpec | null {
  const firstColVals = rows.map((r) => (r[0] || "").trim());

  // Monthly trend → line chart
  const isMonthly =
    /趋势|月度|淡旺季|月份|搜索趋势/.test(heading) ||
    firstColVals.filter((v) => /^\d{1,2}月$/.test(v)).length >= 6;

  if (isMonthly) {
    const numericCols: number[] = [];
    for (let ci = 1; ci < headers.length; ci++) {
      const vals = rows.map((r) => parseNum(r[ci]));
      if (vals.filter((v) => !isNaN(v)).length >= Math.floor(rows.length * 0.5)) {
        numericCols.push(ci);
      }
    }
    if (numericCols.length === 0) return null;
    const palette = ["#16a34a", "#3b82f6", "#f59e0b"];
    return {
      id: chartId,
      type: "line",
      title: heading || "月度趋势",
      labels: firstColVals,
      datasets: numericCols.slice(0, 3).map((ci, i) => ({
        label: headers[ci] || `指标${i + 1}`,
        data: rows.map((r) => parseNum(r[ci])),
        color: palette[i % 3],
      })),
    };
  }

  // Price distribution → bar chart
  const isPriceDist =
    /价格区间|价格带|价格分布/.test(heading) ||
    /价格区间|价格段/.test(headers[0] || "");
  if (isPriceDist) {
    const preferOrder = ["产品数", "asin数", "月销量", "月均销量", "占比", "数量", "销售额"];
    let targetCol = 1;
    for (const pref of preferOrder) {
      const idx = headers.findIndex((h) => h.toLowerCase().includes(pref.toLowerCase()));
      if (idx > 0) { targetCol = idx; break; }
    }
    return {
      id: chartId,
      type: "bar",
      title: heading || "价格区间分布",
      labels: firstColVals,
      datasets: [{ label: headers[targetCol] || "数量", data: rows.map((r) => parseNum(r[targetCol])), color: "#16a34a" }],
    };
  }

  // Market share → doughnut
  const shareColIdx = headers.findIndex((h) => /市场份额|占比|份额/.test(h));
  if (
    shareColIdx > 0 &&
    rows.length <= 12 &&
    /市场格局|垄断|竞争格局|份额|top|TOP/.test(heading)
  ) {
    return {
      id: chartId,
      type: "doughnut",
      title: heading || "市场份额",
      labels: firstColVals.slice(0, 8),
      datasets: [{ label: "市场份额", data: rows.slice(0, 8).map((r) => parseNum(r[shareColIdx])), color: "#16a34a" }],
    };
  }

  return null;
}

function buildHtmlWithCharts(text: string, chartSpecs: HtmlChartSpec[]): string {
  const lines = text.split("\n");
  const out: string[] = [];
  let i = 0;
  let currentHeading = "";
  let chartCounter = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.startsWith("```")) {
      const lang = esc(line.slice(3).trim());
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(esc(lines[i]));
        i++;
      }
      out.push(`<pre>${lang ? `<div class="lang">${lang}</div>` : ""}<code>${codeLines.join("\n")}</code></pre>`);
      i++;
      continue;
    }

    if (line.startsWith("|") && i + 1 < lines.length && lines[i + 1].match(/^\|[\s\-|:]+\|$/)) {
      const headers = parseCells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        rows.push(parseCells(lines[i]));
        i++;
      }
      const ths = headers.map((h) => `<th>${inlineToHtml(h)}</th>`).join("");
      const trs = rows
        .map((row) => "<tr>" + row.map((c) => `<td>${inlineToHtml(c)}</td>`).join("") + "</tr>")
        .join("\n");
      out.push(`<table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table>`);

      const chartId = `chrt${chartCounter++}`;
      const spec = detectChartForTable(
        currentHeading,
        headers.map(stripMd),
        rows.map((r) => r.map(stripMd)),
        chartId,
      );
      if (spec) {
        chartSpecs.push(spec);
        out.push(`<div class="chart-wrap"><canvas id="${spec.id}"></canvas></div>`);
      }
      continue;
    }

    if (line.startsWith("# ")) {
      currentHeading = line.slice(2).trim();
      out.push(`<h1>${inlineToHtml(line.slice(2))}</h1>`);
    } else if (line.startsWith("## ")) {
      currentHeading = line.slice(3).trim();
      out.push(`<h2>${inlineToHtml(line.slice(3))}</h2>`);
    } else if (line.startsWith("### ")) {
      currentHeading = line.slice(4).trim();
      out.push(`<h3>${inlineToHtml(line.slice(4))}</h3>`);
    } else if (line.startsWith("> ")) {
      out.push(`<blockquote><p>${inlineToHtml(line.slice(2))}</p></blockquote>`);
    } else if (line.startsWith("- ") || line.startsWith("* ")) {
      out.push(`<ul><li>${inlineToHtml(line.slice(2))}</li></ul>`);
    } else if (/^\d+\. /.test(line)) {
      out.push(`<ol><li>${inlineToHtml(line.replace(/^\d+\. /, ""))}</li></ol>`);
    } else if (line.startsWith("---") || line.startsWith("===")) {
      out.push("<hr>");
    } else if (line.trim() === "") {
      out.push("");
    } else {
      out.push(`<p>${inlineToHtml(line)}</p>`);
    }
    i++;
  }

  return out.join("\n").replace(/<\/ul>\n<ul>/g, "").replace(/<\/ol>\n<ol>/g, "");
}

function buildChartInitJs(specs: HtmlChartSpec[]): string {
  const js = (s: string) => s.replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/[\r\n]/g, "");
  const PALETTE = ["#16a34a","#3b82f6","#f59e0b","#ef4444","#8b5cf6","#06b6d4","#ec4899","#14b8a6","#f97316","#6366f1"];
  const lines: string[] = ["(function(){var C=window.Chart;if(!C)return;"];
  for (const spec of specs) {
    lines.push(`(function(){var el=document.getElementById('${spec.id}');if(!el)return;`);
    const labels = JSON.stringify(spec.labels);
    if (spec.type === "doughnut") {
      const data = JSON.stringify(spec.datasets[0]?.data ?? []).replace(/\bNaN\b/g, "0");
      const colors = JSON.stringify(PALETTE.slice(0, spec.labels.length));
      lines.push(`new C(el,{type:'doughnut',data:{labels:${labels},datasets:[{data:${data},backgroundColor:${colors},borderWidth:2,borderColor:'#fff'}]},options:{responsive:true,plugins:{legend:{position:'right'},title:{display:true,text:'${js(spec.title)}',font:{size:13,weight:'600'}}}}});`);
    } else if (spec.type === "bar") {
      const data = JSON.stringify(spec.datasets[0]?.data ?? []).replace(/\bNaN\b/g, "0");
      const lbl = js(spec.datasets[0]?.label ?? "");
      lines.push(`new C(el,{type:'bar',data:{labels:${labels},datasets:[{label:'${lbl}',data:${data},backgroundColor:'rgba(22,163,74,0.65)',borderColor:'#16a34a',borderWidth:1,borderRadius:4}]},options:{responsive:true,plugins:{legend:{display:false},title:{display:true,text:'${js(spec.title)}',font:{size:13,weight:'600'}}},scales:{y:{beginAtZero:true,grid:{color:'#f3f4f6'}}}}});`);
    } else {
      const datasets = spec.datasets.map((ds) => {
        const data = JSON.stringify(ds.data).replace(/\bNaN\b/g, "null");
        return `{label:'${js(ds.label)}',data:${data},borderColor:'${ds.color}',backgroundColor:'${ds.color}22',tension:0.4,fill:false,pointRadius:4,pointHoverRadius:6}`;
      }).join(",");
      lines.push(`new C(el,{type:'line',data:{labels:${labels},datasets:[${datasets}]},options:{responsive:true,plugins:{title:{display:true,text:'${js(spec.title)}',font:{size:13,weight:'600'}}},scales:{y:{grid:{color:'#f3f4f6'}},x:{grid:{display:false}}}}});`);
    }
    lines.push("})();");
  }
  lines.push("})();");
  return lines.join("\n");
}

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins}分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}天前`;
  return new Date(ts).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

/* Markdown 表格行 → 单元格。MarkdownReport 已改为复用 lib/reportFormat 的共享版，
   但本页另有三处表格解析（导出 CSV / 图表取数）还在用它，所以这个帮手留在这里。 */
function parseCells(line: string): string[] {
  return line.split("|").slice(1, -1).map((c) => c.trim());
}
