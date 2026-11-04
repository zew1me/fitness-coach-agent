// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { uploadedFileParts } from "../../lib/chat-attachments";
import { useChatDraft } from "../../lib/use-chat-draft";

const mocks = vi.hoisted(() => ({
  compress: vi.fn(),
  loaded: vi.fn(),
  presign: vi.fn(),
  upload: vi.fn(),
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
  logger: { info: vi.fn(), error: vi.fn() },
}));

beforeEach(() => {
  mocks.compress.mockReset();
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
      expect.stringContaining("cannot reduce below 4.5 MB"),
    );
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
