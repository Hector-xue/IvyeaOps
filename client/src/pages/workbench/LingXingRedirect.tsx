import { Navigate, useSearchParams } from "react-router-dom";

/**
 * `/lingxing` 的去处 —— 领星 ERP 已并入运营驾驶舱。
 *
 * 存在的唯一理由是**老书签和老 localStorage 值**。直接挂一个 `<Navigate to="/dashboard">`
 * 会丢掉 query（React Router 不会自动带），于是一个存着「优化建议」的书签会落到
 * 「大盘流量」上 —— 用户只会觉得功能没了。
 *
 * 两层映射，都必须留着：
 *
 * - 领星板块从 8 个 tab 收成 6 个时留下的那层（optimizer/auto/operate/help）；
 * - 这次合并的这层：大盘并进了广告看板，数据浏览/审计/配置进了齿轮对话框。
 */
const TAB_MAP: Record<string, string> = {
  // ── 合并前的 6 个 tab ──
  dashboard: "?tab=ads",              // 大盘撤掉，广告看板是它的超集
  suggest: "?tab=suggest",
  tickets: "?tab=tickets",
  browse: "?tab=ads&panel=browse",    // 三个工具性入口进齿轮对话框；tab 给一个合理的
  audit: "?tab=ads&panel=audit",      // 落点，免得把对话框关掉之后背后是一片空白
  config: "?tab=ads&panel=config",
  // ── 更早的 8-tab 布局（这层映射原本在 LingXing.tsx 里，不能跟着它一起删） ──
  optimizer: "?tab=suggest",
  auto: "?tab=suggest",
  operate: "?tab=tickets",
  help: "?tab=ads&panel=config",
};

/** 领星板块自己的 localStorage（`lingxing.ui.v1`）里存的上次 tab。 */
function lastLingXingView(): string {
  try { return JSON.parse(localStorage.getItem("lingxing.ui.v1") || "{}").view || ""; }
  catch { return ""; }
}

export default function LingXingRedirect() {
  const [params] = useSearchParams();
  const view = params.get("tab") || lastLingXingView();
  return <Navigate to={"/dashboard" + (TAB_MAP[view] || "?tab=ads")} replace />;
}
