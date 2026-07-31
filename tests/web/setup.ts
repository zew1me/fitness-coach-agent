import { createWriteStream, type WriteStream } from "fs";
import { tmpdir } from "os";
import { join } from "path";

import { afterAll, beforeAll } from "vitest";

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
