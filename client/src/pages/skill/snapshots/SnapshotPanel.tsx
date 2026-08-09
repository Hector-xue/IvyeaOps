import { useCallback, useEffect, useState } from "react";
import {
  createSnapshot,
  deleteSnapshot,
  listSnapshots,
  restoreSnapshot,
  SnapshotMeta,
} from "../../../api/skill";
import ConfirmDialog from "../ConfirmDialog";
import DiffViewer from "./DiffViewer";
import { errText } from "../../../lib/errText";

export type SnapshotPanelProps = {
  name: string;
  /** Called after a successful restore so the editor can reload detail. */
  onRestored?: (preRestoreId: string | null) => void;
  /** Switch back to edit mode. */
  onBack: () => void;
};

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function fmtLocal(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch { return iso; }
}

type Confirming =
  | { kind: "restore"; snap: SnapshotMeta }
  | { kind: "delete"; snap: SnapshotMeta }
  | null;

type Toast = { text: string; undoId?: string | null } | null;

export default function SnapshotPanel({ name, onRestored, onBack }: SnapshotPanelProps) {
  const [items, setItems] = useState<SnapshotMeta[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [diffing, setDiffing] = useState<SnapshotMeta | null>(null);
  const [toast, setToast] = useState<Toast>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      setItems(await listSnapshots(name));
    } catch (e: any) {
      setErr(errText(e, "加载失败"));
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => { void load(); }, [load]);

  const doCreate = async () => {
    setCreating(true);
    setErr(null);
    try {
      const snap = await createSnapshot(name, label.trim() || undefined);
      setItems((cur) => [snap, ...cur]);
      setLabel("");
      setToast({ text: `已创建快照 ${snap.id.slice(0, 8)}` });
      window.setTimeout(() => setToast(null), 3000);
    } catch (e: any) {
      setErr(errText(e, "创建快照失败"));
    } finally {
      setCreating(false);
    }
  };

  const doRestore = async (snap: SnapshotMeta) => {
    const res = await restoreSnapshot(name, snap.id);
    await load();
    onRestored?.(res.pre_restore_snapshot_id);
    setToast({
      text: `已恢复到 ${snap.id.slice(0, 8)}（原状态已自动存为 ${res.pre_restore_snapshot_id?.slice(0, 8) ?? "—"}）`,
      undoId: res.pre_restore_snapshot_id,
    });
    window.setTimeout(() => setToast(null), 8000);
  };

  const doDelete = async (snap: SnapshotMeta) => {
    await deleteSnapshot(name, snap.id);
    setItems((cur) => cur.filter((s) => s.id !== snap.id));
    setToast({ text: `已删除快照 ${snap.id.slice(0, 8)}` });
    window.setTimeout(() => setToast(null), 3000);
  };

  const undoRestore = async (undoId: string) => {
    // Undo = restore the pre-restore snapshot we captured above.
    setToast(null);
    try {
      await restoreSnapshot(name, undoId);
      await load();
      onRestored?.(null);
      setToast({ text: "已撤销恢复" });
      window.setTimeout(() => setToast(null), 3000);
    } catch (e: any) {
      setErr(errText(e, "撤销失败"));
    }
  };

  return (
    <div className="sks-snap">
      <header className="sks-snap-head">
        <div className="left">
          <button className="tbtn" onClick={onBack} title="返回编辑">← 返回</button>
          <span className="title">快照 · {name}</span>
        </div>
        <button className="tbtn" onClick={() => void load()} disabled={loading}>
          {loading ? "刷新中…" : "刷新"}
        </button>
      </header>

      <div className="sks-snap-toolbar">
        <input
          className="sks-input"
          placeholder="可选标签（如 before-refactor）"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          maxLength={80}
          disabled={creating}
        />
        <button className="tbtn primary" onClick={doCreate} disabled={creating}>
          {creating ? "创建中…" : "+ 建快照"}
        </button>
      </div>

      {err && <div className="sks-error">⚠ {err}</div>}

      <div className="sks-snap-list">
        {!loading && items.length === 0 ? (
          <div className="sks-empty">还没有任何快照</div>
        ) : (
          items.map((s) => (
            <div key={s.id} className="sks-snap-row">
              <div className="main">
                <div className="head-line">
                  <code className="id">{s.id.slice(0, 8)}</code>
                  {s.label && <span className="label">{s.label}</span>}
                </div>
                <div className="meta">
                  <span>{fmtLocal(s.created_at)}</span>
                  <span>·</span>
                  <span>{s.file_count} 文件</span>
                  <span>·</span>
                  <span>{fmtBytes(s.size_bytes)}</span>
                </div>
              </div>
              <div className="actions">
                <button className="tbtn" onClick={() => setDiffing(s)}>Diff</button>
                <button className="tbtn primary" onClick={() => setConfirming({ kind: "restore", snap: s })}>恢复</button>
                <button className="tbtn danger" onClick={() => setConfirming({ kind: "delete", snap: s })}>删除</button>
              </div>
            </div>
          ))
        )}
      </div>

      {diffing && (
        <DiffViewer name={name} snapshotId={diffing.id} onClose={() => setDiffing(null)} />
      )}

      {confirming?.kind === "restore" && (
        <ConfirmDialog
          title="恢复到此快照"
          message={
            <>
              将 <b>{name}</b> 恢复到快照 <code>{confirming.snap.id.slice(0, 8)}</code>。
              <br />当前状态会自动存为一个新快照，恢复后可撤销。
            </>
          }
          confirmLabel="恢复"
          onConfirm={() => doRestore(confirming.snap)}
          onClose={() => setConfirming(null)}
        />
      )}
      {confirming?.kind === "delete" && (
        <ConfirmDialog
          title="删除快照"
          message={
            <>将永久删除快照 <code>{confirming.snap.id.slice(0, 8)}</code>，操作不可恢复。</>
          }
          confirmLabel="删除"
          danger
          onConfirm={() => doDelete(confirming.snap)}
          onClose={() => setConfirming(null)}
        />
      )}

      {toast && (
        <div className="sks-toast">
          <span>{toast.text}</span>
          {toast.undoId && (
            <button
              className="tbtn"
              style={{ marginLeft: 10 }}
              onClick={() => undoRestore(toast.undoId as string)}
            >
              撤销
            </button>
          )}
        </div>
      )}
    </div>
  );
}
