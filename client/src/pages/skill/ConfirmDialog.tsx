import { ReactNode, useState } from "react";
import Modal from "./Modal";
import { errText } from "../../lib/errText";

export type ConfirmDialogProps = {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
};

/**
 * Two-button confirmation wrapper. Disables buttons and keeps the modal open
 * while onConfirm is pending; errors bubble up to the caller (handle there).
 */
export default function ConfirmDialog({
  title,
  message,
  confirmLabel = "确认",
  cancelLabel = "取消",
  danger = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handle = async () => {
    setErr(null);
    setBusy(true);
    try {
      await onConfirm();
      onClose();
    } catch (e: any) {
      setErr(errText(e, "操作失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={title}
      onClose={onClose}
      locked={busy}
      width={420}
      footer={
        <>
          <button className="tbtn" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            className={"tbtn " + (danger ? "danger" : "primary")}
            onClick={handle}
            disabled={busy}
          >
            {busy ? "处理中…" : confirmLabel}
          </button>
        </>
      }
    >
      {err && <div className="sks-error" style={{ marginBottom: 10 }}>⚠ {err}</div>}
      <div style={{ fontSize: "var(--fs-12)", color: "var(--t2)", lineHeight: 1.6 }}>{message}</div>
    </Modal>
  );
}
