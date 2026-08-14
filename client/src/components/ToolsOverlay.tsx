import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { lockBodyScroll } from "../lib/scrollLock";
import Icon from "./Icon";
import { boardPath, toolSections, type BoardEntry, type NavSection } from "../lib/navRegistry";
import { getPinnedBoards, getRecentBoards, toggleBoardPin } from "../lib/boardPrefs";

/**
 * 「全部工具」浮层 —— 18 个板块的目录。
 *
 * ── 为什么它不该待在侧栏里 ───────────────────────────────────────────────
 * 改造前这些板块是侧栏底部一个折叠组，展开占 774px：1440×900 下会话列表被整个
 * 挤到折叠线以下，而**会话才是这个外壳的主角**。折叠起来又变成一个看不出装了
 * 什么的按钮。问题的实质是：一个 18 项的目录不适合放进一条 196px 宽的竖栏，
 * 无论折叠还是展开。
 *
 * ── 为什么也不该待在设置里 ───────────────────────────────────────────────
 * 设置回答"怎么改配置"，工具回答"去哪儿"。两者混在一起的结果是两边都找不到。
 *
 * 所以给它一个自己的地方：一个带搜索的浮层，⌘K 直接唤起。侧栏只留一个入口，
 * 常用的钉出来（见 lib/boardPrefs）。
 *
 * ── 为什么不复用 agents 子树那个 CommandPalette ───────────────────────────
 * 那个在懒加载 chunk 里、带自己那套 Tailwind 作用域和 shadcn 变量。跨进主树用
 * 会把两套隔离规则搅在一起，而隔离正是 agents 子树能安稳待着的唯一原因。
 */

export type ToolsOverlayProps = {
  open: boolean;
  onClose: () => void;
  visibility: { isAdmin: boolean; permissions: string[] };
};

const RECENT_TITLE = "最近";
const ALL_TITLE = "全部";

