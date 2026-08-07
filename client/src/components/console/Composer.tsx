/**
 * 任务台输入器 —— 对标 MyLevis 底部那一行 chip：
 *   `+ | 默认工作区 ▾ | 默认审批 ▾ | 广告分析规划助手 ▾ | gpt-5.6-sol ▾ | ➤`
 *
 * 这些 chip 不是装饰：每一枚都直接映射到 agent serve 的 chat payload 字段
 * （workspace / plan_mode+approval / skill / 模型），选了就真的按那个跑。
 */
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import SheetSelect from "../SheetSelect";
import type { IvyeaSkillInfo } from "../../api/ivyeaAgent";

/** `@` 引用的一条：把知识库里的东西真的带进本轮，而不只是在文字里提一嘴。 */
export type ComposerRef = { id: string; title: string; path: string };

/** `/` 菜单里的一条命令。 */
type SlashItem = { key: string; label: string; hint?: string; run: () => void };

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
  references = [],
  picked = [],
  onPickedChange,
  scenes = [],
  onNewTask,
  images = [],
  onImagesChange,
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
  /** @ 可引用的知识库条目（卡片/上传件）。 */
  references?: ComposerRef[];
  /** 已选中的引用；提交时由任务台取正文带进本轮。 */
  picked?: ComposerRef[];
  onPickedChange?: (next: ComposerRef[]) => void;
  /** / 菜单里的场景（点一下填提示词）。 */
  scenes?: { label: string; prompt: string }[];
  onNewTask?: () => void;
  /** 待发送的图片（data URI）。粘贴/拖入即入列，发送时由任务台读成文字带下去。 */
  images?: string[];
  onImagesChange?: (next: string[]) => void;
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

  // ── `/` 命令与 `@` 引用 ────────────────────────────────────────────────────
  // 触发规则：光标前最后一个 token 以 / 或 @ 开头，且它处在行首或空白之后。
  // 这样正常打字里的 a@b、路径里的 / 都不会误触发。
  const [menu, setMenu] = useState<{ kind: "slash" | "at"; query: string; from: number } | null>(null);
  const [cursor, setCursor] = useState(0);

  const detectMenu = (text: string, caret: number) => {
    const before = text.slice(0, caret);
    const m = before.match(/(^|\s)([/@])([^\s]*)$/);
    if (!m) return null;
    return {
      kind: (m[2] === "/" ? "slash" : "at") as "slash" | "at",
      query: m[3].toLowerCase(),
      from: caret - m[3].length - 1,
    };
  };

  const slashItems: SlashItem[] = useMemo(() => {
    const items: SlashItem[] = [];
    for (const s of skills) {
      items.push({
        key: "skill:" + s.id, label: `技能 · ${s.title || s.id}`, hint: s.domain,
        run: () => onChange({ skill: s.id }),
      });
    }
    for (const sc of scenes) {
      items.push({
        key: "scene:" + sc.label, label: `场景 · ${sc.label}`,
        run: () => onChange({ text: sc.prompt }),
      });
    }
    for (const m of APPROVAL_MODES) {
      items.push({
        key: "mode:" + m.value, label: `审批 · ${m.label}`, hint: m.hint,
        run: () => onChange({ approval: m.value }),
      });
    }
    if (onNewTask) items.push({ key: "new", label: "新建任务", run: onNewTask });
    return items;
  }, [skills, scenes, onNewTask, onChange]);

  const matches = useMemo(() => {
    if (!menu) return [] as { key: string; label: string; hint?: string; run: () => void }[];
    const q = menu.query;
    const pool = menu.kind === "slash"
      ? slashItems
      : references.map((r) => ({
          key: "ref:" + r.id, label: r.title, hint: r.path.split("/").pop(),
          run: () => onPickedChange?.([...picked.filter((p) => p.id !== r.id), r]),
        }));
    return pool.filter((it) => !q || it.label.toLowerCase().includes(q)).slice(0, 8);
  }, [menu, slashItems, references, picked, onPickedChange]);

  const [active, setActive] = useState(0);
  useEffect(() => { setActive(0); }, [menu?.kind, menu?.query]);

  /** 选中一项：执行它，并把输入框里那段 `/xxx`（或 `@xxx`）替换掉。 */
  const choose = (idx: number) => {
    const it = matches[idx];
    if (!it || !menu) return;
    it.run();
    const text = value.text;
    const after = text.slice(cursor);
    const head = text.slice(0, menu.from);
    // 场景命令会自己重写整段文本，这时不要再拼接残留
    if (!it.key.startsWith("scene:")) onChange({ text: head + after });
    setMenu(null);
  };

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
    // 正在打 / 或 @ 时，回车一律归菜单 —— **哪怕一个都没匹配上**。
    // 否则输入 `/预算` 这种没命中的查询，回车会把这行原文当消息发出去
    // （实测踩到）。没匹配时回车只是收起菜单，让人接着改。
    if (menu) {
      if (e.key === "Escape") { e.preventDefault(); setMenu(null); return; }
      if (matches.length) {
        if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => (i + 1) % matches.length); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => (i - 1 + matches.length) % matches.length); return; }
        if ((e.key === "Enter" || e.key === "Tab") && !e.nativeEvent.isComposing) {
          e.preventDefault(); choose(active); return;
        }
      } else if (e.key === "Enter" && !e.nativeEvent.isComposing) {
        e.preventDefault(); setMenu(null); return;
      }
    }
    // Enter 发送 / Shift+Enter 换行 —— 与站内其它输入框（AI 问答、市场调研）一致。
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const onInput = (e: ChangeEvent<HTMLTextAreaElement>) => {
    const caret = e.target.selectionStart ?? e.target.value.length;
    setCursor(caret);
    setMenu(detectMenu(e.target.value, caret));
    onChange({ text: e.target.value });
  };

  const MAX_IMAGES = 4;

  const addImageFiles = (files: File[]) => {
    if (!onImagesChange) return;
    const pics = files.filter((f) => f.type.startsWith("image/"));
    if (!pics.length) return;
    const room = MAX_IMAGES - images.length;
    Promise.all(pics.slice(0, Math.max(0, room)).map((f) => new Promise<string>((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(String(r.result || ""));
      r.onerror = rej;
      r.readAsDataURL(f);
    }))).then((uris) => onImagesChange([...images, ...uris.filter(Boolean)]))
      .catch(() => void 0);
  };

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (files.some((f) => f.type.startsWith("image/"))) {
      e.preventDefault();          // 别让图片同时以文件名形式插进文本
      addImageFiles(files);
    }
  };

  const onDrop = (e: React.DragEvent) => {
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.some((f) => f.type.startsWith("image/"))) {
      e.preventDefault();
      addImageFiles(files);
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
      {menu && matches.length > 0 && (
        <div className="cc-menu">
          <div className="cc-menu-head">
            {menu.kind === "slash" ? "/ 命令 —— 技能 · 场景 · 审批档位" : "@ 引用知识库内容"}
          </div>
          {matches.map((it, i) => (
            <button
              key={it.key}
              type="button"
              className={"cc-menu-item" + (i === active ? " active" : "")}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => { e.preventDefault(); choose(i); }}
            >
              <span className="cc-menu-label">{it.label}</span>
              {it.hint && <span className="cc-menu-hint">{it.hint}</span>}
            </button>
          ))}
        </div>
      )}

      {images.length > 0 && (
        <div className="cc-imgs">
          {images.map((src, i) => (
            <span className="cc-img" key={i}>
              <img src={src} alt={`图片 ${i + 1}`} />
              <button type="button" title="移除"
                      onClick={() => onImagesChange?.(images.filter((_, j) => j !== i))}>✕</button>
            </span>
          ))}
          <span className="cc-img-note">图片会先被视觉模型读成文字再交给 Agent</span>
        </div>
      )}

      {picked.length > 0 && (
        <div className="cc-refs">
          {picked.map((r) => (
            <span className="cc-ref" key={r.id} title={r.path}>
              <i>@</i>{r.title}
              <button type="button" onClick={() => onPickedChange?.(picked.filter((p) => p.id !== r.id))}>✕</button>
            </span>
          ))}
        </div>
      )}

      <textarea
        ref={taRef}
        className="cc-input scroll-thin"
        value={value.text}
        placeholder={placeholder}
        rows={rows}
        onChange={onInput}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        onDrop={onDrop}
        onDragOver={(e) => e.preventDefault()}
        onBlur={() => window.setTimeout(() => setMenu(null), 120)}
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
