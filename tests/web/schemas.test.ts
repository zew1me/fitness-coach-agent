import { describe, expect, it } from "vitest";

import { chatMessageSchema, chatRequestBodySchema } from "../../lib/schemas";

const baseMessage = {
  attachments: [],
  created_at: "2026-01-01T00:00:00Z",
  id: "msg-1",
  metadata: {},
  role: "user",
  thread_id: "thread-1",
  user_id: "user-1",
};

describe("chatMessageSchema", () => {
  it("accepts a message that already has parts", () => {
    const result = chatMessageSchema.parse({
      ...baseMessage,
      content: "hello",
      parts: [{ type: "text", text: "hello" }],
    });
    expect(result.parts).toEqual([{ type: "text", text: "hello" }]);
  });

  it("reconstructs parts from content for legacy rows missing parts", () => {
    const result = chatMessageSchema.parse({
      ...baseMessage,
      content: "legacy text",
    });
    expect(result.parts).toEqual([{ type: "text", text: "legacy text" }]);
  });

  it("produces empty parts for legacy rows with no content", () => {
    const result = chatMessageSchema.parse({
      ...baseMessage,
      content: "",
    });
    expect(result.parts).toEqual([]);
  });

  it("produces empty parts for a legacy row with no parts field and no content", () => {
    const result = chatMessageSchema.parse({ ...baseMessage });
    expect(result.parts).toEqual([]);
  });
});

describe("chatRequestBodySchema", () => {
  it("rejects request bodies that include both single-message and history forms", () => {
    const message = {
      id: "message-1",
      role: "user",
      parts: [{ type: "text", text: "hello" }],
    };

    expect(() =>
      chatRequestBodySchema.parse({ message, messages: [message] }),
    ).toThrow();
  });
});
