import { readFileSync } from "fs";
import { resolve } from "path";

import { defineConfig } from "vitest/config";

function loadEnvLocal(): Record<string, string> {
  try {
    const text = readFileSync(resolve(".env.local"), "utf8");
    return Object.fromEntries(
      text
        .split("\n")
        .filter((l) => l.trim() && !l.startsWith("#"))
        .map((l) => {
          const idx = l.indexOf("=");
          if (idx === -1) return null;
          const k = l.slice(0, idx).trim();
          const v = l
            .slice(idx + 1)
            .trim()
            .replace(/^["']|["']$/g, "");
          return [k, v] as [string, string];
        })
        .filter(
          (entry): entry is [string, string] =>
            entry !== null && entry[0].length > 0,
        ),
    );
  } catch {
    return {};
  }
}

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/integration/**/*.test.ts"],
    env: loadEnvLocal(),
  },
});
