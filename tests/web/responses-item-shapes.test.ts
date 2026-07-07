import { describe, expect, it } from "vitest";

import { toResponsesCompactInputItem } from "../../lib/agent/responses-item-shapes";

describe("toResponsesCompactInputItem", () => {
  it("normalizes to a single call_id key when both callId and call_id are present", () => {
    const result = toResponsesCompactInputItem({
      type: "function_call",
      callId: "call-1",
      call_id: "call-1",
      name: "some_tool",
      arguments: "{}",
    }) as unknown as Record<string, unknown>;

    expect(result["call_id"]).toBe("call-1");
    expect("callId" in result).toBe(false);
  });

  it("converts a reasoning item lacking an id into the compact summary shape", () => {
    const result = toResponsesCompactInputItem({
      type: "reasoning",
      content: [{ type: "input_text", text: "thinking about it" }],
    }) as unknown as Record<string, unknown>;

    expect(result["summary"]).toEqual([
      { type: "summary_text", text: "thinking about it" },
    ]);
    expect("content" in result).toBe(false);
    expect("id" in result).toBe(false);
  });
});
