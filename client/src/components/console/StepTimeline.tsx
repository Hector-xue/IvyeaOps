/**
 * 步骤时间线 —— 任务台里「Agent 刚才干了什么」的可视化。
 *
 * 对标 beili 的「✓ 匹配技能 → ✓ 调用 MCP」和 MyLevis 的可折叠工具行：
 * 每一步用业务语言说清楚调了什么、成没成、花了多久，参数按需展开。
 *
 * 三类行：
 *   - 技能命中（skill_match 事件）→ 一排技能芯片
 *   - 结构化步骤（step 事件）    → 带状态与耗时的芯片，可展开看原始参数
 *   - 自由文本注记（老 agent）   → 一行灰字叙述，不猜工具名
 */
import { useState } from "react";
import { formatMs, type ConsoleStep } from "../../lib/stepLabels";

export type MatchedSkill = { id: string; title: string; domain?: string; score?: number };

function StatusMark({ status }: { status: ConsoleStep["status"] }) {
  if (status === "running") return <span className="cs-mark cs-run" aria-label="进行中" />;
  if (status === "error") return <span className="cs-mark cs-err" aria-label="失败">✕</span>;
  return <span className="cs-mark cs-ok" aria-label="完成">✓</span>;
}

function StepChip({ step }: { step: ConsoleStep }) {
  const [open, setOpen] = useState(false);
  const hasArgs = !!step.args && Object.keys(step.args).length > 0;
  const ms = formatMs(step.ms);

  return (
    <div className={"cs-chip cs-" + step.phase + (step.destructive ? " cs-destructive" : "")}>
      <button
        type="button"
        className="cs-chip-head"
        onClick={() => hasArgs && setOpen((v) => !v)}
        // 没有参数可展开时不装成可点的样子
        style={hasArgs ? undefined : { cursor: "default" }}
        aria-expanded={hasArgs ? open : undefined}
      >
        <i className="cs-icon">{step.icon}</i>
        <span className="cs-title">{step.title}</span>
        {step.detail && <span className="cs-detail">{step.detail}</span>}
        {step.destructive && <span className="cs-badge">写操作</span>}
        {ms && <span className="cs-ms">{ms}</span>}
        <StatusMark status={step.status} />
        {hasArgs && <span className="cs-caret">{open ? "▾" : "▸"}</span>}
      </button>
      {open && hasArgs && (
        <pre className="cs-args scroll-thin">{JSON.stringify(step.args, null, 2)}</pre>
      )}
    </div>
  );
}

function NoteRow({ step }: { step: ConsoleStep }) {
  return (
    <div className="cs-note">
      <i className="cs-icon">{step.icon}</i>
      <span>{step.title}</span>
    </div>
  );
}

export default function StepTimeline({
  steps,
  skills,
  elapsedMs,
  running,
}: {
  steps: ConsoleStep[];
  skills: MatchedSkill[];
  elapsedMs?: number;
  running?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const real = steps.filter((s) => s.phase !== "note");
  const notes = steps.filter((s) => s.phase === "note");
  if (!steps.length && !skills.length) return null;

  // 摘要行对标 MyLevis 的「已匹配 5 项 · 9.4 秒」
  const bits: string[] = [];
  if (skills.length) bits.push(`已匹配 ${skills.length} 项技能`);
  if (real.length) bits.push(`${real.length} 步`);
  const el = formatMs(elapsedMs);
  if (el) bits.push(el);

  return (
    <div className="cs-timeline">
      <button type="button" className="cs-summary" onClick={() => setCollapsed((v) => !v)}>
        <span className={"cs-summary-icon" + (running ? " spinning" : "")}>{running ? "⟳" : "⌕"}</span>
        <span>{bits.join(" · ") || "执行过程"}</span>
        <span className="cs-caret">{collapsed ? "▸" : "▾"}</span>
      </button>

      {!collapsed && (
        <div className="cs-body">
          {skills.length > 0 && (
            <div className="cs-phase">
              <div className="cs-phase-head"><span className="cs-ok">✓</span> 理解问题，匹配最合适的技能</div>
              <div className="cs-chips">
                {skills.map((s) => (
                  <span className="cs-chip cs-skill" key={s.id} title={s.id}>
                    <span className="cs-chip-head" style={{ cursor: "default" }}>
                      <i className="cs-icon">✦</i>
                      <span className="cs-title">Ivyea Skill · {s.title}</span>
                    </span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {real.length > 0 && (
            <div className="cs-phase">
              <div className="cs-phase-head">
                <span className="cs-ok">✓</span> 调用能力读取真实数据
              </div>
              <div className="cs-chips">
                {real.map((s) => <StepChip key={s.key} step={s} />)}
              </div>
            </div>
          )}

          {notes.length > 0 && (
            <div className="cs-phase cs-notes">
              {notes.map((s) => <NoteRow key={s.key} step={s} />)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
