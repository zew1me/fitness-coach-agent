import type { UIMessage } from "ai";
import { describe, expect, it } from "vitest";

import { toAgentInputItems } from "../../lib/agent/agent-input";

describe("toAgentInputItems", () => {
  it("replays errored assistant tool calls with error payload", () => {
    const messages = [
      {
        id: "message-err",
        role: "assistant",
        parts: [
          {
            type: "tool-get_active_plan",
            toolCallId: "call-err-1",
            state: "output-error",
            input: {},
            errorText: "Upstream timeout",
          },
        ],
      },
    ] as UIMessage[];
    expect(toAgentInputItems(messages)).toEqual([
      {
        type: "function_call",
        callId: "call-err-1",
        name: "get_active_plan",
        arguments: "{}",
        status: "completed",
      },
      {
        type: "function_call_result",
        callId: "call-err-1",
        name: "get_active_plan",
        output: JSON.stringify({ error: "Upstream timeout" }),
        status: "completed",
      },
    ]);
  });

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

  it("returns an empty array for an empty message list", () => {
    expect(toAgentInputItems([])).toEqual([]);
  });

  it("skips user messages whose text parts are all empty", () => {
    const messages: UIMessage[] = [
      {
        id: "message-1",
        role: "user",
        parts: [{ type: "text", text: "" }],
      },
    ];
    expect(toAgentInputItems(messages)).toEqual([]);
  });

  it("converts a non-image file attachment to input_file", () => {
    const messages: UIMessage[] = [
      {
        id: "message-1",
        role: "user",
        parts: [
          {
            type: "file",
            filename: "activity.gpx",
            mediaType: "application/gpx+xml",
            url: "https://files.example/activity.gpx",
          },
        ],
      },
    ];

    expect(toAgentInputItems(messages)).toEqual([
      {
        role: "user",
        content: [
          {
            type: "input_file",
            file: { url: "https://files.example/activity.gpx" },
            filename: "activity.gpx",
          },
        ],
      },
    ]);
  });

  it("concatenates multiple text parts in a system message", () => {
    const messages = [
      {
        id: "system-1",
        role: "system",
        parts: [
          { type: "text", text: "You are a coach." },
          { type: "text", text: "Be concise." },
        ],
      },
    ] as UIMessage[];

    expect(toAgentInputItems(messages)).toEqual([
      { role: "system", content: "You are a coach.\nBe concise." },
    ]);
  });

  it("emits only function_call (in_progress) for tool parts without output", () => {
    const messages = [
      {
        id: "message-1",
        role: "assistant",
        parts: [
          {
            type: "tool-get_active_plan",
            toolCallId: "call-1",
            state: "input-available",
            input: {},
          },
        ],
      },
    ] as UIMessage[];

    expect(toAgentInputItems(messages)).toEqual([
      {
        type: "function_call",
        callId: "call-1",
        name: "get_active_plan",
        arguments: "{}",
        status: "in_progress",
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
