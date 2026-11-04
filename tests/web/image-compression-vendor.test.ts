import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

import { expect, it } from "vitest";

import {
  IMAGE_COMPRESSION_WORKER_INTEGRITY,
  IMAGE_COMPRESSION_WORKER_URL,
} from "../../lib/image-compression";

it("pins the CDN worker version and integrity to the installed package", () => {
  const require = createRequire(import.meta.url);
  const installed = require.resolve("browser-image-compression");
  const { version } = JSON.parse(
    readFileSync(path.join(path.dirname(installed), "../package.json"), "utf8"),
  ) as { version: string };
  expect(IMAGE_COMPRESSION_WORKER_URL).toBe(
    `https://cdn.jsdelivr.net/npm/browser-image-compression@${version}/dist/browser-image-compression.js`,
  );
  const digest = createHash("sha384")
    .update(readFileSync(installed))
    .digest("base64");
  expect(IMAGE_COMPRESSION_WORKER_INTEGRITY).toBe(`sha384-${digest}`);
});
