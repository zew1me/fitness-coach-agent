// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LocalAttachment } from "../../lib/chat-attachments";
import { useChatDraft } from "../../lib/use-chat-draft";

afterEach(() => {
  cleanup();
});

function attachment(id: string): LocalAttachment {
  return {
    id,
    content_type: "application/gpx+xml",
    filename: `${id}.gpx`,
    object_key: `uploads/${id}.gpx`,
    preview_url: null,
    public_url: null,
    status: "uploaded",
  };
}

describe("useChatDraft latest-draft mirrors", () => {
  // The failed-send restore path (use-chat-send) reads these refs after an
  // awaited request to decide whether the athlete started a new draft. They
  // must track every committed draft change.
  it("track committed composer and attachment changes", () => {
    const { result } = renderHook(() => useChatDraft(vi.fn()));
    expect(result.current.latestComposerRef.current).toBe("");
    expect(result.current.latestAttachmentsRef.current).toEqual([]);

    const next = [attachment("a")];
    act(() => {
      result.current.setComposer("new draft");
      result.current.setAttachments(next);
    });
    expect(result.current.latestComposerRef.current).toBe("new draft");
    expect(result.current.latestAttachmentsRef.current).toBe(next);

    act(() => {
      result.current.setComposer("");
      result.current.setAttachments([]);
    });
    expect(result.current.latestComposerRef.current).toBe("");
    expect(result.current.latestAttachmentsRef.current).toEqual([]);
  });
});
