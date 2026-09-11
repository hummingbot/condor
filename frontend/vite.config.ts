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
    // `lightweight-charts` is resolved dynamically by every chart component, so
    // two mounts in one `act()` request it concurrently and Vitest's mocker
    // answers a per-file `vi.mock` to one of them and raw-imports the real
    // library for the other — a real chart widget in a canvas-less jsdom, whose
    // draw frame fires after teardown and exits the run 1 while every test
    // reports green (CORR-360, CORR-368). Substituting at the resolver instead
    // of per file catches every resolution, including the losing one, so no
    // test can reach the real library even by forgetting to mock it.
    alias: {
      "lightweight-charts": path.resolve(__dirname, "./src/test/lightweight-charts-double.ts"),
    },
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
