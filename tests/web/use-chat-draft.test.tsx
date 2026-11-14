// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { uploadedFileParts } from "../../lib/chat-attachments";
import { uploadRequestSchema } from "../../lib/schemas";
import { useChatDraft } from "../../lib/use-chat-draft";

const mocks = vi.hoisted(() => ({
  compress: vi.fn(),
  loaded: vi.fn(),
  presign: vi.fn(),
  upload: vi.fn(),
  logError: vi.fn(),
}));
vi.mock("browser-image-compression", () => {
  mocks.loaded();
  return { default: mocks.compress };
});
vi.mock("../../lib/coach-api", () => ({
  createChatUploadIntent: mocks.presign,
  uploadFile: mocks.upload,
}));
vi.mock("@sentry/nextjs", () => ({
  logger: { info: vi.fn(), error: mocks.logError },
}));

beforeEach(() => {
  mocks.compress.mockReset();
  mocks.logError.mockReset();
  mocks.presign.mockReset().mockResolvedValue({ object_key: "key" });
  mocks.upload.mockReset().mockResolvedValue({
    object_key: "key",
    public_url: "https://example.com/photo.jpg",
  });
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  vi.stubGlobal(
    "URL",
    Object.assign(URL, {
      createObjectURL: vi.fn(() => "blob:preview"),
      revokeObjectURL: vi.fn(),
    }),
  );
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("uploads small files as-is without loading the library", async () => {
  const { result } = renderHook(() => useChatDraft(vi.fn()));
  const file = new File(["small"], "photo.png", { type: "image/png" });
  await act(() => result.current.handleFilesAdded([file]));
  expect(mocks.loaded).not.toHaveBeenCalled();
  expect(mocks.presign).toHaveBeenCalledWith({
    content_length: file.size,
    content_type: "image/png",
    filename: "photo.png",
    purpose: "chat-attachment",
  });
  expect(mocks.upload.mock.calls[0]?.[1]).toBe(file);
});

it("shows progress, patches metadata, then presigns and uploads the prepared file", async () => {
  const timeout = vi.spyOn(AbortSignal, "timeout");
  let finish!: (_file: Blob) => void;
  mocks.compress.mockImplementation((_file, options) => {
    options.onProgress(42);
    return new Promise<Blob>((resolve) => {
      finish = resolve;
    });
  });
  const { result } = renderHook(() => useChatDraft(vi.fn()));
  const file = new File([new Uint8Array(26 * 1024 * 1024)], "IMG_1.png", {
    type: "image/png",
  });
  let pending!: Promise<void>;
  await act(async () => {
    pending = result.current.handleFilesAdded([file]);
    await vi.waitFor(() => expect(mocks.compress).toHaveBeenCalled());
  });
  expect(result.current.attachments[0]).toMatchObject({
    status: "compressing",
    progress: 42,
  });
  expect(mocks.presign).not.toHaveBeenCalled();
  expect(timeout).toHaveBeenCalledWith(60_000);
  expect(timeout).not.toHaveBeenCalledWith(20_000);
  await act(async () => {
    finish(new Blob(["jpeg"], { type: "image/jpeg" }));
    await pending;
  });
  expect(mocks.presign).toHaveBeenCalledWith({
    content_length: 4,
    content_type: "image/jpeg",
    filename: "IMG_1.jpg",
    purpose: "chat-attachment",
  });
  expect(result.current.attachments[0]).toMatchObject({
    status: "uploaded",
    progress: null,
    content_type: "image/jpeg",
    filename: "IMG_1.jpg",
  });
  expect(timeout).toHaveBeenLastCalledWith(20_000);
  expect(mocks.upload.mock.calls[0]?.[1]).toMatchObject({
    name: "IMG_1.jpg",
    type: "image/jpeg",
    size: 4,
  });
  expect(uploadedFileParts(result.current.attachments)[0]).toMatchObject({
    filename: "IMG_1.jpg",
    mediaType: "image/jpeg",
  });
});

it("queues large images from separate additions instead of failing the second one", async () => {
  const releases: Array<(_blob: Blob) => void> = [];
  mocks.compress.mockImplementation((_file, options) => {
    options.onProgress(42);
    return new Promise<Blob>((resolve) => {
      releases.push(resolve);
    });
  });
  const error = vi.fn();
  const { result } = renderHook(() => useChatDraft(error));
  let first!: Promise<void>;
  let second!: Promise<void>;
  await act(async () => {
    first = result.current.handleFilesAdded([
      new File([new Uint8Array(4_000_000)], "first.png", { type: "image/png" }),
    ]);
    await vi.waitFor(() => expect(mocks.compress).toHaveBeenCalledTimes(1));
  });
  await act(async () => {
    second = result.current.handleFilesAdded([
      new File([new Uint8Array(4_000_000)], "second.png", {
        type: "image/png",
      }),
    ]);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  expect(mocks.compress).toHaveBeenCalledTimes(1);
  expect(
    result.current.attachments.map(({ status, progress }) => ({
      status,
      progress,
    })),
  ).toEqual([
    { status: "compressing", progress: 42 },
    { status: "compressing", progress: null },
  ]);
  expect(error).not.toHaveBeenCalled();
  await act(async () => {
    releases[0]!(new Blob(["jpeg"], { type: "image/jpeg" }));
    await first;
    await vi.waitFor(() => expect(mocks.compress).toHaveBeenCalledTimes(2));
    releases[1]!(new Blob(["jpeg"], { type: "image/jpeg" }));
    await second;
  });
  expect(
    result.current.attachments.map(({ filename, status }) => ({
      filename,
      status,
    })),
  ).toEqual([
    { filename: "first.jpg", status: "uploaded" },
    { filename: "second.jpg", status: "uploaded" },
  ]);
  expect(mocks.presign.mock.calls.map(([payload]) => payload.filename)).toEqual(
    ["first.jpg", "second.jpg"],
  );
  expect(error).not.toHaveBeenCalled();
});

it.each(["too-large", "throw", "still-large"])(
  "never presigns or uploads a %s failure",
  async (failure) => {
    mocks.compress.mockRejectedValue(new Error("decode failed"));
    if (failure === "still-large")
      mocks.compress.mockResolvedValue(
        new Blob([new Uint8Array(4_400_001)], { type: "image/jpeg" }),
      );
    const error = vi.fn();
    const { result } = renderHook(() => useChatDraft(error));
    const file = new File(
      [new Uint8Array(4_400_001)],
      failure === "too-large" ? "ride.zip" : "photo.png",
      { type: failure === "too-large" ? "application/zip" : "image/png" },
    );
    await act(() => result.current.handleFilesAdded([file]));
    expect(mocks.presign).not.toHaveBeenCalled();
    expect(mocks.upload).not.toHaveBeenCalled();
    expect(result.current.attachments[0]).toMatchObject({
      status: "error",
      progress: null,
    });
    expect(error).toHaveBeenCalledWith(
      expect.stringContaining(
        failure === "throw"
          ? "couldn't decode or optimize"
          : "cannot reduce below 4.5 MB",
      ),
    );
  },
);

it("logs the original byte size when compression fails before the prepared event", async () => {
  mocks.compress.mockRejectedValue(new Error("decoder failed"));
  const { result } = renderHook(() => useChatDraft(vi.fn()));
  const file = new File([new Uint8Array(4_000_000)], "photo.png", {
    type: "image/png",
  });
  await act(() => result.current.handleFilesAdded([file]));
  expect(mocks.logError).toHaveBeenCalledWith("chat attachment upload failed", {
    filename_suffix: ".png",
    content_type: "image/png",
    original_size: file.size,
    failure_stage: "preparation",
    failure_reason: "decoder failed",
  });
  expect(mocks.presign).not.toHaveBeenCalled();
});

it.each(["compression", "upload"])(
  "reports a %s timeout with the correct stage and recovery advice",
  async (stage) => {
    const controller = new AbortController();
    const reason = new DOMException("Operation timed out", "TimeoutError");
    vi.spyOn(AbortSignal, "timeout").mockReturnValue(controller.signal);
    let finish!: (_blob: Blob) => void;
    if (stage === "compression") {
      mocks.compress.mockImplementation(
        () =>
          new Promise<Blob>((resolve) => {
            finish = resolve;
          }),
      );
    } else {
      mocks.upload.mockRejectedValue(reason);
    }
    const error = vi.fn();
    const { result } = renderHook(() => useChatDraft(error));
    const file = new File(
      [new Uint8Array(stage === "compression" ? 4_000_000 : 4)],
      "photo.png",
      { type: "image/png" },
    );
    await act(async () => {
      const pending = result.current.handleFilesAdded([file]);
      if (stage === "compression") {
        await vi.waitFor(() => expect(mocks.compress).toHaveBeenCalled());
        controller.abort(reason);
      }
      await pending;
      if (stage === "compression")
        finish(new Blob(["jpeg"], { type: "image/jpeg" }));
    });
    expect(error).toHaveBeenCalledWith(
      expect.stringContaining(
        stage === "compression"
          ? "Optimizing that image took too long"
          : "Check your connection",
      ),
    );
    expect(mocks.logError).toHaveBeenCalledWith(
      "chat attachment upload failed",
      expect.objectContaining({
        failure_stage: stage === "compression" ? "preparation" : "upload",
        failure_reason: reason.message,
      }),
    );
    expect(result.current.attachments[0]).toMatchObject({
      status: "error",
      progress: null,
    });
    if (stage === "compression") expect(mocks.presign).not.toHaveBeenCalled();
  },
);

it("shows readable presign validation errors but retains server error details", async () => {
  mocks.presign.mockImplementation((payload) =>
    uploadRequestSchema.parseAsync(payload),
  );
  const error = vi.fn();
  const { result } = renderHook(() => useChatDraft(error));
  await act(() =>
    result.current.handleFilesAdded([
      new File([], "empty.zip", { type: "application/zip" }),
    ]),
  );
  expect(error).toHaveBeenLastCalledWith(
    "That attachment couldn't be uploaded. Choose a non-empty file with a valid filename and try again.",
  );
  expect(mocks.upload).not.toHaveBeenCalled();
  expect(result.current.attachments[0]?.status).toBe("error");
  mocks.presign.mockRejectedValue(
    new Error("Storage is temporarily unavailable"),
  );
  await act(() =>
    result.current.handleFilesAdded([
      new File(["zip"], "ride.zip", { type: "application/zip" }),
    ]),
  );
  expect(error).toHaveBeenLastCalledWith("Storage is temporarily unavailable");
});

it("rejects an oversized ZIP immediately while an earlier image is still compressing", async () => {
  let finish!: (_file: Blob) => void;
  mocks.compress.mockImplementation(
    () =>
      new Promise<Blob>((resolve) => {
        finish = resolve;
      }),
  );
  const error = vi.fn();
  const { result } = renderHook(() => useChatDraft(error));
  let pending!: Promise<void>;
  await act(async () => {
    pending = result.current.handleFilesAdded([
      new File([new Uint8Array(4_000_000)], "photo.png", { type: "image/png" }),
      new File([new Uint8Array(4_400_001)], "ride.zip", {
        type: "application/zip",
      }),
    ]);
    await vi.waitFor(() => expect(mocks.compress).toHaveBeenCalled());
  });
  expect(error).toHaveBeenCalledWith(
    expect.stringContaining("cannot reduce below 4.5 MB"),
  );
  expect(result.current.attachments.map(({ status }) => status)).toEqual([
    "compressing",
    "error",
  ]);
  expect(mocks.presign).not.toHaveBeenCalled();
  await act(async () => {
    finish(new Blob(["jpeg"], { type: "image/jpeg" }));
    await pending;
  });
  expect(mocks.presign).toHaveBeenCalledTimes(1);
  expect(mocks.upload).toHaveBeenCalledTimes(1);
});

it("cancels active preparation on unmount and never uploads the discarded image", async () => {
  let finish!: (_blob: Blob) => void;
  let preparationSignal!: AbortSignal;
  let progress!: (_progress: number) => void;
  mocks.compress.mockImplementation((_file, options) => {
    preparationSignal = options.signal;
    progress = options.onProgress;
    return new Promise<Blob>((resolve) => {
      finish = resolve;
    });
  });
  const error = vi.fn();
  const { result, unmount } = renderHook(() => useChatDraft(error));
  let pending!: Promise<void>;
  await act(async () => {
    pending = result.current.handleFilesAdded([
      new File([new Uint8Array(4_000_000)], "discard.png", {
        type: "image/png",
      }),
    ]);
    await vi.waitFor(() => expect(mocks.compress).toHaveBeenCalledTimes(1));
  });
  unmount();
  await pending;
  expect(preparationSignal.aborted).toBe(true);
  progress(80);
  finish(new Blob(["jpeg"], { type: "image/jpeg" }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(mocks.presign).not.toHaveBeenCalled();
  expect(mocks.upload).not.toHaveBeenCalled();
  expect(error).not.toHaveBeenCalled();
  expect(mocks.logError).not.toHaveBeenCalled();
});

it("removes a discarded queued draft without decoding it or blocking a new draft", async () => {
  let finish!: (_blob: Blob) => void;
  mocks.compress.mockImplementationOnce(
    () =>
      new Promise<Blob>((resolve) => {
        finish = resolve;
      }),
  );
  mocks.compress.mockResolvedValue(new Blob(["jpeg"], { type: "image/jpeg" }));
  const active = renderHook(() => useChatDraft(vi.fn()));
  const discarded = renderHook(() => useChatDraft(vi.fn()));
  const photo = (name: string): File =>
    new File([new Uint8Array(4_000_000)], name, { type: "image/png" });
  let first!: Promise<void>;
  let queued!: Promise<void>;
  await act(async () => {
    first = active.result.current.handleFilesAdded([photo("first.png")]);
    await vi.waitFor(() => expect(mocks.compress).toHaveBeenCalledTimes(1));
    queued = discarded.result.current.handleFilesAdded([photo("discard.png")]);
  });
  discarded.unmount();
  await queued;
  await act(async () => {
    finish(new Blob(["jpeg"], { type: "image/jpeg" }));
    await first;
    await active.result.current.handleFilesAdded([photo("next.png")]);
  });
  expect(mocks.compress.mock.calls.map(([input]) => input.name)).toEqual([
    "first.png",
    "next.png",
  ]);
  expect(mocks.presign.mock.calls.map(([payload]) => payload.filename)).toEqual(
    ["first.jpg", "next.jpg"],
  );
});

it.each(["presign", "upload"])(
  "stops discarded work during %s without reporting an error",
  async (stage) => {
    let finish!: () => void;
    let uploadSignal: AbortSignal | undefined;
    const response = {
      object_key: "key",
      public_url: "https://example.com/photo.png",
    };
    if (stage === "presign")
      mocks.presign.mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = (): void => resolve(response);
          }),
      );
    else
      mocks.upload.mockImplementation((_key, _file, _fetch, signal) => {
        uploadSignal = signal;
        return new Promise((resolve) => {
          finish = (): void => resolve(response);
        });
      });
    const error = vi.fn();
    const { result, unmount } = renderHook(() => useChatDraft(error));
    let pending!: Promise<void>;
    await act(async () => {
      pending = result.current.handleFilesAdded([
        new File(["png"], "photo.png", { type: "image/png" }),
      ]);
      await vi.waitFor(() =>
        expect(
          stage === "presign" ? mocks.presign : mocks.upload,
        ).toHaveBeenCalledTimes(1),
      );
    });
    unmount();
    if (stage === "upload") expect(uploadSignal?.aborted).toBe(true);
    finish();
    await pending;
    if (stage === "presign") expect(mocks.upload).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
    expect(mocks.logError).not.toHaveBeenCalled();
  },
);

it("keeps GIFs and activity files that already fit unchanged", async () => {
  const { result } = renderHook(() => useChatDraft(vi.fn()));
  for (const [name, type] of [
    ["animation.gif", "image/gif"],
    ["ride.fit", ""],
  ] as const) {
    const file = new File([new Uint8Array(4_400_000)], name, { type });
    await act(() => result.current.handleFilesAdded([file]));
    expect(mocks.upload).toHaveBeenLastCalledWith(
      "key",
      file,
      undefined,
      expect.any(AbortSignal),
    );
  }
  expect(mocks.compress).not.toHaveBeenCalled();
});
