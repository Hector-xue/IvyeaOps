/**
 * 上下文进度条 —— 这条会话把模型的窗口用掉多少了。
 *
 * 为什么值得占一行：agentic 会话是**上下文先耗尽、再谈别的**。用户看到的症状是
 * "聊着聊着它开始忘事 / 突然变慢 / 报了个看不懂的错"，而真实原因是窗口满了在压缩。
 * 一条进度条把这件事提前摆出来，用户自己就知道该新开一轮还是继续。
 *
 * 三条规矩：
 * ① **没有数就整块不出现。** 老 agent 不发 context 事件，这时画任何百分比都是编的。
 * ② **明说是估算。** 数字前面带 ~，细账里写清"按字符估算"。一个标着估算的数比一个
 *    看着精确、来路不明的数诚实。
 * ③ 细账要能回答"该动哪里"：系统提示词 / 工具 / 对话消息三档分开列。
 */
import { useEffect, useRef, useState } from "react";
import type { IvyeaContextUsage } from "../../api/ivyeaAgent";

/** 三档的配色。和条形图里的分段一一对应，细账靠这个点认。 */
const PARTS: { key: keyof IvyeaContextUsage["breakdown"]; label: string; cls: string }[] = [
  { key: "system", label: "系统提示词", cls: "sys" },
  { key: "tools", label: "工具", cls: "tools" },
  { key: "messages", label: "对话消息", cls: "msgs" },
];

export function fmtTok(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e5 ? 0 : 1)}K`;
  return String(Math.round(n));
}

export default function ContextMeter({ usage }: { usage?: IvyeaContextUsage | null }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // 点外面收起。细账是个浮层，不给出路的话它会一直挡着底下的统计条。
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!usage || !usage.window || !Number.isFinite(usage.used)) return null;

  const window_ = usage.window;
  const used = Math.max(0, usage.used);
  const pct = Math.min(100, (used * 100) / window_);
  // 1% 以下也要看得见一丝 —— 完全空的条会被读成"这个功能没接上"。
  const shown = pct > 0 ? Math.max(0.6, pct) : 0;
  const level = pct >= 90 ? "crit" : pct >= 70 ? "warn" : "ok";
  const b = usage.breakdown || { system: 0, tools: 0, messages: 0 };

  return (
    <div className={"cc-ctx level-" + level} ref={wrapRef}>
      <button
        type="button"
        className="cc-ctx-trigger"
        onClick={() => setOpen((v) => !v)}
        title={`上下文已用 ${pct.toFixed(pct < 10 ? 1 : 0)}% · 约 ${fmtTok(used)} / ${fmtTok(window_)} token（估算）`}
        aria-expanded={open}
      >
        <span className="cc-ctx-track">
          {/* 分段填充：三档按各自占比铺，一眼看出是谁吃掉的 */}
          {PARTS.map((p) => (
            <i
              key={p.key}
              className={"cc-ctx-seg seg-" + p.cls}
              style={{ width: `${Math.min(100, ((b as any)[p.key] || 0) * 100 / window_)}%` }}
            />
          ))}
          {shown > 0 && shown < 0.8 && <i className="cc-ctx-seg seg-msgs" style={{ width: "0.6%" }} />}
        </span>
        <span className="cc-ctx-num">{pct < 1 ? "<1" : pct.toFixed(pct < 10 ? 1 : 0)}%</span>
      </button>

      {open && (
        <div className="cc-ctx-pop" role="dialog" aria-label="上下文用量细账">
          <div className="cc-ctx-pop-head">
            <span className="cc-ctx-pop-title">上下文已用 {pct < 1 ? "<1" : pct.toFixed(pct < 10 ? 1 : 0)}%</span>
            <span className="cc-ctx-pop-total">~{fmtTok(used)} / {fmtTok(window_)}</span>
          </div>
          <span className="cc-ctx-track big">
            {PARTS.map((p) => (
              <i
                key={p.key}
                className={"cc-ctx-seg seg-" + p.cls}
                style={{ width: `${Math.min(100, ((b as any)[p.key] || 0) * 100 / window_)}%` }}
              />
            ))}
          </span>
          <ul className="cc-ctx-list">
            {PARTS.map((p) => (
              <li key={p.key}>
                <i className={"cc-ctx-dot seg-" + p.cls} />
                <span className="cc-ctx-k">{p.label}</span>
                <span className="cc-ctx-v">~{fmtTok((b as any)[p.key] || 0)}</span>
              </li>
            ))}
          </ul>
          <div className="cc-ctx-foot">
            按字符估算，非服务商回报值{usage.model ? ` · ${usage.model}` : ""}
            {pct >= 70 && <b>　窗口快满了，新开一轮会更快也更准</b>}
          </div>
        </div>
      )}
    </div>
  );
}
