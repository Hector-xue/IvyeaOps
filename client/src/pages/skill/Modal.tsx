import { ReactNode, useEffect } from "react";
import { lockBodyScroll } from "../../lib/scrollLock";

export type ModalProps = {
  title: string;
  onClose: () => void;
  /** Disable backdrop click / ESC close (e.g. while a submission is in flight). */
  locked?: boolean;
  /** Max width px; defaults to 480. */
  width?: number;
  children: ReactNode;
  footer?: ReactNode;
};

/**
 * Lightweight modal. Backdrop click + ESC closes (unless locked). Traps body
 * scrolling while open. z-index 1000 so it sits above the workbench chrome.
 */
export default function Modal({
  title,
  onClose,
  locked = false,
  width = 480,
  children,
  footer,
}: ModalProps) {
  useEffect(() => {
    const releaseScroll = lockBodyScroll();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !locked) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      releaseScroll();
      window.removeEventListener("keydown", onKey);
    };
  }, [locked, onClose]);

  return (
    <div
      className="sks-modal-backdrop"
      onClick={() => { if (!locked) onClose(); }}
    >
      <div
        className="sks-modal"
        style={{ maxWidth: width }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <header className="sks-modal-head">
          <span className="t">{title}</span>
          <button
            className="tbtn"
            onClick={onClose}
            disabled={locked}
            title="关闭"
          >
            ✕
          </button>
        </header>
        <div className="sks-modal-body">{children}</div>
        {footer && <footer className="sks-modal-foot">{footer}</footer>}
      </div>
    </div>
  );
}
