import { useCallback, useEffect, useRef, useState } from "react";
import { errText } from "../lib/errText";

export type SaveStatus = "idle" | "dirty" | "saving" | "saved" | "error";

export type AutoSaveState = {
  status: SaveStatus;
  lastSavedAt: Date | null;
  error: string | null;
};

export type UseAutoSaveOptions<T> = {
  /** Current in-memory value. */
  value: T;
  /** The initial / server-side baseline. If `value` deep-equals this, we stay idle. */
  baseline: T;
  /** Called to persist. Throws to surface error. */
  onSave: (value: T) => Promise<void>;
  /** Debounce window. */
  debounceMs?: number;
  /** Set false to freeze the saver (e.g. readonly skill or file not loaded). */
  enabled?: boolean;
};

export type UseAutoSaveReturn = AutoSaveState & {
  /** Cancel pending timer and save immediately. Returns when save resolves. */
  flush: () => Promise<void>;
  /** Reset status to idle (use when switching files). */
  reset: () => void;
};

/**
 * Debounced auto-save with a manual flush(). Caller owns the value; this hook
 * only tracks dirty state and fires onSave. It does NOT persist across
 * file switches — call reset() when the active file changes.
 */
export function useAutoSave<T>({
  value,
  baseline,
  onSave,
  debounceMs = 600,
  enabled = true,
}: UseAutoSaveOptions<T>): UseAutoSaveReturn {
  const [state, setState] = useState<AutoSaveState>({
    status: "idle",
    lastSavedAt: null,
    error: null,
  });

  // We keep the latest value / onSave in refs so the flush closure is stable.
  const valueRef = useRef(value);
  const baselineRef = useRef(baseline);
  const onSaveRef = useRef(onSave);
  const timerRef = useRef<number | null>(null);
  const inflightRef = useRef<Promise<void> | null>(null);

  useEffect(() => { valueRef.current = value; }, [value]);
  useEffect(() => { baselineRef.current = baseline; }, [baseline]);
  useEffect(() => { onSaveRef.current = onSave; }, [onSave]);

  const clearTimer = () => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const doSave = useCallback(async () => {
    // Only one save in flight at a time; if one is already running, wait for it
    // and then re-check dirtiness.
    if (inflightRef.current) {
      try { await inflightRef.current; } catch { /* swallow, handled below */ }
    }

    const snapshot = valueRef.current;
    if (snapshot === baselineRef.current) {
      setState((s) => ({ ...s, status: "idle" }));
      return;
    }
    setState((s) => ({ ...s, status: "saving", error: null }));
    const p = (async () => {
      try {
        await onSaveRef.current(snapshot);
        baselineRef.current = snapshot;
        setState({ status: "saved", lastSavedAt: new Date(), error: null });
      } catch (e: any) {
        const msg = errText(e, "保存失败");
        setState({ status: "error", lastSavedAt: null, error: String(msg) });
        throw e;
      }
    })();
    inflightRef.current = p;
    try { await p; } finally { inflightRef.current = null; }
  }, []);

  // Dirty-watch: mark dirty and schedule a save whenever value diverges from
  // baseline. We compare by reference for objects or by === for primitives;
  // callers passing objects should keep a stable reference when unchanged.
  useEffect(() => {
    if (!enabled) return;
    if (value === baseline) return;
    setState((s) => (s.status === "saving" ? s : { ...s, status: "dirty" }));
    clearTimer();
    timerRef.current = window.setTimeout(() => {
      doSave().catch(() => { /* state already updated */ });
    }, debounceMs);
    return () => clearTimer();
  }, [value, baseline, enabled, debounceMs, doSave]);

  const flush = useCallback(async () => {
    clearTimer();
    if (!enabled) return;
    await doSave().catch(() => {});
  }, [enabled, doSave]);

  const reset = useCallback(() => {
    clearTimer();
    inflightRef.current = null;
    setState({ status: "idle", lastSavedAt: null, error: null });
  }, []);

  return { ...state, flush, reset };
}
