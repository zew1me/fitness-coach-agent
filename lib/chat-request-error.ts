const DEFAULT_CHAT_REQUEST_ERROR = "Unable to send your message.";

/**
 * A chat POST that failed before any response stream started: a non-2xx
 * status from `/api/chat`, or a network failure reaching it.
 */
export class ChatRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ChatRequestError";
  }
}

/**
 * Reads the athlete-facing message from a failed `/api/chat` response. The
 * route answers some failures with plain text and others with
 * `{ "error": "..." }`, so unwrap the JSON form rather than showing it raw.
 */
export async function chatRequestErrorFromResponse(
  response: Response,
): Promise<ChatRequestError> {
  let body = "";
  try {
    body = (await response.text()).trim();
  } catch {
    // An unreadable body still failed; fall through to the default message.
  }
  return new ChatRequestError(
    jsonErrorField(body) ?? (body || DEFAULT_CHAT_REQUEST_ERROR),
  );
}

function jsonErrorField(body: string): string | null {
  if (!body.startsWith("{")) return null;
  try {
    const parsed: unknown = JSON.parse(body);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "error" in parsed &&
      typeof parsed.error === "string" &&
      parsed.error.trim().length > 0
    ) {
      return parsed.error;
    }
  } catch {
    // Not JSON after all; the caller shows the body as plain text.
  }
  return null;
}
