import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const ANSI_RE = /(\x9B|\x1B\[)[0-?]*[ -/]*[@-~]|\x1B(?:[^[\]]|\][^\x07\x1B]*(?:\x07|\x1B\\))/g;
function stripAnsi(s: string): string {
  return s.replace(ANSI_RE, "").replace(/\r\n?/g, "\n");
}
import { api } from "../../api/client";
import {
  LegacySnapshot,
  LegacyTtydStatus,
  LiveSnapshot,
  TerminalHistoryItem,
  TerminalSession,
  captureLegacySnapshot,
  captureLiveSnapshot,
  clearLegacySnapshots,
  clearLiveSnapshots,
  closeTerminalSession,
  createTerminalSession,
  deleteTerminalSession,
  getLegacySnapshot,
  getLegacyTtydStatus,
  getLiveSnapshot,
  getTerminalHistory,
  listLegacySnapshots,
  listLiveSnapshots,
  listTerminalSessions,
  startLegacyTtyd,
  stopLegacyTtyd,
  updateTerminalSession,
} from "../../api/terminalLive";
import TerminalLivePane from "../../components/TerminalLivePane";
import TerminalToolbar from "../../components/TerminalToolbar";
import { useConfirm } from "../../components/ConfirmDialog";
import { getSettings } from "../../api/settings";
import { errText } from "../../lib/errText";

const LEGACY_SESSION_ID = "__legacy_ttyd__";
const STORAGE_KEY = "ivyea-ops-terminal-current-session";
const HISTORY_INITIAL_LIMIT = 300;
const HISTORY_LOAD_MORE_LIMIT = 400;
const LEGACY_SNAPSHOT_REFRESH_MS = 30_000;
const LEGACY_SNAPSHOT_LIST_LIMIT = 80;

function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function fmtTime(v?: string | null) {
  if (!v) return "-";
  try {
    return new Date(v).toLocaleString();
  } catch {
    return v;
  }
}

function mergeHistory(prev: TerminalHistoryItem[], incoming: TerminalHistoryItem[]) {
  const map = new Map<number, TerminalHistoryItem>();
  for (const item of prev) map.set(item.id, item);
  for (const item of incoming) map.set(item.id, item);
  return Array.from(map.values()).sort((a, b) => a.seq - b.seq);
}

function normalizeHistoryText(content: string): string {
  return stripAnsi(content || "").replace(/\r\n?/g, "\n");
}

function isMeaningfulInput(content: string): boolean {
  return normalizeHistoryText(content).trim().length > 0;
}

// Generic 3-slot snapshot panel. Backend keeps at most three rows per
// session (当前 / 上一个 / 之前). The two callers (legacy ttyd main
// terminal and per-session live PTY) supply their own adapter functions.
type SnapshotItem = {
  id: number;
  ts: string;
  size: number;
  role?: "snap_curr" | "snap_prev" | "snap_before";
  label?: string;
  source?: string;  // legacy holdover; kept for older payloads
};

type SnapshotAdapter = {
  list: () => Promise<{ items: SnapshotItem[]; total: number }>;
  getContent: (id: number) => Promise<string>;
  capture: () => Promise<{ ok: boolean; skipped?: boolean; reason?: string; id?: number; error?: string }>;
  clearAll: () => Promise<void>;
  introText: React.ReactNode;
};

