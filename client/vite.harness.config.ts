// 验证台的 vite 配置 —— 只给 headless 截图用，**不参与产品构建**。
//
// 存在的理由见 harness/mockApi.ts 顶部：上一轮改外观时用手写 HTML 当验证对象，
// 抄漏的元素（会话搜索框 / 加载更多 / 右侧产物栏）被改坏了也照样"验证通过"。
// 这里跑的是真实组件树，漏不掉。
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFile } from "node:fs/promises";

/**
 * 附图取原图：`GET /api/assistant/image/ref/<id>`。
 *
 * 图片是浏览器自己去取的（`<img src>`），**不经过 mockApi 的 fetch 拦截** —— 不在
 * 这里回一张真图，历史会话里的缩略图在验证台永远是空的，等于验不到"会话记录里看得见
 * 我发的图"这条。回哪张不重要，回得出来才重要。
 */
const imageRefStub = {
  name: "harness-image-ref",
  configureServer(server: any) {
    server.middlewares.use((req: any, res: any, next: any) => {
      if (!String(req.url || "").startsWith("/api/assistant/image/ref/")) return next();
      const file = new URL("./public/art/bg.png", import.meta.url);
      res.setHeader("content-type", "image/png");
      readFile(file).then((buf) => res.end(buf)).catch(() => { res.statusCode = 404; res.end(); });
    });
  },
};

export default defineConfig({
  root: "harness",
  plugins: [react(), imageRefStub],
  // public 在项目根，harness 作为 root 时要显式指回去，否则 /favicon.png、
  // /ivyea-logo.png、/art/bg.png 全 404。
  publicDir: "../public",
  server: {
    host: "127.0.0.1",
    port: 5199,
    strictPort: true,
    // root 是 harness/，而真正要跑的组件在 ../src、依赖在 ../node_modules。
    // 不放开这条，vite 会以"越出 root"为由拒绝提供它们，页面白屏且没有报错。
    fs: { allow: [".."] },
  },
});
