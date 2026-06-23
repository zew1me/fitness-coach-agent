#!/usr/bin/env bun
/**
 * Shrink a GPX track file below an upload-size budget by geometric simplification.
 *
 * Vercel serverless functions reject request bodies over ~4.5 MB (issue #182), so large
 * activity exports (e.g. a 7-8 MB StravaGPX recording) can't be uploaded as-is. This
 * script reduces the point count with the Ramer-Douglas-Peucker algorithm (via the tiny,
 * browser-ready `simplify-js`) until the file fits, while preserving every retained
 * point byte-for-byte -- elevation, timestamps, and all `<extensions>` ride along
 * untouched. It never re-serializes the XML, so nothing about kept points can drift.
 *
 * The algorithm is intentionally dependency-light and side-effect-free so it can be
 * lifted into client-side code later (the eventual fix for #182).
 *
 * Usage:
 *   bun scripts/shrink-gpx.ts <input.gpx> [--out <path>] [--target-mb 4.5] [--margin-mb 0.1]
 *
 * Writes `<input>.shrunk.gpx` next to the source by default and never overwrites the input.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, extname, join, resolve } from "node:path";

import simplify from "simplify-js";

/** Megabytes are reported in decimal (1 MB = 1,000,000 bytes) to match Vercel's limit. */
const MB = 1_000_000;
/** Approximate meters per degree of latitude; good enough for local DP distances. */
const METERS_PER_DEGREE = 111_320;

/** Emit a line to stdout (this is a CLI; `console` is disallowed by the repo lint config). */
function log(message: string): void {
  process.stdout.write(`${message}\n`);
}

type IndexedPoint = {
  x: number;
  y: number;
  /** Index into the global `blocks` array, recovered after simplification. */
  idx: number;
};

type ParsedGpx = {
  /** Text before the first `<trkpt>` (XML header, metadata, opening tags). */
  header: string;
  /** Each `<trkpt ...>...</trkpt>` verbatim, in document order. */
  blocks: string[];
  /** Separator text preceding each block; `gaps[i]` sits between block i-1 and block i. */
  gaps: string[];
  /** Text after the last `</trkpt>` (closing tags). */
  footer: string;
  /** Groups of block indices, one per `<trkseg>`, so DP never spans a segment break. */
  segments: number[][];
};

type Cli = {
  input: string;
  out: string | undefined;
  targetMb: number;
  marginMb: number;
};

function validateCli(
  cli: { input: string | undefined } & Omit<Cli, "input">,
): Cli {
  if (cli.input === undefined) {
    throw new Error(
      "usage: bun scripts/shrink-gpx.ts <input.gpx> [--out <path>] [--target-mb 4.5] [--margin-mb 0.1]",
    );
  }
  if (!Number.isFinite(cli.targetMb) || cli.targetMb <= 0) {
    throw new Error(
      `--target-mb must be a positive number, got ${cli.targetMb}`,
    );
  }
  if (!Number.isFinite(cli.marginMb) || cli.marginMb < 0) {
    throw new Error(
      `--margin-mb must be a non-negative number, got ${cli.marginMb}`,
    );
  }
  return { ...cli, input: cli.input };
}

function parseArgs(argv: string[]): Cli {
  const cli: { input: string | undefined } & Omit<Cli, "input"> = {
    input: undefined,
    out: undefined,
    targetMb: 4.5,
    marginMb: 0.1,
  };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--out") cli.out = argv[++i];
    else if (arg === "--target-mb") cli.targetMb = Number(argv[++i]);
    else if (arg === "--margin-mb") cli.marginMb = Number(argv[++i]);
    else if (arg !== undefined && !arg.startsWith("--")) cli.input ??= arg;
  }

  return validateCli(cli);
}

/**
 * Slice the GPX into header / verbatim `<trkpt>` blocks / gaps / footer. Segment
 * boundaries are detected by a `</trkseg>` appearing in the gap before a block.
 */
