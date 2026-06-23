import { describe, expect, it } from "vitest";

import { shrinkGpxXml } from "../../scripts/shrink-gpx";

type LatLon = { lat: number; lon: number };

/** Build a deterministic point with a wobble so Douglas-Peucker retains a graded subset. */
function point(i: number): LatLon {
  return { lat: 47 + i * 0.001, lon: -120 + Math.sin(i) * 0.0005 };
}

function trkpt({ lat, lon }: LatLon, i: number): string {
  return [
    `   <trkpt lat="${lat.toFixed(7)}" lon="${lon.toFixed(7)}">`,
    `    <ele>${(100 + i).toFixed(1)}</ele>`,
    `    <time>2026-06-21T16:0${i % 10}:00Z</time>`,
    "    <extensions>",
    "     <gpxtpx:TrackPointExtension>",
    `      <gpxtpx:hr>${120 + (i % 30)}</gpxtpx:hr>`,
    "     </gpxtpx:TrackPointExtension>",
    "    </extensions>",
    "   </trkpt>",
  ].join("\n");
}

/** Assemble a GPX document; `segmentSizes` splits points across multiple <trkseg>. */
function makeGpx(count: number, segmentSizes?: number[]): string {
  const points = Array.from({ length: count }, (_, i) => trkpt(point(i), i));
  const sizes = segmentSizes ?? [count];
  const segments: string[] = [];
  let offset = 0;
  for (const size of sizes) {
    segments.push(
      `  <trkseg>\n${points.slice(offset, offset + size).join("\n")}\n  </trkseg>`,
    );
    offset += size;
  }
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<gpx creator="test" version="1.1" xmlns:gpxtpx="x">',
    " <trk>",
    "  <name>Test</name>",
    segments.join("\n"),
    " </trk>",
    "</gpx>",
    "",
  ].join("\n");
}

function trkptBlocks(xml: string): string[] {
  return xml.match(/<trkpt\b[^>]*>[\s\S]*?<\/trkpt>/g) ?? [];
}

const bytes = (s: string): number => Buffer.byteLength(s, "utf8");

describe("shrinkGpxXml", () => {
  it("shrinks below the byte budget", () => {
    const xml = makeGpx(300);
    const target = Math.floor(bytes(xml) * 0.5);
    const result = shrinkGpxXml(xml, target);

    expect(result.outputBytes).toBeLessThanOrEqual(target);
    expect(result.keptPoints).toBeLessThan(result.totalPoints);
    expect(result.tolerance).toBeGreaterThan(0);
  });

  it("produces a well-formed document and preserves the first and last trackpoint", () => {
    const xml = makeGpx(300);
    const source = trkptBlocks(xml);
    const result = shrinkGpxXml(xml, Math.floor(bytes(xml) * 0.5));
    const kept = trkptBlocks(result.xml);

    expect(result.xml.startsWith("<?xml")).toBe(true);
    expect(result.xml.trimEnd().endsWith("</gpx>")).toBe(true);
    expect(kept[0]).toBe(source[0]);
    expect(kept.at(-1)).toBe(source.at(-1));
  });

  it("keeps retained trackpoints byte-for-byte, including extensions", () => {
    const xml = makeGpx(300);
    const result = shrinkGpxXml(xml, Math.floor(bytes(xml) * 0.5));
    for (const block of trkptBlocks(result.xml)) {
      expect(xml).toContain(block);
      expect(block).toContain("<gpxtpx:hr>");
    }
  });

  it("returns every point when the input already fits the budget", () => {
    const xml = makeGpx(50);
    const result = shrinkGpxXml(xml, bytes(xml) * 2);

    expect(result.keptPoints).toBe(result.totalPoints);
    expect(result.tolerance).toBe(0);
    expect(result.xml).toBe(xml);
  });

  it("preserves segment boundaries and each segment's endpoints", () => {
    const xml = makeGpx(300, [150, 150]);
    const source = trkptBlocks(xml);
    const result = shrinkGpxXml(xml, Math.floor(bytes(xml) * 0.5));

    // Both <trkseg> wrappers survive.
    expect((result.xml.match(/<trkseg>/g) ?? []).length).toBe(2);
    expect((result.xml.match(/<\/trkseg>/g) ?? []).length).toBe(2);

    const kept = trkptBlocks(result.xml);
    // First/last of segment one and first/last of segment two are all retained.
    expect(kept).toContain(source[0]);
    expect(kept).toContain(source[149]);
    expect(kept).toContain(source[150]);
    expect(kept).toContain(source.at(-1));
  });

  it("keeps fewer points as the budget tightens", () => {
    const xml = makeGpx(400);
    const loose = shrinkGpxXml(xml, Math.floor(bytes(xml) * 0.7));
    const tight = shrinkGpxXml(xml, Math.floor(bytes(xml) * 0.3));

    expect(tight.keptPoints).toBeLessThanOrEqual(loose.keptPoints);
    expect(tight.outputBytes).toBeLessThanOrEqual(loose.outputBytes);
  });

  it("rejects documents without trackpoints", () => {
    const empty =
      '<?xml version="1.0"?>\n<gpx><trk><trkseg></trkseg></trk></gpx>';
    expect(() => shrinkGpxXml(empty, 1000)).toThrow(/no <trkpt>/);
  });

  it("rejects a non-positive byte budget", () => {
    expect(() => shrinkGpxXml(makeGpx(10), 0)).toThrow(/positive/);
  });
});
