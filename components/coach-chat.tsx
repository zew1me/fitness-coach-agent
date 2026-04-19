"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type FileUIPart, type UIMessage } from "ai";
import Link from "next/link";
import React, { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, JSX } from "react";

import {
  createChatUploadIntent,
  fetchBrowserToken,
  loadChatThread,
  loadProfile,
  saveProfile,
} from "../lib/coach-api";
import { siteConfig } from "../lib/site";
import type {
  AdaptedPlan,
  AthleteProfile,
  BrowserTokenResponse,
  ChatAttachment,
  ChatMessage,
  ChatThreadResponse,
} from "../lib/types";
import { useTheme } from "../lib/use-theme";
import type { ThemeMode } from "../lib/use-theme";

import styles from "./coach-chat.module.css";

type SessionState = {
  error: string | null;
  loading: boolean;
  token: BrowserTokenResponse | null;
};

type LocalAttachment = ChatAttachment & {
  previewUrl: string | null;
  status: "error" | "uploaded" | "uploading";
};

function emptyProfile(userId: string): AthleteProfile {
  return {
    user_id: userId,
    coaching_state: "onboarding",
    primary_sports: [],
  };
}

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
    if (attachment.previewUrl !== null) {
      URL.revokeObjectURL(attachment.previewUrl);
    }
  }
}

function toUiMessage(message: ChatMessage): UIMessage {
  return {
    id: message.id,
    parts: [{ type: "text", text: message.content }],
    role: message.role,
  };
}

function uiPartText(part: UIMessage["parts"][number]): string | null {
  if (part.type === "text") {
    return part.text;
  }

  if (part.type === "dynamic-tool") {
    return `Using ${part.toolName}`;
  }

  if (part.type.startsWith("tool-")) {
    return `Using ${part.type.slice("tool-".length)}`;
  }

  return null;
}

function uiMessageText(message: UIMessage): string {
  return message.parts.flatMap((part) => {
    const text = uiPartText(part);
    return text === null ? [] : [text];
  }).join("\n");
}

