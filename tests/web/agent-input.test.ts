import type { UIMessage } from "ai";
import { describe, expect, it } from "vitest";

import { toAgentInputItems } from "../../lib/agent/agent-input";

describe("toAgentInputItems", () => {
  it("preserves text and image attachments in user input", () => {
    const messages: UIMessage[] = [
      {
        id: "message-1",
        role: "user",
        parts: [
          { type: "text", text: "Review this workout." },
          {
            type: "file",
            filename: "workout.png",
            mediaType: "image/png",
            url: "https://files.example/workout.png",
          },
        ],
      },
    ];

    expect(toAgentInputItems(messages)).toEqual([
      {
        role: "user",
        content: [
          { type: "input_text", text: "Review this workout." },
          {
            type: "input_image",
            image: "https://files.example/workout.png",
          },
        ],
      },
    ]);
  });

  it("replays completed assistant tool calls and outputs", () => {
    const messages = [
      {
        id: "message-1",
        role: "assistant",
        parts: [
          {
            type: "tool-get_active_plan",
            toolCallId: "call-1",
            state: "output-available",
            input: {},
            output: { active_plan: null },
          },
          { type: "text", text: "You do not have an active plan." },
        ],
      },
    ] as UIMessage[];

    expect(toAgentInputItems(messages)).toEqual([
      {
        type: "function_call",
        callId: "call-1",
        name: "get_active_plan",
        arguments: "{}",
        status: "completed",
      },
      {
        type: "function_call_result",
        callId: "call-1",
        name: "get_active_plan",
        output: JSON.stringify({ active_plan: null }),
        status: "completed",
      },
      {
        role: "assistant",
        status: "completed",
        content: [
          { type: "output_text", text: "You do not have an active plan." },
        ],
      },
    ]);
  });
});
