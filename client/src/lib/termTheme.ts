/** 把 CSS 变量读成 xterm 能吃的主题对象。
 *
 *  xterm 的配色是 **JS 对象**，不是 CSS —— 它把字符画到 canvas 上，CSS 变量
 *  一个字都进不去。所以站里两个终端（ops 的实时面板、agents 的 shell）此前各自
 *  写死了一套色，换主题时它们是唯二纹丝不动的地方：门道浅色下整页是白纸，
 *  中间挖一块纯黑，非常突兀。
 *
 *  这里在**运行时**把当前主题的 CSS 变量读出来喂给 xterm，并在主题切换时重算。
 *  变量取值一律从 `<html>` 上读 —— 那是 data-theme 挂的地方，也是所有主题变量
 *  真正生效的作用域。
 */

/** 读一个 CSS 变量并解析成 `#rrggbb`。xterm 只认十六进制或 rgb()，不认 var()。 */
function readColor(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!raw) return fallback;
  if (/^#[0-9a-f]{3,8}$/i.test(raw)) return raw;
  // rgb()/rgba()/color-mix() 一律交给浏览器算：挂一个临时元素读回计算值，
  // 比在这里手写一个 CSS 颜色解析器可靠得多。
  const probe = document.createElement("span");
  probe.style.cssText = `position:absolute;visibility:hidden;color:${raw}`;
  document.body.appendChild(probe);
  const resolved = getComputedStyle(probe).color;
  probe.remove();
  const m = resolved.match(/([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/);
  if (!m) return fallback;
  const hex = (v: string) => Math.round(+v).toString(16).padStart(2, "0");
  return `#${hex(m[1])}${hex(m[2])}${hex(m[3])}`;
}

/** 带透明度的选中高亮。xterm 的 selectionBackground 支持 rgba。 */
function readRgba(name: string, alpha: number, fallback: string): string {
  const hex = readColor(name, "");
  if (!hex || hex.length < 7) return fallback;
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r},${g},${b},${alpha})`;
}

export type XtermTheme = {
  background: string;
  foreground: string;
  cursor: string;
  selectionBackground: string;
};

/** 当前主题下的终端配色。每次调用都重新读，所以主题切换后调一次即可。 */
export function xtermTheme(): XtermTheme {
  return {
    background: readColor("--term-bg", "#000000"),
    foreground: readColor("--t", "#e8e8e8"),
    cursor: readColor("--acc", "#4ade80"),
    selectionBackground: readRgba("--acc", 0.25, "rgba(74,222,128,.25)"),
  };
}

/**
 * 完整的 ANSI 16 色。
 *
 * **不能只换 background/foreground 就完事**：ANSI 那 16 色是程序自己选的
 * （红=报错、绿=通过），终端只负责给出色值。把深色终端直接放到浅底上，
 * `white` 和 `brightWhite` 会变成白底白字 —— 看不见的不是装饰，是输出。
 *
 * 所以按明暗给两套：深色用 One Dark、浅色用 One Light，都是有出处的成套配色，
 * 不是我现调的。旧 16 套主题一律走 `null`，由调用方保留它原来那份 VSCode Dark，
 * 从而零回归。
 */
export function xtermAnsi(): Record<string, string> | null {
  if (typeof document === "undefined") return null;
  if (document.documentElement.getAttribute("data-skin") !== "flat") return null;
  const dark = document.documentElement.getAttribute("data-theme") === "mendao-dark";
  return dark
    ? {
        black: "#3e444f", red: "#e06c75", green: "#98c379", yellow: "#d19a66",
        blue: "#61afef", magenta: "#c678dd", cyan: "#56b6c2", white: "#9aa0ab",
        brightBlack: "#747a85", brightRed: "#e88b93", brightGreen: "#b0d39a",
        brightYellow: "#e0b483", brightBlue: "#8cc4f3", brightMagenta: "#d79ae6",
        brightCyan: "#7fc9d2", brightWhite: "#d7dae0",
      }
    : {
        // 浅底上 white/brightWhite 必须压成灰，否则等于没输出
        black: "#111111", red: "#c2402f", green: "#50a14f", yellow: "#986801",
        blue: "#4078f2", magenta: "#a626a4", cyan: "#0184bc", white: "#9c9c96",
        brightBlack: "#6e6e68", brightRed: "#d55b48", brightGreen: "#66b765",
        brightYellow: "#b07d0a", brightBlue: "#5b8ef5", brightMagenta: "#bd42ba",
        brightCyan: "#12a0d6", brightWhite: "#6e6e68",
      };
}

/**
 * 订阅主题切换，回调里拿到新配色。返回退订函数。
 *
 * 用 ops 自己那条 `ivyea-ops:theme-changed` 事件（MainLayout.selectTheme 发的），
 * 不用 MutationObserver 盯 data-theme —— 事件是明确的契约，属性变化不是。
 */
export function onThemeChange(cb: (theme: XtermTheme) => void): () => void {
  const handler = () => cb(xtermTheme());
  window.addEventListener("ivyea-ops:theme-changed", handler);
  return () => window.removeEventListener("ivyea-ops:theme-changed", handler);
}
