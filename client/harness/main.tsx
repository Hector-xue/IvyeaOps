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
const theme = q.get("t") || "quiet-light";
localStorage.setItem("ivyea-ops.theme", theme);
localStorage.setItem("ivyea-ops.theme.v", "3");
// 侧栏保持展开，否则截图里全是收起态。
localStorage.removeItem("ivyea-ops.sidebar.collapsed");

// 各板块的首次引导弹层会盖住半个屏幕 —— 它是要验的界面之外的东西，
// 直接标记成"看过了"。（真实使用中它只出现一次，不是常态。）
for (const p of ["/console", "/dashboard", "/market", "/tools", "/agents",
                 "/terminal", "/listing", "/skill-hub", "/hub-settings"]) {
  localStorage.setItem("ivyea-tour:" + p, "1");
}
// 换主题提示条同理：一次性的，不该出现在每张截图里。
localStorage.setItem("ivyea-ops.theme.v", "3");

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

// 真实入口。放在最后 import：它内部会立刻跑主题启动逻辑并 render。
import("../src/main");

export {};
