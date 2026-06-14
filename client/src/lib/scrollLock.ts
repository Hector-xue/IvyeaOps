// Reference-counted <body> scroll lock.
//
// Why this exists: several modals/drawers each used to do
//   const prev = document.body.style.overflow;
//   document.body.style.overflow = "hidden";
//   // ...restore `prev` on close
// When two overlapped, the inner one captured prev="hidden" (the value the OUTER
// modal had set) and, on close, restored "hidden" with NO modal open — leaving
// the whole page unscrollable until a hard refresh. (Reported as: "偶尔无法滚动,
// 刷新才好".)
//
// A single counter removes the race: the lock is applied exactly once when the
// count goes 0→1 and removed exactly once when it returns to 0. Each caller gets
// an idempotent release fn, so double-calls / unmount-after-route-change can't
// under/over-count.

let lockCount = 0;
let savedOverflow = "";

/** Lock body scroll; returns an idempotent release fn. */
export function lockBodyScroll(): () => void {
  if (typeof document === "undefined") return () => {};
  if (lockCount === 0) {
    savedOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
  lockCount += 1;

  let released = false;
  return () => {
    if (released) return;
    released = true;
    lockCount = Math.max(0, lockCount - 1);
    if (lockCount === 0) {
      document.body.style.overflow = savedOverflow;
    }
  };
}

/**
 * Last-resort safety net (call on route change): force-unlock and zero the
 * counter so a missed release can never leave the page permanently unscrollable.
 * A no-op for the user when nothing leaked.
 */
export function resetBodyScrollLock(): void {
  if (typeof document === "undefined") return;
  lockCount = 0;
  savedOverflow = "";
  document.body.style.overflow = "";
}
