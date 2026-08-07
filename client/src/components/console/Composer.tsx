/**
 * 任务台输入器 —— 对标 MyLevis 底部那一行 chip：
 *   `+ | 默认工作区 ▾ | 默认审批 ▾ | 广告分析规划助手 ▾ | gpt-5.6-sol ▾ | ➤`
 *
 * 这些 chip 不是装饰：每一枚都直接映射到 agent serve 的 chat payload 字段
 * （workspace / plan_mode+approval / skill / 模型），选了就真的按那个跑。
 */
import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import SheetSelect from "../SheetSelect";
import type { IvyeaSkillInfo } from "../../api/ivyeaAgent";

/** 审批档位 → payload。只读是今天的默认语义，逐项审批需要 agent ≥ v1.9。 */
export type ApprovalMode = "readonly" | "ask";

export const APPROVAL_MODES: { value: ApprovalMode; label: string; hint: string }[] = [
  { value: "readonly", label: "只读建议", hint: "Agent 只分析和给方案，绝不改动任何线上数据" },
  { value: "ask", label: "逐项审批", hint: "需要写入时停下来问你，确认后才执行" },
];

export function approvalPayload(mode: ApprovalMode): { plan_mode: boolean; approval: "none" | "remote" } {
  return mode === "ask"
    ? { plan_mode: false, approval: "remote" }
    : { plan_mode: true, approval: "none" };
}

export type ComposerValue = {
  text: string;
  workspace: string;
  approval: ApprovalMode;
  skill: string;
};

export default function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  onAttach,
  busy,
  skills,
  workspaces,
  modelLabel,
  onModelClick,
  placeholder = "描述任务、粘贴材料，或说说你想让 Ivyea 先看什么…",
  autoFocus,
  compact,
  attaching,
}: {
  value: ComposerValue;
  onChange: (patch: Partial<ComposerValue>) => void;
  onSubmit: () => void;
  onStop?: () => void;
  onAttach?: (file: File) => void;
  busy?: boolean;
  skills: IvyeaSkillInfo[];
  workspaces: string[];
  /**
   * 当前主脑模型。**只显示，不在这里切**：agent 的模型是全局配置
   * （/v1/model/configure），不支持按轮次覆盖；做成下拉框会是个点了没反应的假开关。
   * 点它跳「系统配置」，那里才是真正切换的地方。
   */
  modelLabel: string;
  onModelClick: () => void;
  placeholder?: string;
  autoFocus?: boolean;
  /** 会话态：贴底、少留白。 */
  compact?: boolean;
  attaching?: boolean;
}) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [rows, setRows] = useState(1);

  useEffect(() => {
    if (autoFocus) taRef.current?.focus();
  }, [autoFocus]);

  // 自适应高度：最多长到 8 行，超过就内部滚动。
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, compact ? 160 : 200);
    el.style.height = next + "px";
    setRows(Math.max(1, Math.round(next / 20)));
  }, [value.text, compact]);

  const submit = () => {
    if (busy || !value.text.trim()) return;
    onSubmit();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter 发送 / Shift+Enter 换行 —— 与站内其它输入框（AI 问答、市场调研）一致。
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const pickFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f && onAttach) onAttach(f);
    if (fileRef.current) fileRef.current.value = "";
  };

  const skillOptions = [
    { value: "", label: "自动选技能" },
    ...skills.map((s) => ({ value: s.id, label: s.title || s.id, sub: s.domain })),
  ];
  const workspaceOptions = (workspaces.length ? workspaces : ["默认工作区"]).map((w) => ({
    value: w,
    label: w,
  }));

  return (
    <div className={"cc-composer" + (compact ? " compact" : "")}>
      <textarea
        ref={taRef}
        className="cc-input scroll-thin"
        value={value.text}
        placeholder={placeholder}
        rows={rows}
        onChange={(e) => onChange({ text: e.target.value })}
        onKeyDown={onKeyDown}
      />
      <div className="cc-bar">
        <button
          type="button"
          className="cc-chip cc-chip-icon"
          title="添加文件到知识库，然后就能直接问它的内容"
          onClick={() => fileRef.current?.click()}
          disabled={!onAttach || attaching}
        >
          {attaching ? <span className="spin" /> : "+"}
        </button>
        <input ref={fileRef} type="file" style={{ display: "none" }} onChange={pickFile} />

        <SheetSelect
          className="cc-chip xsel-compact"
          title="选择工作区"
          value={value.workspace}
          onChange={(v) => onChange({ workspace: v })}
          options={workspaceOptions}
          ariaLabel="工作区"
        />
        <SheetSelect
          className={"cc-chip xsel-compact" + (value.approval === "ask" ? " cc-chip-warn" : "")}
          title="审批档位"
          value={value.approval}
          onChange={(v) => onChange({ approval: v as ApprovalMode })}
          options={APPROVAL_MODES.map((m) => ({ value: m.value, label: m.label, sub: m.hint }))}
          ariaLabel="审批档位"
        />
        <SheetSelect
          className="cc-chip xsel-compact"
          title="使用技能"
          value={value.skill}
          onChange={(v) => onChange({ skill: v })}
          options={skillOptions}
          ariaLabel="技能"
        />
        <button
          type="button"
          className="cc-chip cc-chip-model"
          onClick={onModelClick}
          title="当前主脑模型 · 点击去「系统配置」切换"
        >
          <span className="cc-chip-label">{modelLabel || "模型未配置"}</span>
        </button>

        <div className="cc-bar-spacer" />
        {busy && onStop ? (
          <button type="button" className="cc-send cc-stop" onClick={onStop} title="停止本轮">■</button>
        ) : (
          <button
            type="button"
            className="cc-send"
            onClick={submit}
            disabled={busy || !value.text.trim()}
            title="发送（Enter）"
          >
            ➤
          </button>
        )}
      </div>
    </div>
  );
}
