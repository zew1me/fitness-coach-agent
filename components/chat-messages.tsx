"use client";

import type { FileUIPart, UIMessage } from "ai";
import type { ReactNode, RefObject } from "react";

import { fileTypeBadge } from "../lib/chat-attachments";
import { onlyWelcomeMessage } from "../lib/chat-ui-messages";
import type { AdaptedPlan, ChatMessage } from "../lib/types";
import { MESSAGE_RENDER_BATCH_SIZE } from "../lib/use-message-window";

import styles from "./coach-chat.module.css";
import { StatusCard } from "./status-card";

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

function readableTime(timestamp: string): string {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function friendlyToolStatus(toolName: string): string {
  const statuses: Record<string, string> = {
    analyze_screenshot: "Reviewing your uploaded image...",
    calculate_zones: "Calculating your training zones...",
    get_athlete_context: "Looking up your info...",
    process_uploaded_file: "Reading your file...",
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

function PlanCard({ plan }: Readonly<{ plan: AdaptedPlan }>): ReactNode {
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

function AttachmentTile({ part }: Readonly<{ part: FileUIPart }>): ReactNode {
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

/**
 * Indeterminate "the coach is working" affordance for an in-flight assistant
 * bubble that has no renderable content yet (issue #406).
 *
 * Visual only: `ComposerHint` already announces WAITING_STATUSES through an
 * aria-live region while `sending`, so a second live region here would
 * double-announce to screen readers.
 */
function ThinkingIndicator(): ReactNode {
  return (
    <span
      aria-hidden="true"
      className={styles.thinkingIndicator}
      data-testid="thinking-indicator"
    >
      <span className={styles.thinkingDot} />
      <span className={styles.thinkingDot} />
      <span className={styles.thinkingDot} />
    </span>
  );
}

/**
 * The SDK pushes a `step-start` part (and reasoning parts) into a live assistant
 * message before any text or tool part exists; `uiPartText` returns null for
 * those, so the bubble would otherwise mount visibly empty (#406). The check is
 * deliberately trigger-agnostic, so a future unrenderable part type is covered
 * too.
 *
 * Scoped to the *streaming* message: persisted rows with no renderable content
 * (e.g. an old tool-only turn) keep their timestamp-only appearance rather than
 * pinning a permanent spinner into the transcript.
 */
function shouldShowThinking(
  message: ChatMessage,
  { hasRenderableContent }: Readonly<{ hasRenderableContent: boolean }>,
): boolean {
  if (hasRenderableContent) return false;
  return (
    message.role === "assistant" &&
    message.metadata.message_kind === "streaming"
  );
}

function MessageBubble({
  message,
}: Readonly<{ message: ChatMessage }>): ReactNode {
  const rowClass =
    message.role === "assistant" ? styles.rowAssistant : styles.rowUser;
  const bubbleClass =
    message.role === "assistant"
      ? `${styles.bubble} ${styles.assistantBubble}`
      : `${styles.bubble} ${styles.userBubble}`;
  const plan = message.metadata.plan;
  const parts = message.parts as UIMessage["parts"];
  const textBlocks = parts
    .map((part) => uiPartText(part))
    .filter((text): text is string => text !== null && text.length > 0);
  const fileParts = parts.filter(
    (part): part is FileUIPart => part.type === "file",
  );
  const showThinking = shouldShowThinking(message, {
    hasRenderableContent:
      textBlocks.length > 0 || fileParts.length > 0 || Boolean(plan),
  });

  return (
    <div className={rowClass}>
      <div
        className={bubbleClass}
        data-role={message.role}
        data-testid="chat-bubble"
      >
        {showThinking ? <ThinkingIndicator /> : null}
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
  loadingOlder,
  onShowMore,
  messageEndRef,
  olderAvailable,
}: Readonly<{
  messages: ChatMessage[];
  hiddenMessageCount: number;
  loadingOlder: boolean;
  onShowMore: () => void;
  messageEndRef: RefObject<HTMLDivElement | null>;
  olderAvailable: boolean;
}>): ReactNode {
  return (
    <div className={styles.messageStack}>
      {hiddenMessageCount > 0 || olderAvailable ? (
        <button
          className={styles.historyLoadButton}
          disabled={loadingOlder}
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
}>): ReactNode {
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
      <StatusCard body={body} headingLevel="h1" title={title}>
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
      </StatusCard>
    </div>
  );
}

export function MessagesSection({
  messages,
  hiddenMessageCount,
  loadingOlder,
  onShowMore,
  onPrefillStarter,
  messageEndRef,
  profileComplete,
  olderAvailable,
}: Readonly<{
  messages: ChatMessage[];
  hiddenMessageCount: number;
  loadingOlder: boolean;
  onShowMore: () => void;
  onPrefillStarter: (_text: string) => void;
  messageEndRef: RefObject<HTMLDivElement | null>;
  profileComplete: boolean;
  olderAvailable: boolean;
}>): ReactNode {
  const messageList = (
    <MessageList
      hiddenMessageCount={hiddenMessageCount}
      loadingOlder={loadingOlder}
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
