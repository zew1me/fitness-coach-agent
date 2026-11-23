import { inflateRawSync, crc32 as nodeCrc32 } from "node:zlib";

import { describe, expect, it } from "vitest";

import { crc32, packActivityFile } from "../../lib/activity-zip";

type ZipEntry = {
  name: string;
  method: number;
  flags: number;
  crc: number;
  compressedSize: number;
  size: number;
  data: Uint8Array;
};

// Independent reader: walks the end-of-central-directory record and central
// directory the way an unzipper does, then cross-checks the local header.
function readSingleEntry(bytes: Uint8Array): ZipEntry {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const eocd = bytes.length - 22;
  expect(view.getUint32(eocd, true)).toBe(0x06054b50);
  expect(view.getUint16(eocd + 8, true)).toBe(1);
  const centralOffset = view.getUint32(eocd + 16, true);
  expect(centralOffset + view.getUint32(eocd + 12, true)).toBe(eocd);
  expect(view.getUint32(centralOffset, true)).toBe(0x02014b50);
  const nameLength = view.getUint16(centralOffset + 28, true);
  const localOffset = view.getUint32(centralOffset + 42, true);
  expect(view.getUint32(localOffset, true)).toBe(0x04034b50);
  const compressedSize = view.getUint32(centralOffset + 20, true);
  const dataStart = localOffset + 30 + view.getUint16(localOffset + 26, true);
  for (const offset of [6, 8, 10, 12, 14, 18, 22]) {
    // flags, method, time, date, crc, sizes agree between local and central.
    const width = offset >= 14 ? 4 : 2;
    const read = (base: number): number =>
      width === 4 ? view.getUint32(base, true) : view.getUint16(base, true);
    expect(read(localOffset + offset)).toBe(read(centralOffset + offset + 2));
  }
  return {
    name: new TextDecoder().decode(
      bytes.subarray(centralOffset + 46, centralOffset + 46 + nameLength),
    ),
    flags: view.getUint16(centralOffset + 8, true),
    method: view.getUint16(centralOffset + 10, true),
    crc: view.getUint32(centralOffset + 16, true),
    compressedSize,
    size: view.getUint32(centralOffset + 24, true),
    data: bytes.subarray(dataStart, dataStart + compressedSize),
  };
}

function gpx(points: number): string {
  const rows = Array.from(
    { length: points },
    (_, i) =>
      `<trkpt lat="47.${600000 + i}" lon="-122.${300000 + i}"><ele>${50 + (i % 7)}</ele>` +
      `<time>2026-11-01T08:${String(Math.floor(i / 60) % 60).padStart(2, "0")}:${String(i % 60).padStart(2, "0")}Z</time>` +
      `<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>${120 + (i % 30)}</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>`,
  );
  return `<?xml version="1.0"?><gpx><trk><trkseg>${rows.join("")}</trkseg></trk></gpx>`;
}

describe("crc32", () => {
  it("matches the standard check value and node's implementation", () => {
    const check = new TextEncoder().encode("123456789");
    expect(crc32(check)).toBe(0xcbf43926);
    const sample = new TextEncoder().encode(gpx(50));
    expect(crc32(sample)).toBe(nodeCrc32(sample));
  });
});

describe("packActivityFile", () => {
  it("round-trips an activity file byte-for-byte as a single deflated entry", async () => {
    const original = new TextEncoder().encode(gpx(40_000));
    const file = new File([original], "Morning Ride é.gpx", {
      type: "application/gpx+xml",
      lastModified: Date.UTC(2026, 10, 1, 12),
    });

    const packed = await packActivityFile(file, new AbortController().signal);

    expect(packed.name).toBe("Morning Ride é.gpx.zip");
    expect(packed.type).toBe("application/zip");
    // Verbose GPX XML must shrink several-fold, or packing would not help.
    expect(packed.size * 4).toBeLessThan(file.size);
    const entry = readSingleEntry(new Uint8Array(await packed.arrayBuffer()));
    expect(entry).toMatchObject({
      name: "Morning Ride é.gpx",
      method: 8,
      flags: 0x0800,
      size: original.length,
      crc: nodeCrc32(original),
    });
    expect(Buffer.compare(inflateRawSync(entry.data), original)).toBe(0);
  });

  it("refuses to start once the draft is discarded", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      packActivityFile(new File(["x"], "ride.gpx"), controller.signal),
    ).rejects.toThrow();
  });

  it("rejects with the abort reason while the file read is still pending", async () => {
    const controller = new AbortController();
    const stalled = new File(["x"], "ride.gpx");
    stalled.arrayBuffer = (): Promise<ArrayBuffer> => new Promise(() => {});
    const packing = packActivityFile(stalled, controller.signal);
    const reason = new DOMException("timed out", "TimeoutError");
    controller.abort(reason);
    await expect(packing).rejects.toBe(reason);
  });
});
