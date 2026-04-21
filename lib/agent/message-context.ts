import type { UIMessage } from "ai";

const MODEL_RECENT_MESSAGE_LIMIT = 24;

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
