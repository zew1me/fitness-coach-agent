import { describe, expect, it } from "vitest";

import { chatRequestBodySchema } from "../../lib/schemas";

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
