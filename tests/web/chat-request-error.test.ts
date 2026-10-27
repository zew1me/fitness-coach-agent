import { describe, expect, it } from "vitest";

import {
  ChatRequestError,
  chatRequestErrorFromResponse,
} from "../../lib/chat-request-error";

describe("chatRequestErrorFromResponse", () => {
  it("uses a plain-text body as the message", async () => {
    const error = await chatRequestErrorFromResponse(
      new Response("Coach is out to lunch. Please try again later.", {
        status: 503,
      }),
    );

    expect(error).toBeInstanceOf(ChatRequestError);
    expect(error.message).toBe(
      "Coach is out to lunch. Please try again later.",
    );
  });

  it("unwraps the error field of a JSON body", async () => {
    const error = await chatRequestErrorFromResponse(
      Response.json({ error: "Invalid request body." }, { status: 400 }),
    );

    expect(error.message).toBe("Invalid request body.");
  });

  it("shows a JSON body without a string error field as text", async () => {
    const error = await chatRequestErrorFromResponse(
      Response.json({ detail: "nope" }, { status: 500 }),
    );

    expect(error.message).toBe('{"detail":"nope"}');
  });

  it("falls back to a generic message for an empty body", async () => {
    const error = await chatRequestErrorFromResponse(
      new Response("   ", { status: 502 }),
    );

    expect(error.message).toBe("Unable to send your message.");
  });
});
