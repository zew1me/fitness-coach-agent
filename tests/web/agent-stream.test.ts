import type { RunStreamEvent } from "@openai/agents";
import type { UIMessageStreamWriter } from "ai";
import { describe, expect, it, vi } from "vitest";

import {
  finishAgentText,
  writeAgentStreamEvent,
} from "../../lib/agent/agent-stream";

function makeWriter(): { writer: UIMessageStreamWriter; parts: unknown[] } {
  const parts: unknown[] = [];
  const writer = {
    write: vi.fn((part: unknown) => parts.push(part)),
  } as unknown as UIMessageStreamWriter;
  return { writer, parts };
}

function makeState(): { textId: string; textStarted: boolean } {
  return { textId: "t0", textStarted: false };
}

describe("writeAgentStreamEvent", () => {
  it("emits text-start and text-delta on output_text_delta events", () => {
    const { writer, parts } = makeWriter();
    const state = makeState();

    const event = {
      type: "raw_model_stream_event",
      data: { type: "output_text_delta", delta: "Hello" },
    } as unknown as RunStreamEvent;

    writeAgentStreamEvent(event, writer, state);

    expect(parts).toEqual([
      { type: "text-start", id: "t0" },
      { type: "text-delta", id: "t0", delta: "Hello" },
    ]);
    expect(state.textStarted).toBe(true);
  });

  it("does not emit text-start twice across consecutive deltas", () => {
    const { writer, parts } = makeWriter();
    const state = makeState();

    for (const delta of ["Hel", "lo"]) {
      writeAgentStreamEvent(
        {
          type: "raw_model_stream_event",
          data: { type: "output_text_delta", delta },
        } as unknown as RunStreamEvent,
        writer,
        state,
      );
    }

    expect(
      parts.filter((p) => (p as { type: string }).type === "text-start"),
    ).toHaveLength(1);
    expect(
      parts.filter((p) => (p as { type: string }).type === "text-delta"),
    ).toHaveLength(2);
  });

  it("ignores events with other data types", () => {
    const { writer, parts } = makeWriter();
    const state = makeState();

    writeAgentStreamEvent(
      {
        type: "raw_model_stream_event",
        data: { type: "response.output_text.delta", delta: "oops" },
      } as unknown as RunStreamEvent,
      writer,
      state,
    );

    expect(parts).toHaveLength(0);
  });

  it("skips empty delta strings", () => {
    const { writer, parts } = makeWriter();
    const state = makeState();

    writeAgentStreamEvent(
      {
        type: "raw_model_stream_event",
        data: { type: "output_text_delta", delta: "" },
      } as unknown as RunStreamEvent,
      writer,
      state,
    );

    expect(parts).toHaveLength(0);
  });
});

describe("finishAgentText", () => {
  it("emits text-end when text has started", () => {
    const { writer, parts } = makeWriter();
    const state = { textId: "t0", textStarted: true };

    finishAgentText(writer, state);

    expect(parts).toEqual([{ type: "text-end", id: "t0" }]);
  });

  it("is a no-op when text has not started", () => {
    const { writer, parts } = makeWriter();
    const state = makeState();

    finishAgentText(writer, state);

    expect(parts).toHaveLength(0);
  });
});
