// Decimal MB deliberately leave room for multipart framing below Vercel's 4.5 MB cap.
export const IMAGE_COMPRESSION_THRESHOLD = 4_000_000;
export const ATTACHMENT_HARD_LIMIT = 4_400_000;
const COMPRESSION_TARGET_BYTES = 3_800_000;
const CANNOT_REDUCE_MESSAGE =
  "That file cannot reduce below 4.5 MB. Try a smaller file.";

export const IMAGE_COMPRESSION_WORKER_URL =
  "https://cdn.jsdelivr.net/npm/browser-image-compression@2.0.2/dist/browser-image-compression.js";
export const IMAGE_COMPRESSION_WORKER_INTEGRITY =
  "sha384-dHP9fwqd9BAiDh9uJ0p10khgbbcFMh34bVEiCnJ1Ah/AT2T2k4t572VEo3WXzxXp";

type UploadPlan = "as-is" | "compress" | "too-large";

export function planAttachmentUpload(file: File): UploadPlan {
  if (file.size < IMAGE_COMPRESSION_THRESHOLD) return "as-is";
  if (["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    return "compress";
  }
  return file.size > ATTACHMENT_HARD_LIMIT ? "too-large" : "as-is";
}

export class AttachmentSizeError extends Error {
  constructor() {
    super(CANNOT_REDUCE_MESSAGE);
    this.name = "AttachmentSizeError";
  }
}

export function attachmentSizeError(): Error {
  return new AttachmentSizeError();
}

async function verifiedWorkerURL(
  signal: AbortSignal,
): Promise<string | undefined> {
  const controller = new AbortController();
  const onAbort = (): void => controller.abort(signal.reason);
  signal.addEventListener("abort", onAbort, { once: true });
  if (signal.aborted) onAbort();
  // Reserve most of the 60 s compression budget for a safe main-thread fallback.
  const timer = setTimeout(() => controller.abort(), 3_000);
  try {
    // Avoid shipping a second worker copy within Vercel deployment size budgets
    // (2.0.2's script is 57,069 bytes; ~860 KB is the whole installed package).
    // Pin both version AND SHA-384: fetch verifies integrity before any code runs.
    // importScripts has no SRI option, so the worker gets only this verified blob.
    const response = await fetch(IMAGE_COMPRESSION_WORKER_URL, {
      integrity: IMAGE_COMPRESSION_WORKER_INTEGRITY,
      credentials: "omit",
      signal: controller.signal,
    });
    if (!response.ok) return undefined;
    return URL.createObjectURL(
      new Blob([await response.arrayBuffer()], { type: "text/javascript" }),
    );
  } catch {
    signal.throwIfAborted();
    // Offline/CORS/SRI failures use our bundled dependency on the main thread;
    // never let the library fall back to its default, unverified CDN URL.
    return undefined;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", onAbort);
  }
}

// Share a FIFO across separate picks, drops, pastes and draft instances. Advance
// only after actual work settles: the library's main-thread decoder cannot be
// cancelled, so a caller timing out must not let another decode overlap it.
let compressionTail: Promise<void> = Promise.resolve();

async function compress(
  file: File,
  onProgress: (_progress: number) => void,
  signal: AbortSignal,
): Promise<File> {
  signal.throwIfAborted();
  // Loading lazily keeps the large codec bundle out of small uploads and SSR.
  const { default: imageCompression } =
    await import("browser-image-compression");
  signal.throwIfAborted();
  const workerURL = await verifiedWorkerURL(signal);
  let result: Blob;
  try {
    signal.throwIfAborted();
    result = await imageCompression(file, {
      fileType: "image/jpeg",
      initialQuality: 0.8,
      maxWidthOrHeight: 2560,
      // The library interprets maxSizeMB as MiB, unlike our request byte limits.
      maxSizeMB: COMPRESSION_TARGET_BYTES / (1024 * 1024),
      onProgress,
      preserveExif: false,
      signal,
      useWebWorker: workerURL !== undefined,
      ...(workerURL === undefined ? {} : { libURL: workerURL }),
    });
  } finally {
    if (workerURL !== undefined) URL.revokeObjectURL(workerURL);
  }
  signal.throwIfAborted();
  if (result.size > ATTACHMENT_HARD_LIMIT) throw attachmentSizeError();
  // Never label a browser's unexpected canvas fallback as JPEG.
  if (result.type !== "image/jpeg") throw attachmentSizeError();
  const name = `${file.name.replace(/\.[^.]*$/, "")}.jpg`;
  return new File([result], name, {
    type: result.type,
    lastModified: file.lastModified,
  });
}

export async function compressImage(
  file: File,
  {
    onProgress,
    signal,
  }: {
    onProgress: (_progress: number) => void;
    signal: AbortSignal;
  },
): Promise<File> {
  let onAbort = (): void => {};
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = (): void => reject(signal.reason);
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
  });
  try {
    // Enqueue before import/fetch to preserve invocation order. Canceled queued
    // work fails the first signal check in compress(), before loading or decoding.
    const work = compressionTail.then(() => compress(file, onProgress, signal));
    // Recover failures so one bad file cannot poison subsequent preparations.
    compressionTail = work.then(
      () => {},
      () => {},
    );
    // The existing timeout includes queue waiting, but never advances the tail.
    return await Promise.race([work, aborted]);
  } finally {
    signal.removeEventListener("abort", onAbort);
  }
}
