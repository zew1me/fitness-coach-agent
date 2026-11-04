import { describe, expect, it } from "vitest";

import {
  hasSendableContent,
  type LocalAttachment,
  uploadedFileParts,
} from "../../lib/chat-attachments";

function attachment(status: LocalAttachment["status"]): LocalAttachment {
  return {
    id: status,
    status,
    progress: null,
    content_type: "image/jpeg",
    filename: "photo.jpg",
    object_key: "key",
    preview_url: null,
    public_url: "https://example.com/photo.jpg",
  };
}

describe("sendable attachments", () => {
  it.each(["compressing", "uploading"] as const)(
    "blocks send during %s, even alongside a ready attachment",
    (status) => {
      const pending = attachment(status);
      expect(hasSendableContent("hello", [pending])).toBe(false);
      expect(hasSendableContent("", [pending, attachment("uploaded")])).toBe(
        false,
      );
      expect(
        hasSendableContent("hello", [pending, attachment("uploaded")]),
      ).toBe(false);
    },
  );

  it("allows text or ready files but not failed files alone", () => {
    expect(hasSendableContent("hello", [attachment("error")])).toBe(true);
    expect(hasSendableContent("", [attachment("uploaded")])).toBe(true);
    expect(hasSendableContent("", [attachment("error")])).toBe(false);
  });

  it("sends the prepared name and MIME type to the model", () => {
    expect(
      uploadedFileParts([attachment("uploaded"), attachment("compressing")]),
    ).toEqual([
      {
        type: "file",
        filename: "photo.jpg",
        mediaType: "image/jpeg",
        url: "https://example.com/photo.jpg",
      },
    ]);
  });
});
