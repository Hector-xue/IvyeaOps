import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    proxy: {
      // In dev, proxy API calls to FastAPI at 127.0.0.1:8001
      "/api": {
        target: "http://127.0.0.1:8001",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        // Pin only react/router into a stable, cacheable chunk. Everything else
        // is left to rollup's automatic splitting: with the boards now route-lazy
        // loaded, each board's heavy deps (xterm with Terminal, codemirror with
        // the editors, syntax-highlighter/katex with markdown views) land in that
        // board's on-demand chunk instead of an always-loaded vendor blob.
        // (A finer manual split of interdependent vendors caused a circular-init
        // white-screen, so we only force the safe react leaf chunk.)
        // 下面几组是**按整簇**切的，不是按包切。之前那次白屏就是把互相依赖的
        // 包拆进了不同 chunk，循环初始化时拿到半成品模块。所以规则只有一条：
        // 一簇互相依赖的包要么全在一起，要么别动。
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          // 编译器塞进来的运行时小助手（_extends 之类，各 200 字节）。它们被
          // 到处引用，rollup 会把它们并进某个"共同祖先"块 —— 一旦并进了下面
          // 那几个大块中的一个，所有引用者就都得先拉那 700 kB 才能开机。
          // 这正是 Agents 首屏块曾经拖着整个 CodeMirror 的原因。**必须先于
          // 大块判定**，单独成块。
          if (/[\\/](@babel[\\/]runtime|tslib|@swc[\\/]helpers)[\\/]/.test(id)) {
            return "runtime-helpers";
          }
          if (/[\\/](react|react-dom|react-router-dom|scheduler)[\\/]/.test(id)) {
            return "react-vendor";
          }
          // CodeMirror 与它的 lezer 语法包是一整簇（约 570 kB），只有文件编辑器
          // 和 PRD 编辑器用得到。这两个入口本来就是 lazy 的，但因为共用 CodeMirror，
          // rollup 会把它提升到共同祖先（也就是 Agents 首屏块）里去。
          // 显式切出来，它才真正跟着编辑器按需加载。
          if (/[\\/](@codemirror|@lezer|@uiw[\\/]react-codemirror|@replit[\\/]codemirror-minimap|codemirror)[\\/]/.test(id)) {
            return "codemirror";
          }
          // katex 引擎本身约 260 kB，只有消息里真的出现数学公式时才用得上。
          // **只圈 katex 本体**：把 rehype-katex / remark-math 也算进来的话，
          // 它俩依赖的那套 micromark / mdast-util 共享工具会被一起卷进这个块，
          // 而这些工具 remark-gfm 也在用 —— 于是首屏为了拿几个工具函数，
          // 得先把 260 kB 的 katex 拖下来。同 runtime-helpers 那条注释。
          if (/[\\/]katex[\\/]/.test(id)) return "katex";
          // 终端：xterm 本体 + WebGL 渲染器约 390 kB，点开 Shell 才需要。
          if (/[\\/]@xterm[\\/]/.test(id)) return "xterm";
          return undefined;
        },
      },
    },
  },
});
