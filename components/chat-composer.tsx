"use client";

import React, { useRef, useState } from "react";
import type { ChangeEvent, JSX } from "react";

import {
  fileTypeBadge,
  hasSendableContent,
  type LocalAttachment,
} from "../lib/chat-attachments";

import styles from "./coach-chat.module.css";

const CHAT_ATTACHMENT_ACCEPT =
  "image/*,application/gpx+xml,.gpx,.fit,.tcx,application/zip,.zip";

function SendIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      className={styles.sendIcon}
      focusable="false"
      viewBox="0 0 20 20"
    >
      <path
        d="M3 10L17 3L13 17L10 11L3 10Z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function UploadChip({
  attachment,
}: Readonly<{ attachment: LocalAttachment }>): JSX.Element {
  const badge = fileTypeBadge(attachment);
  const statusLabel =
    attachment.status === "uploading"
      ? "Uploading"
      : attachment.status === "uploaded"
        ? "Ready"
        : "Upload failed";
  return (
    <div className={styles.uploadChip}>
      {badge ? <span className={styles.uploadBadge}>{badge}</span> : null}
      <span>{attachment.filename}</span>
      <span className={styles.uploadStatus}>{statusLabel}</span>
    </div>
  );
}

function UploadChips({
  attachments,
}: Readonly<{ attachments: LocalAttachment[] }>): JSX.Element | null {
  if (attachments.length === 0) return null;
  return (
    <div className={styles.uploadRow}>
      {attachments.map((attachment) => (
        <UploadChip attachment={attachment} key={attachment.id} />
      ))}
    </div>
  );
}

function SendButton({
  composer,
  attachments,
  composerBusy,
  sending,
  syncingThread,
  onSend,
}: Readonly<{
  composer: string;
  attachments: LocalAttachment[];
  composerBusy: boolean;
  sending: boolean;
  syncingThread: boolean;
  onSend: () => void;
}>): JSX.Element {
  const disabled = composerBusy || !hasSendableContent(composer, attachments);
  const label = syncingThread ? "Syncing" : sending ? "Sending..." : "Send";
  return (
    <button
      className={styles.sendButton}
      disabled={disabled}
      onClick={onSend}
      type="button"
    >
      <SendIcon />
      <span className={styles.sendButtonText}>{label}</span>
    </button>
  );
}

function ComposerHint({
  leaseInFlight,
  syncingThread,
  sending,
  threadError,
  isMobile,
  waitingStatus,
}: Readonly<{
  leaseInFlight: boolean;
  syncingThread: boolean;
  sending: boolean;
  threadError: string | null;
  isMobile: boolean;
  waitingStatus: string | undefined;
}>): JSX.Element {
  if (syncingThread) {
    return (
      <span aria-live="polite" className={styles.waitingStatus} role="status">
        Syncing coach chat...
      </span>
    );
  }
  if (sending) {
    return (
      <span aria-live="polite" className={styles.waitingStatus} role="status">
        {waitingStatus}
      </span>
    );
  }
  if (leaseInFlight) {
    return (
      <span aria-live="polite" className={styles.waitingStatus} role="status">
        Coach is finishing your previous message...
      </span>
    );
  }
  if (threadError !== null) {
    return <span className={styles.errorTextInline}>{threadError}</span>;
  }
  return (
    <>
      {isMobile
        ? "Tap the send button when you're ready. Add photos with the plus button."
        : "Use Shift+Enter for a new line. Add photos with the plus button."}
    </>
  );
}

