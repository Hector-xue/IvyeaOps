/** 主题注册表 —— 全站唯一一处。
 *
 *  在这个文件出现之前，同一份主题清单**手抄了三份**：
 *    · `styles/workbench.css`      16 个 `[data-theme=x]` 变量块（真正生效的那份）
 *    · `layouts/MainLayout.tsx`    THEMES + LABELS + ICONS + NAMES + ACCENTS 五张表
 *    · `agents/utils/ivyeaOpsTheme.ts`  RAW：16 套调色板的 hex 副本，给 agents 子树用
 *
 *  三份必然漂，而且已经漂了：deep-space / smoke-gold / catppuccin / hermes
 *  这四套的强调色，CSS 里和 TS 里是两个值（hermes 甚至一个是橙 #e8a84a、
 *  一个是绿 #34d399）——主题选择器上的小圆点和点进去之后的界面对不上。
 *  另外 `main.tsx` 里还有第四份：一个只列了 6 项的 VALID_THEMES，导致后 10 套
 *  主题每次刷新都会先闪一下暗夜绿再被 useEffect 纠正。
 *
 *  现在：**CSS 里的变量块是视觉真相，这个文件是元数据真相**（名字、图标、
 *  明暗、选择器上的圆点色）。两者靠 `server/tests/test_theme_registry.py`
 *  钉死——加主题只改这两处，对不上就红。
 */

export type ThemeMode = "light" | "dark";

export type ThemeDef = {
  /** `<html data-theme>` 的值，也是 localStorage 里存的值 */
  id: string;
  /** 中文名，用在选择器和设置页 */
  name: string;
  /** 字符图标。用字符不用 SVG——整套界面的语汇就是等宽字符与线条 */
  icon: string;
  /** 选择器上那颗圆点的颜色。**必须等于 CSS 里该主题的 `--acc`** */
  accent: string;
  /**
   * 明暗。agents 子树靠它决定加不加 `.dark`、代码高亮选哪套。
   *
   * 以前是 `theme !== 'light'` 猜的——只要再加一套浅色主题就会被误判成深色，
   * 表现为「HSL 变量已经是浅色、`.dark` 还挂着」的撕裂。
   */
  mode: ThemeMode;
};

export const THEMES: readonly ThemeDef[] = [
  // 门道两套排在最前 —— 它们是默认，选择器里第一眼要看到的就是它们。
  // accent 必须等于 CSS 里 --acc 解析后的值，也就是 --md-info（见 workbench.css
  // 的接线块）：浅色 #4078f2、深色 #61afef。
  { id: "mendao-light",  name: "门道·浅", icon: "▤", accent: "#4078f2", mode: "light" },
  { id: "mendao-dark",   name: "门道·深", icon: "▥", accent: "#61afef", mode: "dark"  },
  { id: "dark",          name: "暗夜",   icon: "🌲", accent: "#4ade80", mode: "dark"  },
  { id: "deep-space",    name: "星渊",   icon: "🌌", accent: "#4d8fff", mode: "dark"  },
  { id: "smoke-gold",    name: "烟金",   icon: "✦",  accent: "#f0a030", mode: "dark"  },
  { id: "catppuccin",    name: "紫幕",   icon: "🔮", accent: "#cba6f7", mode: "dark"  },
  { id: "hermes",        name: "幽林",   icon: "◆",  accent: "#e8a84a", mode: "dark"  },
  { id: "light",         name: "月岩",   icon: "☀",  accent: "#16a34a", mode: "light" },
  { id: "klein",         name: "克莱蓝", icon: "◈",  accent: "#4d7fff", mode: "dark"  },
  { id: "mars",          name: "马尔绿", icon: "⬡",  accent: "#8aad3c", mode: "dark"  },
  { id: "hermes-orange", name: "爱马橙", icon: "◉",  accent: "#f46020", mode: "dark"  },
  { id: "burgundy",      name: "勃艮红", icon: "⊕",  accent: "#c03060", mode: "dark"  },
  { id: "mummy",         name: "木乃棕", icon: "△",  accent: "#c87838", mode: "dark"  },
  { id: "prussian",      name: "普鲁蓝", icon: "▣",  accent: "#2d8ab5", mode: "dark"  },
  { id: "tiffany",       name: "蒂芙蓝", icon: "◇",  accent: "#50c0b8", mode: "dark"  },
  { id: "titian",        name: "提香红", icon: "✦",  accent: "#c86030", mode: "dark"  },
  { id: "schonbrunn",    name: "申布黄", icon: "⊙",  accent: "#e8b01a", mode: "dark"  },
  { id: "bordeaux",      name: "波尔红", icon: "⊗",  accent: "#b03280", mode: "dark"  },
];

