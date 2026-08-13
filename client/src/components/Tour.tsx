import { useEffect, useLayoutEffect, useRef, useState } from "react";

export type TourStep = {
  /** CSS selector of the element to highlight. Omit for a centered intro step. */
  sel?: string;
  title: string;
  body: string;
  /** Extra padding around the highlight box, px. */
  pad?: number;
};

/**
 * Lightweight interactive product tour: dims everything except the target
 * element (via a big box-shadow "hole"), shows a themed tooltip with prev/next/
 * skip, scrolls the target into view, follows resize/scroll. Steps with no
 * `sel` render a centered intro card.
 */
export default function Tour({ steps, onClose }: { steps: TourStep[]; onClose: () => void }) {
  const [i, setI] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [tipPos, setTipPos] = useState<{ left: number; top: number; place: "below" | "above" | "center" }>({ left: 0, top: 0, place: "center" });
  const tipRef = useRef<HTMLDivElement>(null);
  const step = steps[i];

  // Locate + track the target element.
  useLayoutEffect(() => {
    if (!step) return;
    const measure = () => {
      const el = step.sel ? (document.querySelector(step.sel) as HTMLElement | null) : null;
      setRect(el ? el.getBoundingClientRect() : null);
    };
    const el = step.sel ? (document.querySelector(step.sel) as HTMLElement | null) : null;
    if (el) el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
    measure();
    const t = window.setTimeout(measure, 300); // re-measure after smooth scroll settles
    const onMove = () => measure();
    window.addEventListener("resize", onMove, true);
    window.addEventListener("scroll", onMove, true);
    return () => { clearTimeout(t); window.removeEventListener("resize", onMove, true); window.removeEventListener("scroll", onMove, true); };
  }, [i, step]);

  // Position the tooltip relative to the target (or center if none).
  useLayoutEffect(() => {
    const tip = tipRef.current;
    if (!tip) return;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight, m = 12;
    if (!rect) { setTipPos({ left: (vw - tw) / 2, top: (vh - th) / 2, place: "center" }); return; }
    const below = rect.bottom + m + th <= vh;
    const top = below ? rect.bottom + m : Math.max(m, rect.top - m - th);
    let left = rect.left + rect.width / 2 - tw / 2;
    left = Math.min(Math.max(m, left), vw - tw - m);
    setTipPos({ left, top, place: below ? "below" : "above" });
  }, [rect, i]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight" || e.key === "Enter") next();
      else if (e.key === "ArrowLeft") setI((p) => Math.max(0, p - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const next = () => { if (i >= steps.length - 1) onClose(); else setI((p) => p + 1); };
  if (!step) return null;

  const pad = step.pad ?? 6;
  const spot = rect
    ? {
        position: "fixed" as const,
        left: rect.left - pad, top: rect.top - pad,
        width: rect.width + pad * 2, height: rect.height + pad * 2,
        borderRadius: 8,
        boxShadow: "0 0 0 3px var(--acc), 0 0 0 100vmax rgba(0,0,0,.62)",
        zIndex: 100000, pointerEvents: "none" as const, transition: "all .22s cubic-bezier(.4,0,.2,1)",
      }
    : null;

  return (
    <>
      {/* Backdrop: only when there's no target (otherwise the box-shadow dims). */}
      {!rect && <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.62)", zIndex: 100000 }} />}
      {spot && <div style={spot} />}
      <div
        ref={tipRef}
        style={{
          position: "fixed", left: tipPos.left, top: tipPos.top, width: 320, maxWidth: "calc(100vw - 24px)",
          zIndex: 100001, background: "var(--bg1)", border: "1px solid var(--b2)", borderRadius: 10,
          boxShadow: "0 12px 40px rgba(0,0,0,.45)", padding: "14px 15px", fontFamily: "var(--sans)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
          <div style={{ fontSize: "var(--fs-135)", fontWeight: 700, color: "var(--t)" }}>{step.title}</div>
          <span style={{ fontSize: "var(--fs-11)", color: "var(--t3)" }}>{i + 1} / {steps.length}</span>
        </div>
        <div style={{ fontSize: "var(--fs-125)", lineHeight: 1.7, color: "var(--t2)", whiteSpace: "pre-line" }}>{step.body}</div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 13 }}>
          <button onClick={onClose} style={btn("ghost")}>跳过</button>
          <div style={{ display: "flex", gap: 8 }}>
            {i > 0 && <button onClick={() => setI((p) => p - 1)} style={btn("ghost")}>上一步</button>}
            <button onClick={next} style={btn("primary")}>{i >= steps.length - 1 ? "完成" : "下一步"}</button>
          </div>
        </div>
      </div>
    </>
  );
}

function btn(kind: "primary" | "ghost"): React.CSSProperties {
  const base: React.CSSProperties = {
    fontFamily: "var(--sans)", fontSize: "var(--fs-12)", padding: "5px 12px", borderRadius: 5, cursor: "pointer",
    border: "1px solid var(--b2)", transition: "all .12s",
  };
  return kind === "primary"
    ? { ...base, background: "var(--acc)", color: "#06140c", borderColor: "var(--acc)", fontWeight: 600 }
    : { ...base, background: "transparent", color: "var(--t2)" };
}
