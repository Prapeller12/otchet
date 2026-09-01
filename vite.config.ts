import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  root: "frontend",
  plugins: [react()],
  build: {
    outDir: "../dist/frontend",
    emptyOutDir: true,
    modulePreload: { polyfill: false },
  },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/setup.ts"],
  },
});