function toLiveChatMessage(message: UIMessage, threadId: string, userId: string): ChatMessage | null {
  if (message.role !== "assistant" && message.role !== "user") {
    return null;
  }

  return {
    attachments: [],
    content: uiMessageText(message),
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

function ChatLoading(): JSX.Element {
  return (
    <main className={styles.landingWrap}>
      <section className={styles.statusBanner}>
        <p className={styles.meta}>Checking your browser session…</p>
      </section>
    </main>
  );
}

function LoggedOutLanding({ error }: Readonly<{ error: string | null }>): JSX.Element {
  return (
    <main className={styles.landingWrap}>
      <section className={styles.landingCard}>
        <p className={styles.eyebrow}>Athlete Coach</p>
        <h1 className={styles.landingTitle}>A simpler coaching experience, built like chat.</h1>
        <p className={styles.landingText}>
          Sign in once, then use a single focused conversation for check-ins, plan requests,
          and photo-backed coaching updates. The forms are gone from the main surface so the
          experience feels closer to a modern chat assistant than a dashboard.
        </p>
        {error ? (
          <p className={styles.landingHint}>
            Sign in to start your coaching chat. If the app feels slow to wake up, give it a
            moment and try again.
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

function ChatErrorState({ error }: Readonly<{ error: string | null }>): JSX.Element {
  return (
    <main className={styles.landingWrap}>
      <section className={styles.errorCard}>
        <OutRunningIllustration />
        <p className={styles.eyebrow}>Coach unavailable</p>
        <h1 className={styles.errorTitle}>Sorry, we&apos;re out running.</h1>
        <p className={styles.errorText}>
          We&apos;ll be back soon. You&apos;ve got this. In the meantime, hang onto the thread and
          try again in a minute or two.
        </p>
        {error ? <p className={styles.errorDetail}>{error}</p> : null}
        <div className={styles.actionRow}>
          <button className={styles.primaryButton} onClick={() => window.location.reload()} type="button">
            Retry
          </button>
        </div>
      </section>
    </main>
  );
}

// eslint-disable-next-line complexity
export function CoachChat(): JSX.Element {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const messageEndRef = useRef<HTMLDivElement | null>(null);
  const [session, setSession] = useState<SessionState>({
    token: null,
    error: null,
    loading: true,
  });
  const [threadState, setThreadState] = useState<{
    data: ChatThreadResponse | null;
    error: string | null;
    loading: boolean;
  }>({
    data: null,
    error: null,
    loading: false,
  });
  const [composer, setComposer] = useState("");
  const [sending, setSending] = useState(false);
  const [attachments, setAttachments] = useState<LocalAttachment[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [drawerStatus, setDrawerStatus] = useState<string | null>(null);
  const [profile, setProfile] = useState<AthleteProfile | null>(null);
  const { mode: themeMode, setTheme } = useTheme();
  const chatMessages = useMemo<UIMessage[]>(
    () => threadState.data?.thread.messages.map(toUiMessage) ?? [],
    [threadState.data?.thread.messages],
  );
  const { messages: liveMessages, sendMessage } = useChat({
    id: threadState.data?.thread.id ?? "coach-chat",
    messages: chatMessages,
    transport: new DefaultChatTransport({
      api: "/api/chat",
      credentials: "include",
    }),
  });
  const displayedMessages = useMemo<ChatMessage[]>(() => {
    if (threadState.data === null || session.token === null) {
      return [];
    }

    const thread = threadState.data.thread;
    const token = session.token;
    const persistedMessages = thread.messages;
    const persistedIds = new Set(persistedMessages.map((message) => message.id));
    const additionalMessages = liveMessages
      .filter((message) => !persistedIds.has(message.id))
      .map((message) => toLiveChatMessage(message, thread.id, token.user_id))
      .filter((message): message is ChatMessage => message !== null && message.content.length > 0);

    return [...persistedMessages, ...additionalMessages];
  }, [liveMessages, session.token, threadState.data]);

  useEffect(() => {
    async function bootstrap(): Promise<void> {
      try {
        const token = await fetchBrowserToken();
        setSession({ token, error: null, loading: false });
      } catch (error) {
        setSession({
          token: null,
          error: error instanceof Error ? error.message : "Unable to connect your browser session.",
          loading: false,
        });
      }
    }

    void bootstrap();
  }, []);

  useEffect(() => {
    if (session.token === null) {
      return;
    }

    async function loadThread(): Promise<void> {
      setThreadState((current) => ({ ...current, loading: true, error: null }));
      try {
        const thread = await loadChatThread();
        setThreadState({ data: thread, error: null, loading: false });
      } catch (error) {
        setThreadState({
          data: null,
          error: error instanceof Error ? error.message : "Unable to load the coaching conversation.",
          loading: false,
        });
      }
    }

    void loadThread();
  }, [session.token]);

  useEffect(() => {
    const scrollTarget = messageEndRef.current;
    if (scrollTarget !== null && typeof scrollTarget.scrollIntoView === "function") {
      scrollTarget.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [threadState.data?.thread.messages.length, sending]);

  useEffect((): (() => void) => {
    return () => {
      removePreviewUrls(attachments);
    };
  }, [attachments]);

  async function handleSend(): Promise<void> {
    if (sending || threadState.data === null) {
      return;
    }
    if (composer.trim().length === 0 && attachments.length === 0) {
      return;
    }

    setSending(true);
    setThreadState((current) => ({ ...current, error: null }));
    try {
      const token = session.token;
      if (token === null) {
        throw new Error("Unable to send without an active browser session.");
      }
      const thread = threadState.data.thread;
      const optimisticMessage: ChatMessage = {
        id: `local-${Date.now()}`,
        attachments: attachments
          .filter((attachment) => attachment.status === "uploaded")
          .map(({ content_type, filename, object_key, public_url }) => ({
            content_type,
            created_at: new Date().toISOString(),
            filename,
            object_key,
            public_url,
            user_id: token.user_id,
          })),
        content: composer,
        created_at: new Date().toISOString(),
        metadata: { message_kind: "user_turn" },
        role: "user",
        thread_id: thread.id,
        user_id: token.user_id,
      };
      await sendMessage({
        parts: [{ type: "text", text: optimisticMessage.content }, ...uploadedFileParts(attachments)],
      });
      removePreviewUrls(attachments);
      setAttachments([]);
      setComposer("");
      setThreadState({
        data: {
          ...threadState.data,
          thread: {
            ...thread,
            messages: [...thread.messages, optimisticMessage],
          },
        },
        error: null,
        loading: false,
      });
    } catch (error) {
      setThreadState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Unable to send your message.",
      }));
    } finally {
      setSending(false);
    }
  }

  async function handleFileSelect(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const selectedFiles = Array.from(event.target.files ?? []);
    if (selectedFiles.length === 0) {
      return;
    }

    const nextLocalAttachments = selectedFiles
      .filter((file) => file.type.startsWith("image/"))
      .map<LocalAttachment>((file) => ({
        content_type: file.type,
        filename: file.name,
        object_key: "",
        previewUrl: URL.createObjectURL(file),
        public_url: null,
        status: "uploading",
      }));
    setAttachments((current) => [...current, ...nextLocalAttachments]);

    for (const file of selectedFiles) {
      if (!file.type.startsWith("image/")) {
        setThreadState((current) => ({
          ...current,
          error: "Only image attachments are supported in the coach chat.",
        }));
        continue;
      }

      try {
        const intent = await createChatUploadIntent({
          content_length: file.size,
          content_type: file.type,
          filename: file.name,
          purpose: "chat-attachment",
        });

        await fetch(intent.upload_url, {
          method: intent.method,
          headers: intent.headers,
          body: file,
        });

        setAttachments((current) =>
          current.map((attachment) =>
            attachment.filename === file.name && attachment.object_key === ""
              ? {
                  ...attachment,
                  object_key: intent.object_key,
                  public_url: intent.public_url,
                  status: "uploaded",
                }
              : attachment,
          ),
        );
      } catch (error) {
        setAttachments((current) =>
          current.map((attachment) =>
            attachment.filename === file.name && attachment.object_key === ""
              ? {
                  ...attachment,
                  status: "error",
                }
              : attachment,
          ),
        );
        setThreadState((current) => ({
          ...current,
          error: error instanceof Error ? error.message : "Unable to upload that attachment.",
        }));
      }
    }

    event.target.value = "";
  }

  async function openDrawer(): Promise<void> {
    if (session.token === null) {
      return;
    }
    setDrawerOpen(true);
    if (profile !== null) {
      return;
    }
    setDrawerLoading(true);
    setDrawerStatus(null);
    try {
      const loaded = await loadProfile(session.token.user_id);
      setProfile(loaded);
    } catch {
      setProfile(emptyProfile(session.token.user_id));
    } finally {
      setDrawerLoading(false);
    }
  }

  async function handleSaveProfile(): Promise<void> {
    if (profile === null) {
      return;
    }
    setDrawerLoading(true);
    setDrawerStatus(null);
    try {
      const saved = await saveProfile(profile);
      setProfile(saved);
      setDrawerStatus("Saved your athlete settings.");
      if (session.token !== null) {
        const thread = await loadChatThread();
        setThreadState({ data: thread, error: null, loading: false });
      }
    } catch (error) {
      setDrawerStatus(error instanceof Error ? error.message : "Unable to save your athlete settings.");
    } finally {
      setDrawerLoading(false);
    }
  }

  function renderPlan(plan: AdaptedPlan): JSX.Element {
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

  function renderMessages(messages: ChatMessage[]): JSX.Element {
    return (
      <div className={styles.messageStack}>
        {messages.map((message) => {
          const rowClass =
            message.role === "assistant" ? styles.rowAssistant : styles.rowUser;
          const bubbleClass =
            message.role === "assistant"
              ? `${styles.bubble} ${styles.assistantBubble}`
              : `${styles.bubble} ${styles.userBubble}`;
          const plan = message.metadata.plan;

          return (
            <div className={rowClass} key={message.id}>
              <div className={bubbleClass}>
                {message.content ? <p className={styles.messageText}>{message.content}</p> : null}
                {message.attachments.length > 0 ? (
                  <div className={styles.attachmentGrid}>
                    {message.attachments.map((attachment) => (
                      <div className={styles.attachmentThumb} key={attachment.id ?? attachment.object_key}>
                        {attachment.public_url ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img alt={attachment.filename} src={attachment.public_url} />
                        ) : (
                          <div className={styles.attachmentThumb} />
                        )}
                        <span className={styles.attachmentName}>{attachment.filename}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
                {plan ? renderPlan(plan) : null}
                <div className={styles.attachmentName}>{readableTime(message.created_at)}</div>
              </div>
            </div>
          );
        })}
        <div ref={messageEndRef} />
      </div>
    );
  }

  if (session.loading) {
    return <ChatLoading />;
  }

  if (session.token === null) {
    return <LoggedOutLanding error={session.error} />;
  }

  if (threadState.loading) {
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

  if (threadState.data === null) {
    return <ChatErrorState error={threadState.error} />;
  }

  const messages = displayedMessages;

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <div className={styles.frame}>
          <header className={styles.topbar}>
            <div className={styles.brandBlock}>
              <p className={styles.brand}>{siteConfig.appName}</p>
              <span className={styles.meta}>
                Coaching {threadState.data.profile_complete ? "ready" : "collecting your setup"} for{" "}
                {session.token.user_id}
              </span>
            </div>
            <div className={styles.topbarActions}>
              <button className={styles.settingsButton} onClick={() => void openDrawer()} type="button">
                Settings
              </button>
              <Link className={styles.ghostButton} href="/login?return_to=/">
                Switch login
              </Link>
            </div>
          </header>

          <section className={styles.messagesPane}>
            {onlyWelcomeMessage(messages) ? (
              <div className={styles.emptyState}>
                <div className={styles.emptyCard}>
                  <p className={styles.eyebrow}>Coach Chat</p>
                  <h1 className={styles.emptyTitle}>What should we work on next?</h1>
                  <p className={styles.emptyText}>
                    Use this thread for quick training updates, image-backed check-ins, and your next
                    14-day plan. I’ll keep the details in the background and keep the surface focused.
                  </p>
                  <div className={styles.starterRow}>
                    <button
                      className={styles.starterButton}
                      onClick={() => setComposer("I just finished my ride and want to log how it felt.")}
                      type="button"
                    >
                      Log today’s ride
                    </button>
                    <button
                      className={styles.starterButton}
                      onClick={() => setComposer("Build my next 14-day training plan.")}
                      type="button"
                    >
                      Generate next plan
                    </button>
                    <button
                      className={styles.starterButton}
                      onClick={() => setComposer("I have some soreness and travel coming up this week.")}
                      type="button"
                    >
                      Adapt around fatigue
                    </button>
                  </div>
                  {renderMessages(messages)}
                </div>
              </div>
            ) : (
              renderMessages(messages)
            )}
          </section>

          <div className={styles.composerWrap}>
            <div className={styles.composerCard}>
              {attachments.length > 0 ? (
                <div className={styles.uploadRow}>
                  {attachments.map((attachment) => (
                    <div className={styles.uploadChip} key={`${attachment.filename}-${attachment.previewUrl ?? ""}`}>
                      <span>{attachment.filename}</span>
                      <span className={styles.uploadStatus}>
                        {attachment.status === "uploading"
                          ? "Uploading"
                          : attachment.status === "uploaded"
                            ? "Ready"
                            : "Upload failed"}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}

              <div className={styles.composerRow}>
                <input
                  accept="image/*"
                  className={styles.hiddenInput}
                  multiple
                  onChange={(event) => {
                    void handleFileSelect(event);
                  }}
                  ref={fileInputRef}
                  type="file"
                />
                <button
                  className={styles.attachButton}
                  disabled={!threadState.data.attachments_enabled || sending}
                  onClick={() => fileInputRef.current?.click()}
                  title={
                    threadState.data.attachments_enabled
                      ? "Add photo"
                      : "Photo uploads are not configured in this environment"
                  }
                  type="button"
                >
                  +
                </button>
                <textarea
                  className={styles.composerInput}
                  onChange={(event) => setComposer(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void handleSend();
                    }
                  }}
                  placeholder="Ask anything about your training..."
                  rows={1}
                  value={composer}
                />
                <button
                  className={styles.sendButton}
                  disabled={sending || (composer.trim().length === 0 && attachments.length === 0)}
                  onClick={() => {
                    void handleSend();
                  }}
                  type="button"
                >
                  {sending ? "Sending..." : "Send"}
                </button>
              </div>
              <div className={styles.composerHint}>
                {threadState.error ? (
                  <span className={styles.errorTextInline}>{threadState.error}</span>
                ) : (
                  "Use Shift+Enter for a new line. Add photos with the plus button."
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {drawerOpen ? (
        <div
          className={styles.drawerBackdrop}
          onClick={() => setDrawerOpen(false)}
          role="presentation"
        >
          <aside
            aria-label="Athlete settings"
            className={styles.drawer}
            onClick={(event) => event.stopPropagation()}
          >
            <div className={styles.drawerHeader}>
              <div>
                <h2 className={styles.drawerTitle}>Athlete settings</h2>
                <p className={styles.drawerText}>
                  The main experience stays chat-first, but you can still review or edit the profile the coach uses.
                </p>
              </div>
              <button className={styles.drawerClose} onClick={() => setDrawerOpen(false)} type="button">
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

            {drawerLoading && profile === null ? (
              <p className={styles.drawerStatus}>Loading your settings…</p>
            ) : profile ? (
              <div className={styles.fieldGrid}>
                <label className={styles.fieldLabel}>
                  User ID
                  <input
                    className={styles.fieldInput}
                    readOnly
                    value={profile.user_id}
                  />
                </label>
                <label className={styles.fieldLabel}>
                  Display name
                  <input
                    className={styles.fieldInput}
                    onChange={(event) => setProfile({ ...profile, display_name: event.target.value || null })}
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
                    placeholder="e.g. running, cycling"
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
                        weekly_available_hours: event.target.value === "" ? null : Number(event.target.value),
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
                    disabled={drawerLoading}
                    onClick={() => {
                      void handleSaveProfile();
                    }}
                    type="button"
                  >
                    {drawerLoading ? "Saving..." : "Save settings"}
                  </button>
                </div>
                {drawerStatus ? <p className={styles.drawerStatus}>{drawerStatus}</p> : null}
              </div>
            ) : (
              <p className={styles.drawerStatus}>No profile loaded yet.</p>
            )}
          </aside>
        </div>
      ) : null}
    </main>
  );
}
