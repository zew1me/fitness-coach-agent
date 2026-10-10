import type { UIMessage } from "ai";

import { uploadedFileText } from "./uploaded-file-stub";

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

// Screenshot extraction is a multi-second vision call per image, so a turn carrying
// several screenshots is the worst case. Bounded rather than unbounded because each
// slot holds an inflight model call.
const IMAGE_EXTRACTION_CONCURRENCY = 4;

async function mapWithConcurrency<T, R>(
  items: readonly T[],
  limit: number,
  run: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;

  const worker = async (): Promise<void> => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await run(items[index] as T);
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, worker),
  );
  return results;
}

export async function appendImageExtractionsToMessages(
  messages: UIMessage[],
  extractImage: ImageExtractor,
): Promise<UIMessage[]> {
  // Collect every pending image across every message first, then extract them as one
  // bounded pool. These used to be awaited one at a time within each message, which
  // serialized a multi-screenshot turn into N back-to-back round-trips — visible in
  // production traces as consecutive analyze-screenshot transactions, each starting as
  // the previous one finished.
  const pending = messages.flatMap((message, messageIndex) =>
    message.parts.flatMap((part) => {
      const image = imageFilePart(part);
      if (image === null || hasExtractionForFilename(message, image.filename)) {
        return [];
      }
      return [{ image, messageIndex }];
    }),
  );

  if (pending.length === 0) {
    return messages;
  }

  const extractions = await mapWithConcurrency(
    pending,
    IMAGE_EXTRACTION_CONCURRENCY,
    ({ image }) => extractImage(image),
  );

  // `pending` is built in message order, then part order, so appending in index order
  // reproduces the sequential version's output exactly.
  const textsByMessage = new Map<number, string[]>();
  pending.forEach((entry, index) => {
    const extraction = extractions[index];
    if (extraction === undefined || extraction === null) {
      return;
    }
    const texts = textsByMessage.get(entry.messageIndex) ?? [];
    texts.push(extractedImageText(entry.image.filename, extraction));
    textsByMessage.set(entry.messageIndex, texts);
  });

  return messages.map((message, messageIndex) => {
    const texts = textsByMessage.get(messageIndex);
    if (texts === undefined || texts.length === 0) {
      return message;
    }
    return {
      ...message,
      parts: [
        ...message.parts,
        ...texts.map((text) => ({ type: "text" as const, text })),
      ],
    };
  });
}

export type NonImageFilePart = {
  filename: string;
  url: string;
  mediaType: string;
};

export function nonImageFilePart(
  part: UIMessage["parts"][number],
): NonImageFilePart | null {
  const record = partRecord(part);
  const type = record["type"];
  const mediaType = record["mediaType"];
  const url = record["url"];

  if (
    type !== "file" ||
    typeof mediaType !== "string" ||
    mediaType.startsWith("image/")
  ) {
    return null;
  }

  if (typeof url !== "string" || url.length === 0) {
    return null;
  }

  const filename =
    typeof record["filename"] === "string" && record["filename"].length > 0
      ? record["filename"]
      : "uploaded file";

  return { filename, url, mediaType };
}

export function convertUnsupportedFilePartsToText(
  messages: UIMessage[],
): UIMessage[] {
  return messages.map((message) => {
    const nextParts: UIMessage["parts"] = [];
    let changed = false;

    for (const part of message.parts) {
      const file = nonImageFilePart(part);
      if (file !== null) {
        nextParts.push({ type: "text", text: uploadedFileText(file) });
        changed = true;
      } else {
        nextParts.push(part);
      }
    }

    return changed ? { ...message, parts: nextParts } : message;
  });
}
