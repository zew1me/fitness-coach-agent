import { createWriteStream, type WriteStream } from "fs";
import { tmpdir } from "os";
import { join } from "path";

import { afterAll, beforeAll, beforeEach, vi } from "vitest";

// Model, effort, verbosity and retry settings (`lib/agent/model-tiers.ts`) are
// read from `OPENAI_*` env vars, so a shell or sandbox that exports them would
// change the ladder the suite asserts against. Unset them (only the API key is
// kept, for the opt-in live integration test) so tests see the shipped defaults;
// a test that needs a value stubs it itself. This runs at module scope because
// `MODEL_TIERS` is resolved at import time, and again before each test because
// a test file's `vi.unstubAllEnvs()` restores the ambient values.
function unsetAmbientOpenAIConfig(): void {
  for (const name of Object.keys(process.env)) {
    if (name.startsWith("OPENAI_") && name !== "OPENAI_API_KEY") {
      vi.stubEnv(name, undefined);
    }
  }
}

unsetAmbientOpenAIConfig();
beforeEach(unsetAmbientOpenAIConfig);

const logPath = join(tmpdir(), `vitest-console-${process.pid}.log`);
let logStream: WriteStream;
const originalError = console.error.bind(console);
const originalWarn = console.warn.bind(console);

beforeAll(() => {
  process.stdout.write(`[setup] console errors/warnings → ${logPath}\n`);
  logStream = createWriteStream(logPath, { flags: "a" });
  logStream.on("error", (err) => {
    console.error = originalError;
    console.warn = originalWarn;
    originalError(
      `[setup] log stream error, restoring console: ${err.message}`,
    );
  });
  console.error = (...args: unknown[]): void => {
    logStream.write(`[error] ${args.map(String).join(" ")}\n`);
  };
  console.warn = (...args: unknown[]): void => {
    logStream.write(`[warn] ${args.map(String).join(" ")}\n`);
  };
});

afterAll(() => {
  console.error = originalError;
  console.warn = originalWarn;
  return new Promise<void>((resolve) => {
    logStream.end(() => resolve());
  });
});
