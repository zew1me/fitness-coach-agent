import type { UIMessage } from "ai";

const MODEL_RECENT_MESSAGE_LIMIT = 24;
const EXTRACTED_IMAGE_PREFIX = "Extracted image content from ";

type ImageExtraction = {
  data: unknown;
  screenshot_type: string;
};

type ImageExtractionRequest = {
  filename: string;
  imageUrl: string;
  mediaType: string;
};

type ImageExtractor = (
  request: ImageExtractionRequest,
) => Promise<ImageExtraction | null>;

function partRecord(part: UIMessage["parts"][number]): Record<string, unknown> {
  return part as unknown as Record<string, unknown>;
}

function imageFilePart(
  part: UIMessage["parts"][number],
): ImageExtractionRequest | null {
  const record = partRecord(part);
  const type = record["type"];
  const mediaType = record["mediaType"];
  const url = record["url"];

  if (
    type !== "file" ||
    typeof mediaType !== "string" ||
    !mediaType.startsWith("image/")
  ) {
    return null;
  }

  if (typeof url !== "string" || url.length === 0) {
    return null;
  }

  const filename =
    typeof record["filename"] === "string" && record["filename"].length > 0
      ? record["filename"]
      : "uploaded image";

  return { filename, imageUrl: url, mediaType };
}

function hasExtractionForFilename(
  message: UIMessage,
  filename: string,
): boolean {
  const expectedPrefix = `${EXTRACTED_IMAGE_PREFIX}${filename} `;
  return message.parts.some((part) => {
    const record = partRecord(part);
    return (
      record["type"] === "text" &&
      typeof record["text"] === "string" &&
      record["text"].startsWith(expectedPrefix)
    );
  });
}

function extractedImageText(
  filename: string,
  extraction: ImageExtraction,
): string {
  return `${EXTRACTED_IMAGE_PREFIX}${filename} (${extraction.screenshot_type}):\n${JSON.stringify(
    extraction.data,
    null,
    2,
  )}`;
}

// OpenAI only accepts image and PDF file parts; any other media type
// (GPX/FIT/TCX activity files, etc.) makes the provider throw
// "file part media type ... functionality not supported" and aborts the turn.
const MODEL_SUPPORTED_FILE_MEDIA_TYPES = ["application/pdf"];

function isModelSupportedPart(part: UIMessage["parts"][number]): boolean {
  const record = partRecord(part);
  if (record["type"] !== "file") return true;
  const mediaType = record["mediaType"];
  return (
    typeof mediaType === "string" &&
    (mediaType.startsWith("image/") ||
      MODEL_SUPPORTED_FILE_MEDIA_TYPES.includes(mediaType))
  );
}

function unsupportedFileNoticeText(part: UIMessage["parts"][number]): string {
  const record = partRecord(part);
  const filename =
    typeof record["filename"] === "string" && record["filename"].length > 0
      ? record["filename"]
      : "a file";
  const mediaType =
    typeof record["mediaType"] === "string"
      ? record["mediaType"]
      : "unknown type";
  return (
    `[Athlete uploaded ${filename} (${mediaType}). The file is stored but its raw bytes ` +
    `cannot be passed to the model; process it with the appropriate athlete-data tool if needed.]`
  );
}

// Replace file parts the model cannot ingest with a text notice so the turn
// streams instead of throwing. The original part is left untouched in the
// persisted message, so the UI attachment chip still renders.
export function stripUnsupportedModelFileParts(
  messages: UIMessage[],
): UIMessage[] {
  return messages.map((message) => {
    if (message.parts.every(isModelSupportedPart)) {
      return message;
    }
    const nextParts: UIMessage["parts"] = message.parts.map((part) =>
      isModelSupportedPart(part)
        ? part
        : { type: "text", text: unsupportedFileNoticeText(part) },
    );
    return { ...message, parts: nextParts };
  });
}

export function selectMessagesForModel(messages: UIMessage[]): UIMessage[] {
  if (messages.length <= MODEL_RECENT_MESSAGE_LIMIT) {
    return messages;
  }

  const omittedCount = messages.length - MODEL_RECENT_MESSAGE_LIMIT;
  return [
    {
      id: "context-window-notice",
      parts: [
        {
          type: "text",
          text:
            `The previous ${omittedCount} chat messages are persisted in the coaching history ` +
            "but omitted from this model turn to keep context focused. Continue from the recent " +
            "messages and use athlete data tools when older details are needed.",
        },
      ],
      role: "system",
    },
    ...messages.slice(-MODEL_RECENT_MESSAGE_LIMIT),
  ];
}

export async function appendImageExtractionsToMessages(
  messages: UIMessage[],
  extractImage: ImageExtractor,
): Promise<UIMessage[]> {
  return Promise.all(
    messages.map(async (message) => {
      const nextParts = [...message.parts];

      for (const part of message.parts) {
        const image = imageFilePart(part);
        if (
          image === null ||
          hasExtractionForFilename(message, image.filename)
        ) {
          continue;
        }

        const extraction = await extractImage(image);
        if (extraction === null) {
          continue;
        }

        nextParts.push({
          type: "text",
          text: extractedImageText(image.filename, extraction),
        });
      }

      return nextParts.length === message.parts.length
        ? message
        : { ...message, parts: nextParts };
    }),
  );
}
