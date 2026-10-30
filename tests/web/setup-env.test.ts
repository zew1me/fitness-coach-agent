import { readdirSync, readFileSync } from "fs";
import { join } from "path";

import { describe, expect, it } from "vitest";

import { MODEL_TIERS } from "../../lib/agent/model-tiers";

import { MODEL_CONFIG_ENV_VARS } from "./model-config-env";

// Guards the ambient-env isolation in `tests/web/setup.ts` (issue #544).

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

describe("test setup env isolation", () => {
  it("resolves the import-time model ladder from shipped defaults", () => {
    expect(MODEL_TIERS.map((tier) => tier.model)).toEqual([
      "gpt-6-luna",
      "gpt-5.4-mini",
      "gpt-6.1-terra",
    ]);
  });

  it("hides every app model setting from tests", () => {
    for (const name of MODEL_CONFIG_ENV_VARS) {
      expect(process.env[name], name).toBeUndefined();
    }
  });

  it("lists every OPENAI_* setting the app reads, except the API key", () => {
    const read = new Set<string>();
    for (const file of sourceFiles("lib")) {
      for (const match of readFileSync(file, "utf8").matchAll(
        /process\.env(?:\[["']|\.)(OPENAI_[A-Z0-9_]+)/g,
      )) {
        if (match[1] !== undefined) read.add(match[1]);
      }
    }
    read.delete("OPENAI_API_KEY");
    expect([...read].sort()).toEqual([...MODEL_CONFIG_ENV_VARS].sort());
  });
});
