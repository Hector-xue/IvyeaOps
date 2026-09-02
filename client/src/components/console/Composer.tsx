/**
 * 任务台输入器 —— 对标 MyLevis 底部那一行 chip：
 *   `+ | 默认工作区 ▾ | 默认审批 ▾ | 广告分析规划助手 ▾ | gpt-5.6-sol ▾ | ➤`
 *
 * 这些 chip 不是装饰：每一枚都直接映射到 agent serve 的 chat payload 字段
 * （workspace / plan_mode+approval / skill / model），选了就真的按那个跑 ——
 * 包括最右那枚模型芯片：它下发 payload.model，只对这条会话生效。
 */
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import Icon from "../Icon";
import SheetSelect from "../SheetSelect";
import {
  APPROVAL_MODES,
  APPROVAL_INFO,
  approvalFromWire,
  approvalPayload,
  type ApprovalMode,
} from "../../lib/approvalModes";
import type { ConsolePreset, IvyeaSkillInfo } from "../../api/ivyeaAgent";
import ModelPicker from "./ModelPicker";
import { openLightbox } from "../../lib/lightbox";

/** `@` 引用的一条：把知识库里的东西真的带进本轮，而不只是在文字里提一嘴。 */
/**
 * 一份会话附件：只给这一轮对话用的文档，**不进知识库**。
 *
 * `text` 是 agent 抽出来的正文（发送时作为 attachment 带下去），`file` 留着是为了
 * 用户临时改主意点「收进知识库」时还能把原件传上去 —— 抽完正文就丢掉原件的话，
 * 那个按钮就只能让他重新选一次文件。
 */
export type ComposerDoc = { name: string; text: string; chars: number;
                            truncated: boolean; url: string; file: File };

export type ComposerRef = { id: string; title: string; path: string };

/**
 * `/` 菜单里的一条命令。
 *
 * keywords：**中文标签之外**的可搜词。菜单是按标签模糊匹配的，而 `/model` 这类
 * 命令的标签是中文 —— 用户照着 CLI 的习惯打 `/model`，匹配不上就是一个空菜单
 * （看起来像这条命令根本不存在）。
 */
type SlashItem = { key: string; label: string; hint?: string; keywords?: string; run: () => void };

/*
 * 审批三档的定义搬到了 lib/approvalModes —— 任务台、能力市场的预设表、这里三处
 * 都要用同一套档位，各写一份必然走偏。这里只做转出口，老的引用路径不用改。
 */
export { APPROVAL_MODES, approvalPayload };
export type { ApprovalMode };

export type ComposerValue = {
  text: string;
  workspace: string;
  approval: ApprovalMode;
  skill: string;
  /** 套用的预设名。只用来显示那枚芯片，让人知道现在带着谁的人设在跑。 */
  preset?: string;
  /** 预设携带的人设，会整段并进这一轮的系统提示。 */
  system?: string;
};