function parseGpx(xml: string): ParsedGpx {
  const blocks: string[] = [];
  const gaps: string[] = [];
  const re = /<trkpt\b[^>]*>[\s\S]*?<\/trkpt>/g;

  let lastEnd = 0;
  let header = "";
  let match: RegExpExecArray | null;
  while ((match = re.exec(xml)) !== null) {
    const gap = xml.slice(lastEnd, match.index);
    if (blocks.length === 0) {
      header = gap;
      gaps.push(""); // gaps[0] is unused (header holds the pre-first-block text)
    } else {
      gaps.push(gap);
    }
    blocks.push(match[0]);
    lastEnd = match.index + match[0].length;
  }

  if (blocks.length === 0) {
    throw new Error("no <trkpt> elements found -- is this a GPX track file?");
  }

  const footer = xml.slice(lastEnd);

  const segments: number[][] = [];
  let current: number[] = [0];
  for (let i = 1; i < blocks.length; i++) {
    const gap = gaps[i];
    if (gap !== undefined && gap.includes("</trkseg>")) {
      segments.push(current);
      current = [i];
    } else {
      current.push(i);
    }
  }
  segments.push(current);

  return { header, blocks, gaps, footer, segments };
}

function readCoord(block: string, attr: "lat" | "lon"): number {
  const m = block.match(new RegExp(`${attr}="(-?[\\d.]+)"`));
  if (m?.[1] === undefined) {
    throw new Error(`<trkpt> missing ${attr}: ${block.slice(0, 80)}`);
  }
  return Number(m[1]);
}

/** Project lat/lon to local meters via equirectangular approximation around `lat0`. */
function projectSegments(parsed: ParsedGpx): IndexedPoint[][] {
  const firstLat = readCoord(parsed.blocks[0] ?? "", "lat");
  const cosLat0 = Math.cos((firstLat * Math.PI) / 180);

  return parsed.segments.map((segment) =>
    segment.map((idx) => {
      const block = parsed.blocks[idx] ?? "";
      const lat = readCoord(block, "lat");
      const lon = readCoord(block, "lon");
      return {
        x: lon * cosLat0 * METERS_PER_DEGREE,
        y: lat * METERS_PER_DEGREE,
        idx,
      };
    }),
  );
}

/** Block indices retained after running DP per segment at the given tolerance (meters). */
function keptAtTolerance(
  projected: IndexedPoint[][],
  toleranceMeters: number,
): Set<number> {
  const kept = new Set<number>();
  for (const points of projected) {
    const simplified = simplify(
      points,
      toleranceMeters,
      false,
    ) as IndexedPoint[];
    for (const point of simplified) kept.add(point.idx);
  }
  return kept;
}

/** Reassemble the GPX keeping only blocks in `kept`, preserving original separators. */
function rebuild(parsed: ParsedGpx, kept: Set<number>): string {
  const parts: string[] = [parsed.header];
  let first = true;
  for (let i = 0; i < parsed.blocks.length; i++) {
    if (!kept.has(i)) continue;
    if (!first) parts.push(parsed.gaps[i] ?? "");
    parts.push(parsed.blocks[i] ?? "");
    first = false;
  }
  parts.push(parsed.footer);
  return parts.join("");
}

function byteLength(text: string): number {
  return Buffer.byteLength(text, "utf8");
}

/**
 * Find the smallest tolerance whose output fits the byte budget (monotonic: larger
 * tolerance -> fewer points -> smaller file), keeping as much detail as possible.
 */
