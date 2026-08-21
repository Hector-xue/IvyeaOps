// 外观偏好（字体族 + 全局字号缩放）—— 纯前端显示偏好，存 localStorage，绑设备。
//
// 背景：默认主题的 UI 字体是等宽（JetBrains Mono），中文回退到细系统字体 → 又小又细
// 又不清晰；且 font-size 全硬编码 px，无法用根字号缩放。所以：
//   - 字体族：设 inline 的 `--font` 覆盖主题的 `--font`（全 UI 都用 var(--font)）。
//   - 字号：用 CSS `zoom`（见 workbench.css `#root{zoom:var(--ui-zoom)}`）整体等比缩放，
//     清晰不糊，等同浏览器 Ctrl+加号。
// 默认「跟随主题」＝不设 inline 覆盖，观感与现在完全一致（零冲击）。

export type FontOption = { id: string; label: string; stack: string };

// stack 为空 = 不覆盖（跟随主题）。每个栈都含中英文回退，兼顾清晰度。
export const FONT_OPTIONS: FontOption[] = [
  { id: "theme", label: "跟随主题（默认）", stack: "" },
  { id: "system", label: "系统默认 · 清晰", stack: 'system-ui,-apple-system,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif' },
  { id: "yahei", label: "微软雅黑", stack: '"Microsoft YaHei","PingFang SC",system-ui,-apple-system,sans-serif' },
  { id: "pingfang", label: "苹方 PingFang", stack: '"PingFang SC","Microsoft YaHei",system-ui,-apple-system,sans-serif' },
  { id: "source", label: "思源黑体", stack: '"Source Han Sans SC","Noto Sans SC","Microsoft YaHei",system-ui,sans-serif' },
  // 衬线/等宽在手机（安卓/iOS）上也有系统字体，能看出明显区别；黑体类在安卓只有一种系统字，
  // 各选项看起来一样。手机上想直观改变观感，选「衬线体」或「等宽」。
  { id: "serif", label: "衬线体（宋体）", stack: '"Songti SC","SimSun","Noto Serif CJK SC",Georgia,"Times New Roman",serif' },
  { id: "mono", label: "等宽 · 终端风", stack: '"JetBrains Mono","Fira Code","SF Mono",Consolas,monospace' },
];

export type ZoomOption = { id: string; label: string; value: number };

export const ZOOM_OPTIONS: ZoomOption[] = [
  { id: "s", label: "小", value: 0.9 },
  { id: "m", label: "标准", value: 1.0 },
  { id: "l", label: "大", value: 1.15 },
  { id: "xl", label: "特大", value: 1.3 },
];

// 字重：治"太细"。应用于 `#root{font-weight:var(--ui-weight)}`——只加粗**没有显式 font-weight
// 的正文**（继承 #root），已显式加粗的标题/按钮（600+）保持不变→治太细又不破坏层级。跨平台生效
// （含安卓，不依赖特定字体）。
export type WeightOption = { id: string; label: string; value: number };

// value 400 = **跟随主题**，不写 inline 覆盖（和字体族的 "theme" 档同一个约定）。
// 以前这一档是"写死 400"，于是主题**永远没法定义自己的默认字重** —— inline
// 样式压过一切 CSS。琉璃的正文基准是 500（Medium），被那个 400 静默盖掉了，
// 表现就是"换了主题字重和原来一模一样"。
export const WEIGHT_OPTIONS: WeightOption[] = [
  { id: "normal", label: "跟随主题（默认）", value: 400 },
  { id: "medium", label: "中等", value: 500 },
  { id: "bold", label: "加粗", value: 600 },
];

const FONT_KEY = "ivyea-ops.ui.font";
const ZOOM_KEY = "ivyea-ops.ui.zoom";
const WEIGHT_KEY = "ivyea-ops.ui.weight";
export const APPEARANCE_EVENT = "ivyea-appearance";

export function getFontId(): string {
  const id = localStorage.getItem(FONT_KEY) || "theme";
  return FONT_OPTIONS.some((o) => o.id === id) ? id : "theme";
}

export function getZoom(): number {
  const v = parseFloat(localStorage.getItem(ZOOM_KEY) || "1");
  return Number.isFinite(v) && v >= 0.5 && v <= 2 ? v : 1;
}

export function getWeight(): number {
  const v = parseInt(localStorage.getItem(WEIGHT_KEY) || "400", 10);
  return WEIGHT_OPTIONS.some((o) => o.value === v) ? v : 400;
}

function emit() {
  try { window.dispatchEvent(new Event(APPEARANCE_EVENT)); } catch { /* noop */ }
}

/** 应用字体族。id="theme" → 移除 inline 覆盖，回到主题字体。 */
export function applyFont(id: string, persist = true): void {
  const opt = FONT_OPTIONS.find((o) => o.id === id) || FONT_OPTIONS[0];
  const root = document.documentElement;
  if (!opt.stack) root.style.removeProperty("--font");
  else root.style.setProperty("--font", opt.stack);
  if (persist) { try { localStorage.setItem(FONT_KEY, opt.id); } catch { /* noop */ } }
  if (persist) emit();
}

/** 应用全局字号缩放（CSS zoom）。 */
export function applyZoom(value: number, persist = true): void {
  const v = Number.isFinite(value) && value >= 0.5 && value <= 2 ? value : 1;
  document.documentElement.style.setProperty("--ui-zoom", String(v));
  if (persist) { try { localStorage.setItem(ZOOM_KEY, String(v)); } catch { /* noop */ } }
  if (persist) emit();
}

/** 应用全局字重（治"太细"，见 workbench.css `#root{font-weight:var(--ui-weight)}`）。 */
export function applyWeight(value: number, persist = true): void {
  const v = WEIGHT_OPTIONS.some((o) => o.value === value) ? value : 400;
  // 400 = 跟随主题：**移除** inline 覆盖，让主题里的 --ui-weight 生效
  // （其余 17 套主题没定义它，`var(--ui-weight,400)` 回落 400，观感零变化）。
  if (v === 400) document.documentElement.style.removeProperty("--ui-weight");
  else document.documentElement.style.setProperty("--ui-weight", String(v));
  if (persist) { try { localStorage.setItem(WEIGHT_KEY, String(v)); } catch { /* noop */ } }
  if (persist) emit();
}

/** render 之前调用（main.tsx），按持久化偏好设 --font / --ui-zoom / --ui-weight，防闪烁。 */
export function applyAppearance(): void {
  applyFont(getFontId(), false);
  applyZoom(getZoom(), false);
  applyWeight(getWeight(), false);
}