export default function ToolsOverlay({ open, onClose, visibility }: ToolsOverlayProps) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [cat, setCat] = useState(ALL_TITLE);
  const [cursor, setCursor] = useState(0);
  // pin 状态存在 localStorage 里，改完要重渲染 —— 用一个计数器当触发器，
  // 比把整张列表复制进 state 简单，也不会和 localStorage 失去同步。
  const [pinTick, setPinTick] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const sections: NavSection[] = useMemo(() => toolSections(visibility), [visibility]);
  const allBoards = useMemo(() => sections.flatMap((s) => s.items), [sections]);

  // 打开时重置到干净状态并聚焦搜索框 —— 上一次搜过的词留着是纯干扰。
  useEffect(() => {
    if (!open) return;
    setQ("");
    setCat(ALL_TITLE);
    setCursor(0);
    setPinTick((n) => n + 1);
    const t = window.setTimeout(() => inputRef.current?.focus(), 20);
    const release = lockBodyScroll();
    return () => { window.clearTimeout(t); release(); };
  }, [open]);

  const recent = useMemo(() => {
    if (!open) return [];
    const order = getRecentBoards();
    return order
      .map((to) => allBoards.find((b) => b.to === to))
      .filter((b): b is BoardEntry => !!b);
    // pinTick 不参与：最近列表由路由变化写入，浮层每次打开重算即可
  }, [open, allBoards]);

  // 一次读、一个 Set —— 逐张卡片调 isBoardPinned 会在每次渲染里跑 18 次
  // localStorage 读 + JSON.parse。
  const pinnedSet = useMemo(() => new Set(getPinnedBoards()), [pinTick]);
  const pinned = useMemo(
    () => getPinnedBoards()
      .map((to) => allBoards.find((b) => b.to === to))
      .filter((b): b is BoardEntry => !!b),
    [allBoards, pinTick],
  );

  /** 当前该显示哪些板块：搜索优先于分类。 */
  const shown: NavSection[] = useMemo(() => {
    const kw = q.trim().toLowerCase();
    if (kw) {
      const hit = allBoards.filter(
        (b) => b.label.toLowerCase().includes(kw) || b.to.toLowerCase().includes(kw),
      );
      return hit.length ? [{ title: `匹配 ${hit.length} 个`, items: hit }] : [];
    }
    if (cat === RECENT_TITLE) return recent.length ? [{ title: RECENT_TITLE, items: recent }] : [];
    if (cat === ALL_TITLE) {
      return pinned.length ? [{ title: "已钉住", items: pinned }, ...sections] : sections;
    }
    return sections.filter((s) => s.title === cat);
  }, [q, cat, sections, allBoards, recent, pinned]);

  // 键盘导航走的是**拍平后的可见项**，和眼睛看到的顺序一致。
  const flat = useMemo(() => shown.flatMap((s) => s.items), [shown]);
  useEffect(() => { setCursor(0); }, [q, cat]);

  const go = (b: BoardEntry) => {
    onClose();
    navigate(b.to);
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, flat.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
      else if (e.key === "Enter") {
        const b = flat[cursor];
        if (b) { e.preventDefault(); go(b); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, flat, cursor]);

  // 键盘移动时把选中项滚进视野，否则往下按几次人就跟丢了。
  useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector<HTMLElement>(".tv-card.cursor")
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor, open]);

  if (!open) return null;

  const cats = [ALL_TITLE, ...(recent.length ? [RECENT_TITLE] : []), ...sections.map((s) => s.title)];
  let idx = -1;   // 拍平序号，用来对上 cursor

  return (
    <div className="tv-backdrop" onMouseDown={onClose} role="dialog" aria-modal="true" aria-label="全部工具">
      <div className="tv-panel" onMouseDown={(e) => e.stopPropagation()}>
        <div className="tv-head">
          <i className="tv-search-ic"><Icon name="search" size={16} /></i>
          <input
            ref={inputRef}
            className="tv-search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索板块…"
            aria-label="搜索板块"
          />
          <kbd className="tv-kbd">Esc</kbd>
          <button className="tv-close" onClick={onClose} aria-label="关闭"><Icon name="close" size={15} /></button>
        </div>

        <div className="tv-body">
          {/* 搜索时分类栏没有意义 —— 搜索本来就是跨分类的 */}
          {!q.trim() && (
            <div className="tv-cats scroll-thin">
              {cats.map((c) => (
                <button
                  key={c}
                  className={"tv-cat" + (c === cat ? " active" : "")}
                  onClick={() => setCat(c)}
                >
                  {c}
                </button>
              ))}
            </div>
          )}

          <div className="tv-list scroll-thin" ref={listRef}>
            {shown.length === 0 && (
              <div className="tv-empty">没有匹配的板块。换个词试试，或按 Esc 退出。</div>
            )}
            {shown.map((sec) => (
              <div key={sec.title} className="tv-sec">
                <div className="tv-sec-title">{sec.title}</div>
                <div className="tv-grid">
                  {sec.items.map((b) => {
                    idx += 1;
                    const mine = idx;
                    const isPinned = pinnedSet.has(b.to);
                    return (
                      <button
                        key={sec.title + b.to}
                        className={"tv-card" + (mine === cursor ? " cursor" : "")}
                        onClick={() => go(b)}
                        onMouseEnter={() => setCursor(mine)}
                        title={boardPath(b)}
                      >
                        <i className="tv-card-ic"><Icon name={b.icon} size={17} /></i>
                        <span className="tv-card-label">{b.label}</span>
                        <span
                          className={"tv-pin" + (isPinned ? " on" : "")}
                          role="button"
                          tabIndex={-1}
                          aria-label={isPinned ? "取消钉住" : "钉到侧栏"}
                          title={isPinned ? "取消钉住" : "钉到侧栏"}
                          onClick={(e) => {
                            // 别让它冒泡成"打开这个板块"
                            e.stopPropagation();
                            toggleBoardPin(b.to);
                            setPinTick((n) => n + 1);
                          }}
                        >
                          <Icon name="pin" size={13} />
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="tv-foot">
          <span><kbd className="tv-kbd">↑↓</kbd> 选择</span>
          <span><kbd className="tv-kbd">↵</kbd> 打开</span>
          <span><Icon name="pin" size={12} /> 钉到侧栏</span>
          <span className="tv-foot-spacer" />
          <span>共 {allBoards.length} 个板块</span>
        </div>
      </div>
    </div>
  );
}
