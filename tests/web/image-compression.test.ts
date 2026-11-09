// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ATTACHMENT_HARD_LIMIT,
  compressImage,
  IMAGE_COMPRESSION_WORKER_URL,
  IMAGE_COMPRESSION_WORKER_INTEGRITY,
  planAttachmentUpload,
} from "../../lib/image-compression";

const { compress } = vi.hoisted(() => ({ compress: vi.fn() }));
vi.mock("browser-image-compression", () => ({ default: compress }));

function file(size: number, type = "image/png", name = "IMG_1.png"): File {
  return new File([new Uint8Array(size)], name, { type });
}

beforeEach(() => {
  compress.mockReset();
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("upload planning", () => {
  it.each([
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "application/zip",
  ])("passes small %s files through unchanged", (type) => {
    expect(planAttachmentUpload(file(3_999_999, type))).toBe("as-is");
  });

  it.each(["image/png", "image/jpeg", "image/webp"])(
    "compresses %s starting at exactly 4 MB, even above the presign cap",
    (type) => {
      expect(planAttachmentUpload(file(4_000_000, type))).toBe("compress");
      expect(planAttachmentUpload(file(26 * 1024 * 1024, type))).toBe(
        "compress",
      );
    },
  );

  it.each([
    "image/gif",
    "application/zip",
    "application/gpx+xml",
    "application/vnd.garmin.fit",
    "application/vnd.garmin.tcx+xml",
  ])("preserves %s through 4.4 MB and rejects larger files", (type) => {
    expect(planAttachmentUpload(file(4_000_000, type))).toBe("as-is");
    expect(planAttachmentUpload(file(ATTACHMENT_HARD_LIMIT, type))).toBe(
      "as-is",
    );
    expect(planAttachmentUpload(file(ATTACHMENT_HARD_LIMIT + 1, type))).toBe(
      "too-large",
    );
  });
});

describe("compression", () => {
  it.each(["IMG_1.png", "IMG_1.webp", "IMG_1.jpeg", "IMG_1"])(
    "rewrites %s using the real JPEG blob type",
    async (name) => {
      compress.mockResolvedValue(new Blob(["jpeg"], { type: "image/jpeg" }));
      const onProgress = vi.fn();
      const signal = new AbortController().signal;
      const result = await compressImage(file(4_000_000, "image/png", name), {
        onProgress,
        signal,
      });
      expect(result.name).toBe("IMG_1.jpg");
      expect(result.type).toBe("image/jpeg");
      expect(result.size).toBe(4);
      expect(compress).toHaveBeenCalledWith(
        expect.any(File),
        expect.objectContaining({
          fileType: "image/jpeg",
          initialQuality: 0.8,
          maxWidthOrHeight: 2560,
          maxSizeMB: 3_800_000 / (1024 * 1024),
          preserveExif: false,
          useWebWorker: false,
          onProgress,
          signal,
        }),
      );
    },
  );

  it("passes only an integrity-verified blob URL to the worker and revokes it", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("verified script"));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal(
      "URL",
      Object.assign(URL, {
        createObjectURL: vi.fn(() => "blob:verified-worker"),
        revokeObjectURL: vi.fn(),
      }),
    );
    compress.mockResolvedValue(new Blob(["jpeg"], { type: "image/jpeg" }));
    const signal = new AbortController().signal;
    await compressImage(file(4_000_000), { onProgress: vi.fn(), signal });
    expect(fetchMock).toHaveBeenCalledWith(IMAGE_COMPRESSION_WORKER_URL, {
      integrity: IMAGE_COMPRESSION_WORKER_INTEGRITY,
      credentials: "omit",
      signal: expect.any(AbortSignal),
    });
    expect(compress).toHaveBeenCalledWith(
      expect.any(File),
      expect.objectContaining({
        useWebWorker: true,
        libURL: "blob:verified-worker",
      }),
    );
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:verified-worker");
  });

  it("falls back promptly when the CDN stalls, without aborting compression", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(
      (_url, { signal }: { signal: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(signal.reason), {
            once: true,
          });
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    compress.mockResolvedValue(new Blob(["jpeg"], { type: "image/jpeg" }));
    const signal = new AbortController().signal;
    const pending = compressImage(file(4_000_000), {
      onProgress: vi.fn(),
      signal,
    });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await vi.advanceTimersByTimeAsync(3_000);
    await expect(pending).resolves.toHaveProperty("type", "image/jpeg");
    expect(signal.aborted).toBe(false);
    expect(compress).toHaveBeenCalledWith(
      expect.any(File),
      expect.objectContaining({ useWebWorker: false, signal }),
    );
    expect(vi.getTimerCount()).toBe(0);
  });

  it("forwards caller aborts during the CDN fetch and clears its fallback timer", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(
      (_url, { signal }: { signal: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(signal.reason), {
            once: true,
          });
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const pending = compressImage(file(4_000_000), {
      onProgress: vi.fn(),
      signal: controller.signal,
    });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(compress).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("accepts the hard-limit boundary", async () => {
    compress.mockResolvedValue(file(ATTACHMENT_HARD_LIMIT, "image/jpeg"));
    await expect(
      compressImage(file(4_000_000), {
        onProgress: vi.fn(),
        signal: new AbortController().signal,
      }),
    ).resolves.toHaveProperty("size", ATTACHMENT_HARD_LIMIT);
  });

  it.each(["oversize", "throw", "wrong-type"])(
    "rejects %s with an actionable error",
    async (failure) => {
      if (failure === "throw")
        compress.mockRejectedValue(new Error("decoder failed"));
      else
        compress.mockResolvedValue(
          file(
            failure === "oversize" ? ATTACHMENT_HARD_LIMIT + 1 : 4,
            failure === "wrong-type" ? "image/png" : "image/jpeg",
          ),
        );
      await expect(
        compressImage(file(4_000_000), {
          onProgress: vi.fn(),
          signal: new AbortController().signal,
        }),
      ).rejects.toThrow(
        failure === "throw" ? "decoder failed" : "cannot reduce below 4.5 MB",
      );
    },
  );

  it.each(["AbortError", "TimeoutError"])(
    "preserves %s and blocks retries until the underlying decoder settles",
    async (name) => {
      let finish!: (_blob: Blob) => void;
      compress.mockImplementationOnce(
        () =>
          new Promise<Blob>((resolve) => {
            finish = resolve;
          }),
      );
      const controller = new AbortController();
      const pending = compressImage(file(4_000_000), {
        onProgress: vi.fn(),
        signal: controller.signal,
      });
      await vi.waitFor(() => expect(compress).toHaveBeenCalledTimes(1));
      const reason = new DOMException("Compression cancelled", name);
      controller.abort(reason);
      await expect(pending).rejects.toBe(reason);
      for (let retry = 0; retry < 3; retry++) {
        await expect(
          compressImage(file(4_000_000), {
            onProgress: vi.fn(),
            signal: new AbortController().signal,
          }),
        ).rejects.toMatchObject({ name: "ImageCompressionBusyError" });
      }
      expect(compress).toHaveBeenCalledTimes(1);
      finish(new Blob(["jpeg"], { type: "image/jpeg" }));
      // Let the real compression promise (not the timeout race) release its slot.
      await new Promise((resolve) => setTimeout(resolve, 0));
      compress.mockResolvedValue(new Blob(["jpeg"], { type: "image/jpeg" }));
      await expect(
        compressImage(file(4_000_000), {
          onProgress: vi.fn(),
          signal: new AbortController().signal,
        }),
      ).resolves.toHaveProperty("type", "image/jpeg");
      expect(compress).toHaveBeenCalledTimes(2);
    },
  );

  it("does not load compression for an already aborted request", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      compressImage(file(4_000_000), {
        onProgress: vi.fn(),
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(compress).not.toHaveBeenCalled();
  });
});
