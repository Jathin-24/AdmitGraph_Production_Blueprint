import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

/** Vitest runner for the frontend critical-path tests (P2-24).
 *  - jsdom for DOM/interaction tests (nav, explore filters).
 *  - `@/*` alias mirrors tsconfig.json; tests otherwise import relatively.
 *  - Tests must stay hermetic: no network (fetch is stubbed per test). */
export default defineConfig({
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    include: ["app/**/*.test.{ts,tsx}"],
    setupFiles: ["./vitest.setup.ts"],
    css: false,
  },
});
