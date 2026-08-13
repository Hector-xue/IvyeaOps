import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
// 先调色板、后接线层：先有"门道有哪些颜色"，再有"ops 的哪个位置用哪一支"。
//
// **顺序在这里不是审美问题。** `:root` 和 `[data-theme=x]` 的特异性同为
// (0,1,0)，同一个属性谁后写谁赢 —— tokens 文件先引入，它里面的
// `[data-theme=mendao-*]{--ag-n900:…}` 就会被 workbench.css 里 `:root` 的
// 默认值盖掉（实测过：agents 的灰阶在门道主题下纹丝不动）。
// 所以 mendao-tokens.css 里一律写成 `:root[data-theme=…]`(0,2,0) 提权，
// 让它与引入顺序无关。这行注释是给下一个想调换顺序的人看的。
import "./styles/mendao-tokens.css";
import "./styles/workbench.css";
// 形状层最后引入：它靠 !important 压过一切，放最后只是让阅读顺序和生效顺序一致。
import "./styles/mendao-skin.css";
import { applyAppearance } from "./lib/appearance";
import { DEFAULT_THEME, THEME_KEY, applyThemeAttrs, isThemeId, migrateTheme } from "./lib/themes";

// 挂载前先把主题打上，避免先画错一帧再翻过来。
//
// 这里以前有一份**只列了 6 个主题**的 VALID_THEMES 硬编码副本，而 MainLayout
// 有 16 个：选了后 10 套中任意一套的用户，每次刷新都会先看到一下暗夜绿
// （校验失败 → 回落 dark），再被 MainLayout 的 useEffect 纠正回去。
// 现在两边都从 lib/themes 读，这类漂移不可能再发生。
// data-skin 也必须在这里同步写 —— 只写 data-theme 的话，门道主题会先按
// 圆角+背景画画一帧，再被 React 挂载后的 useEffect 抹平，肉眼能看见那一闪。
// 一次性迁移必须在读取之前跑：默认主题从「暗夜」换成了「门道·浅」，
// 而老用户的 localStorage 里存着旧值，不迁的话他们永远看不到新默认。
// 迁移只在这台浏览器第一次跑到这行时发生一次，之后手动选的主题不会被碰。
const migrated = migrateTheme();
const saved = localStorage.getItem(THEME_KEY);
applyThemeAttrs(isThemeId(saved) ? saved : DEFAULT_THEME);
if (migrated) {
  // 提示交给 React 那边渲染（此刻还没挂载）。用全局标记而不是事件：
  // 事件在监听器注册之前发出去就丢了。
  (window as unknown as { __ivyeaThemeMigrated?: boolean }).__ivyeaThemeMigrated = true;
}

// Apply persisted appearance (user font override + global zoom) before mount,
// same reason as theme — avoid a flash of the wrong font/size.
applyAppearance();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
