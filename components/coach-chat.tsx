"use client";

import Link from "next/link";
import React, { useEffect, useRef, useState } from "react";
import type { ChangeEvent, JSX } from "react";

import {
  createChatUploadIntent,
  fetchBrowserToken,
  loadChatThread,
  loadProfile,
  parseListInput,
  saveProfile,
  sendChatMessage,
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
    constraints: [],
    goals: [],
    injuries_rehab: [],
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

function withOptionalNumber(
  profile: AthleteProfile,
  field: "age" | "cycling_ftp_watts" | "weight_kg",
  rawValue: string,
): AthleteProfile {
  if (rawValue === "") {
    const nextProfile = { ...profile };
    delete nextProfile[field];
    return nextProfile;
  }

  return {
    ...profile,
    [field]: Number(rawValue),
  };
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
        {error ? <p className={styles.errorTextInline}>{error}</p> : null}
        <div className={styles.actionRow}>
          <Link className={styles.primaryButton} href="/login?return_to=/">
            Continue with magic link
          </Link>
          <Link className={styles.secondaryButton} href="/consent">
            OAuth consent
          </Link>
        </div>
      </section>
    </main>
  );
}

function ChatErrorState({ error }: Readonly<{ error: string | null }>): JSX.Element {
  return (
    <main className={styles.landingWrap}>
      <section className={styles.errorCard}>
        <p className={styles.eyebrow}>Coach unavailable</p>
        <h1 className={styles.errorTitle}>The coach chat could not start.</h1>
        <p className={styles.errorText}>
          {error ??
            "The post-login assistant needs the chat backend and model key configured before it can respond."}
        </p>
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
  const [profileLists, setProfileLists] = useState({
    constraints: "",
    goals: "",
    injuries_rehab: "",
  });

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
      const nextThread = await sendChatMessage({
        content: composer,
        attachments: attachments
          .filter((attachment) => attachment.status === "uploaded")
          .map(({ content_type, filename, object_key, public_url }) => ({
            content_type,
            filename,
            object_key,
            public_url,
          })),
      });
      removePreviewUrls(attachments);
      setAttachments([]);
      setComposer("");
      setThreadState({ data: nextThread, error: null, loading: false });
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
      setProfileLists({
        constraints: loaded.constraints.join("\n"),
        goals: loaded.goals.join("\n"),
        injuries_rehab: loaded.injuries_rehab.join("\n"),
      });
    } catch {
      const freshProfile = emptyProfile(session.token.user_id);
      setProfile(freshProfile);
      setProfileLists({
        constraints: "",
        goals: "",
        injuries_rehab: "",
      });
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
      const saved = await saveProfile({
        ...profile,
        constraints: parseListInput(profileLists.constraints),
        goals: parseListInput(profileLists.goals),
        injuries_rehab: parseListInput(profileLists.injuries_rehab),
      });
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

  const messages = threadState.data.thread.messages;

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

            {drawerLoading && profile === null ? (
              <p className={styles.drawerStatus}>Loading your settings…</p>
            ) : profile ? (
              <div className={styles.fieldGrid}>
                <label className={styles.fieldLabel}>
                  User ID
                  <input
                    className={styles.fieldInput}
                    onChange={(event) => setProfile({ ...profile, user_id: event.target.value })}
                    value={profile.user_id}
                  />
                </label>
                <label className={styles.fieldLabel}>
                  Goals
                  <textarea
                    className={styles.fieldInput}
                    onChange={(event) =>
                      setProfileLists((current) => ({ ...current, goals: event.target.value }))
                    }
                    rows={4}
                    value={profileLists.goals}
                  />
                </label>
                <label className={styles.fieldLabel}>
                  FTP (watts)
                  <input
                    className={styles.fieldInput}
                    onChange={(event) =>
                      setProfile(withOptionalNumber(profile, "cycling_ftp_watts", event.target.value))
                    }
                    type="number"
                    value={profile.cycling_ftp_watts ?? ""}
                  />
                </label>
                <label className={styles.fieldLabel}>
                  Weight (kg)
                  <input
                    className={styles.fieldInput}
                    onChange={(event) =>
                      setProfile(withOptionalNumber(profile, "weight_kg", event.target.value))
                    }
                    step="0.1"
                    type="number"
                    value={profile.weight_kg ?? ""}
                  />
                </label>
                <label className={styles.fieldLabel}>
                  Age
                  <input
                    className={styles.fieldInput}
                    onChange={(event) =>
                      setProfile(withOptionalNumber(profile, "age", event.target.value))
                    }
                    type="number"
                    value={profile.age ?? ""}
                  />
                </label>
                <label className={styles.fieldLabel}>
                  Constraints
                  <textarea
                    className={styles.fieldInput}
                    onChange={(event) =>
                      setProfileLists((current) => ({ ...current, constraints: event.target.value }))
                    }
                    rows={4}
                    value={profileLists.constraints}
                  />
                </label>
                <label className={styles.fieldLabel}>
                  Injuries / rehab
                  <textarea
                    className={styles.fieldInput}
                    onChange={(event) =>
                      setProfileLists((current) => ({
                        ...current,
                        injuries_rehab: event.target.value,
                      }))
                    }
                    rows={3}
                    value={profileLists.injuries_rehab}
                  />
                </label>
                <label className={styles.fieldLabel}>
                  Notes
                  <textarea
                    className={styles.fieldInput}
                    onChange={(event) => setProfile({ ...profile, notes: event.target.value })}
                    rows={4}
                    value={profile.notes ?? ""}
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
