import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/workbench.css";
import { applyAppearance } from "./lib/appearance";
import { DEFAULT_THEME, isThemeId } from "./lib/themes";

// 挂载前先把主题打上，避免先画错一帧再翻过来。
//
// 这里以前有一份**只列了 6 个主题**的 VALID_THEMES 硬编码副本，而 MainLayout
// 有 16 个：选了后 10 套中任意一套的用户，每次刷新都会先看到一下暗夜绿
// （校验失败 → 回落 dark），再被 MainLayout 的 useEffect 纠正回去。
// 现在两边都从 lib/themes 读，这类漂移不可能再发生。
const THEME_KEY = "ivyea-ops.theme";
const saved = localStorage.getItem(THEME_KEY);
const theme = isThemeId(saved) ? saved : DEFAULT_THEME;
document.documentElement.setAttribute("data-theme", theme);

// Apply persisted appearance (user font override + global zoom) before mount,
// same reason as theme — avoid a flash of the wrong font/size.
applyAppearance();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
