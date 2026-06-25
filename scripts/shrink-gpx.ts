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
 * The pure core (`shrinkGpxXml`) is dependency-light and side-effect-free -- it uses only
 * string ops, `simplify-js`, and `TextEncoder` -- so it can be lifted into client-side
 * code later (the eventual fix for #182).
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

const encoder = new TextEncoder();

/** UTF-8 byte length; `TextEncoder` keeps the core portable to browsers (no `Buffer`). */
function byteLength(text: string): number {
  return encoder.encode(text).length;
}

/** Emit a line to stdout (this is a CLI; `console` is disallowed by the repo lint config). */
function log(message: string): void {
  process.stdout.write(`${message}\n`);
}

type IndexedPoint = {
  x: number;
  y: number;
  /** Index into `ParsedGpx.points`, recovered after simplification. */
  idx: number;
};

/** A single `<trkpt>` plus the separator text that precedes it in the document. */
type TrackPoint = {
  /** Text between the previous block and this one; `""` for the first point (see `header`). */
  leadingGap: string;
  /** The `<trkpt ...>...</trkpt>` (or self-closing `<trkpt .../>`) substring, verbatim. */
  block: string;
};

type ParsedGpx = {
  /** Text before the first `<trkpt>` (XML header, metadata, opening tags). */
  header: string;
  /** Every trackpoint in document order, each carrying its own leading separator. */
  points: TrackPoint[];
  /** Text after the last `</trkpt>` (closing tags). */
  footer: string;
  /** Groups of `points` indices, one per `<trkseg>`, so DP never spans a segment break. */
  segments: number[][];
};

type Cli = {
  input: string;
  out: string | undefined;
  targetMb: number;
  marginMb: number;
};

/** A `Cli` before validation, where `input` may still be missing. */
type RawCli = { input: string | undefined } & Omit<Cli, "input">;

function validateCli(cli: RawCli): Cli {
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
  const cli: RawCli = {
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
 * Slice the GPX into header / verbatim `<trkpt>` blocks / footer. Both full
 * (`<trkpt>...</trkpt>`) and self-closing (`<trkpt .../>`) trackpoints are recognized.
 * Segment boundaries are detected by a `</trkseg>` appearing in a block's leading gap.
 */
function parseGpx(xml: string): ParsedGpx {
  const points: TrackPoint[] = [];
  const re = /<trkpt\b[^>]*?(?:\/>|>[\s\S]*?<\/trkpt>)/g;

  let lastEnd = 0;
  let header = "";
  let match: RegExpExecArray | null;
  while ((match = re.exec(xml)) !== null) {
    const gap = xml.slice(lastEnd, match.index);
    if (points.length === 0) {
      header = gap; // pre-first-block text lives here; the first point's leadingGap is ""
      points.push({ leadingGap: "", block: match[0] });
    } else {
      points.push({ leadingGap: gap, block: match[0] });
    }
    lastEnd = match.index + match[0].length;
  }

  if (points.length === 0) {
    throw new Error("no <trkpt> elements found -- is this a GPX track file?");
  }

  const footer = xml.slice(lastEnd);

  const segments: number[][] = [];
  let current: number[] = [0];
  for (let i = 1; i < points.length; i++) {
    if (points[i]?.leadingGap.includes("</trkseg>")) {
      segments.push(current);
      current = [i];
    } else {
      current.push(i);
    }
  }
  segments.push(current);

  return { header, points, footer, segments };
}

function readCoord(block: string, attr: "lat" | "lon"): number {
  const m = block.match(new RegExp(`${attr}="(-?[\\d.]+)"`));
  if (m?.[1] === undefined) {
    throw new Error(`<trkpt> missing ${attr}: ${block.slice(0, 80)}`);
  }
  const value = Number(m[1]);
  if (!Number.isFinite(value)) {
    throw new Error(
      `<trkpt> has non-numeric ${attr}="${m[1]}": ${block.slice(0, 80)}`,
    );
  }
  return value;
}

function pointAt(parsed: ParsedGpx, idx: number): TrackPoint {
  const point = parsed.points[idx];
  if (point === undefined) {
    throw new Error(`internal error: point index ${idx} out of range`);
  }
  return point;
}

/** Project lat/lon to local meters via equirectangular approximation around `lat0`. */
function projectSegments(parsed: ParsedGpx): IndexedPoint[][] {
  const firstLat = readCoord(pointAt(parsed, 0).block, "lat");
  const cosLat0 = Math.cos((firstLat * Math.PI) / 180);

  return parsed.segments.map((segment) =>
    segment.map((idx) => {
      const block = pointAt(parsed, idx).block;
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

/**
 * Block indices retained after running Douglas-Peucker per segment at the given tolerance.
 * `highQuality` (true) skips simplify-js's radial-distance pre-pass, giving pure DP, whose
 * retained set is monotonic in tolerance -- the property `fitToBudget` relies on.
 */
function keptAtTolerance(
  projected: IndexedPoint[][],
  toleranceMeters: number,
): Set<number> {
  const kept = new Set<number>();
  for (const points of projected) {
    const simplified = simplify(
      points,
      toleranceMeters,
      true,
    ) as IndexedPoint[];
    for (const point of simplified) kept.add(point.idx);
  }
  return kept;
}

/** Reassemble the GPX keeping only points in `kept`, preserving original separators. */
function rebuild(parsed: ParsedGpx, kept: Set<number>): string {
  const parts: string[] = [parsed.header];
  let first = true;
  for (let i = 0; i < parsed.points.length; i++) {
    if (!kept.has(i)) continue;
    const point = pointAt(parsed, i);
    if (!first) parts.push(point.leadingGap);
    parts.push(point.block);
    first = false;
  }
  parts.push(parsed.footer);
  return parts.join("");
}

/**
 * Find the smallest tolerance whose output fits the byte budget. Pure Douglas-Peucker keeps
 * a subset of points as tolerance grows, so file size decreases monotonically; we binary
 * search the tolerance and the returned `hi` is always re-verified to fit the budget.
 */
function fitToBudget(
  parsed: ParsedGpx,
  projected: IndexedPoint[][],
  targetBytes: number,
): { kept: Set<number>; tolerance: number } {
  const sizeAt = (tol: number): number =>
    byteLength(rebuild(parsed, keptAtTolerance(projected, tol)));

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
 * client-side code. If the input already fits, every point is kept and the output is
 * byte-identical to the input.
 */
export function shrinkGpxXml(xml: string, targetBytes: number): ShrinkResult {
  if (!Number.isFinite(targetBytes) || targetBytes <= 0) {
    throw new Error(
      `targetBytes must be a positive number, got ${targetBytes}`,
    );
  }
  const parsed = parseGpx(xml);
  const totalPoints = parsed.points.length;
  const inputBytes = byteLength(xml);

  // Already under budget: keep every point. rebuild() of the full set reconstructs the
  // original byte-for-byte, so this is a true no-op rather than a tolerance-0 simplify
  // (which would still drop duplicate/collinear points).
  if (inputBytes <= targetBytes) {
    const keptAll = new Set<number>(parsed.points.map((_, i) => i));
    const verbatim = rebuild(parsed, keptAll);
    return {
      xml: verbatim,
      inputBytes,
      outputBytes: byteLength(verbatim),
      totalPoints,
      keptPoints: totalPoints,
      tolerance: 0,
    };
  }

  const projected = projectSegments(parsed);
  const { kept, tolerance } = fitToBudget(parsed, projected, targetBytes);
  const output = rebuild(parsed, kept);
  return {
    xml: output,
    inputBytes,
    outputBytes: byteLength(output),
    totalPoints,
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