function SnapshotPanel({ visible, adapter, refreshKey }: {
  visible: boolean;
  adapter: SnapshotAdapter;
  refreshKey?: string;  // bumps to force reload when the adapter target changes
}) {
  const [items, setItems] = useState<SnapshotItem[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [content, setContent] = useState<string>("");
  const [contentLoading, setContentLoading] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [msg, setMsg] = useState<string>("");

  const refresh = useCallback(async () => {
    setListLoading(true);
    try {
      const data = await adapter.list();
      setItems(data.items);
      setTotal(data.total);
      // Use functional update so we always read the latest selectedId, not a
      // stale closure value from when the polling interval was created.
      setSelectedId((prev) => {
        if (prev === null) return data.items[0]?.id ?? null;  // first load
        const stillExists = data.items.some((s) => s.id === prev);
        return stillExists ? prev : (data.items[0]?.id ?? null);
      });
    } catch (e: any) {
      setMsg(errText(e, "加载快照列表失败"));
      setTimeout(() => setMsg(""), 3000);
    } finally {
      setListLoading(false);
    }
  }, [adapter]);

  const handleClear = async () => {
    if (!confirm("确定清空全部 3 张快照（当前 / 上一个 / 之前）？此操作不可撤销。")) return;
    try {
      await adapter.clearAll();
      setSelectedId(null);
      await refresh();
    } catch (e: any) {
      setMsg(errText(e, "清空失败"));
      setTimeout(() => setMsg(""), 3000);
    }
  };

  // Reset when target changes (e.g. switching between live sessions).
  useEffect(() => {
    setItems([]);
    setSelectedId(null);
    setContent("");
  }, [refreshKey]);

  // Initial load + polling while panel is visible.
  useEffect(() => {
    if (!visible) return;
    refresh();
    const t = window.setInterval(refresh, LEGACY_SNAPSHOT_REFRESH_MS);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, refreshKey]);

  // Load full content for the selected snapshot.
  useEffect(() => {
    if (!visible || selectedId == null) {
      setContent("");
      return;
    }
    let cancelled = false;
    setContentLoading(true);
    adapter.getContent(selectedId)
      .then((c) => { if (!cancelled) setContent(c || ""); })
      .catch(() => { if (!cancelled) setContent(""); })
      .finally(() => { if (!cancelled) setContentLoading(false); });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, selectedId, refreshKey]);

  const handleCapture = async () => {
    setCapturing(true);
    setMsg("");
    try {
      const res = await adapter.capture();
      if (res.error) {
        setMsg(res.error);
      } else if (res.skipped) {
        setMsg("画面与上次保存一致，已跳过");
      } else if (res.ok) {
        setMsg(res.id ? `已保存 #${res.id}` : "已保存");
      }
      await refresh();
    } catch (e: any) {
      setMsg(errText(e, "保存失败"));
    } finally {
      setCapturing(false);
      setTimeout(() => setMsg(""), 3000);
    }
  };

  const selected = items.find((s) => s.id === selectedId) || null;

  // Display order: 当前 → 上一个 → 之前 (backend already returns in this order)
  return (
    <div className="legacy-snapshot-panel">
      <div className="terminal-history-meta">{adapter.introText}</div>
      <div className="legacy-snapshot-toolbar">
        <button className="tbtn" onClick={handleCapture} disabled={capturing}>
          {capturing ? "保存中…" : "📷 立即保存"}
        </button>
        <button className="tbtn" onClick={refresh} disabled={listLoading}>
          {listLoading ? "刷新中…" : "↻ 刷新"}
        </button>
        <button
          className="tbtn"
          onClick={handleClear}
          disabled={items.length === 0}
          style={{ marginLeft: "auto", color: "var(--red)", borderColor: "rgba(248,113,113,.35)" }}
          title="清空全部 3 张快照"
        >🗑 清空</button>
      </div>
      {msg && <div className="legacy-snapshot-msg">{msg}</div>}

      {/* Top half: fixed 3-row list */}
      <div className="legacy-snapshot-list">
        {listLoading && items.length === 0 ? (
          <div className="terminal-empty">快照加载中…</div>
        ) : items.length === 0 ? (
          <div className="terminal-empty">还没有快照。点「立即保存」抓第一张。</div>
        ) : items.map((s) => {
          const label = s.label
            || (s.role === "snap_curr" ? "当前"
              : s.role === "snap_prev" ? "上一个"
              : s.role === "snap_before" ? "之前" : "");
          return (
            <div
              key={s.id}
              onClick={() => setSelectedId(s.id)}
              className={`legacy-snapshot-row${s.id === selectedId ? " active" : ""}`}
            >
              {label && (
                <span className={`legacy-snapshot-slot slot-${s.role}`}>{label}</span>
              )}
              <span className="legacy-snapshot-ts">{fmtTime(s.ts)}</span>
              <span className="legacy-snapshot-size">{fmtBytes(s.size)}</span>
            </div>
          );
        })}
      </div>

      {/* Bottom half: selected snapshot content viewer */}
      <div className="legacy-snapshot-viewer">
        {selected ? (
          <>
            <div className="legacy-snapshot-viewer-head">
              <span>{selected.label || `快照 #${selected.id}`}</span>
              <span>{fmtBytes(selected.size)}</span>
              <span>{fmtTime(selected.ts)}</span>
            </div>
            <div className="legacy-snapshot-viewer-body">
              {contentLoading ? (
                <div className="terminal-empty" style={{ padding: 12 }}>加载内容中…</div>
              ) : content ? (
                <pre>{content}</pre>
              ) : (
                <div className="terminal-empty" style={{ padding: 12 }}>该快照内容为空</div>
              )}
            </div>
          </>
        ) : (
          <div className="terminal-empty" style={{ padding: 20 }}>从上方列表选一张快照查看内容</div>
        )}
      </div>
    </div>
  );
}

// Wrapper: main-terminal (legacy tmux) snapshot view. Backend keeps at most
// 3 snapshots per the rolling window (当前 / 上一个 / 之前).
function LegacySnapshotPanel({ visible }: { visible: boolean }) {
  const adapter = useMemo<SnapshotAdapter>(() => ({
    list: async () => {
      const r = await listLegacySnapshots(LEGACY_SNAPSHOT_LIST_LIMIT, 0);
      const items: SnapshotItem[] = r.sessions.map((s: LegacySnapshot) => ({
        id: s.id, ts: s.ts, size: s.size,
        role: s.role || (
          s.source === "snap_curr" ? "snap_curr"
          : s.source === "snap_prev" ? "snap_prev"
          : s.source === "snap_before" ? "snap_before" : undefined
        ),
        label: s.label,
      }));
      return { items, total: r.total };
    },
    getContent: async (id) => (await getLegacySnapshot(id)).content || "",
    capture: async () => {
      const r = await captureLegacySnapshot("");
      return { ok: r.ok, skipped: r.skipped, reason: r.reason, id: r.id };
    },
    clearAll: async () => { await clearLegacySnapshots(); },
    introText: (
      <>
        主终端每 5 分钟抓一次 tmux 画面到快照，<strong>固定保留 3 张</strong>：当前 / 上一个 / 之前。
        新快照来时，旧的「上一个」会被合并到「之前」（带时间分隔符，单条上限 5MB）。点「立即保存」可立刻抓一张。
      </>
    ),
  }), []);
  return <SnapshotPanel visible={visible} adapter={adapter} refreshKey="legacy" />;
}

// Wrapper: per-session live PTY snapshot view. Same 3-slot rolling model
// as the main terminal. Each snapshot is the ANSI-stripped ring buffer
// (~500 lines of recent activity), so AI CLI / TUI output is preserved.
function LiveSnapshotPanel({ visible, sessionId }: { visible: boolean; sessionId: string }) {
  const adapter = useMemo<SnapshotAdapter>(() => ({
    list: async () => {
      const r = await listLiveSnapshots(sessionId, LEGACY_SNAPSHOT_LIST_LIMIT, 0);
      const items: SnapshotItem[] = r.snapshots.map((s: LiveSnapshot) => ({
        id: s.id, ts: s.ts, size: s.size,
        role: s.role, label: s.label,
      }));
      return { items, total: r.total };
    },
    getContent: async (id) => (await getLiveSnapshot(sessionId, id)).content || "",
    capture: async () => {
      const r = await captureLiveSnapshot(sessionId);
      return { ok: r.ok, skipped: r.skipped, reason: r.reason, id: r.id, error: r.error };
    },
    clearAll: async () => { await clearLiveSnapshots(sessionId); },
    introText: (
      <>
        每 5 分钟自动抓该终端的近 ~500 行输出（含 AI CLI / TUI 实时内容），<strong>固定保留 3 张</strong>：当前 / 上一个 / 之前。
        新快照来时旧的「上一个」自动合并到「之前」。终端删除时所有快照会一并清除。
      </>
    ),
  }), [sessionId]);
  return <SnapshotPanel visible={visible} adapter={adapter} refreshKey={`live-${sessionId}`} />;
}

function buildTranscript(history: TerminalHistoryItem[]): string {
  const chunks: string[] = [];
  for (const item of history) {
    const normalized = normalizeHistoryText(item.content);
    if (!normalized.trim()) continue;
    if (item.stream === "input") {
      if (!isMeaningfulInput(item.content)) continue;
      chunks.push(`$ ${normalized.trim()}`);
      continue;
    }
    if (item.stream === "system") {
      chunks.push(`[${normalized.trim()}]`);
      continue;
    }
    chunks.push(normalized.replace(/\n+$/g, ""));
  }
  return chunks.join("\n\n").trim();
}

export default function Terminal() {
  const confirm = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const historyScrollRef = useRef<HTMLDivElement>(null);
  const paneWrapRef = useRef<HTMLDivElement>(null);
  const legacyFrameRef = useRef<HTMLIFrameElement>(null);
  const liveOutputTimerRef = useRef<number | null>(null);
  const liveOutputCatchupTimerRef = useRef<number | null>(null);
  const historyRef = useRef<TerminalHistoryItem[]>([]);
  const [sessions, setSessions] = useState<TerminalSession[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [history, setHistory] = useState<TerminalHistoryItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [showHistory, setShowHistory] = useState(true);
  const [showSessionList, setShowSessionList] = useState(true);
  const [showArchived, setShowArchived] = useState(false);
  const [showMobileActionSheet, setShowMobileActionSheet] = useState(false);
  const [isMobileLayout, setIsMobileLayout] = useState(false);
  const [isPaneFullscreen, setIsPaneFullscreen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  const [uploadedPath, setUploadedPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ttydStatus, setTtydStatus] = useState<LegacyTtydStatus | null>(null);
  const [ttydBusy, setTtydBusy] = useState(false);
  // Configured ttyd URL ("新窗口" button target when legacy status has no URL).
  const [externalTtydUrl, setExternalTtydUrl] = useState<string>("");
  useEffect(() => {
    getSettings()
      .then((r) => setExternalTtydUrl(r.settings.terminal_url || ""))
      .catch(() => {});
  }, []);

  const activeIsLegacy = currentId === LEGACY_SESSION_ID;
  const current = useMemo(
    () => (activeIsLegacy ? null : sessions.find((s) => s.id === currentId) || null),
    [activeIsLegacy, sessions, currentId],
  );

  const closeMobileSheets = useCallback(() => {
    setShowMobileActionSheet(false);
    setShowSessionList(false);
    setShowHistory(false);
  }, []);

  const syncSessions = useCallback(async (preferredId?: string | null) => {
    const list = await listTerminalSessions(showArchived);
    setSessions(list);
    const saved = preferredId ?? localStorage.getItem(STORAGE_KEY);
    const nextCurrent =
      saved === LEGACY_SESSION_ID
        ? LEGACY_SESSION_ID
        : (saved && list.find((s) => s.id === saved)?.id) || list[0]?.id || null;
    setCurrentId(nextCurrent);
    if (nextCurrent) localStorage.setItem(STORAGE_KEY, nextCurrent);
    else localStorage.removeItem(STORAGE_KEY);
    return list;
  }, [showArchived]);

  const loadLegacyStatus = useCallback(async () => {
    try {
      const data = await getLegacyTtydStatus();
      setTtydStatus(data);
    } catch {
      setTtydStatus(null);
    }
  }, []);

  const loadHistory = useCallback(async (sessionId: string, beforeSeq?: number) => {
    if (!sessionId || sessionId === LEGACY_SESSION_ID) {
      setHistory([]);
      setHistoryTotal(0);
      return;
    }
    if (beforeSeq) setHistoryLoadingMore(true);
    else setHistoryLoading(true);
    try {
      const data = await getTerminalHistory(sessionId, {
        limit: beforeSeq ? HISTORY_LOAD_MORE_LIMIT : HISTORY_INITIAL_LIMIT,
        beforeSeq,
      });
      setHistory((prev) => (beforeSeq ? mergeHistory(data.items, prev) : data.items));
      setHistoryTotal(data.total);
    } finally {
      if (beforeSeq) setHistoryLoadingMore(false);
      else setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    historyRef.current = history;
  }, [history]);

  const pollHistoryTail = useCallback(async (sessionId: string) => {
    if (!sessionId || sessionId === LEGACY_SESSION_ID) return;
    const lastSeq = historyRef.current[historyRef.current.length - 1]?.seq ?? 0;
    if (!lastSeq) {
      await loadHistory(sessionId);
      return;
    }
    const data = await getTerminalHistory(sessionId, { afterSeq: lastSeq, limit: HISTORY_INITIAL_LIMIT });
    if (data.items.length) {
      setHistory((prev) => mergeHistory(prev, data.items));
    }
    setHistoryTotal(data.total);
  }, [loadHistory]);

  const handleLiveOutput = useCallback(() => {
    if (!currentId || currentId === LEGACY_SESSION_ID) return;
    if (liveOutputTimerRef.current === null) {
      liveOutputTimerRef.current = window.setTimeout(() => {
        liveOutputTimerRef.current = null;
        pollHistoryTail(currentId).catch(() => void 0);
      }, 120);
    }
    if (liveOutputCatchupTimerRef.current !== null) {
      window.clearTimeout(liveOutputCatchupTimerRef.current);
    }
    liveOutputCatchupTimerRef.current = window.setTimeout(() => {
      liveOutputCatchupTimerRef.current = null;
      pollHistoryTail(currentId).catch(() => void 0);
    }, 420);
  }, [currentId, pollHistoryTail]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const apply = (matches: boolean) => {
      setIsMobileLayout(matches);
      if (matches) {
        closeMobileSheets();
      } else {
        setShowSessionList(true);
        setShowHistory(true);
        setShowMobileActionSheet(false);
      }
    };
    apply(media.matches);
    const onChange = (event: MediaQueryListEvent) => apply(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsPaneFullscreen(document.fullscreenElement === paneWrapRef.current);
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);

  useEffect(() => {
    return () => {
      if (liveOutputTimerRef.current !== null) {
        window.clearTimeout(liveOutputTimerRef.current);
      }
      if (liveOutputCatchupTimerRef.current !== null) {
        window.clearTimeout(liveOutputCatchupTimerRef.current);
      }
    };
  }, []);

  // Auto-scroll history panel to bottom when new items arrive
  useEffect(() => {
    const el = historyScrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [history]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const list = await listTerminalSessions(showArchived);
        if (cancelled) return;
        if (!list.length && !showArchived) {
          const created = await createTerminalSession({ title: "默认终端" });
          if (cancelled) return;
          setSessions([created]);
          setCurrentId(created.id);
          localStorage.setItem(STORAGE_KEY, created.id);
        } else {
          setSessions(list);
          const saved = localStorage.getItem(STORAGE_KEY);
          const nextCurrent =
            saved === LEGACY_SESSION_ID
              ? LEGACY_SESSION_ID
              : (saved && list.find((s) => s.id === saved)?.id) || list[0]?.id || null;
          setCurrentId(nextCurrent);
          if (nextCurrent) localStorage.setItem(STORAGE_KEY, nextCurrent);
        }
        await loadLegacyStatus();
      } catch (e: any) {
        if (!cancelled) setError(errText(e, "终端会话加载失败"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadLegacyStatus, showArchived]);

  useEffect(() => {
    if (!currentId || currentId === LEGACY_SESSION_ID) {
      setHistory([]);
      setHistoryTotal(0);
      return;
    }
    loadHistory(currentId).catch(() => void 0);
  }, [currentId, loadHistory]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      syncSessions(currentId).catch(() => void 0);
      if (currentId && currentId !== LEGACY_SESSION_ID) pollHistoryTail(currentId).catch(() => void 0);
      loadLegacyStatus().catch(() => void 0);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [currentId, loadLegacyStatus, pollHistoryTail, syncSessions]);

  async function handleUpload(e: any) {
    const file = e.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const { data } = await api.post("/terminal/upload-image", form);
      if (data.ok) setUploadedPath(data.path);
      else alert(data.error || "上传失败");
    } catch {
      alert("上传请求失败");
    }
    e.target.value = "";
  }

  async function handleCreate() {
    try {
      setCreating(true);
      const title = window.prompt("新终端标题（可选）", `终端 ${sessions.length + 1}`) || undefined;
      const workdir = window.prompt("工作目录（可选）", current?.workdir || "/root") || undefined;
      const created = await createTerminalSession({ title, workdir });
      await syncSessions(created.id);
      await loadHistory(created.id);
    } catch (e: any) {
      alert(errText(e, "创建终端失败"));
    } finally {
      setCreating(false);
    }
  }

  async function handleRename() {
    if (!current) return;
    const title = window.prompt("修改终端标题", current.title);
    if (!title || title === current.title) return;
    try {
      const updated = await updateTerminalSession(current.id, { title });
      setSessions((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch (e: any) {
      alert(errText(e, "修改标题失败"));
    }
  }

  async function handleClose() {
    if (!current) return;
    const ok = await confirm({
      title: "关闭当前终端",
      message: `确认关闭终端「${current.title}」？\n历史记录会保留，但当前 shell 进程会结束。`,
      confirmText: "关闭终端",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    try {
      await closeTerminalSession(current.id);
      await syncSessions(current.id);
      await loadHistory(current.id);
    } catch (e: any) {
      alert(errText(e, "关闭终端失败"));
    }
  }

  async function handleDelete() {
    if (!current) return;
    const ok = await confirm({
      title: "删除终端记录",
      message: `确认删除终端「${current.title}」？\n这会同时删除该终端的历史记录。`,
      confirmText: "删除",
      cancelText: "取消",
      danger: true,
    });
    if (!ok) return;
    try {
      const deletingId = current.id;
      await deleteTerminalSession(deletingId);
      const remaining = sessions.filter((s) => s.id !== deletingId);
      setSessions(remaining);
      const nextId = remaining[0]?.id || null;
      setCurrentId(nextId);
      if (nextId) {
        localStorage.setItem(STORAGE_KEY, nextId);
        await loadHistory(nextId);
      } else {
        localStorage.removeItem(STORAGE_KEY);
        setHistory([]);
        setHistoryTotal(0);
      }
      if (!remaining.length && !showArchived) {
        const created = await createTerminalSession({ title: "默认终端" });
        setSessions([created]);
        setCurrentId(created.id);
        localStorage.setItem(STORAGE_KEY, created.id);
        await loadHistory(created.id);
      }
    } catch (e: any) {
      alert(errText(e, "删除终端失败"));
    }
  }

  async function handleToggleArchive() {
    if (!current) return;
    const nextArchived = !current.archived;
    try {
      await updateTerminalSession(current.id, { archived: nextArchived });
      await syncSessions(nextArchived && !showArchived ? null : current.id);
      if (nextArchived && !showArchived) {
        setHistory([]);
        setHistoryTotal(0);
      }
    } catch (e: any) {
      alert(errText(e, nextArchived ? "归档失败" : "取消归档失败"));
    }
  }

  async function handleLoadOlderHistory() {
    if (!current || !history.length) return;
    const firstSeq = history[0]?.seq;
    if (!firstSeq || firstSeq <= 1) return;
    try {
      await loadHistory(current.id, firstSeq);
    } catch (e: any) {
      alert(errText(e, "加载更早历史失败"));
    }
  }

  async function handleLegacyAction(action: "start" | "stop") {
    try {
      setTtydBusy(true);
      const data = action === "start" ? await startLegacyTtyd() : await stopLegacyTtyd();
      setTtydStatus(data);
    } catch (e: any) {
      alert(errText(e, action === "start" ? "启动主终端失败" : "停止主终端失败"));
    } finally {
      setTtydBusy(false);
    }
  }

  async function handleTogglePaneFullscreen() {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
        return;
      }
      await paneWrapRef.current?.requestFullscreen?.();
    } catch (e: any) {
      alert(e?.message || "切换全屏失败");
    }
  }

  const historyLoaded = history.length;
  const hasOlderHistory = !!current && historyLoaded < historyTotal && (history[0]?.seq ?? 1) > 1;
  const transcript = activeIsLegacy ? "" : buildTranscript(history);
  const visibleHistoryCount = activeIsLegacy ? 0 : history.filter((item) => {
    if (item.stream === "input") return isMeaningfulInput(item.content);
    return normalizeHistoryText(item.content).trim().length > 0;
  }).length;

  return (
    <div className={`terminal-workbench-page${isMobileLayout ? " terminal-workbench-mobile" : ""}`}>
      {isMobileLayout ? (
        /* Mobile: single unified control bar — replaces the page title + old two-row mobile bar */
        <div className="terminal-unified-bar">
          <button
            className={`tbtn terminal-unified-chip${showSessionList ? " active" : ""}`}
            onClick={() => {
              setShowMobileActionSheet(false);
              setShowHistory(false);
              setShowSessionList((prev) => !prev);
            }}
          >
            ≡
          </button>
          <div className="terminal-unified-session-info">
            <span className="terminal-unified-session-name">
              {activeIsLegacy ? "主终端" : current?.title || "未选择"}
            </span>
            <span className="terminal-unified-session-state">
              {activeIsLegacy
                ? (ttydStatus?.active ? "shared" : "stopped")
                : current
                  ? (current.archived ? "archived" : current.status)
                  : "idle"}
            </span>
          </div>
          <button
            className={`tbtn terminal-unified-chip${showHistory ? " active" : ""}`}
            onClick={() => {
              setShowMobileActionSheet(false);
              setShowSessionList(false);
              setShowHistory((prev) => !prev);
            }}
          >
            内容
          </button>
          <button className="tbtn terminal-unified-chip" onClick={handleCreate} disabled={creating}>
            +
          </button>
          <button
            className={`tbtn terminal-unified-chip${showMobileActionSheet ? " active" : ""}`}
            onClick={() => {
              setShowSessionList(false);
              setShowHistory(false);
              setShowMobileActionSheet((prev) => !prev);
            }}
          >
            ⋯
          </button>
          <input ref={fileRef} type="file" accept="image/*" style={{ display: "none" }} onChange={handleUpload} />
        </div>
      ) : (
        /* Desktop: full page title */
        <div className="ptitle terminal-page-title">
          <span>/ 服务器终端 · 多终端工作台</span>
          <span className="terminal-page-actions">
            <span className="terminal-page-count" style={{ color: "var(--t3)" }}>
              {loading ? "加载中..." : `${sessions.length} 个终端${showArchived ? "（含归档）" : ""}`}
            </span>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "var(--fs-10)", color: "var(--t3)", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={showArchived}
                onChange={(e) => setShowArchived(e.target.checked)}
                style={{ accentColor: "var(--acc)" }}
              />
              显示已归档
            </label>
            <button className="tbtn" onClick={() => syncSessions(currentId).catch(() => void 0)}>刷新</button>
            <button className="tbtn" onClick={handleCreate} disabled={creating}>+ 开启新终端</button>
            <button className="tbtn" onClick={() => setShowSessionList((v) => !v)}>
              {showSessionList ? "隐藏终端列表" : "显示终端列表"}
            </button>
            <button className="tbtn" onClick={() => setShowHistory((v) => !v)}>
              {showHistory ? "隐藏会话内容" : "显示会话内容"}
            </button>
            <button className="tbtn" onClick={() => fileRef.current?.click()}>📷 上传图片</button>
            <input ref={fileRef} type="file" accept="image/*" style={{ display: "none" }} onChange={handleUpload} />
          </span>
        </div>
      )}
      {isMobileLayout && showMobileActionSheet && (
        <>
          <div className="terminal-sheet-backdrop" onClick={() => setShowMobileActionSheet(false)} />
          <div className="terminal-mobile-sheet card compact" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div className="terminal-section-title">操作</div>
            <button
              className="tbtn"
              onClick={() => { setShowMobileActionSheet(false); handleTogglePaneFullscreen().catch(() => void 0); }}
            >
              {isPaneFullscreen ? "退出全屏" : "全屏"}
            </button>
            <button
              className="tbtn"
              onClick={() => { setShowMobileActionSheet(false); syncSessions(currentId).catch(() => void 0); loadLegacyStatus().catch(() => void 0); }}
            >
              刷新
            </button>
            {activeIsLegacy && (
              <>
                <button className="tbtn" onClick={(e) => { e.stopPropagation(); window.open(ttydStatus?.url || externalTtydUrl, "_blank", "noopener,noreferrer"); setShowMobileActionSheet(false); }}>
                  新窗打开
                </button>
                <button className="tbtn" disabled={ttydBusy || !!ttydStatus?.active} onClick={() => { setShowMobileActionSheet(false); handleLegacyAction("start").catch(() => void 0); }}>
                  启动主终端
                </button>
                <button className="tbtn" disabled={ttydBusy || !ttydStatus?.active} onClick={() => { setShowMobileActionSheet(false); handleLegacyAction("stop").catch(() => void 0); }} style={{ color: "var(--red)", borderColor: "rgba(248,113,113,.35)" }}>
                  停止主终端
                </button>
              </>
            )}
            {!activeIsLegacy && current && (
              <>
                <div style={{ height: 1, background: "var(--b)", margin: "2px 0" }} />
                <button className="tbtn" onClick={() => { setShowMobileActionSheet(false); handleRename(); }}>重命名</button>
                <button className="tbtn" onClick={() => { setShowMobileActionSheet(false); handleToggleArchive(); }}>
                  {current.archived ? "取消归档" : "归档会话"}
                </button>
                <button className="tbtn" onClick={() => { setShowMobileActionSheet(false); handleClose(); }}>关闭 shell</button>
                <button className="tbtn danger" onClick={() => { setShowMobileActionSheet(false); handleDelete(); }}>删除记录</button>
              </>
            )}
            <div style={{ height: 1, background: "var(--b)", margin: "2px 0" }} />
            <label className="terminal-mobile-menu-check">
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} style={{ accentColor: "var(--acc)" }} />
              <span>显示已归档</span>
            </label>
            <button className="tbtn" onClick={() => { fileRef.current?.click(); setShowMobileActionSheet(false); }}>
              📷 上传图片
            </button>
          </div>
        </>
      )}

      {uploadedPath && (
        <div
          className="th-upload-toast"
          onClick={() => {
            if (navigator.clipboard?.writeText) {
              navigator.clipboard.writeText(uploadedPath).then(() => alert("已复制")).catch(() => window.prompt("复制以下路径：", uploadedPath));
            } else {
              window.prompt("复制以下路径：", uploadedPath);
            }
          }}
          style={{ cursor: "pointer" }}
        >
          <span>✅ 已上传：</span>
          <code>{uploadedPath}</code>
          <span style={{ fontSize: "var(--fs-9)", color: "var(--t3)" }}>（点击复制）</span>
          <button className="th-del" onClick={(e) => { e.stopPropagation(); setUploadedPath(null); }}>✕</button>
        </div>
      )}

      {error ? (
        <div className="card" style={{ color: "var(--red)", lineHeight: 1.8 }}>{error}</div>
      ) : (
        <div className="terminal-workbench">
          {showSessionList && (
            <>
              {isMobileLayout && <div className="terminal-sheet-backdrop" onClick={() => setShowSessionList(false)} />}
              <aside className={`terminal-session-list card${isMobileLayout ? " terminal-mobile-sheet terminal-mobile-sheet-list" : ""}`}>
              <div className="terminal-section-title">终端列表</div>
              <div className="terminal-session-scroll">
                <div
                  className={`terminal-session-item${activeIsLegacy ? " active" : ""}`}
                  onClick={() => {
                    setCurrentId(LEGACY_SESSION_ID);
                    localStorage.setItem(STORAGE_KEY, LEGACY_SESSION_ID);
                    if (isMobileLayout) setShowSessionList(false);
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <div className="terminal-session-item-top">
                    <span className="terminal-session-name">主终端</span>
                    <span className={`terminal-session-state ${ttydStatus?.active ? "live" : "closed"}`}>
                      {ttydStatus?.active ? "运行中" : "已停止"}
                    </span>
                  </div>
                  <div className="terminal-session-meta">{ttydStatus ? `服务 ${ttydStatus.status} / ${ttydStatus.substate}` : "读取状态中..."}</div>
                  <div className="terminal-session-preview">长期常驻的 tmux 主会话，多设备复用同一画面；其它入口为临时多终端。</div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button className="tbtn" onClick={(e) => { e.stopPropagation(); window.open(ttydStatus?.url || externalTtydUrl, "_blank", "noopener,noreferrer"); }}>
                      新窗打开
                    </button>
                    <button
                      className="tbtn"
                      disabled={ttydBusy || !!ttydStatus?.active}
                      onClick={(e) => { e.stopPropagation(); handleLegacyAction("start").catch(() => void 0); }}
                    >
                      启动
                    </button>
                    <button
                      className="tbtn"
                      disabled={ttydBusy || !ttydStatus?.active}
                      onClick={(e) => { e.stopPropagation(); handleLegacyAction("stop").catch(() => void 0); }}
                      style={{ color: "var(--red)", borderColor: "rgba(248,113,113,.35)" }}
                    >
                      关闭
                    </button>
                  </div>
                </div>

                {sessions.map((session) => (
                  <button
                    key={session.id}
                    className={`terminal-session-item${session.id === currentId ? " active" : ""}`}
                    onClick={() => {
                      setCurrentId(session.id);
                      localStorage.setItem(STORAGE_KEY, session.id);
                      if (isMobileLayout) setShowSessionList(false);
                    }}
                  >
                    <div className="terminal-session-item-top">
                      <span className="terminal-session-name">{session.title}</span>
                      <span className={`terminal-session-state ${session.status}`}>{session.archived ? "archived" : session.status}</span>
                    </div>
                    <div className="terminal-session-meta">{session.workdir || "/root"}</div>
                    <div className="terminal-session-preview">{session.last_preview || "暂无历史"}</div>
                    <div className="terminal-session-meta">更新于 {fmtTime(session.updated_at)}</div>
                  </button>
                ))}
              </div>
            </aside>
            </>
          )}

          <section className="terminal-main card">
            <div className={`terminal-main-toolbar${isMobileLayout ? " terminal-main-toolbar-mobile-hidden" : ""}`}>
              <div>
                <div className="terminal-current-title">{activeIsLegacy ? "主终端" : current?.title || "未选择终端"}</div>
                <div className="terminal-current-meta">
                  {activeIsLegacy
                    ? `状态 ${ttydStatus?.status || "unknown"} · ${ttydStatus?.substate || "-"}`
                    : current
                      ? `状态 ${current.archived ? "archived" : current.status} · 创建于 ${fmtTime(current.created_at)}`
                      : "请选择左侧终端"}
                </div>
              </div>
              <div className="terminal-inline-actions">
                {activeIsLegacy ? (
                  <>
                    <button className="tbtn" onClick={() => loadLegacyStatus().catch(() => void 0)}>刷新状态</button>
                    <button className="tbtn" onClick={() => window.open(ttydStatus?.url || externalTtydUrl, "_blank", "noopener,noreferrer")}>新窗打开</button>
                    <button className="tbtn" disabled={ttydBusy || !!ttydStatus?.active} onClick={() => handleLegacyAction("start").catch(() => void 0)}>启动服务</button>
                    <button className="tbtn" disabled={ttydBusy || !ttydStatus?.active} onClick={() => handleLegacyAction("stop").catch(() => void 0)} style={{ color: "var(--red)", borderColor: "rgba(248,113,113,.35)" }}>停止服务</button>
                  </>
                ) : (
                  <>
                    <button className="tbtn" onClick={handleRename} disabled={!current}>重命名</button>
                    <button className="tbtn" onClick={handleToggleArchive} disabled={!current}>{current?.archived ? "取消归档" : "归档会话"}</button>
                    <button className="tbtn" onClick={handleClose} disabled={!current}>关闭 shell</button>
                    <button className="tbtn danger" onClick={handleDelete} disabled={!current}>删除记录</button>
                  </>
                )}
              </div>
            </div>
            <div className="terminal-pane-wrap" ref={paneWrapRef}>
              {activeIsLegacy ? (
                ttydStatus?.active ? (
                  <div className="terminal-legacy-shell">
                    <TerminalToolbar iframeRef={legacyFrameRef} iframeUrl={ttydStatus.url} />
                    <iframe
                      ref={legacyFrameRef}
                      title="主终端"
                      src={ttydStatus.url}
                      className="terminal-legacy-frame"
                      style={{ width: "100%", height: "100%", border: "none", background: "#000" }}
                    />
                    <TerminalToolbar iframeRef={legacyFrameRef} iframeUrl={ttydStatus.url} variant="bottom" />
                  </div>
                ) : (
                  <div className="terminal-empty" style={{ gap: 10, padding: 24, textAlign: "center" }}>
                    <div>主终端当前未运行。</div>
                    <button className="tbtn" disabled={ttydBusy} onClick={() => handleLegacyAction("start").catch(() => void 0)}>
                      启动主终端
                    </button>
                  </div>
                )
              ) : current ? (
                <TerminalLivePane
                  session={current}
                  onExit={() => {
                    syncSessions(current.id).catch(() => void 0);
                    loadHistory(current.id).catch(() => void 0);
                  }}
                  onLiveOutput={handleLiveOutput}
                />
              ) : (
                <div className="terminal-empty">暂无终端</div>
              )}
            </div>
          </section>

          {showHistory && (
            <>
              {isMobileLayout && <div className="terminal-sheet-backdrop" onClick={() => setShowHistory(false)} />}
              <aside className={`terminal-history-panel card${isMobileLayout ? " terminal-mobile-sheet terminal-mobile-sheet-history" : ""}`}>
              <div className="terminal-section-title">
                会话内容
                <span style={{ marginLeft: 8, color: "var(--t3)", fontSize: "var(--fs-10)" }}>
                  {activeIsLegacy ? "主终端快照" : current ? "会话快照" : ""}
                </span>
              </div>
              {activeIsLegacy ? (
                <LegacySnapshotPanel visible={activeIsLegacy} />
              ) : current ? (
                <LiveSnapshotPanel visible={!activeIsLegacy} sessionId={current.id} />
              ) : (
                <div className="terminal-empty" style={{ padding: 20 }}>选择左侧任一终端查看快照</div>
              )}
            </aside>
            </>
          )}
        </div>
      )}
    </div>
  );
}
