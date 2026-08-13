import { useCallback, useEffect, useState } from "react";
import {
  listTrash,
  restoreFromTrash,
  purgeTrash,
  TrashEntry,
} from "../../api/skill";
import ConfirmDialog from "./ConfirmDialog";
import { errText } from "../../lib/errText";

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function fmtLocal(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      year: "2-digit", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

/**
 * Returns a human-friendly "time until/since" label for a future ISO timestamp.
 * Rounded to the biggest unit that fits. Negative = already expired.
 */
function fmtRelativeFuture(iso: string, now: number): { label: string; critical: boolean } {
  const t = Date.parse(iso);
  if (isNaN(t)) return { label: "-", critical: false };
  const diffSec = (t - now) / 1000;
  if (diffSec <= 0) return { label: "已到期", critical: true };
  const min = diffSec / 60;
  const hr = min / 60;
  const day = hr / 24;
  let label: string;
  if (day >= 1) label = `还剩 ${Math.floor(day)} 天`;
  else if (hr >= 1) label = `还剩 ${Math.floor(hr)} 小时`;
  else label = `还剩 ${Math.max(1, Math.floor(min))} 分钟`;
  return { label, critical: hr < 24 };
}

export default function TrashList() {
  const [entries, setEntries] = useState<TrashEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<
    | { kind: "restore"; entry: TrashEntry }
    | { kind: "purge"; entry: TrashEntry }
    | null
  >(null);
  const [now, setNow] = useState(Date.now());

  // Tick once a minute so "still N days left" updates without a refresh.
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const data = await listTrash();
      setEntries(data);
    } catch (e: any) {
      setErr(errText(e, "加载失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const doRestore = async (id: string, origName: string) => {
    await restoreFromTrash(id);
    setEntries((cur) => cur.filter((e) => e.id !== id));
    setToast(`已恢复：${origName}`);
    window.setTimeout(() => setToast(null), 3000);
  };

  const doPurge = async (id: string, origName: string) => {
    await purgeTrash(id);
    setEntries((cur) => cur.filter((e) => e.id !== id));
    setToast(`已永久删除：${origName}`);
    window.setTimeout(() => setToast(null), 3000);
  };

  return (
    <div className="sks-browse">
      <div className="sks-list-toolbar">
        <span style={{ fontSize: "var(--fs-11)", color: "var(--t2)" }}>
          已删除的 skill（保留 7 天）
        </span>
        <button
          className="tbtn"
          onClick={() => void load()}
          disabled={loading}
          style={{ marginLeft: "auto" }}
        >
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>

      {err && <div className="sks-error">⚠ {err}</div>}
      {toast && <div className="sks-toast">{toast}</div>}

      <div className="sks-list">
        {!loading && entries.length === 0 ? (
          <div className="sks-empty">回收站为空</div>
        ) : (
          entries.map((e) => {
            const rel = fmtRelativeFuture(e.expires_at, now);
            return (
              <div key={e.id} className="sks-trash-row">
                <div className="main">
                  <div className="name">{e.original_name}</div>
                  <div className="meta">
                    <span>删于 {fmtLocal(e.trashed_at)}</span>
                    <span>·</span>
                    <span className={rel.critical ? "expire warn" : "expire"}>{rel.label}</span>
                    <span>·</span>
                    <span>{fmtBytes(e.size_bytes)}</span>
                  </div>
                </div>
                <div className="actions">
                  <button
                    className="tbtn primary"
                    onClick={() => setConfirming({ kind: "restore", entry: e })}
                  >
                    恢复
                  </button>
                  <button
                    className="tbtn danger"
                    onClick={() => setConfirming({ kind: "purge", entry: e })}
                  >
                    永久删除
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {confirming?.kind === "restore" && (
        <ConfirmDialog
          title="恢复 Skill"
          message={
            <>将 <b>{confirming.entry.original_name}</b> 从回收站恢复到 skills 目录。</>
          }
          confirmLabel="恢复"
          onConfirm={() => doRestore(confirming.entry.id, confirming.entry.original_name)}
          onClose={() => setConfirming(null)}
        />
      )}
      {confirming?.kind === "purge" && (
        <ConfirmDialog
          title="永久删除"
          message={
            <>
              将彻底删除 <b>{confirming.entry.original_name}</b>，此操作不可恢复。
            </>
          }
          confirmLabel="永久删除"
          danger
          onConfirm={() => doPurge(confirming.entry.id, confirming.entry.original_name)}
          onClose={() => setConfirming(null)}
        />
      )}
    </div>
  );
}
