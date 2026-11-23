// Lossless packing for oversized activity files (#262, decision in #182).
//
// GPX/TCX are verbose XML that deflate shrinks several-fold, so an activity file over
// the request limit is first wrapped in a single-entry zip and uploaded as
// `application/zip`. The backend already unpacks zips with bounded, budgeted reads
// (`process-uploaded-zip`) and parses the member by its own suffix, so no new server
// decompressor is needed. Lossy shrinking stays a later fallback (#182 step 2).

// Mirrors `_ZIP_MEMBER_MAX_BYTES` in api/index.py: the server skips any member whose
// uncompressed size exceeds it, so packing a larger file would upload a zip that
// yields nothing. Keep the two in sync.
export const ZIP_MEMBER_MAX_BYTES = 25 * 1024 * 1024;

const ZIP_CONTENT_TYPE = "application/zip";
const DEFLATE = 8;
const VERSION_NEEDED = 20;
// Bit 11: the entry name is UTF-8, so non-ASCII filenames survive extraction.
const UTF8_NAME_FLAG = 0x0800;

const CRC_TABLE = ((): Uint32Array => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    // Index is masked to 0..255, so the lookup always hits; `?? 0` only satisfies TS.
    crc = (CRC_TABLE[(crc ^ byte) & 0xff] ?? 0) ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function dosDateTime(date: Date): { time: number; date: number } {
  // DOS dates cannot represent years before 1980; clamp rather than wrap.
  const year = Math.max(date.getFullYear(), 1980);
  return {
    time:
      (date.getHours() << 11) |
      (date.getMinutes() << 5) |
      Math.floor(date.getSeconds() / 2),
    date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
  };
}

export function canPackActivityFile(): boolean {
  return typeof CompressionStream === "function";
}

async function deflateRaw(
  bytes: Uint8Array<ArrayBuffer>,
  signal: AbortSignal,
): Promise<Uint8Array<ArrayBuffer>> {
  // Response.body gives a byte stream in every runtime we target; Blob.stream() is
  // missing from some test DOMs.
  const source = new Response(bytes).body;
  if (source === null) throw new Error("Could not read the activity file");
  const compressed = await new Response(
    source.pipeThrough(new CompressionStream("deflate-raw")),
  ).arrayBuffer();
  // CompressionStream cannot observe the signal, so discard an aborted result here.
  signal.throwIfAborted();
  return new Uint8Array(compressed);
}

/**
 * Wrap one activity file in a single-entry, deflate-compressed zip.
 *
 * Output is a plain PKZIP archive (no zip64: inputs are capped well under 4 GiB by
 * {@link ZIP_MEMBER_MAX_BYTES}) that Python's `zipfile` reads and CRC-checks.
 */
export async function packActivityFile(
  file: File,
  signal: AbortSignal,
): Promise<File> {
  signal.throwIfAborted();
  const data = new Uint8Array(await file.arrayBuffer());
  signal.throwIfAborted();
  const compressed = await deflateRaw(data, signal);
  const name = new TextEncoder().encode(file.name);
  const crc = crc32(data);
  const stamp = dosDateTime(new Date(file.lastModified || Date.now()));

  const local = new DataView(new ArrayBuffer(30));
  local.setUint32(0, 0x04034b50, true);
  local.setUint16(4, VERSION_NEEDED, true);
  local.setUint16(6, UTF8_NAME_FLAG, true);
  local.setUint16(8, DEFLATE, true);
  local.setUint16(10, stamp.time, true);
  local.setUint16(12, stamp.date, true);
  local.setUint32(14, crc, true);
  local.setUint32(18, compressed.length, true);
  local.setUint32(22, data.length, true);
  local.setUint16(26, name.length, true);
  local.setUint16(28, 0, true);

  const central = new DataView(new ArrayBuffer(46));
  central.setUint32(0, 0x02014b50, true);
  central.setUint16(4, VERSION_NEEDED, true);
  central.setUint16(6, VERSION_NEEDED, true);
  central.setUint16(8, UTF8_NAME_FLAG, true);
  central.setUint16(10, DEFLATE, true);
  central.setUint16(12, stamp.time, true);
  central.setUint16(14, stamp.date, true);
  central.setUint32(16, crc, true);
  central.setUint32(20, compressed.length, true);
  central.setUint32(24, data.length, true);
  central.setUint16(28, name.length, true);
  // Extra field, comment, disk number, attributes and local-header offset stay 0.

  const centralOffset = 30 + name.length + compressed.length;
  const centralSize = 46 + name.length;
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, 1, true);
  end.setUint16(10, 1, true);
  end.setUint32(12, centralSize, true);
  end.setUint32(16, centralOffset, true);

  return new File(
    [local, name, compressed, central, name, end],
    `${file.name}.zip`,
    { type: ZIP_CONTENT_TYPE, lastModified: file.lastModified },
  );
}
