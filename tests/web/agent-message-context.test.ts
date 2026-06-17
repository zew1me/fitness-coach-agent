import type { UIMessage } from "ai";
import { describe, expect, it } from "vitest";

import { stripUnsupportedModelFileParts } from "../../lib/agent/message-context";

function userMessage(parts: UIMessage["parts"]): UIMessage {
  return { id: "m1", role: "user", parts };
}

function filePart(
  mediaType: string,
  filename: string,
): UIMessage["parts"][number] {
  return {
    type: "file",
    mediaType,
    filename,
    url: `https://example.com/${filename}`,
  } as unknown as UIMessage["parts"][number];
}

describe("stripUnsupportedModelFileParts", () => {
  it("replaces an activity (GPX) file part with a text notice", () => {
    const original = userMessage([
      { type: "text", text: "Here is my ride" },
      filePart("application/gpx+xml", "morning-run.gpx"),
    ]);

    const result = stripUnsupportedModelFileParts([original]);
    const message = result[0];
    if (!message) throw new Error("expected a message");

    expect(message.parts[0]).toEqual({ type: "text", text: "Here is my ride" });
    const notice = message.parts[1];
    if (!notice || notice.type !== "text")
      throw new Error("expected a text notice");
    expect(notice.text).toContain("morning-run.gpx");
    expect(notice.text).toContain("application/gpx+xml");
    // Original message object is not mutated.
    expect(original.parts[1]).toMatchObject({ type: "file" });
  });

  it("leaves image and pdf file parts untouched and returns the same reference", () => {
    const original = userMessage([
      filePart("image/png", "chart.png"),
      filePart("application/pdf", "plan.pdf"),
    ]);

    const result = stripUnsupportedModelFileParts([original]);

    // Fully supported message is returned by reference (no copy).
    expect(result[0]).toBe(original);
  });

  it("returns the same message reference when nothing needs stripping", () => {
    const original = userMessage([{ type: "text", text: "hello" }]);
    expect(stripUnsupportedModelFileParts([original])[0]).toBe(original);
  });
});
