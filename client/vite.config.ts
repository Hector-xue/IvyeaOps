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
        // Single vendor chunk: split node_modules out of the app code so the
        // ~2.7MB of rarely-changing deps cache across app updates (app updates
        // then re-download only the smaller app chunk). One combined vendor
        // chunk avoids the circular cross-vendor-chunk init that white-screened
        // a finer-grained split.
        manualChunks(id: string) {
          if (id.includes("node_modules")) return "vendor";
          return undefined;
        },
      },
    },
  },
});
