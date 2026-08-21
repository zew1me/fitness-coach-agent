/** @vitest-environment jsdom */

import { act, renderHook } from "@testing-library/react";
import React, { type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ChatTurnLeaseProvider,
  LEASE_STATUS_POLL_INTERVAL_MS,
  useChatTurnLease,
} from "../../components/chat-turn-lease-provider";
import { loadChatTurnLeaseStatus } from "../../lib/coach-api";

vi.mock("../../lib/coach-api", () => ({
  loadChatTurnLeaseStatus: vi.fn(),
}));

const loadLeaseStatusMock = vi.mocked(loadChatTurnLeaseStatus);
vi.stubGlobal("React", React);

function wrapper({ children }: Readonly<{ children: ReactNode }>): ReactNode {
  return <ChatTurnLeaseProvider>{children}</ChatTurnLeaseProvider>;
}

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("useChatTurnLease", () => {
  it("keeps send blocked and retries when the lease status cannot be loaded", async () => {
    vi.useFakeTimers();
    loadLeaseStatusMock
      .mockRejectedValueOnce(new Error("temporary outage"))
      .mockResolvedValue({ expires_at: null, in_flight: false });

    const { result } = renderHook(() => useChatTurnLease("athlete-1"), {
      wrapper,
    });

    await act(async () => Promise.resolve());
    expect(loadLeaseStatusMock).toHaveBeenCalledOnce();
    expect(result.current.turnInFlight).toBe(true);

    await act(() => vi.advanceTimersByTimeAsync(LEASE_STATUS_POLL_INTERVAL_MS));

    expect(loadLeaseStatusMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(result.current.turnInFlight).toBe(false);
  });
});
