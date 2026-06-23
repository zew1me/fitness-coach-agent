import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/web/**/*.test.ts?(x)"],
    setupFiles: ["tests/web/setup.ts"],
  },
});
