"use client";

import { useChat } from "@ai-sdk/react";
import * as Sentry from "@sentry/nextjs";
import { DefaultChatTransport, type FileUIPart, type UIMessage } from "ai";
import Link from "next/link";
import React, { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, JSX, ReactNode, RefObject } from "react";

import {
  createChatUploadIntent,
  loadChatThread,
  loadChatMessages,
  uploadFile,
} from "../lib/coach-api";
import { errorMessage } from "../lib/errors";
import { siteConfig } from "../lib/site";
import type {
  AdaptedPlan,
  AthleteProfile,
  BrowserTokenResponse,
  ChatMessage,
  ChatThreadResponse,
} from "../lib/types";
import type { AthleteProfileHook } from "../lib/use-athlete-profile";
import { useAthleteProfile } from "../lib/use-athlete-profile";
import { useBrowserSession } from "../lib/use-browser-session";
import { useChatThread } from "../lib/use-chat-thread";
import { useIsMobile } from "../lib/use-is-mobile";
import { useTheme } from "../lib/use-theme";
import type { ThemeMode } from "../lib/use-theme";

import styles from "./coach-chat.module.css";

type LocalAttachment = {
  id: string;
  content_type: string;
  filename: string;
  object_key: string;
  preview_url: string | null;
  public_url: string | null;
  status: "error" | "uploaded" | "uploading";
};

type StarterPrompt = {
  label: string;
  prompt: string;
};

const ONBOARDING_STARTERS: StarterPrompt[] = [
  {
    label: "Running base and consistency",
    prompt: "I'm training for running and want help building consistency.",
  },
  {
    label: "Cycling race prep",
    prompt:
      "I'm training for a cycling race and want help balancing intensity and recovery.",
  },
  {
    label: "Triathlon build",
    prompt:
      "I'm training for triathlon and want help building toward my next event.",
  },
];
const COACHING_STARTERS: StarterPrompt[] = [
  {
    label: "Log a training session",
    prompt: "I just finished a training session and want to log how it felt.",
  },
  {
    label: "Generate next plan",
    prompt: "Build my next 14-day training plan.",
  },
  {
    label: "Adapt around fatigue",
    prompt: "I have some soreness and travel coming up this week.",
  },
];

const CHAT_ATTACHMENT_ACCEPT = "image/*,application/gpx+xml,.gpx,.fit,.tcx";
const MESSAGE_RENDER_BATCH_SIZE = 60;
const ATTACHMENT_UPLOAD_TIMEOUT_MS = 20_000;
const WAITING_STATUS_INTERVAL_MS = 1600;
const WAITING_STATUSES = [
  "Thinking...",
  "Still working...",
  "Checking the coaching notes...",
  "Almost there...",
];

function onlyWelcomeMessage(messages: ChatMessage[]): boolean {
  const firstMessage = messages[0];
  return (
    messages.length === 1 &&
    firstMessage !== undefined &&
    firstMessage.role === "assistant" &&
    firstMessage.metadata.message_kind === "welcome"
  );
}

