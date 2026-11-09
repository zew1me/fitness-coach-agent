import * as Sentry from "@sentry/nextjs";
import { useEffect, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";
import { ZodError } from "zod";

import {
  activityContentType,
  isSupportedAttachment,
  type LocalAttachment,
  removePreviewUrls,
} from "./chat-attachments";
import { createChatUploadIntent, uploadFile } from "./coach-api";
import { errorMessage } from "./errors";
import {
  attachmentSizeError,
  AttachmentSizeError,
  compressImage,
  ImageCompressionBusyError,
  planAttachmentUpload,
} from "./image-compression";

const ATTACHMENT_UPLOAD_TIMEOUT_MS = 20_000;
const ATTACHMENT_COMPRESSION_TIMEOUT_MS = 60_000;

type AttachmentPatch = Partial<
  Pick<
    LocalAttachment,
    | "object_key"
    | "public_url"
    | "status"
    | "content_type"
    | "filename"
    | "progress"
  >
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
  const plan = planAttachmentUpload(file);
  return {
    id: attachmentId,
    content_type: activityContentType(file),
    filename: file.name,
    object_key: "",
    preview_url: file.type.startsWith("image/")
      ? URL.createObjectURL(file)
      : null,
    public_url: null,
    status:
      plan === "too-large"
        ? "error"
        : plan === "compress"
          ? "compressing"
          : "uploading",
    progress: null,
  };
}

async function prepareAttachment(
  attachmentId: string,
  file: File,
  updateAttachment: AttachmentUploadCallbacks["updateAttachment"],
): Promise<File> {
  const plan = planAttachmentUpload(file);
  if (plan === "too-large") throw attachmentSizeError();
  if (plan === "as-is") return file;
  return compressImage(file, {
    signal: AbortSignal.timeout(ATTACHMENT_COMPRESSION_TIMEOUT_MS),
    onProgress: (progress) => {
      updateAttachment(attachmentId, { progress: Math.round(progress) });
    },
  });
}

type AttachmentUploadStage = "preparation" | "presign" | "upload";

function attachmentFailureMessage(
  error: unknown,
  stage: AttachmentUploadStage,
): string {
  if (
    error instanceof AttachmentSizeError ||
    error instanceof ImageCompressionBusyError
  ) {
    return error.message;
  }
  if (stage === "presign" && error instanceof ZodError) {
    return "That attachment couldn't be uploaded. Choose a non-empty file with a valid filename and try again.";
  }
  const timedOut =
    error instanceof DOMException && error.name === "TimeoutError";
  if (stage === "preparation") {
    return timedOut
      ? "Optimizing that image took too long and was cancelled. Try again or choose a smaller image."
      : "We couldn't decode or optimize that image. Try another image or export it again.";
  }
  return timedOut
    ? "That upload took too long and was cancelled. Check your connection and try again."
    : errorMessage(error, "Unable to upload that attachment.");
}

async function uploadOneAttachment(
  attachmentId: string,
  file: File,
  { updateAttachment, setThreadError }: AttachmentUploadCallbacks,
): Promise<void> {
  const originalSize = file.size;
  let contentType = activityContentType(file);
  let stage: AttachmentUploadStage = "preparation";
  try {
    file = await prepareAttachment(attachmentId, file, updateAttachment);
    contentType = activityContentType(file);
    updateAttachment(attachmentId, {
      content_type: contentType,
      filename: file.name,
      status: "uploading",
      progress: null,
    });
    Sentry.logger.info("chat attachment prepared", {
      ...attachmentLogContext(file, contentType),
      original_size: originalSize,
      prepared_size: file.size,
    });
    stage = "presign";
    const intent = await createChatUploadIntent({
      content_length: file.size,
      content_type: contentType,
      filename: file.name,
      purpose: "chat-attachment",
    });
    stage = "upload";
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
    Sentry.logger.error("chat attachment upload failed", {
      ...attachmentLogContext(file, contentType),
      original_size: originalSize,
      failure_stage: stage,
      failure_reason:
        error instanceof DOMException
          ? error.message
          : errorMessage(error, "Unknown attachment failure"),
    });
    updateAttachment(attachmentId, { status: "error", progress: null });
    console.error("Chat attachment upload failed", error);
    setThreadError(attachmentFailureMessage(error, stage));
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
      const attachment = localAttachmentFor(attachmentId, file);
      nextLocalAttachments.push(attachment);
      // Reject immediately, even when an earlier file is still being optimized.
      if (attachment.status === "error") {
        setThreadError(attachmentSizeError().message);
        continue;
      }
      uploadQueue.push({ attachmentId, file });
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
