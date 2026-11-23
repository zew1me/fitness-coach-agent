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
  // Passing the signal to the pipe cancels deflate on abort instead of letting it run.
  const compressed = await new Response(
    source.pipeThrough(new CompressionStream("deflate-raw"), { signal }),
  ).arrayBuffer();
  signal.throwIfAborted();
  return new Uint8Array(compressed);
}

let packTail: Promise<void> = Promise.resolve();

/** Settle with the abort reason as soon as `signal` fires, even if `work` never does. */
function rejectOnAbort<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  let onAbort = (): void => {};
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = (): void => reject(signal.reason);
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });
  });
  return Promise.race([work, aborted]).finally(() =>
    signal.removeEventListener("abort", onAbort),
  );
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
  // One pack at a time across picks, drops and pastes: each holds the whole input
  // (up to 25 MiB) plus its output, so parallel packs would multiply peak memory.
  // Queued work that is cancelled before its turn never reads the file.
  const previous = packTail;
  const work = previous.then(() => {
    signal.throwIfAborted();
    return pack(file, signal);
  });
  // A file read cannot be cancelled, so the attachment timeout must not wait on it.
  const settled = rejectOnAbort(work, signal);
  // This pack's slot opens only after the previous slot, so cancelling a queued pack
  // never lets a later one overtake a pack still running ahead of it. Within its own
  // turn the slot frees on settle *or* abort: an aborted deflate is cancelled through
  // the pipe, and a stalled read of a local file must not wedge every later upload.
  // Each read that stalls past its timeout stays pending (reads cannot be cancelled),
  // so successive stalls can leave several outstanding, one per timed-out file. A
  // pending read holds no bytes in JS until it resolves, and `pack` then drops the
  // result at its post-read abort check, so each costs at most one file (<= 25 MiB)
  // transiently. Local reads stalling for 60 s is rare enough that we accept this.
  packTail = previous
    .then(() => settled)
    .then(
      () => {},
      () => {},
    );
  return settled;
}

async function pack(file: File, signal: AbortSignal): Promise<File> {
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
