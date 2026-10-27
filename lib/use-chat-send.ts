import type { UseChatHelpers } from "@ai-sdk/react";
import * as Sentry from "@sentry/nextjs";
import type { UIMessage } from "ai";
import { useRef } from "react";
import type { RefObject } from "react";

import {
  hasSendableContent,
  type LocalAttachment,
  removePreviewUrls,
  uploadedFileParts,
} from "./chat-attachments";
import { errorMessage } from "./errors";
import type { ChatDraft } from "./use-chat-draft";

type ChatSendOptions = {
  draft: ChatDraft;
  composerBusy: boolean;
  sendMessage: UseChatHelpers<UIMessage>["sendMessage"];
  setMessages: UseChatHelpers<UIMessage>["setMessages"];
  startTurn: () => void;
  cancelTurn: () => void;
  setSending: (_sending: boolean) => void;
  setSyncingThread: (_syncing: boolean) => void;
  setThreadError: (_error: string | null) => void;
  olderRequestVersionRef: RefObject<number>;
  refetchThread: () => Promise<void>;
  resetWindow: () => void;
  refreshSession: () => Promise<void>;
};

function textParts(composer: string): UIMessage["parts"] {
  return composer.trim().length > 0
    ? [{ type: "text" as const, text: composer }]
    : [];
}

// The send failed, so restore the draft into the composer — but only if the
// user hasn't started a new draft (text or attachments) while the send was in
// flight, so we never clobber fresh input. Text and attachments are restored
// together under one check so we never pair a new draft with the failed send's
// attachments. The preview URLs were kept alive on the optimistic clear (see
// skipNextAttachmentPreviewCleanupRef), so restored attachments still render;
// if we don't restore, we revoke them here to avoid a leak.
function restoreFailedDraft(
  draft: ChatDraft,
  pendingComposer: string,
  pendingAttachments: LocalAttachment[],
): boolean {
  const draftUntouched =
    draft.latestComposerRef.current === "" &&
    draft.latestAttachmentsRef.current.length === 0;
  if (draftUntouched) {
    draft.setComposer(pendingComposer);
    draft.setAttachments(pendingAttachments);
  } else {
    removePreviewUrls(pendingAttachments);
  }
  return draftUntouched;
}

type FailedSend = {
  attachments: LocalAttachment[];
  composer: string;
  messageId: string;
};

// The route can persist the user turn before failing (e.g. the athlete-context
// load), and persistence upserts on the message id. Resending the restored
// draft unchanged therefore reuses the failed send's id so the turn is not
// stored twice; any edit makes it a new message with a new id.
function messageIdForSend(
  failedSend: FailedSend | null,
  composer: string,
  attachments: LocalAttachment[],
): string {
  if (
    failedSend !== null &&
    failedSend.composer === composer &&
    failedSend.attachments === attachments
  ) {
    return failedSend.messageId;
  }
  return crypto.randomUUID();
}

/**
 * Sends the current draft as one chat turn: optimistic clear, stream, then a
 * thread refresh, restoring the draft if the send itself fails.
 */
export function useChatSend({
  draft,
  composerBusy,
  sendMessage,
  setMessages,
  startTurn,
  cancelTurn,
  setSending,
  setSyncingThread,
  setThreadError,
  olderRequestVersionRef,
  refetchThread,
  resetWindow,
  refreshSession,
}: ChatSendOptions): () => Promise<void> {
  // Synchronous guard against double-click: state reads aren't atomic across
  // rapid events, so this ref is the authoritative in-flight check.
  const sendInFlightRef = useRef(false);
  const failedSendRef = useRef<FailedSend | null>(null);
  const { composer, attachments } = draft;

  async function handleSend(): Promise<void> {
    if (sendInFlightRef.current || composerBusy) return;
    if (!hasSendableContent(composer, attachments)) return;

    sendInFlightRef.current = true;
    startTurn();
    setSending(true);
    setThreadError(null);
    // Snapshot the draft before we clear it so the catch block can restore it.
    const pendingComposer = composer;
    const pendingAttachments = attachments;
    const messageId = messageIdForSend(
      failedSendRef.current,
      pendingComposer,
      pendingAttachments,
    );
    failedSendRef.current = null;
    try {
      Sentry.logger.info("user turn submitted", {
        has_text: pendingComposer.trim().length > 0,
        attachment_count: pendingAttachments.length,
        message_id: messageId,
      });
      const messageParts = textParts(pendingComposer);
      // Clear the draft optimistically, before awaiting the send, so the input
      // empties the instant the user hits Send. `sendMessage` only resolves once
      // the entire assistant response has streamed back, so clearing after it
      // would leave the message sitting in the composer for the whole reply. If
      // the send fails we restore the snapshot below, so keep the attachment
      // preview URLs alive — suppress the [attachments] cleanup's revocation for
      // this one clear.
      draft.setComposer("");
      draft.skipNextAttachmentPreviewCleanupRef.current =
        pendingAttachments.length > 0;
      draft.setAttachments([]);
      await sendMessage({
        id: messageId,
        parts: [...messageParts, ...uploadedFileParts(pendingAttachments)],
      });
      // Send succeeded: free the preview URLs now that we won't restore them.
      removePreviewUrls(pendingAttachments);
      setSending(false);
      setSyncingThread(true);
      try {
        olderRequestVersionRef.current += 1;
        await refetchThread();
        resetWindow();
      } catch (refreshError) {
        Sentry.logger.warn("chat thread refresh failed after send");
        console.error("Chat thread refresh failed after send", refreshError);
        setThreadError(
          "Message sent, but the thread failed to refresh. Reload to see the latest.",
        );
      } finally {
        setSyncingThread(false);
      }
    } catch (error) {
      Sentry.logger.error("message send failed");
      console.error("Sending coach message failed", error);
      setSyncingThread(false);
      cancelTurn();
      // Drop the optimistic user bubble the SDK added, then restore the draft.
      setMessages((current) =>
        current.filter((message) => message.id !== messageId),
      );
      if (restoreFailedDraft(draft, pendingComposer, pendingAttachments)) {
        failedSendRef.current = {
          attachments: pendingAttachments,
          composer: pendingComposer,
          messageId,
        };
      }
      setThreadError(errorMessage(error, "Unable to send your message."));
      // A chat turn uses the cookie-backed Next.js route directly rather than
      // authorizedFetch. Revalidate here so an expired cookie replaces the
      // vague inline send error with the explicit sign-in-again screen.
      void refreshSession();
    } finally {
      sendInFlightRef.current = false;
      setSending(false);
    }
  }

  return handleSend;
}
