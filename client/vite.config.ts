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
        manualChunks(id: string) {
          if (/[\\/]node_modules[\\/](react|react-dom|react-router-dom|scheduler)[\\/]/.test(id)) {
            return "react-vendor";
          }
          return undefined;
        },
      },
    },
  },
});