export default function Composer({
  value,
  onChange,
  onSubmit,
  onFollowUp,
  onStop,
  stopping,
  onAttach,
  busy,
  queue = [],
  onQueueRemove,
  skills,
  workspaces,
  onNewWorkspace,
  references = [],
  picked = [],
  onPickedChange,
  scenes = [],
  presets = [],
  onNewTask,
  images = [],
  onImagesChange,
  docs = [],
  onDocsChange,
  onDocToKnowledge,
  modelLabel,
  modelValue = "",
  onModelChange,
  modelSwitchable = false,
  onModelSettings,
  onModelDefault,
  placeholder = "告诉 Ivyea 你想做什么，剩下的交给我……",
  autoFocus,
  compact,
  attaching,
}: {
  value: ComposerValue;
  onChange: (patch: Partial<ComposerValue>) => void;
  onSubmit: () => void;
  /**
   * 轮次**跑着的时候**又说了一句话。给了它，忙碌中的发送键就还是发送键 ——
   * 任务跑起来就闭麦是这个输入框以前最大的毛病：想补一句"顺便把 X 也改了"，
   * 只能干等几十分钟或者掐掉重说。不给则退回老行为（忙碌时不能发）。
   */
  onFollowUp?: (text: string) => void;
  onStop?: () => void;
  /** 正在请求 agent 停这一轮 —— 按钮转成等待态，防连点。 */
  stopping?: boolean;
  onAttach?: (file: File) => void;
  busy?: boolean;
  /** 已经说出去、但还没被这一轮读到的追加指令。 */
  queue?: { id: string; text: string; state: "sending" | "injected" | "queued" }[];
  onQueueRemove?: (id: string) => void;
  skills: IvyeaSkillInfo[];
  workspaces: string[];
  /** 点了下拉里的「新建工作区…」。由 Console 弹出创建流程。 */
  onNewWorkspace?: () => void;
  /** @ 可引用的知识库条目（卡片/上传件）。 */
  references?: ComposerRef[];
  /** 已选中的引用；提交时由任务台取正文带进本轮。 */
  picked?: ComposerRef[];
  onPickedChange?: (next: ComposerRef[]) => void;
  /** / 菜单里的场景（点一下填提示词）。 */
  scenes?: { label: string; prompt: string }[];
  /** 智能体预设：一下把技能+审批+工作区三样一起设好。 */
  presets?: ConsolePreset[];
  onNewTask?: () => void;
  /** 待发送的图片（data URI）。粘贴/拖入即入列，发送时由任务台读成文字带下去。 */
  images?: string[];
  onImagesChange?: (next: string[]) => void;
  /**
   * 这一轮要带下去的**会话附件**（文档）。只属于这次对话，**没有进知识库**。
   *
   * 和 `references`/`picked`（@ 引用知识库条目）是两回事：那些是库里已有的东西，
   * 这些是刚传上来、用完就没的。用户的原话是「有些文件只是会话的时候用，
   * 并不需要纳入知识库」。
   */
  docs?: ComposerDoc[];
  onDocsChange?: (next: ComposerDoc[]) => void;
  /** 点了某个附件上的「收进知识库」—— 那是显式动作，不是默认行为。 */
  onDocToKnowledge?: (index: number) => void;
  /**
   * 当前**实际生效**的主脑模型显示名（来自 /health 或本轮 start 事件）。
   *
   * 这里曾经只显示不可切：那时 agent 的模型是纯全局配置，做成下拉框会是个点了
   * 没反应的假开关。agent ≥ v1.15.4 支持按轮次指定模型后才真的可切 —— 所以
   * modelSwitchable 为假（老 agent）时仍然只跳「系统配置」，不给假开关。
   */
  modelLabel: string;
  /** 本会话选中的模型 id（`provider:model`）；"" = 跟随全局。 */
  modelValue?: string;
  onModelChange?: (id: string) => void;
  modelSwitchable?: boolean;
  onModelSettings: () => void;
  /** 把选中的模型写成全局默认。不给就不显示那个按钮。 */
  onModelDefault?: (id: string) => Promise<void>;
  placeholder?: string;
  autoFocus?: boolean;
  /** 会话态：贴底、少留白。 */
  compact?: boolean;
  attaching?: boolean;
}) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  /** `/model` 命令用它叫开模型面板 —— 计数变化即触发，不必把 open 提上来。 */
  const [modelOpen, setModelOpen] = useState(0);

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
    // 预设排在最前：它一次把技能、审批档位、工作区三样都设好，
    // 比逐个挑省事，用户多半是奔着它来的。
    for (const p of presets) {
      items.push({
        key: "preset:" + p.name,
        label: `预设 · ${p.name}`,
        hint: [p.skill, APPROVAL_INFO[approvalFromWire(p.approval)].label, p.workspace].filter(Boolean).join(" · "),
        // 预设存的是**线上语义**（none/remote/auto），composer 用的是界面档位
        // （readonly/ask/full）。两边别混，换算统一走 approvalFromWire。
        run: () => onChange({
          skill: p.skill,
          approval: approvalFromWire(p.approval),
          ...(p.workspace ? { workspace: p.workspace } : {}),
          // 有人设才挂芯片；没写人设的预设不该凭空多出一枚
          preset: p.system ? p.name : "",
          system: p.system || "",
        }),
      });
    }
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
    // `/model` —— 和 IvyeaAgent CLI 里那条命令同名同义。终端里能这么切，
    // 网页上也该能，不然同一个产品两套操作方式。
    items.push({
      key: "model",
      label: "模型 · 切换主脑",
      keywords: "model 模型 主脑 llm",
      hint: modelSwitchable ? (modelValue ? modelValue : modelLabel) : "当前 Agent 版本不支持，去系统配置切换",
      run: () => (modelSwitchable ? setModelOpen((n) => n + 1) : onModelSettings()),
    });
    if (onNewTask) items.push({ key: "new", label: "新建任务", run: onNewTask });
    return items;
  }, [presets, skills, scenes, onNewTask, onChange,
      modelSwitchable, modelValue, modelLabel, onModelSettings]);

  const matches = useMemo(() => {
    if (!menu) return [] as { key: string; label: string; hint?: string; run: () => void }[];
    const q = menu.query;
    const pool = menu.kind === "slash"
      ? slashItems
      : references.map((r) => ({
          key: "ref:" + r.id, label: r.title, hint: r.path.split("/").pop(),
          run: () => onPickedChange?.([...picked.filter((p) => p.id !== r.id), r]),
        }));
    return pool
      .filter((it) => !q || (it.label + " " + ((it as SlashItem).keywords || "")).toLowerCase().includes(q))
      .slice(0, 8);
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

  /**
   * 自适应高度：最多长到 8 行，超过就内部滚动。
   *
   * 两个坑，都踩过：
   * ① **量之前必须先把 min-height 摘掉。** scrollHeight 取的是 max(内容, 盒子内高)，
   *    首屏那只输入框有 min-height:72px，量出来的永远是 72 起步 —— 而不是内容的高度。
   * ② **不能再由这个高度反推 rows。** 上一版还顺手 `setRows(next/20)`：rows 抬高了
   *    盒子的固有高度 → 下一次量到更大的 scrollHeight → rows 又变大……每敲一个字
   *    输入框就长一截（实测 106→132→185px，最后顶到 200 上限）。高度这里已经显式
   *    设了，rows 恒为 1 就够。
   */
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    const keepMin = el.style.minHeight;
    el.style.minHeight = "0px";
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, compact ? 160 : 200);
    el.style.height = next + "px";
    el.style.minHeight = keepMin;   // 交还给 CSS：真正的下限还是它说了算
  }, [value.text, compact]);

  const canFollowUp = Boolean(busy && onFollowUp);

  const submit = () => {
    const text = value.text.trim();
    if (!text) return;
    if (busy) {
      // 跑着的时候按发送 = 追加指令。能真插进这一轮就插（agent 在两个工具步之间
      // 读走），插不进去就排到本轮结束后发 —— 由 Console 判定并在队列条上说清楚。
      if (!onFollowUp) return;
      onFollowUp(text);
      onChange({ text: "" });
      return;
    }
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

  /**
   * 「+」按钮选中的文件。**图片和文档走的是两条完全不同的路**：
   *   · 图片 → 和粘贴/拖拽同一条视觉链路（读成 data URI 进本轮消息，模型真的看得见）
   *   · 其余 → 知识库（落盘 + 索引，之后可以问它的内容）
   *
   * 此前这里不分流，一律塞知识库，于是"传张截图问这是什么"变成了：模型只拿到一个
   * 文件名，然后拿着这个名字在磁盘上翻半天、翻不到，最后老实说"我看不见这张图"
   * （真实会话 20260818-175009 就这么烧掉了 34 步）。选图和粘贴图是同一个意图，
   * 不该因为走了哪个入口而拿到两种结果。
   */
  const pickFile = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    const pics = files.filter((f) => f.type.startsWith("image/"));
    const docs = files.filter((f) => !f.type.startsWith("image/"));
    if (pics.length) addImageFiles(pics);
    if (onAttach) for (const f of docs) onAttach(f);
    if (fileRef.current) fileRef.current.value = "";
  };

  // **新建入口放进这个下拉里**。此前它只在左栏的「工作区 +」上 —— 而用户想切
  // 工作区时点的是这里，点开却只有"默认工作区"一个选项、也没有别的出口，
  // 看起来就是个坏掉的控件。要在用户产生意图的地方给出路，而不是让他去别处找。
  const NEW_WS = "__new__";
  const workspaceOptions = [
    ...(workspaces.length ? workspaces : ["默认工作区"]).map((w) => ({
      value: w, label: w,
      sub: w === "默认工作区" ? "不绑目录，Agent 只在会话里工作" : undefined,
    })),
    { value: NEW_WS, label: "＋ 新建工作区…", sub: "绑一个目录，Agent 的文件操作就在那里面" },
  ];

  // 档位配色：只读安静、审批放行提醒、完全放行警示。**危险的那档必须一眼看出来**，
  // 否则用户会在没意识到的情况下让 Agent 直接改线上数据。
  const approvalTone = APPROVAL_INFO[value.approval]?.tone || "calm";

  return (
    <div className={"cc-composer" + (compact ? " compact" : "")}>
      {menu && matches.length > 0 && (
        <div className="cc-menu">
          <div className="cc-menu-head">
            {menu.kind === "slash" ? "/ 命令 —— 预设 · 技能 · 场景 · 审批档位" : "@ 引用知识库内容"}
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
              {/* 缩略图只有 52px，看不清自己选了什么 —— 点一下看原图。 */}
              <img src={src} alt={`图片 ${i + 1}`} title="点击查看原图"
                   onClick={() => openLightbox(
                     images.map((u, j) => ({ src: u, alt: `图片 ${j + 1}` })), i)} />
              <button type="button" title="移除"
                      onClick={() => onImagesChange?.(images.filter((_, j) => j !== i))}>✕</button>
            </span>
          ))}
          <span className="cc-img-note">看图会先由视觉模型读成文字；说要改图就把原图直接交给作图链路</span>
        </div>
      )}

      {/*
        * 会话附件。**这一栏存在的全部意义是让"没进知识库"这件事看得见** ——
        * 此前上传任何文档都直接入库并重建索引，界面上只有一句"已加进知识库"，
        * 用户想"就这次问问"根本没有出口。所以这里既说清它的临时性，也把
        * 「收进知识库」留成一个**显式按钮**，而不是默认行为。
        */}
      {docs.length > 0 && (
        <div className="cc-docs">
          {docs.map((d, i) => (
            <span className="cc-doc" key={i} title={`${d.name} · ${d.chars} 字${d.truncated ? "（已截断）" : ""}`}>
              <Icon name="file" size={13} />
              <em className="cc-doc-name">{d.name}</em>
              <span className="cc-doc-size">{d.chars} 字{d.truncated ? "·截断" : ""}</span>
              {onDocToKnowledge && (
                <button type="button" className="cc-doc-keep" title="这份要长期留着，收进知识库"
                        onClick={() => onDocToKnowledge(i)}>收进知识库</button>
              )}
              <button type="button" title="不带这份了"
                      onClick={() => onDocsChange?.(docs.filter((_, j) => j !== i))}>✕</button>
            </span>
          ))}
          <span className="cc-img-note">只用于这次对话，没有进知识库；下次对话不会自动还在</span>
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

      {queue.length > 0 && (
        /*
         * 说出去的话去哪了，必须看得见。三种状态分得很清：正在送、已经插进这一轮
         * （模型下一步就看得见）、排到本轮结束后发。没有这条，用户在跑着的时候
         * 补一句就成了往井里扔石头 —— 不知道进没进去、什么时候起作用。
         */
        <div className="cc-queue">
          {queue.map((q) => (
            <span key={q.id} className={"cc-queue-item is-" + q.state} title={q.text}>
              <i className="cc-queue-dot" />
              <em>{q.state === "injected" ? "已插入本轮"
                : q.state === "queued" ? "本轮结束后发" : "正在送…"}</em>
              <span className="cc-queue-text">{q.text}</span>
              {q.state === "queued" && onQueueRemove && (
                <button type="button" title="不发了" onClick={() => onQueueRemove(q.id)}>✕</button>
              )}
            </span>
          ))}
        </div>
      )}

      <textarea
        ref={taRef}
        className="cc-input scroll-thin"
        value={value.text}
        placeholder={canFollowUp ? "正在跑…… 想补一句就直接说，会插进这一轮" : placeholder}
        rows={1}
        onChange={onInput}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        onDrop={onDrop}
        onDragOver={(e) => e.preventDefault()}
        onBlur={() => window.setTimeout(() => setMenu(null), 120)}
      />
      {/*
        * 底栏分左右两组：左边是"这一轮怎么跑"（工作区 / 审批档位 / 人设），
        * 右边是"用谁跑、发不发"（模型 / 发送）。此前它们挤在一条 flex 里靠一个
        * spacer 分开，芯片一多就换行、发送键被顶到第二行 —— 对标图里那条底栏
        * 永远是一行，因为两组各自成块。
        */}
      <div className="cc-bar">
        <div className="cc-bar-left">
          <button
            type="button"
            className="cc-chip cc-chip-icon"
            title="选图片就直接给我看（和粘贴一样），选文档就带进这轮对话；想长期留着再点附件上的「收进知识库」"
            onClick={() => fileRef.current?.click()}
            disabled={!onAttach || attaching}
          >
            {attaching ? <span className="spin" /> : <Icon name="attach" size={15} />}
          </button>
          <input ref={fileRef} type="file" multiple style={{ display: "none" }} onChange={pickFile} />

          <SheetSelect
            className="cc-chip xsel-compact"
            title="工作区 —— 决定 Agent 在哪个目录里读写文件"
            value={value.workspace}
            onChange={(v) => { if (v === NEW_WS) onNewWorkspace?.(); else onChange({ workspace: v }); }}
            options={workspaceOptions}
            ariaLabel="工作区"
            leading={<Icon name="file" size={14} />}
            dropdownClassName="cc-sheet"
          />
          <SheetSelect
            className={"cc-chip xsel-compact cc-chip-mode tone-" + approvalTone}
            title={"审批档位 —— " + APPROVAL_INFO[value.approval].hint}
            value={value.approval}
            onChange={(v) => onChange({ approval: v as ApprovalMode })}
            options={APPROVAL_MODES.map((m) => ({ value: m.value, label: m.label, sub: m.hint }))}
            ariaLabel="审批档位"
            leading={<Icon name={"mode-" + value.approval} size={14} />}
            dropdownClassName="cc-sheet cc-sheet-mode"
          />
          {value.preset && (
            /*
             * 人设**必须可见**。一段看不见的系统提示在悄悄改变回答的口吻和判断标准，
             * 用户只会觉得"今天的 Agent 怪怪的"却不知道为什么。所以套用预设后摆一枚
             * 芯片说明现在带着谁，并且能一键摘掉。
             */
            <span className="cc-chip cc-chip-preset" title={value.system || value.preset}>
              <i>◉</i>
              {value.preset}
              <button
                type="button"
                className="cc-chip-x"
                title="取消套用这个预设的人设"
                onClick={() => onChange({ preset: "", system: "" })}
              >✕</button>
            </span>
          )}
        </div>

        <div className="cc-bar-right">
          <ModelPicker
            currentLabel={modelLabel}
            value={modelValue}
            onChange={(id) => onModelChange?.(id)}
            switchable={modelSwitchable}
            onOpenSettings={onModelSettings}
            onSetDefault={onModelDefault}
            openSignal={modelOpen}
          />
          {busy && onStop && (
            /*
             * 停止**不再是主键**（跑着的时候主键是"追加发送"），但它现在是**真的
             * 停止**：agent 会在下一个模型事件/工具步边界收摊，不再烧 token。
             * 已经跑出来的内容照常留下。
             */
            <button type="button" className={"cc-stop-secondary" + (stopping ? " is-stopping" : "")}
                    onClick={onStop} disabled={stopping}
                    title={stopping ? "正在停…" : "停止这一轮（真的中止，不再烧 token；已经跑出来的内容会留下）"}>
              {stopping ? <span className="spin" /> : <Icon name="stop" size={13} strokeWidth={3} />}
            </button>
          )}
          <button
            type="button"
            className={"cc-send" + (canFollowUp ? " cc-send-followup" : "")}
            onClick={submit}
            disabled={(busy && !canFollowUp) || !value.text.trim()}
            title={canFollowUp ? "追加给正在跑的这一轮（Enter）" : "发送（Enter）"}
          >
            <Icon name="send" size={17} strokeWidth={2.4} />
          </button>
        </div>
      </div>
    </div>
  );
}
