import type { FileUIPart } from "ai";

export type LocalAttachment = {
  id: string;
  content_type: string;
  filename: string;
  object_key: string;
  preview_url: string | null;
  public_url: string | null;
  status: "error" | "uploaded" | "uploading";
};

export function removePreviewUrls(attachments: LocalAttachment[]): void {
  for (const attachment of attachments) {
    if (attachment.preview_url !== null) {
      URL.revokeObjectURL(attachment.preview_url);
    }
  }
}

export function uploadedFileParts(
  attachments: LocalAttachment[],
): FileUIPart[] {
  return attachments.flatMap((attachment) => {
    if (attachment.status !== "uploaded" || attachment.public_url === null) {
      return [];
    }
    return [
      {
        filename: attachment.filename,
        mediaType: attachment.content_type,
        type: "file",
        url: attachment.public_url,
      },
    ];
  });
}

export function hasSendableContent(
  composer: string,
  attachments: LocalAttachment[],
): boolean {
  const hasText = composer.trim().length > 0;
  const hasPendingAttachment = attachments.some(
    (attachment) => attachment.status === "uploading",
  );

  return hasText
    ? !hasPendingAttachment
    : attachments.some((attachment) => attachment.status === "uploaded");
}

export function activityContentType(file: File): string {
  if (file.type) {
    return file.type;
  }
  const name = file.name.toLowerCase();
  if (name.endsWith(".gpx")) return "application/gpx+xml";
  if (name.endsWith(".fit")) return "application/vnd.garmin.fit";
  if (name.endsWith(".tcx")) return "application/vnd.garmin.tcx+xml";
  if (name.endsWith(".zip")) return "application/zip";
  return "application/octet-stream";
}

export function isSupportedAttachment(file: File): boolean {
  if (file.type.startsWith("image/")) {
    return true;
  }
  const name = file.name.toLowerCase();
  return (
    name.endsWith(".gpx") ||
    name.endsWith(".fit") ||
    name.endsWith(".tcx") ||
    name.endsWith(".zip")
  );
}

export function fileTypeBadge(attachment: {
  content_type: string;
  filename: string;
}): string | null {
  if (attachment.content_type.startsWith("image/")) {
    return null;
  }
  const suffix = attachment.filename.split(".").pop()?.toUpperCase();
  return suffix && ["GPX", "FIT", "TCX", "ZIP"].includes(suffix)
    ? suffix
    : "FILE";
}
