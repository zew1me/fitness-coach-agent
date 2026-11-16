// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../lib/types";
import {
  MESSAGE_RENDER_BATCH_SIZE,
  useMessageWindow,
} from "../../lib/use-message-window";

afterEach(() => {
  cleanup();
});

function messages(threadId: string, count: number): ChatMessage[] {
  return Array.from({ length: count }, (_, index) => ({
    attachments: [],
    created_at: "2026-11-13T00:00:00Z",
    id: `${threadId}-${index}`,
    metadata: {},
    parts: [],
    role: "user" as const,
    thread_id: threadId,
    user_id: "user-1",
  }));
}

const messageCount = MESSAGE_RENDER_BATCH_SIZE + 40;

describe("useMessageWindow", () => {
  it("resets the window in the first render after a thread switch", () => {
    const hiddenByThread: Array<[string, number]> = [];
    const { result, rerender } = renderHook(
      ({ threadId }: { threadId: string }) => {
        const messageWindow = useMessageWindow({
          messages: messages(threadId, messageCount),
          persistedMessageCount: messageCount,
          sending: false,
          threadId,
          olderRequestVersionRef: { current: 0 },
          fetchOlderMessages: vi.fn(() => Promise.resolve(0)),
          loadingOlder: false,
          olderAvailable: false,
          setThreadError: vi.fn(),
        });
        hiddenByThread.push([threadId, messageWindow.hiddenMessageCount]);
        return messageWindow;
      },
      { initialProps: { threadId: "thread-a" } },
    );
    expect(result.current.hiddenMessageCount).toBe(40);

    act(() => {
      result.current.showMore();
    });
    expect(result.current.hiddenMessageCount).toBe(0);

    rerender({ threadId: "thread-b" });

    expect(result.current.hiddenMessageCount).toBe(40);
    // No render of thread-b may use thread-a's expanded window.
    const threadBRenders = hiddenByThread.filter(([id]) => id === "thread-b");
    expect(threadBRenders.length).toBeGreaterThan(0);
    expect(threadBRenders.every(([, hidden]) => hidden === 40)).toBe(true);

    // Returning to thread-a starts from the default batch again, not from the
    // window it had expanded before the switch.
    rerender({ threadId: "thread-a" });
    expect(result.current.hiddenMessageCount).toBe(40);
  });
});
