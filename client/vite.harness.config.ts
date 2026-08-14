// 验证台的 vite 配置 —— 只给 headless 截图用，**不参与产品构建**。
//
// 存在的理由见 harness/mockApi.ts 顶部：上一轮改外观时用手写 HTML 当验证对象，
// 抄漏的元素（会话搜索框 / 加载更多 / 右侧产物栏）被改坏了也照样"验证通过"。
// 这里跑的是真实组件树，漏不掉。
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: "harness",
  // public 在项目根，harness 作为 root 时要显式指回去，否则 /favicon.png、
  // /ivyea-logo.png、/art/bg.png 全 404。
  publicDir: "../public",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5199,
    strictPort: true,
    // root 是 harness/，而真正要跑的组件在 ../src、依赖在 ../node_modules。
    // 不放开这条，vite 会以"越出 root"为由拒绝提供它们，页面白屏且没有报错。
    fs: { allow: [".."] },
  },
});