function fitToBudget(
  parsed: ParsedGpx,
  projected: IndexedPoint[][],
  targetBytes: number,
): { kept: Set<number>; tolerance: number } {
  const sizeAt = (tol: number): number =>
    byteLength(rebuild(parsed, keptAtTolerance(projected, tol)));

  if (sizeAt(0) <= targetBytes) {
    return { kept: keptAtTolerance(projected, 0), tolerance: 0 };
  }

  let hi = 1;
  while (sizeAt(hi) > targetBytes) {
    hi *= 2;
    if (hi > 1e9)
      throw new Error(
        "could not shrink below target even at extreme tolerance",
      );
  }

  let lo = 0;
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    if (sizeAt(mid) <= targetBytes) hi = mid;
    else lo = mid;
  }
  return { kept: keptAtTolerance(projected, hi), tolerance: hi };
}

function defaultOutPath(input: string): string {
  const ext = extname(input);
  const stem = basename(input, ext);
  return join(dirname(input), `${stem}.shrunk${ext}`);
}

export type ShrinkResult = {
  /** The shrunk GPX document. */
  xml: string;
  inputBytes: number;
  outputBytes: number;
  totalPoints: number;
  keptPoints: number;
  /** Douglas-Peucker tolerance in meters that was applied (0 = no simplification). */
  tolerance: number;
};

/**
 * Pure core: shrink a GPX document to `targetBytes` by geometric simplification, keeping
 * retained trackpoints verbatim. No filesystem access -- safe to reuse in tests or
 * client-side code.
 */
export function shrinkGpxXml(xml: string, targetBytes: number): ShrinkResult {
  if (!Number.isFinite(targetBytes) || targetBytes <= 0) {
    throw new Error(
      `targetBytes must be a positive number, got ${targetBytes}`,
    );
  }
  const parsed = parseGpx(xml);
  const projected = projectSegments(parsed);
  const { kept, tolerance } = fitToBudget(parsed, projected, targetBytes);
  const output = rebuild(parsed, kept);
  return {
    xml: output,
    inputBytes: byteLength(xml),
    outputBytes: byteLength(output),
    totalPoints: parsed.blocks.length,
    keptPoints: kept.size,
    tolerance,
  };
}

function main(): void {
  const cli = parseArgs(process.argv.slice(2));
  const inputPath = resolve(cli.input);
  const outPath = resolve(cli.out ?? defaultOutPath(inputPath));

  if (outPath === inputPath) {
    throw new Error(
      "refusing to overwrite the input file -- choose a different --out path",
    );
  }

  const xml = readFileSync(inputPath, "utf8");
  const targetBytes = Math.max(
    1,
    Math.round((cli.targetMb - cli.marginMb) * MB),
  );
  if (byteLength(xml) <= targetBytes) {
    log(
      `Input already under budget (${(byteLength(xml) / MB).toFixed(2)} MB <= ${(targetBytes / MB).toFixed(2)} MB); writing a verbatim copy.`,
    );
  }

  const result = shrinkGpxXml(xml, targetBytes);
  writeFileSync(outPath, result.xml, "utf8");

  const pctPoints = ((result.keptPoints / result.totalPoints) * 100).toFixed(1);
  const pctSize = ((result.outputBytes / result.inputBytes) * 100).toFixed(1);
  log(`Shrank ${basename(inputPath)}:`);
  log(
    `  points : ${result.totalPoints} -> ${result.keptPoints} (${pctPoints}% kept)`,
  );
  log(
    `  size   : ${(result.inputBytes / MB).toFixed(2)} MB -> ${(result.outputBytes / MB).toFixed(2)} MB (${pctSize}% of original)`,
  );
  log(
    `  budget : ${(targetBytes / MB).toFixed(2)} MB (target ${cli.targetMb} MB - margin ${cli.marginMb} MB)`,
  );
  log(`  tol    : ${result.tolerance.toFixed(3)} m`);
  log(`  out    : ${outPath}`);

  if (result.outputBytes > targetBytes) {
    throw new Error(
      `output ${(result.outputBytes / MB).toFixed(2)} MB still exceeds budget`,
    );
  }
}

// Only run the CLI when executed directly (Bun sets import.meta.main); stays inert on import.
if ((import.meta as { main?: boolean }).main === true) {
  main();
}
