// 验证台入口 —— 渲染**真实的 <App/>**，只把 HTTP 层换成假的。
//
// 用法：`npx vite --config vite.harness.config.ts`，然后
//   http://127.0.0.1:5199/?t=quiet-light&r=/console
//   ?t = 主题 id（默认 quiet-light）
//   ?r = 初始路由（默认 /console）
//
// 和产品的唯一差别是 mockApi 换掉了 axios 适配器与 /api 的 fetch。
// 组件树、路由、懒加载、样式引入顺序全部走 src/main.tsx 的真实路径。
import { installMockApi } from "./mockApi";

installMockApi();

// URL 里的路由要在 App 挂载前写进 history —— App 用的是 BrowserRouter，
// 它读的是真实地址栏。
const q = new URLSearchParams(location.search);
const route = q.get("r") || "/console";
if (location.pathname !== route) {
  history.replaceState(null, "", route + location.search);
}

// 主题：绕开一次性迁移，直接钉死，方便逐套对比。
// **?t=none —— 装成一台全新的浏览器**：主题和迁移版本号都不写，走 src/main 里
// 「没存过就吃 DEFAULT_THEME」那条路。改默认主题时只有这样才验得到，钉死一个 id
// 验的永远是"钉的那个"，不是默认值。
const theme = q.get("t") || "quiet-light";
if (theme === "none") {
  localStorage.removeItem("ivyea-ops.theme");
  localStorage.removeItem("ivyea-ops.theme.v");
} else {
  localStorage.setItem("ivyea-ops.theme", theme);
  localStorage.setItem("ivyea-ops.theme.v", "3");
}
// 侧栏默认展开，否则截图里全是收起态；?sb=collapsed 用来验收起态
// （收起态有自己一套 .sb.collapsed 规则，点一下按钮再截图只会拍到动画中间帧）。
localStorage.setItem("ivyea-ops.sidebar.collapsed",
  q.get("sb") === "collapsed" ? "1" : "0");

// ?wn=1 —— 装成"刚升级上来的老用户"：清掉版本更新说明的已读记录，让它再弹一次。
// ?wn=fresh —— 装成"全新安装第一次打开"：连同这个站写下的全部使用痕迹一起清掉，
//   并且**不再补写** tour 标记（补了就又变成老用户了）。这一档专门验"新装不该弹"。
const wn = q.get("wn");
const fresh = wn === "fresh";
if (wn) {
  localStorage.removeItem("ivyea-ops.whatsnew");
}
if (fresh) {
  for (const k of ["lingxing.ui.v1", "ivyea-ops-home-tab", "ivyea-ops.theme",
                   "ivyea-ops.shell", "ivyea-tour:/dashboard", "ivyea-tour:/console"]) {
    localStorage.removeItem(k);
  }
}

// 各板块的首次引导弹层会盖住半个屏幕 —— 它是要验的界面之外的东西，
// 直接标记成"看过了"。（真实使用中它只出现一次，不是常态。）
if (!fresh) {
  for (const p of ["/console", "/dashboard", "/market", "/tools", "/agents",
                   "/terminal", "/listing", "/skill-hub", "/hub-settings"]) {
    localStorage.setItem("ivyea-tour:" + p, "1");
  }
}
// 换主题提示条同理：一次性的，不该出现在每张截图里。
if (theme !== "none") localStorage.setItem("ivyea-ops.theme.v", "3");

// ?open=tools —— 页面稳定后按一下 ⌘K 把「全部工具」浮层打开。
// 不做这个就只能验首页，而上一轮改坏的搜索框恰恰在浮层里。
if (q.get("open") === "tools") {
  setTimeout(() => {
    // 先 blur：⌘K 的处理器在输入框里是**故意不接管**的（用户正在打字时按 ⌘K
    // 几乎不可能是想换板块）。不 blur 就永远打不开，看起来像功能坏了。
    (document.activeElement as HTMLElement | null)?.blur();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true, bubbles: true }));
  }, 1800);
}
// ?open=account —— 打开左下角账户菜单。
if (q.get("open") === "account") {
  setTimeout(() => {
    document.querySelector<HTMLElement>(".sb-acct")?.click();
  }, 1800);
}

// ?probe=[["选择器",["属性",…]],…] —— 页面稳定后把 computed 值吐进一个隐藏 <pre>，
// 外面用 --dump-dom 捞。**判据要读数，不能靠肉眼裁图猜**：边框到底是 0 还是
// 透明的 1px、圆角到底是 0 还是 10px，这些看图分辨不了。
const probe = q.get("probe");
if (probe) {
  setTimeout(() => {
    let out: string[] = [];
    try {
      for (const [sel, props] of JSON.parse(probe) as [string, string[]][]) {
        const el = document.querySelector(sel);
        if (!el) { out.push(sel.padEnd(24) + ":: 页面上没有这个元素"); continue; }
        const cs = getComputedStyle(el);
        out.push(sel.padEnd(24) + props.map((p) => `${p}=${cs.getPropertyValue(p)}`).join("  "));
      }
    } catch (e) {
      out = ["探针参数解析失败: " + String(e)];
    }
    const pre = document.createElement("pre");
    pre.style.cssText = "position:fixed;left:-9999px";
    pre.textContent = "PROBE_BEGIN\n" + out.join("\n") + "\nPROBE_END";
    document.body.appendChild(pre);
  }, 2500);
}

// ?overflow=1 —— 找出**谁把页面撑宽了**。窄屏下出现横向滚动条时，肉眼只能看到
// 最外层被撑开，看不出源头在哪一层。这里从 body 往下走，报告所有右边界越过视口
// 的元素，并带上它自己的 min-width / width —— 撑宽的那一层通常就是链条里
// **最深的那个**（再往里的孩子只是跟着被拉宽）。
if (q.get("overflow") === "1") {
  setTimeout(() => {
    const vw = document.documentElement.clientWidth;
    const rows: string[] = [];
    document.querySelectorAll<HTMLElement>("body *").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.right <= vw + 0.5) return;
      const cs = getComputedStyle(el);
      if (cs.position === "fixed" || cs.position === "absolute") return;  // 浮层不算撑宽
      const name = el.tagName.toLowerCase()
        + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).join(".") : "");
      rows.push(
        `${name.slice(0, 60).padEnd(62)} right=${r.right.toFixed(0)} w=${r.width.toFixed(0)}` +
        ` min-w=${cs.minWidth} flex=${cs.flex} overflow-x=${cs.overflowX}`,
      );
    });
    const pre = document.createElement("pre");
    pre.style.cssText = "position:fixed;left:-9999px";
    pre.textContent = `OVERFLOW_BEGIN\nviewport=${vw}\n` + rows.join("\n") + "\nOVERFLOW_END";
    document.body.appendChild(pre);
  }, 2500);
}

// ?font=<FONT_OPTIONS 的 id> —— 验字体族那几档。必须**在 import 真实入口之前**
// 写进 localStorage：applyAppearance() 是在 src/main 里同步跑的，晚一步就来不及。
const font = q.get("font");
if (font) localStorage.setItem("ivyea-ops.ui.font", font);

// 真实入口。放在最后 import：它内部会立刻跑主题启动逻辑并 render。
import("../src/main");

export {};
