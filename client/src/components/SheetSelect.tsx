import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { FLAG_URL } from "../lib/marketplaces";

export interface SheetOption {
  value: string;
  /** 显示文本，缺省回退到 value */
  label?: ReactNode;
  /** 副标题（如国家中文名） */
  sub?: string;
  /** 国旗 / 图标 URL；flags 为真时按 value 自动推导 */
  flag?: string;
  /** 不可选项（置灰、点击无效） */
  disabled?: boolean;
}

type RawOption = string | SheetOption;

export interface SheetSelectProps {
  value: string;
  onChange: (value: string) => void;
  options: RawOption[];
  /** 国家/站点模式：自动取国旗 + 抽屉用 2 列国旗网格 */
  flags?: boolean;
  /** 抽屉标题 */
  title?: string;
  /** 当前无匹配项时触发按钮显示的占位文本 */
  placeholder?: string;
  disabled?: boolean;
  /** 透传到触发器的类名，继承各处输入框样式 */
  className?: string;
  /** 透传到触发器的内联样式，保留 flex/宽度布局 */
  style?: CSSProperties;
  ariaLabel?: string;
  /** 触发器上、标签之前的图标。任务台的芯片靠它认档位，不是装饰。 */
  leading?: ReactNode;
  /**
   * 透传到浮层/抽屉的类名。
   * **必须能分场景**：站点选择器的 sub 是国家名（"US 美国"，同一行才对），
   * 任务台档位的 sub 是一整句解释（必须换行、否则被裁成"绝不改…"）。
   * 一套样式满足不了两边，所以让调用方指定。
   */
  dropdownClassName?: string;
}

function normalize(opt: RawOption): SheetOption {
  return typeof opt === "string" ? { value: opt, label: opt } : opt;
}

/**
 * 原生 <select> 的视觉升级替代：
 * 桌面端锚定浮层下拉，手机端底部抽屉（带选中高亮）。
 * 复刻 Market 站点选择器样式，通过 CSS 类在两端切换，无需 JS 媒体查询。
 */
