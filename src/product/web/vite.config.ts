// From vitest/config, not vite: the `test` key is Vitest's and Vite's own
// defineConfig does not know about it.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server proxies /api to the local engine so the client is same-origin
// in development as it is in production. Without it the two differ, and the
// difference shows up as a CORS bug on the day you ship.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    // No remote chunks, no CDN, no analytics: the built client must load with
    // the network unplugged (ADR-0008).
    assetsInlineLimit: 4096,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    css: true,
  },
});