function readableTime(timestamp: string): string {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function removePreviewUrls(attachments: LocalAttachment[]): void {
  for (const attachment of attachments) {
    if (attachment.preview_url !== null) {
      URL.revokeObjectURL(attachment.preview_url);
    }
  }
}

function coachingStatusLabel(profileComplete: boolean): string {
  return profileComplete ? "Coaching ready" : "Building your athlete profile";
}

function accountLabel(profile: AthleteProfile | null): string {
  if (profile === null) return "Account";
  const displayName = profile.display_name?.trim();
  return displayName ? displayName : "Account";
}

function readString(
  record: Record<string, unknown>,
  key: string,
): string | null {
  const value = record[key];
  return typeof value === "string" ? value : null;
}

function legacyAttachmentToFilePart(
  attachment: Record<string, unknown>,
): UIMessage["parts"][number] | null {
  const url = readString(attachment, "public_url");
  if (url === null) return null;
  const mediaType =
    readString(attachment, "content_type") ??
    readString(attachment, "mediaType") ??
    "application/octet-stream";
  const filename = readString(attachment, "filename") ?? "attachment";
  return { type: "file", mediaType, filename, url };
}

// Real persisted messages always have `parts` populated (the chat-parts migration
// backfilled every legacy row). This shim covers in-flight test fixtures and
// any callers still constructing the legacy shape: synthesize parts from the
// text `content` + legacy `attachments` join so the renderer never has to
// branch on shape.
function deriveParts(message: ChatMessage): UIMessage["parts"] {
  if (message.parts && message.parts.length > 0) {
    return message.parts as UIMessage["parts"];
  }
  const synthesized: UIMessage["parts"] = [];
  if (message.content && message.content.length > 0) {
    synthesized.push({ type: "text", text: message.content });
  }
  for (const attachment of message.attachments) {
    const part = legacyAttachmentToFilePart(attachment);
    if (part !== null) synthesized.push(part);
  }
  return synthesized;
}

function toUiMessage(message: ChatMessage): UIMessage {
  return {
    id: message.id,
    parts: deriveParts(message),
    role: message.role,
  };
}

function serializeChatHistoryJsonl(messages: ChatMessage[]): string {
  return messages.map((message) => JSON.stringify(message)).join("\n");
}

function downloadTextFile(filename: string, text: string, type: string): void {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function friendlyToolStatus(toolName: string): string {
  const statuses: Record<string, string> = {
    analyze_screenshot: "Reviewing your uploaded image...",
    calculate_zones: "Calculating your training zones...",
    get_athlete_context: "Looking up your info...",
    process_uploaded_file: "Reading your activity file...",
    save_check_in: "Saving your check-in...",
    update_athlete_profile: "Updating your profile...",
  };

  return statuses[toolName] ?? "Working on that...";
}

function uiPartText(part: UIMessage["parts"][number]): string | null {
  if (part.type === "text") {
    return part.text;
  }

  if (part.type === "dynamic-tool") {
    return friendlyToolStatus(part.toolName);
  }

  if (part.type.startsWith("tool-")) {
    return friendlyToolStatus(part.type.slice("tool-".length));
  }

  return null;
}

function toLiveChatMessage(
  message: UIMessage,
  threadId: string,
  userId: string,
): ChatMessage | null {
  if (message.role !== "assistant" && message.role !== "user") {
    return null;
  }

  return {
    attachments: [],
    parts: message.parts as NonNullable<ChatMessage["parts"]>,
    created_at: new Date().toISOString(),
    id: message.id,
    metadata: { message_kind: "streaming" },
    role: message.role,
    thread_id: threadId,
    user_id: userId,
  };
}

function uploadedFileParts(attachments: LocalAttachment[]): FileUIPart[] {
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

function hasSendableContent(
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

function activityContentType(file: File): string {
  if (file.type) {
    return file.type;
  }
  const name = file.name.toLowerCase();
  if (name.endsWith(".gpx")) return "application/gpx+xml";
  if (name.endsWith(".fit")) return "application/vnd.garmin.fit";
  if (name.endsWith(".tcx")) return "application/vnd.garmin.tcx+xml";
  return "application/octet-stream";
}

function isSupportedAttachment(file: File): boolean {
  if (file.type.startsWith("image/")) {
    return true;
  }
  const name = file.name.toLowerCase();
  return (
    name.endsWith(".gpx") || name.endsWith(".fit") || name.endsWith(".tcx")
  );
}

function fileTypeBadge(attachment: {
  content_type: string;
  filename: string;
}): string | null {
  if (attachment.content_type.startsWith("image/")) {
    return null;
  }
  const suffix = attachment.filename.split(".").pop()?.toUpperCase();
  return suffix && ["GPX", "FIT", "TCX"].includes(suffix) ? suffix : "FILE";
}

function composerPlaceholderFor(
  messages: ChatMessage[],
  profileComplete: boolean,
): string {
  if (profileComplete) return "Ask your coach...";
  if (onlyWelcomeMessage(messages)) {
    return "Tell your coach your sport and goal...";
  }
  return "Reply to your coach...";
}

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

function ChatLoading(): JSX.Element {
  return (
    <main className={styles.landingWrap}>
      <section className={styles.statusBanner}>
        <p className={styles.meta}>Checking your browser session…</p>
      </section>
    </main>
  );
}

function LoggedOutLanding({
  error,
}: Readonly<{ error: string | null }>): JSX.Element {
  return (
    <main className={styles.landingWrap}>
      <section className={styles.landingCard}>
        <p className={styles.eyebrow}>Athlete Coach</p>
        <h1 className={styles.landingTitle}>
          A simpler coaching experience, built like chat.
        </h1>
        <p className={styles.landingText}>
          Sign in once, then use a single focused conversation for check-ins,
          plan requests, and photo-backed coaching updates. The forms are gone
          from the main surface so the experience feels closer to a modern chat
          assistant than a dashboard.
        </p>
        {error ? (
          <p className={styles.landingHint}>
            Sign in to start your coaching chat. If the app feels slow to wake
            up, give it a moment and try again.
          </p>
        ) : null}
        <div className={styles.actionRow}>
          <Link className={styles.primaryButton} href="/login?return_to=/">
            Continue with magic link
          </Link>
        </div>
      </section>
    </main>
  );
}

function OutRunningIllustration(): JSX.Element {
  return (
    <div aria-hidden="true" className={styles.errorIllustration}>
      <svg className={styles.errorArt} viewBox="0 0 240 180">
        <defs>
          <linearGradient id="skyWash" x1="0%" x2="100%" y1="0%" y2="100%">
            <stop offset="0%" stopColor="#f8fafc" />
            <stop offset="100%" stopColor="#dbeafe" />
          </linearGradient>
        </defs>
        <rect fill="url(#skyWash)" height="180" rx="28" width="240" />
        <circle cx="182" cy="44" fill="#f59e0b" opacity="0.18" r="18" />
        <path
          d="M24 130C53 98 76 84 96 84C118 84 131 107 154 107C174 107 192 92 216 66"
          fill="none"
          stroke="#0f766e"
          strokeLinecap="round"
          strokeWidth="8"
        />
        <path
          d="M28 128L72 94L102 111L150 78L210 113"
          fill="none"
          opacity="0.85"
          stroke="#1d4ed8"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="6"
        />
        <path
          d="M92 56L120 34L147 56"
          fill="none"
          stroke="#0f172a"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="7"
        />
        <path
          d="M80 82C96 68 109 61 121 61C138 61 149 74 166 74"
          fill="none"
          opacity="0.35"
          stroke="#0f172a"
          strokeLinecap="round"
          strokeWidth="6"
        />
        <circle cx="112" cy="109" fill="#ea580c" r="6" />
        <path
          d="M112 116L104 130M112 116L123 125M104 130L95 145M123 125L133 140M103 121L90 126"
          fill="none"
          stroke="#ea580c"
          strokeLinecap="round"
          strokeWidth="5"
        />
      </svg>
    </div>
  );
}

function ChatErrorState({
  error,
}: Readonly<{ error: string | null }>): JSX.Element {
  return (
    <main className={styles.landingWrap}>
      <section className={styles.errorCard}>
        <OutRunningIllustration />
        <p className={styles.eyebrow}>Coach unavailable</p>
        <h1 className={styles.errorTitle}>Sorry, we&apos;re out running.</h1>
        <p className={styles.errorText}>
          We&apos;ll be back soon. You&apos;ve got this. In the meantime, hang
          onto the thread and try again in a minute or two.
        </p>
        {error ? <p className={styles.errorDetail}>{error}</p> : null}
        <div className={styles.actionRow}>
          <button
            className={styles.primaryButton}
            onClick={() => window.location.reload()}
            type="button"
          >
            Retry
          </button>
        </div>
      </section>
    </main>
  );
}

/**
 * Render the Coach Chat user interface for browser-based coaching interactions.
 *
 * The component manages browser session bootstrap, loads and syncs the chat thread
 * and athlete profile, handles message composition (text and attachments),
 * supports image and activity-file uploads, displays assistant/user messages
 * (including tool statuses and attachments), and provides a profile/settings drawer
 * and export functionality.
 *
 * @returns A JSX element containing the complete coach chat interface.
 */
function ChatLoadingShell(): JSX.Element {
  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <div className={styles.frame}>
          <div className={styles.topbar}>
            <div className={styles.brandBlock}>
              <p className={styles.brand}>{siteConfig.appName}</p>
              <span className={styles.meta}>Loading your coach chat…</span>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}

function PlanCard({ plan }: Readonly<{ plan: AdaptedPlan }>): JSX.Element {
  return (
    <section className={styles.planCard}>
      <div className={styles.planMeta}>
        <span className={styles.planPill}>{plan.summary}</span>
        <span className={styles.planPill}>{plan.trend}</span>
        <span className={styles.planPill}>{plan.hours} hours</span>
      </div>
      <div className={styles.planDays}>
        {plan.days.map((day) => (
          <article className={styles.planDay} key={day.day_index}>
            <h3 className={styles.planDayTitle}>
              Day {day.day_index}: {day.focus}
            </h3>
            <p className={styles.planDayNote}>{day.notes}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function AttachmentTile({ part }: Readonly<{ part: FileUIPart }>): JSX.Element {
  const filename = part.filename ?? "attachment";
  const contentType = part.mediaType;
  const isImage = contentType.startsWith("image/");
  const badge = fileTypeBadge({ content_type: contentType, filename });
  return (
    <div className={styles.attachmentThumb}>
      {isImage ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img alt={filename} src={part.url} />
      ) : (
        <div className={styles.attachmentFileCard}>
          <span>{badge ?? "FILE"}</span>
        </div>
      )}
      <span className={styles.attachmentName}>{filename}</span>
    </div>
  );
}

function MessageBubble({
  message,
}: Readonly<{ message: ChatMessage }>): JSX.Element {
  const rowClass =
    message.role === "assistant" ? styles.rowAssistant : styles.rowUser;
  const bubbleClass =
    message.role === "assistant"
      ? `${styles.bubble} ${styles.assistantBubble}`
      : `${styles.bubble} ${styles.userBubble}`;
  const plan = message.metadata.plan;
  const parts = deriveParts(message);
  const textBlocks = parts
    .map((part) => uiPartText(part))
    .filter((text): text is string => text !== null && text.length > 0);
  const fileParts = parts.filter(
    (part): part is FileUIPart => part.type === "file",
  );

  return (
    <div className={rowClass}>
      <div
        className={bubbleClass}
        data-role={message.role}
        data-testid="chat-bubble"
      >
        {textBlocks.map((text, idx) => (
          <p className={styles.messageText} key={`text-${idx}`}>
            {text}
          </p>
        ))}
        {fileParts.length > 0 ? (
          <div className={styles.attachmentGrid}>
            {fileParts.map((part, idx) => (
              <AttachmentTile key={`file-${part.url}-${idx}`} part={part} />
            ))}
          </div>
        ) : null}
        {plan ? <PlanCard plan={plan} /> : null}
        <div className={styles.attachmentName}>
          {readableTime(message.created_at)}
        </div>
      </div>
    </div>
  );
}

function MessageList({
  messages,
  hiddenMessageCount,
  onShowMore,
  messageEndRef,
  olderAvailable,
}: Readonly<{
  messages: ChatMessage[];
  hiddenMessageCount: number;
  onShowMore: () => void;
  messageEndRef: RefObject<HTMLDivElement | null>;
  olderAvailable: boolean;
}>): JSX.Element {
  return (
    <div className={styles.messageStack}>
      {hiddenMessageCount > 0 || olderAvailable ? (
        <button
          className={styles.historyLoadButton}
          onClick={onShowMore}
          type="button"
        >
          {hiddenMessageCount > 0
            ? `Show ${Math.min(MESSAGE_RENDER_BATCH_SIZE, hiddenMessageCount)} older messages`
            : "Show older messages"}
        </button>
      ) : null}
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
      <div ref={messageEndRef} />
    </div>
  );
}

function EmptyChatLandingCard({
  variant,
  onPrefill,
  children,
}: Readonly<{
  variant: "coaching" | "onboarding";
  onPrefill: (_text: string) => void;
  children: ReactNode;
}>): JSX.Element {
  const starters =
    variant === "onboarding" ? ONBOARDING_STARTERS : COACHING_STARTERS;
  const title =
    variant === "onboarding"
      ? "Start with your sport and goal"
      : "What should we work on next?";
  const body =
    variant === "onboarding"
      ? "Tell me what you are training for and what you want coaching around. A short answer is enough."
      : "Use this thread for quick training updates, image-backed check-ins, and your next 14-day plan. I’ll keep the details in the background and keep the surface focused.";
  return (
    <div className={styles.emptyState}>
      <div className={styles.emptyCard}>
        <p className={styles.eyebrow}>Coach Chat</p>
        <h1 className={styles.emptyTitle}>{title}</h1>
        <p className={styles.emptyText}>{body}</p>
        <div className={styles.starterRow}>
          {starters.map((starter) => (
            <button
              className={styles.starterButton}
              key={starter.label}
              onClick={() => onPrefill(starter.prompt)}
              type="button"
            >
              {starter.label}
            </button>
          ))}
        </div>
        {children}
      </div>
    </div>
  );
}

function MessagesSection({
  messages,
  hiddenMessageCount,
  onShowMore,
  onPrefillStarter,
  messageEndRef,
  profileComplete,
  olderAvailable,
}: Readonly<{
  messages: ChatMessage[];
  hiddenMessageCount: number;
  onShowMore: () => void;
  onPrefillStarter: (_text: string) => void;
  messageEndRef: RefObject<HTMLDivElement | null>;
  profileComplete: boolean;
  olderAvailable: boolean;
}>): JSX.Element {
  const messageList = (
    <MessageList
      hiddenMessageCount={hiddenMessageCount}
      messageEndRef={messageEndRef}
      messages={messages}
      onShowMore={onShowMore}
      olderAvailable={olderAvailable}
    />
  );
  if (!onlyWelcomeMessage(messages)) {
    return <section className={styles.messagesPane}>{messageList}</section>;
  }
  const variant = profileComplete ? "coaching" : "onboarding";
  return (
    <section className={styles.messagesPane}>
      <EmptyChatLandingCard onPrefill={onPrefillStarter} variant={variant}>
        {messageList}
      </EmptyChatLandingCard>
    </section>
  );
}

function AccountMenu({
  profile,
  onOpenProfile,
  onExport,
}: Readonly<{
  profile: AthleteProfile | null;
  onOpenProfile: () => void;
  onExport: () => void;
}>): JSX.Element {
  return (
    <div aria-label="Account" className={styles.accountMenu} role="menu">
      <div className={styles.accountSummary}>
        <span>Signed in</span>
        <strong>{accountLabel(profile)}</strong>
      </div>
      <button
        className={styles.menuItem}
        onClick={onOpenProfile}
        role="menuitem"
        type="button"
      >
        Profile
      </button>
      <button
        className={styles.menuItem}
        onClick={onExport}
        role="menuitem"
        type="button"
      >
        Export JSONL
      </button>
      <form
        action="/api/oauth/browser-session/logout"
        className={styles.menuForm}
        method="post"
      >
        <button className={styles.menuItem} role="menuitem" type="submit">
          Sign out
        </button>
      </form>
    </div>
  );
}

function ChatTopbar({
  profile,
  coachingStatus,
  onOpenDrawer,
  onExport,
}: Readonly<{
  profile: AthleteProfile | null;
  coachingStatus: string;
  onOpenDrawer: () => void;
  onExport: () => void;
}>): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <header className={styles.topbar}>
      <div className={styles.brandBlock}>
        <p className={styles.brand}>{siteConfig.appName}</p>
        <span className={styles.meta}>{coachingStatus}</span>
      </div>
      <div className={styles.topbarActions}>
        <div className={styles.accountMenuWrap}>
          <button
            aria-expanded={open}
            aria-haspopup="menu"
            aria-label="Account menu"
            className={styles.accountButton}
            onClick={() => setOpen((prev) => !prev)}
            type="button"
          >
            Account
            <span aria-hidden="true">⌄</span>
          </button>
          {open ? (
            <AccountMenu
              profile={profile}
              onOpenProfile={() => {
                setOpen(false);
                onOpenDrawer();
              }}
              onExport={() => {
                setOpen(false);
                onExport();
              }}
            />
          ) : null}
        </div>
      </div>
    </header>
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
  syncingThread,
  sending,
  threadError,
  isMobile,
  waitingStatus,
}: Readonly<{
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

function Composer({
  composer,
  onComposerChange,
  attachments,
  composerBusy,
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
    if (composerBusy) return;
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
    if (composerBusy) return;
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
  const attachClass = composerBusy
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
              disabled={composerBusy}
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

function ProfileDrawerFields({
  profile,
  setProfile,
  saving,
  status,
  onSave,
}: Readonly<{
  profile: AthleteProfile;
  setProfile: (_profile: AthleteProfile) => void;
  saving: boolean;
  status: string | null;
  onSave: () => void;
}>): JSX.Element {
  return (
    <div className={styles.fieldGrid}>
      <label className={styles.fieldLabel}>
        Display name
        <input
          className={styles.fieldInput}
          onChange={(event) =>
            setProfile({
              ...profile,
              display_name: event.target.value || null,
            })
          }
          placeholder="Your name (optional)"
          value={profile.display_name ?? ""}
        />
      </label>
      <label className={styles.fieldLabel}>
        Sports (comma-separated)
        <input
          className={styles.fieldInput}
          onChange={(event) =>
            setProfile({
              ...profile,
              primary_sports: event.target.value
                .split(",")
                .map((s) => s.trim())
                .filter((s) => s.length > 0),
            })
          }
          placeholder="e.g. running, cycling, strength"
          value={profile.primary_sports.join(", ")}
        />
      </label>
      <label className={styles.fieldLabel}>
        Weekly training hours
        <input
          className={styles.fieldInput}
          min="0"
          onChange={(event) =>
            setProfile({
              ...profile,
              weekly_available_hours:
                event.target.value === "" ? null : Number(event.target.value),
            })
          }
          step="0.5"
          type="number"
          value={profile.weekly_available_hours ?? ""}
        />
      </label>
      <div className={styles.actionRow}>
        <button
          className={styles.primaryButton}
          disabled={saving}
          onClick={onSave}
          type="button"
        >
          {saving ? "Saving..." : "Save profile"}
        </button>
      </div>
      {status !== null ? <p className={styles.drawerStatus}>{status}</p> : null}
    </div>
  );
}

function ProfileDrawer({
  open,
  onClose,
  profile,
  setProfile,
  themeMode,
  setTheme,
  saving,
  status,
  onSave,
}: Readonly<{
  open: boolean;
  onClose: () => void;
  profile: AthleteProfile | null;
  setProfile: (_profile: AthleteProfile) => void;
  themeMode: ThemeMode;
  setTheme: (_mode: ThemeMode) => void;
  saving: boolean;
  status: string | null;
  onSave: () => void;
}>): JSX.Element | null {
  if (!open) return null;
  return (
    <div
      className={styles.drawerBackdrop}
      onClick={onClose}
      role="presentation"
    >
      <aside
        aria-label="Profile and preferences"
        className={styles.drawer}
        onClick={(event) => event.stopPropagation()}
      >
        <div className={styles.drawerHeader}>
          <div>
            <h2 className={styles.drawerTitle}>Profile</h2>
            <p className={styles.drawerText}>
              Review the profile details your coach uses for training guidance.
            </p>
          </div>
          <button
            className={styles.drawerClose}
            onClick={onClose}
            type="button"
          >
            Close
          </button>
        </div>

        <div className={styles.themeRow}>
          {(["light", "system", "dark"] as ThemeMode[]).map((option) => (
            <label className={styles.themeOption} key={option}>
              <input
                checked={themeMode === option}
                name="theme"
                onChange={() => setTheme(option)}
                type="radio"
                value={option}
              />
              {option.charAt(0).toUpperCase() + option.slice(1)}
            </label>
          ))}
        </div>

        <ProfileDrawerBody
          profile={profile}
          saving={saving}
          setProfile={setProfile}
          status={status}
          onSave={onSave}
        />
      </aside>
    </div>
  );
}

function ProfileDrawerBody({
  profile,
  setProfile,
  saving,
  status,
  onSave,
}: Readonly<{
  profile: AthleteProfile | null;
  setProfile: (_profile: AthleteProfile) => void;
  saving: boolean;
  status: string | null;
  onSave: () => void;
}>): JSX.Element {
  if (saving && profile === null) {
    return <p className={styles.drawerStatus}>Loading your settings…</p>;
  }
  if (profile === null) {
    return <p className={styles.drawerStatus}>No profile loaded yet.</p>;
  }
  return (
    <ProfileDrawerFields
      profile={profile}
      saving={saving}
      setProfile={setProfile}
      status={status}
      onSave={onSave}
    />
  );
}

export function CoachChat(): JSX.Element {
  const session = useBrowserSession();
  if (session.loading) {
    return <ChatLoading />;
  }
  if (session.token === null) {
    return <LoggedOutLanding error={session.error} />;
  }
  return <SignedInChat token={session.token} />;
}

function SignedInChat({
  token,
}: Readonly<{ token: BrowserTokenResponse }>): JSX.Element {
  const thread = useChatThread(token);
  const athleteProfile = useAthleteProfile(token);
  if (thread.loading) {
    return <ChatLoadingShell />;
  }
  if (thread.data === null) {
    return <ChatErrorState error={thread.error} />;
  }
  return (
    <CoachChatBody
      athleteProfile={athleteProfile}
      setThreadData={thread.setData}
      setThreadError={thread.setError}
      threadData={thread.data}
      threadError={thread.error}
      token={token}
    />
  );
}

function CoachChatBody({
  token,
  threadData,
  threadError,
  setThreadData,
  setThreadError,
  athleteProfile,
}: Readonly<{
  token: BrowserTokenResponse;
  threadData: ChatThreadResponse;
  threadError: string | null;
  setThreadData: (_thread: ChatThreadResponse) => void;
  setThreadError: (_error: string | null) => void;
  athleteProfile: AthleteProfileHook;
}>): JSX.Element {
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const [composer, setComposer] = useState("");
  const [visibleMessageCount, setVisibleMessageCount] = useState(
    MESSAGE_RENDER_BATCH_SIZE,
  );
  const [sending, setSending] = useState(false);
  const [syncingThread, setSyncingThread] = useState(false);
  const [waitingStatusIndex, setWaitingStatusIndex] = useState(0);
  const [attachments, setAttachments] = useState<LocalAttachment[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const { mode: themeMode, setTheme } = useTheme();
  const isMobile = useIsMobile();

  const persistedMessages = threadData.thread.messages;
  const threadId = threadData.thread.id;

  const chatMessages = useMemo<UIMessage[]>(
    () => persistedMessages.map(toUiMessage),
    [persistedMessages],
  );
  const { messages: liveMessages, sendMessage } = useChat({
    id: threadId,
    messages: chatMessages,
    transport: new DefaultChatTransport({
      api: "/api/chat",
      credentials: "include",
      prepareSendMessagesRequest: ({
        messages,
      }): { body: Record<string, unknown> } => ({
        body:
          process.env["NEXT_PUBLIC_COACH_CONTEXT_STRATEGY"] === "full_history"
            ? { messages }
            : { message: messages.at(-1) },
      }),
    }),
  });
  const composerBusy = sending || syncingThread;
  const displayedMessages = useMemo<ChatMessage[]>(() => {
    const persistedIds = new Set(persistedMessages.map((m) => m.id));
    const additional = liveMessages
      .filter((m) => !persistedIds.has(m.id))
      .map((m) => toLiveChatMessage(m, threadId, token.user_id))
      .filter(
        (m): m is ChatMessage => m !== null && (m.parts ?? []).length > 0,
      );
    return [...persistedMessages, ...additional];
  }, [liveMessages, persistedMessages, threadId, token.user_id]);

  useEffect(() => {
    const scrollTarget = messageEndRef.current;
    if (
      scrollTarget !== null &&
      typeof scrollTarget.scrollIntoView === "function"
    ) {
      scrollTarget.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [persistedMessages.length, sending]);

  useEffect(() => {
    setVisibleMessageCount(MESSAGE_RENDER_BATCH_SIZE);
  }, [threadId]);

  useEffect((): (() => void) | void => {
    if (!sending) {
      setWaitingStatusIndex(0);
      return;
    }
    const intervalId = window.setInterval(() => {
      setWaitingStatusIndex(
        (current) => (current + 1) % WAITING_STATUSES.length,
      );
    }, WAITING_STATUS_INTERVAL_MS);
    return (): void => {
      window.clearInterval(intervalId);
    };
  }, [sending]);

  useEffect((): (() => void) => {
    return (): void => {
      removePreviewUrls(attachments);
    };
  }, [attachments]);

  async function uploadOneAttachment(
    attachmentId: string,
    file: File,
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
        setAttachments((current) =>
          current.map((attachment) =>
            attachment.id === attachmentId
              ? {
                  ...attachment,
                  object_key: uploaded.object_key,
                  public_url: null,
                  status: "error",
                }
              : attachment,
          ),
        );
        Sentry.logger.error("chat: attachment upload returned no public_url", {
          filename_suffix: file.name.includes(".")
            ? file.name.slice(file.name.lastIndexOf(".")).slice(0, 16)
            : "",
          content_type: contentType,
        });
        setThreadError(
          "We uploaded the file but couldn't get a shareable link back. Ask an admin to check the storage configuration.",
        );
        return;
      }
      Sentry.logger.info("chat attachment ready", {
        filename_suffix: file.name.includes(".")
          ? file.name.slice(file.name.lastIndexOf(".")).slice(0, 16)
          : "",
        content_type: contentType,
        has_public_url: true,
      });
      setAttachments((current) =>
        current.map((attachment) =>
          attachment.id === attachmentId
            ? {
                ...attachment,
                object_key: uploaded.object_key,
                public_url: uploaded.public_url,
                status: "uploaded",
              }
            : attachment,
        ),
      );
    } catch (error) {
      Sentry.logger.error("chat attachment upload failed", {
        filename_suffix: file.name.includes(".")
          ? file.name.slice(file.name.lastIndexOf(".")).slice(0, 16)
          : "",
        content_type: contentType,
      });
      setAttachments((current) =>
        current.map((attachment) =>
          attachment.id === attachmentId
            ? { ...attachment, status: "error" }
            : attachment,
        ),
      );
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

  async function handleFilesAdded(files: File[]): Promise<void> {
    if (files.length === 0) return;

    const uploadQueue: Array<{ attachmentId: string; file: File }> = [];
    const nextLocalAttachments: LocalAttachment[] = [];

    for (const file of files) {
      if (!isSupportedAttachment(file)) {
        setThreadError(
          "Only image, GPX, FIT, and TCX attachments are supported in the coach chat.",
        );
        continue;
      }
      const attachmentId = crypto.randomUUID();
      uploadQueue.push({ attachmentId, file });
      nextLocalAttachments.push({
        id: attachmentId,
        content_type: activityContentType(file),
        filename: file.name,
        object_key: "",
        preview_url: file.type.startsWith("image/")
          ? URL.createObjectURL(file)
          : null,
        public_url: null,
        status: "uploading",
      });
    }
    setAttachments((current) => [...current, ...nextLocalAttachments]);

    for (const { attachmentId, file } of uploadQueue) {
      await uploadOneAttachment(attachmentId, file);
    }
  }

  async function handleSend(): Promise<void> {
    if (composerBusy) return;
    if (!hasSendableContent(composer, attachments)) return;

    setSending(true);
    setThreadError(null);
    try {
      const pendingComposer = composer;
      const pendingAttachments = attachments;
      const messageId = crypto.randomUUID();
      Sentry.logger.info("user turn submitted", {
        has_text: pendingComposer.trim().length > 0,
        attachment_count: pendingAttachments.length,
        message_id: messageId,
      });
      const messageParts: UIMessage["parts"] =
        pendingComposer.trim().length > 0
          ? [{ type: "text" as const, text: pendingComposer }]
          : [];
      await sendMessage({
        id: messageId,
        parts: [...messageParts, ...uploadedFileParts(pendingAttachments)],
      });
      // Clear the draft only after the send succeeds so a failed send leaves the
      // composer text and attachments intact for the user to retry. The textarea
      // stays editable while the request is in flight, so only clear the text if
      // it still matches what we sent — otherwise we'd wipe newly typed input.
      removePreviewUrls(pendingAttachments);
      setAttachments([]);
      setComposer((current) => (current === pendingComposer ? "" : current));
      setSending(false);
      setSyncingThread(true);
      try {
        const refreshed = await loadChatThread();
        setThreadData(refreshed);
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
      setThreadError(errorMessage(error, "Unable to send your message."));
    } finally {
      setSending(false);
    }
  }

  async function openDrawer(): Promise<void> {
    setDrawerOpen(true);
    await athleteProfile.ensureLoaded();
  }

  async function handleSaveProfile(): Promise<void> {
    const saved = await athleteProfile.save();
    if (saved === null) return;
    try {
      const refreshed = await loadChatThread();
      setThreadData(refreshed);
    } catch (refreshError) {
      Sentry.logger.warn("chat thread refresh failed after profile save");
      console.warn(
        "Profile saved but chat thread refresh failed",
        refreshError,
      );
    }
  }

  function handleExportJsonl(): void {
    const exportDate = new Date().toISOString().slice(0, 10);
    downloadTextFile(
      `coaching-history-${exportDate}.jsonl`,
      serializeChatHistoryJsonl(persistedMessages),
      "application/x-ndjson;charset=utf-8",
    );
  }

  const messages = displayedMessages;
  const hiddenMessageCount = Math.max(0, messages.length - visibleMessageCount);
  const visibleMessages =
    hiddenMessageCount > 0 ? messages.slice(hiddenMessageCount) : messages;
  const composerPlaceholder = composerPlaceholderFor(
    visibleMessages,
    threadData.profile_complete,
  );

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <div className={styles.frame}>
          <ChatTopbar
            coachingStatus={coachingStatusLabel(threadData.profile_complete)}
            onExport={handleExportJsonl}
            onOpenDrawer={() => {
              void openDrawer();
            }}
            profile={athleteProfile.profile}
          />

          <MessagesSection
            hiddenMessageCount={hiddenMessageCount}
            messageEndRef={messageEndRef}
            messages={visibleMessages}
            onPrefillStarter={setComposer}
            olderAvailable={(threadData.next_cursor ?? null) !== null}
            onShowMore={() => {
              if (hiddenMessageCount > 0) {
                setVisibleMessageCount(
                  (current) => current + MESSAGE_RENDER_BATCH_SIZE,
                );
                return;
              }
              if (!threadData.next_cursor) return;
              void loadChatMessages(threadData.next_cursor)
                .then((page) => {
                  setThreadData({
                    ...threadData,
                    next_cursor: page.next_cursor,
                    thread: {
                      ...threadData.thread,
                      messages: [...page.messages, ...persistedMessages],
                    },
                  });
                  setVisibleMessageCount(
                    (current) => current + page.messages.length,
                  );
                })
                .catch((error) =>
                  setThreadError(
                    errorMessage(error, "Unable to load older messages."),
                  ),
                );
            }}
            profileComplete={threadData.profile_complete}
          />

          <Composer
            attachments={attachments}
            composer={composer}
            composerBusy={composerBusy}
            isMobile={isMobile}
            onComposerChange={setComposer}
            onFilesAdded={(files) => {
              void handleFilesAdded(files);
            }}
            onSend={() => {
              void handleSend();
            }}
            placeholder={composerPlaceholder}
            sending={sending}
            syncingThread={syncingThread}
            threadError={threadError}
            waitingStatus={WAITING_STATUSES[waitingStatusIndex]}
          />
        </div>
      </div>

      <ProfileDrawer
        onClose={() => setDrawerOpen(false)}
        onSave={() => {
          void handleSaveProfile();
        }}
        open={drawerOpen}
        profile={athleteProfile.profile}
        saving={athleteProfile.saving}
        setProfile={athleteProfile.setProfile}
        setTheme={setTheme}
        status={athleteProfile.status}
        themeMode={themeMode}
      />
    </main>
  );
}