export function Composer({
  composer,
  onComposerChange,
  attachments,
  composerBusy,
  leaseInFlight,
  sending,
  syncingThread,
  threadError,
  isMobile,
  waitingStatus,
  placeholder,
  onSend,
  onFilesAdded,
}: Readonly<{
  composer: string;
  onComposerChange: (_next: string) => void;
  attachments: LocalAttachment[];
  composerBusy: boolean;
  leaseInFlight: boolean;
  sending: boolean;
  syncingThread: boolean;
  threadError: string | null;
  isMobile: boolean;
  waitingStatus: string | undefined;
  placeholder: string;
  onSend: () => void;
  onFilesAdded: (_files: File[]) => void;
}>): JSX.Element {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [dragActive, setDragActive] = useState(false);

  // Dragging a file onto a bare <textarea> makes the browser navigate to the
  // file URL (issue #161). Capturing dragover/drop on the composer wrapper and
  // calling preventDefault keeps the file in-app and routes it through the same
  // upload path as the + button.
  function handleDragOver(event: React.DragEvent): void {
    if (!Array.from(event.dataTransfer.types).includes("Files")) return;
    // preventDefault must come before the composerBusy guard: the browser
    // decides whether to allow a drop based on whether dragover calls
    // preventDefault. Skipping it here would make the drop target invalid
    // even if handleDrop also calls preventDefault.
    event.preventDefault();
    if (sending || syncingThread) return;
    setDragActive(true);
  }

  function handleDragLeave(event: React.DragEvent): void {
    // Only clear when the pointer actually leaves the wrapper, not when it
    // crosses into a child element (relatedTarget still inside the wrapper).
    if (event.currentTarget.contains(event.relatedTarget as Node | null))
      return;
    setDragActive(false);
  }

  function handleDrop(event: React.DragEvent): void {
    // preventDefault must come first — if called after the composerBusy guard,
    // a drop while sending would not suppress the browser's default navigation.
    event.preventDefault();
    setDragActive(false);
    if (sending || syncingThread) return;
    const files = Array.from(event.dataTransfer.files);
    if (files.length === 0) return;
    onFilesAdded(files);
  }

  function handlePaste(event: React.ClipboardEvent): void {
    const imageFiles = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    if (imageFiles.length > 0) {
      event.preventDefault();
      onFilesAdded(imageFiles);
    }
  }

  function handleFileSelect(event: ChangeEvent<HTMLInputElement>): void {
    onFilesAdded(Array.from(event.target.files ?? []));
    event.target.value = "";
  }

  const rowClass = dragActive
    ? `${styles.composerRow} ${styles.composerRowDragActive}`
    : styles.composerRow;
  const attachmentBusy = sending || syncingThread;
  const attachClass = attachmentBusy
    ? `${styles.attachButton} ${styles.attachDisabled}`
    : styles.attachButton;

  return (
    <div className={styles.composerWrap}>
      <div className={styles.composerCard}>
        <UploadChips attachments={attachments} />

        <div
          className={rowClass}
          data-testid="composer-row"
          onDragEnter={handleDragOver}
          onDragLeave={handleDragLeave}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
        >
          <label
            aria-label="Add photo or activity file"
            className={attachClass}
            title="Add photo or activity file"
          >
            <input
              accept={CHAT_ATTACHMENT_ACCEPT}
              className={styles.hiddenInput}
              disabled={attachmentBusy}
              multiple
              onChange={handleFileSelect}
              ref={fileInputRef}
              type="file"
            />
            +
          </label>
          <textarea
            className={styles.composerInput}
            onChange={(event) => onComposerChange(event.target.value)}
            onKeyDown={(event) => {
              if (!isMobile && event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSend();
              }
            }}
            onPaste={handlePaste}
            placeholder={placeholder}
            rows={1}
            value={composer}
          />
          <SendButton
            attachments={attachments}
            composer={composer}
            composerBusy={composerBusy}
            onSend={onSend}
            sending={sending}
            syncingThread={syncingThread}
          />
        </div>
        <div className={styles.composerHint}>
          <ComposerHint
            isMobile={isMobile}
            leaseInFlight={leaseInFlight}
            sending={sending}
            syncingThread={syncingThread}
            threadError={threadError}
            waitingStatus={waitingStatus}
          />
        </div>
      </div>
    </div>
  );
}