export default function SheetSelect({
  value,
  onChange,
  options,
  flags = false,
  title = "请选择",
  placeholder = "请选择",
  disabled = false,
  className,
  style,
  ariaLabel,
  leading,
  dropdownClassName,
}: SheetSelectProps) {
  const [open, setOpen] = useState(false);
  const [ddStyle, setDdStyle] = useState<CSSProperties>({});
  const wrapRef = useRef<HTMLDivElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const ddRef = useRef<HTMLDivElement>(null);

  const items = options.map(normalize);
  const current = items.find((o) => o.value === value);
  const flagOf = (o: SheetOption) =>
    o.flag || (flags ? FLAG_URL(o.value) : undefined);

  // 桌面端：测量触发器位置，定位 fixed 浮层；空间不足时向上翻转
  const computePos = useCallback(() => {
    const el = wrapRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const vh = window.innerHeight;
    const spaceBelow = vh - r.bottom;
    const spaceAbove = r.top;
    const maxH = 320;
    const openUp = spaceBelow < Math.min(maxH, 240) && spaceAbove > spaceBelow;
    const estW = Math.min(280, Math.max(r.width, 160));
    const left = Math.max(8, Math.min(Math.round(r.left), window.innerWidth - estW - 8));
    const s: CSSProperties = {
      position: "fixed",
      left,
      minWidth: Math.round(r.width),
      maxHeight: Math.max(120, Math.round((openUp ? spaceAbove : spaceBelow) - 8)),
    };
    if (openUp) { s.bottom = Math.round(vh - r.top + 4); s.top = "auto"; }
    else { s.top = Math.round(r.bottom + 4); s.bottom = "auto"; }
    setDdStyle(s);
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    computePos();
    const onScroll = () => computePos();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open, computePos]);

  // 外点击 / ESC 关闭
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      const inWrap = wrapRef.current?.contains(t);
      const inSheet = sheetRef.current?.contains(t);
      const inDd = ddRef.current?.contains(t);
      if (!inWrap && !inSheet && !inDd) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (v: string) => {
    onChange(v);
    setOpen(false);
  };

  const curFlag = current ? flagOf(current) : undefined;

  return (
    <div
      ref={wrapRef}
      className={"xsel-wrap" + (className ? " " + className : "")}
      style={style}
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-disabled={disabled || undefined}
      aria-label={ariaLabel}
      onClick={(e) => {
        if (disabled) return;
        e.stopPropagation();
        setOpen((o) => !o);
      }}
      onKeyDown={(e) => {
        if (disabled) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setOpen((o) => !o);
        }
      }}
    >
      {leading && <span className="xsel-leading">{leading}</span>}
      {curFlag && (
        <span className="xsel-flag">
          <img src={curFlag} alt="" />
        </span>
      )}
      <span className="xsel-label">
        {current ? current.label ?? current.value : placeholder}
      </span>
      <span className="xsel-caret">{open ? "▴" : "▾"}</span>

      {open && (
        <>
          {/* 桌面端浮层（portal 到 body + fixed 定位，避免被 overflow 祖先裁剪 / 越界看不到） */}
          {createPortal(
            <div
              ref={ddRef}
              className={"xsel-dropdown hide-mobile-picker"
                + (dropdownClassName ? " " + dropdownClassName : "")}
              role="listbox"
              style={ddStyle}
              onMouseDown={(e) => e.stopPropagation()}
            >
              {items.map((o) => {
                const f = flagOf(o);
                return (
                  <button
                    key={o.value}
                    type="button"
                    className={"xsel-option" + (o.value === value ? " active" : "")}
                    role="option"
                    aria-selected={o.value === value}
                    disabled={o.disabled}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!o.disabled) pick(o.value);
                    }}
                  >
                    {f && <img className="xsel-option-flag" src={f} alt="" />}
                    <span className="xsel-option-label">{o.label ?? o.value}</span>
                    {o.sub && <span className="xsel-option-sub">{o.sub}</span>}
                    <span className="xsel-option-check" aria-hidden>✓</span>
                  </button>
                );
              })}
            </div>,
            document.body,
          )}

          {/* 手机端底部抽屉（portal 到 body，避免被 overflow/transform 祖先裁剪或错位） */}
          {createPortal(
          <div className={"show-mobile-picker" + (dropdownClassName ? " " + dropdownClassName : "")}
               ref={sheetRef}>
            <div
              className="xsel-sheet-backdrop"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
              }}
            />
            <div className="xsel-sheet" onClick={(e) => e.stopPropagation()}>
              <div className="xsel-sheet-handle" />
              <div className="xsel-sheet-title">{title}</div>
              <div className={flags ? "xsel-sheet-grid" : "xsel-sheet-list"}>
                {items.map((o) => {
                  const f = flagOf(o);
                  const active = o.value === value;
                  return flags ? (
                    <button
                      key={o.value}
                      type="button"
                      className={"xsel-sheet-cell" + (active ? " active" : "")}
                      disabled={o.disabled}
                      onClick={() => !o.disabled && pick(o.value)}
                    >
                      {f && <img className="xsel-sheet-cell-flag" src={f} alt="" />}
                      <span className="xsel-sheet-cell-code">{o.label ?? o.value}</span>
                      {o.sub && <span className="xsel-sheet-cell-name">{o.sub}</span>}
                    </button>
                  ) : (
                    <button
                      key={o.value}
                      type="button"
                      className={"xsel-sheet-row" + (active ? " active" : "")}
                      disabled={o.disabled}
                      onClick={() => !o.disabled && pick(o.value)}
                    >
                      <span className="xsel-sheet-row-label">{o.label ?? o.value}</span>
                      {o.sub && <span className="xsel-sheet-row-sub">{o.sub}</span>}
                      {active && <span className="xsel-sheet-row-check">✓</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>,
          document.body,
          )}
        </>
      )}
    </div>
  );
}
