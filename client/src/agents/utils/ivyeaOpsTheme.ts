/** ops 主题 → agents 子树的桥。
 *
 *  agents 是移植进来的 claudecodeui，用的是 shadcn 那套 HSL 变量
 *  （`--background: 222 84% 5%` 这种三元组）；ops 用的是自己的 `--bg/--acc/--t`。
 *  这个文件把前者按后者算出来，inline 打在 `#agents-root` 上。
 *
 *  **这里以前手抄了 16 套调色板的 hex。** 那是第三份副本（另外两份在
 *  workbench.css 和 MainLayout.tsx），而且已经漂了：deep-space / smoke-gold /
 *  catppuccin / hermes 四套的 acc 和 CSS 里根本不是一个色，hermes 一个是橙
 *  一个是绿。现在改成**运行时读 `<html>` 上实际生效的变量** —— 新增主题时
 *  这个文件一个字都不用改，也不可能再漂。
 */
import { themeMode } from '../../lib/themes';

function hexToHsl(hex: string): string {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  return rgbToHsl(r * 255, g * 255, b * 255);
}

function rgbToHsl(r255: number, g255: number, b255: number): string {
  const r = r255 / 255, g = g255 / 255, b = b255 / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h = 0, s = 0;
  const l = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r: h = ((g - b) / d + (g < b ? 6 : 0)) / 6; break;
      case g: h = ((b - r) / d + 2) / 6; break;
      case b: h = ((r - g) / d + 4) / 6; break;
    }
  }
  return `${Math.round(h * 360)} ${Math.round(s * 100)}% ${Math.round(l * 100)}%`;
}

/**
 * 把 ops 的一个颜色变量读成 HSL 三元组。
 *
 * **alpha 直接丢掉，只取 RGB 通道。** ops 的表面色是 `rgba(12,12,12,.72)`
 * 这种半透明值（靠背景画透出层次），而 agents 子树是不透明的实心容器。
 * 丢 alpha 得到的正是原来那张手抄表里的"solid equivalent"
 * （`rgba(12,12,12,.72)` → `#0c0c0c`），所以视觉结果和以前一致。
 */
function readColor(cs: CSSStyleDeclaration, name: string, fallback: string): string {
  const raw = cs.getPropertyValue(name).trim();
  if (!raw) return hexToHsl(fallback);
  const rgb = raw.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i);
  if (rgb) return rgbToHsl(+rgb[1], +rgb[2], +rgb[3]);
  if (/^#[0-9a-f]{6}$/i.test(raw)) return hexToHsl(raw);
  if (/^#[0-9a-f]{3}$/i.test(raw)) {
    return hexToHsl('#' + raw.slice(1).split('').map((c) => c + c).join(''));
  }
  // color-mix() 之类算不出来的写法：交给浏览器算。挂一个临时元素读回计算值。
  const probe = document.createElement('span');
  probe.style.cssText = `position:absolute;visibility:hidden;color:${raw}`;
  document.body.appendChild(probe);
  const resolved = getComputedStyle(probe).color;
  probe.remove();
  const m = resolved.match(/([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/);
  return m ? rgbToHsl(+m[1], +m[2], +m[3]) : hexToHsl(fallback);
}

export function applyIvyeaOpsTheme(themeName: string, target?: HTMLElement): void {
  const root = target ?? document.documentElement;
  // 读 <html> 上实际生效的那套 —— 调用方保证 data-theme 已经写好了
  // （MainLayout.selectTheme 先 setAttribute 再 dispatch，getComputedStyle
  //  本身会强制一次样式重算，所以同一 tick 读到的就是新值）。
  const cs = getComputedStyle(document.documentElement);
  const isLight = themeMode(themeName) === 'light';

  // `.dark` 只由这里写。ThemeContext 以前也写一份，两处算出不同结果时会出现
  // 「HSL 变量已经是浅色、.dark 还挂着」的撕裂。
  root.classList.toggle('dark', !isLight);

  const bg     = readColor(cs, '--bg',  '#0c0c0c');
  const bg1    = readColor(cs, '--bg1', '#111111');
  const bg2    = readColor(cs, '--bg2', '#161616');
  const border = readColor(cs, '--b',   '#262626');
  const fg     = readColor(cs, '--t',   '#e8e8e8');
  const fgMut  = readColor(cs, '--t2',  '#a8a8a8');
  const acc    = readColor(cs, '--acc', '#4ade80');

  const vars: Record<string, string> = {
    '--background':             bg,
    '--foreground':             fg,
    '--card':                   bg1,
    '--card-foreground':        fg,
    '--popover':                bg1,
    '--popover-foreground':     fg,
    '--primary':                acc,
    '--primary-foreground':     isLight ? '0 0% 100%' : bg,
    '--secondary':              bg2,
    '--secondary-foreground':   fg,
    '--muted':                  bg2,
    '--muted-foreground':       fgMut,
    '--accent':                 acc,
    '--accent-foreground':      isLight ? '0 0% 100%' : bg,
    '--destructive':            '0 63% 31%',
    '--destructive-foreground': fg,
    '--border':                 border,
    '--input':                  border,
    '--ring':                   acc,
    '--radius':                 '0.5rem',
    '--nav-glass-bg':           `${bg1} / 0.75`,
    '--nav-tab-glow':           `${acc} / 0.20`,
    '--nav-tab-ring':           `${acc} / 0.12`,
    '--nav-float-ring':         `${border} / 0.4`,
    '--nav-divider-color':      `${border} / 0.6`,
    '--nav-input-bg':           `${bg2} / 0.6`,
    '--nav-input-focus-ring':   `${acc} / 0.22`,
  };

  for (const [k, v] of Object.entries(vars)) {
    root.style.setProperty(k, v);
  }
}
