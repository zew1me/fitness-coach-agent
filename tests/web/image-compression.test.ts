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
      signal,
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
      ).rejects.toThrow("cannot reduce below 4.5 MB");
    },
  );

  it("bounds a stalled decoder with the signal", async () => {
    compress.mockImplementation(() => new Promise(() => {}));
    const controller = new AbortController();
    const result = compressImage(file(4_000_000), {
      onProgress: vi.fn(),
      signal: controller.signal,
    });
    await vi.waitFor(() => expect(compress).toHaveBeenCalled());
    controller.abort();
    await expect(result).rejects.toThrow("cannot reduce below 4.5 MB");
  });

  it("does not load compression for an already aborted request", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      compressImage(file(4_000_000), {
        onProgress: vi.fn(),
        signal: controller.signal,
      }),
    ).rejects.toThrow("cannot reduce below 4.5 MB");
    expect(compress).not.toHaveBeenCalled();
  });
});
