import { createWriteStream, type WriteStream } from "fs";
import { tmpdir } from "os";
import { join } from "path";

import { afterAll, beforeAll } from "vitest";

// Production always has this set, and `new OpenAI()` throws without it — the
// `DurableCompactionSession` constructor builds a client eagerly, so any test
// reaching it dies before the code under test runs. Assign unconditionally
// rather than defaulting: a developer with a real key in their shell would
// otherwise exercise a different code path than CI, which is exactly how a
// durable-session regression stayed green locally and failed only on CI. The
// value is never used to reach the network — the live OpenAI suite runs under
// vitest.oai.config.ts, which does not load this setup file.
process.env["OPENAI_API_KEY"] = "test-openai-key-not-a-real-credential";

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
