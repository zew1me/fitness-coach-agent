import * as Sentry from "@sentry/nextjs";
import { useEffect, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";

import {
  activityContentType,
  isSupportedAttachment,
  type LocalAttachment,
  removePreviewUrls,
} from "./chat-attachments";
import { createChatUploadIntent, uploadFile } from "./coach-api";
import { errorMessage } from "./errors";

const ATTACHMENT_UPLOAD_TIMEOUT_MS = 20_000;

type AttachmentPatch = Partial<
  Pick<LocalAttachment, "object_key" | "public_url" | "status">
>;

type AttachmentUploadCallbacks = {
  updateAttachment: (_attachmentId: string, _patch: AttachmentPatch) => void;
  setThreadError: (_error: string | null) => void;
};

export type ChatDraft = {
  composer: string;
  setComposer: Dispatch<SetStateAction<string>>;
  attachments: LocalAttachment[];
  setAttachments: Dispatch<SetStateAction<LocalAttachment[]>>;
  latestComposerRef: RefObject<string>;
  latestAttachmentsRef: RefObject<LocalAttachment[]>;
  skipNextAttachmentPreviewCleanupRef: RefObject<boolean>;
  handleFilesAdded: (_files: File[]) => Promise<void>;
};

function attachmentLogContext(
  file: File,
  contentType: string,
): { filename_suffix: string; content_type: string } {
  return {
    filename_suffix: file.name.includes(".")
      ? file.name.slice(file.name.lastIndexOf(".")).slice(0, 16)
      : "",
    content_type: contentType,
  };
}

function localAttachmentFor(attachmentId: string, file: File): LocalAttachment {
  return {
    id: attachmentId,
    content_type: activityContentType(file),
    filename: file.name,
    object_key: "",
    preview_url: file.type.startsWith("image/")
      ? URL.createObjectURL(file)
      : null,
    public_url: null,
    status: "uploading",
  };
}

async function uploadOneAttachment(
  attachmentId: string,
  file: File,
  { updateAttachment, setThreadError }: AttachmentUploadCallbacks,
): Promise<void> {
  const contentType = activityContentType(file);
  try {
    const intent = await createChatUploadIntent({
      content_length: file.size,
      content_type: contentType,
      filename: file.name,
      purpose: "chat-attachment",
    });
    const uploaded = await uploadFile(
      intent.object_key,
      file,
      undefined,
      AbortSignal.timeout(ATTACHMENT_UPLOAD_TIMEOUT_MS),
    );
    if (uploaded.public_url === null) {
      updateAttachment(attachmentId, {
        object_key: uploaded.object_key,
        public_url: null,
        status: "error",
      });
      Sentry.logger.error(
        "chat: attachment upload returned no public_url",
        attachmentLogContext(file, contentType),
      );
      setThreadError(
        "We uploaded the file but couldn't get a shareable link back. Ask an admin to check the storage configuration.",
      );
      return;
    }
    Sentry.logger.info("chat attachment ready", {
      ...attachmentLogContext(file, contentType),
      has_public_url: true,
    });
    updateAttachment(attachmentId, {
      object_key: uploaded.object_key,
      public_url: uploaded.public_url,
      status: "uploaded",
    });
  } catch (error) {
    Sentry.logger.error(
      "chat attachment upload failed",
      attachmentLogContext(file, contentType),
    );
    updateAttachment(attachmentId, { status: "error" });
    const timedOut =
      error instanceof DOMException && error.name === "TimeoutError";
    console.error("Chat attachment upload failed", error);
    setThreadError(
      timedOut
        ? "That upload took too long and was cancelled. Check your connection and try again."
        : errorMessage(error, "Unable to upload that attachment."),
    );
  }
}

/**
 * Owns the unsent draft: composer text, local attachments and their upload
 * lifecycle, plus the latest-value mirrors the failed-send restore path reads.
 */
export function useChatDraft(
  setThreadError: (_error: string | null) => void,
): ChatDraft {
  const [composer, setComposer] = useState("");
  const [attachments, setAttachments] = useState<LocalAttachment[]>([]);
  // When we clear attachments optimistically on send, the [attachments] cleanup
  // effect below would otherwise revoke the preview URLs we still need for the
  // failure-restore path. This flag tells that cleanup to skip one revocation.
  const skipNextAttachmentPreviewCleanupRef = useRef(false);
  // Mirror the latest draft so the failed-send restore path can tell whether the
  // user has started a new draft since we cleared it (see handleSend's catch).
  const latestComposerRef = useRef(composer);
  latestComposerRef.current = composer;
  const latestAttachmentsRef = useRef(attachments);
  latestAttachmentsRef.current = attachments;

  useEffect((): (() => void) => {
    return (): void => {
      if (skipNextAttachmentPreviewCleanupRef.current) {
        skipNextAttachmentPreviewCleanupRef.current = false;
        return;
      }
      removePreviewUrls(attachments);
    };
  }, [attachments]);

  function updateAttachment(
    attachmentId: string,
    patch: AttachmentPatch,
  ): void {
    setAttachments((current) =>
      current.map((attachment) =>
        attachment.id === attachmentId
          ? { ...attachment, ...patch }
          : attachment,
      ),
    );
  }

  async function handleFilesAdded(files: File[]): Promise<void> {
    if (files.length === 0) return;

    const uploadQueue: Array<{ attachmentId: string; file: File }> = [];
    const nextLocalAttachments: LocalAttachment[] = [];

    for (const file of files) {
      if (!isSupportedAttachment(file)) {
        setThreadError(
          "Only image, GPX, FIT, TCX, and ZIP attachments are supported in the coach chat.",
        );
        continue;
      }
      const attachmentId = crypto.randomUUID();
      uploadQueue.push({ attachmentId, file });
      nextLocalAttachments.push(localAttachmentFor(attachmentId, file));
    }
    setAttachments((current) => [...current, ...nextLocalAttachments]);

    for (const { attachmentId, file } of uploadQueue) {
      await uploadOneAttachment(attachmentId, file, {
        setThreadError,
        updateAttachment,
      });
    }
  }

  return {
    attachments,
    composer,
    handleFilesAdded,
    latestAttachmentsRef,
    latestComposerRef,
    setAttachments,
    setComposer,
    skipNextAttachmentPreviewCleanupRef,
  };
}