export const DEFAULT_THEME = "mendao-light";

/** localStorage 键。写在这里，免得四个启动路径各拼一遍字符串。 */
export const THEME_KEY = "ivyea-ops.theme";
/**
 * 迁移版本号。**只有它缺席时才动用户已存的主题**，所以这次迁移一辈子只跑一次；
 * 用户之后手动选的任何主题都不会再被覆盖。
 */
const MIGRATION_KEY = "ivyea-ops.theme.v";
const MIGRATION = "2";

/**
 * 把默认主题换成门道，同时不粗暴对待存量用户。
 *
 * 直接改 DEFAULT_THEME 是不够的：老用户的 localStorage 里已经存着 `dark`，
 * 他们永远不会知道有新主题。而无条件改写又太横 —— 那等于把手动选过主题的人
 * 也一起改了。
 *
 * 折中：只在**这台浏览器从没跑过这次迁移**时切一次，并返回 true 让调用方
 * 提示一句"可以换回去"。
 *
 * @returns 是否发生了迁移（调用方据此决定要不要提示）
 */
export function migrateTheme(): boolean {
  try {
    if (localStorage.getItem(MIGRATION_KEY) === MIGRATION) return false;
    localStorage.setItem(MIGRATION_KEY, MIGRATION);
    const current = localStorage.getItem(THEME_KEY);
    // 没存过 = 新用户，直接吃默认值，不必提示
    if (!isThemeId(current)) return false;
    if (current.startsWith("mendao-")) return false;
    localStorage.setItem(THEME_KEY, DEFAULT_THEME);
    return true;
  } catch {
    return false;   // 隐私模式下 localStorage 会抛，静默放弃即可
  }
}

const BY_ID = new Map(THEMES.map((t) => [t.id, t]));

export function isThemeId(value: unknown): value is string {
  return typeof value === "string" && BY_ID.has(value);
}

/** 认不出的 id 一律回落默认主题，绝不返回 undefined —— 调用方遍布启动路径。 */
export function getTheme(id: string | null | undefined): ThemeDef {
  return (id && BY_ID.get(id)) || BY_ID.get(DEFAULT_THEME)!;
}

export function themeMode(id: string | null | undefined): ThemeMode {
  return getTheme(id).mode;
}

/** 选择器上那一行：图标 + 中文名。 */
export function themeLabel(id: string): string {
  const t = getTheme(id);
  return `${t.icon} ${t.name}`;
}

/**
 * 形状皮肤。`data-theme` 管配色，`data-skin` 管形状（圆角/阴影/背景画）。
 *
 * **只有门道两套用 flat，这不是一个可以自由组合的开关** —— 旧 16 套的四级
 * 表面是半透明的、层次靠背景画叠出来，关掉画之后会塌成同色。理由和白名单
 * 都写在 styles/mendao-skin.css 顶部。
 */
export function themeSkin(id: string | null | undefined): "flat" | "classic" {
  return getTheme(id).id.startsWith("mendao-") ? "flat" : "classic";
}

/** 把主题的两个维度一次打到 <html> 上。启动路径和切换路径共用，避免只改一半。 */
export function applyThemeAttrs(id: string): void {
  const root = document.documentElement;
  root.setAttribute("data-theme", id);
  root.setAttribute("data-skin", themeSkin(id));
  // 让浏览器自带控件（滚动条、表单、日期选择器）也跟着走。少了它，
  // 浅色主题上会冒出一条深色滚动条。
  root.style.colorScheme = themeMode(id);
}
