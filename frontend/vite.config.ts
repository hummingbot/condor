/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    chunkSizeWarningLimit: 600,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    // Pure helpers are the common case — no DOM, no React — so `node` stays the
    // default and keeps the run fast. A component test that needs a DOM opts in
    // per file with a `@vitest-environment jsdom` docblock (see
    // components/ui/AnchoredMenu.test.tsx).
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
  server: {
    proxy: {
      "/api": "http://localhost:8088",
      "/ws": {
        target: "ws://localhost:8088",
        ws: true,
      },
    },
  },
});
