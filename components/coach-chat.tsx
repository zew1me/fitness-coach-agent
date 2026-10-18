"use client";

import * as Sentry from "@sentry/nextjs";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";

import { onlyWelcomeMessage } from "../lib/chat-ui-messages";
import { siteConfig } from "../lib/site";
import type {
  AthleteProfile,
  BrowserTokenResponse,
  ChatMessage,
  ChatThreadResponse,
} from "../lib/types";
import type { AthleteProfileHook } from "../lib/use-athlete-profile";
import { useAthleteProfile } from "../lib/use-athlete-profile";
import { useBrowserSession } from "../lib/use-browser-session";
import { useChatDraft } from "../lib/use-chat-draft";
import { useChatSend } from "../lib/use-chat-send";
import { type ChatThreadHook, useChatThread } from "../lib/use-chat-thread";
import { useIsMobile } from "../lib/use-is-mobile";
import { useLiveChatMessages } from "../lib/use-live-chat-messages";
import { useMessageWindow } from "../lib/use-message-window";
import { useProfileDrawer } from "../lib/use-profile-drawer";
import { useWaitingStatus } from "../lib/use-waiting-status";

import { AccountMenuButton } from "./account-menu";
import { Composer } from "./chat-composer";
import { MessagesSection } from "./chat-messages";
import { useChatTurnLease } from "./chat-turn-lease-provider";
import styles from "./coach-chat.module.css";
import { ProfileDrawer } from "./profile-drawer";
import { SessionLoading } from "./session-loading";

function coachingStatusLabel(profileComplete: boolean): string {
  return profileComplete ? "Coaching ready" : "Building your athlete profile";
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

function exportChatHistoryJsonl(messages: ChatMessage[]): void {
  const exportDate = new Date().toISOString().slice(0, 10);
  downloadTextFile(
    `coaching-history-${exportDate}.jsonl`,
    serializeChatHistoryJsonl(messages),
    "application/x-ndjson;charset=utf-8",
  );
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

function LoggedOutLanding({
  authenticationRequired,
  error,
}: Readonly<{
  authenticationRequired: boolean;
  error: string | null;
}>): JSX.Element {
  return (
    <main className={styles.landingWrap}>
      <section className={styles.landingCard}>
        <p className={styles.eyebrow}>
          {authenticationRequired ? "Session ended" : "Athlete Coach"}
        </p>
        <h1 className={styles.landingTitle}>
          {authenticationRequired
            ? "Sign in to keep coaching."
            : "A simpler coaching experience, built like chat."}
        </h1>
        <p className={styles.landingText}>
          {authenticationRequired
            ? "Your browser sign-in has expired or is no longer available. Your coaching history is safe—sign in again to continue where you left off."
            : "Sign in once, then use a single focused conversation for check-ins, plan requests, and photo-backed coaching updates. The forms are gone from the main surface so the experience feels closer to a modern chat assistant than a dashboard."}
        </p>
        {error && !authenticationRequired ? (
          <p className={styles.landingHint}>
            Sign in to start your coaching chat. If the app feels slow to wake
            up, give it a moment and try again.
          </p>
        ) : null}
        <div className={styles.actionRow}>
          <Link className={styles.primaryButton} href="/login?return_to=/">
            {authenticationRequired
              ? "Sign in again"
              : "Continue with magic link"}
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
  return (
    <header className={styles.topbar}>
      <div className={styles.brandBlock}>
        <p className={styles.brand}>{siteConfig.appName}</p>
        <span className={styles.meta}>{coachingStatus}</span>
      </div>
      <div className={styles.topbarActions}>
        <Link
          aria-label="Training calendar"
          className={styles.accountButton}
          data-testid="chat-open-calendar"
          href="/calendar"
          title="Training calendar"
        >
          <svg
            aria-hidden="true"
            className={styles.accountIcon}
            viewBox="0 0 24 24"
          >
            <rect
              fill="none"
              height="15"
              rx="2.5"
              stroke="currentColor"
              strokeWidth="1.8"
              width="17"
              x="3.5"
              y="5"
            />
            <path
              d="M3.5 9.5H20.5M8 3V6.5M16 3V6.5"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeWidth="1.8"
            />
            <circle cx="8.5" cy="13.5" fill="currentColor" r="1.2" />
            <circle cx="12" cy="13.5" fill="currentColor" r="1.2" />
            <circle cx="15.5" cy="13.5" fill="currentColor" r="1.2" />
          </svg>
        </Link>
        <AccountMenuButton
          extraItems={
            <button
              className={styles.menuItem}
              onClick={onExport}
              role="menuitem"
              type="button"
            >
              Export JSONL
            </button>
          }
          onOpenProfile={onOpenDrawer}
          profile={profile}
        />
      </div>
    </header>
  );
}

export function CoachChat(): JSX.Element {
  const session = useBrowserSession();
  if (session.loading) {
    return <SessionLoading />;
  }
  if (session.token === null) {
    return (
      <LoggedOutLanding
        authenticationRequired={session.authenticationRequired}
        error={session.error}
      />
    );
  }
  return (
    <SignedInChat refreshSession={session.refresh} token={session.token} />
  );
}

function SignedInChat({
  token,
  refreshSession,
}: Readonly<{
  token: BrowserTokenResponse;
  refreshSession: () => Promise<void>;
}>): JSX.Element {
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
      refreshSession={refreshSession}
      thread={thread}
      threadData={thread.data}
      token={token}
    />
  );
}

function CoachChatBody({
  token,
  thread,
  threadData,
  refreshSession,
  athleteProfile,
}: Readonly<{
  token: BrowserTokenResponse;
  thread: ChatThreadHook;
  /** `thread.data`, narrowed to the loaded thread by `SignedInChat`. */
  threadData: ChatThreadResponse;
  refreshSession: () => Promise<void>;
  athleteProfile: AthleteProfileHook;
}>): JSX.Element {
  const {
    error: threadError,
    fetchOlderMessages,
    loadingOlder,
    olderAvailable,
    refetch: refetchThread,
    setError: setThreadError,
  } = thread;
  const isMobile = useIsMobile();
  const persistedMessages = threadData.thread.messages;
  const threadId = threadData.thread.id;
  const { displayedMessages, sendMessage, setMessages } = useLiveChatMessages(
    persistedMessages,
    threadId,
    token.user_id,
  );
  const { releaseVersion, startTurn, turnInFlight } = useChatTurnLease(
    token.user_id,
  );
  const [sending, setSending] = useState(false);
  const [syncingThread, setSyncingThread] = useState(false);
  const olderRequestVersionRef = useRef(0);
  const composerBusy = sending || syncingThread || turnInFlight;
  // Hook call order keeps the effects in their original sequence: scroll and
  // window reset, the waiting-status timer, the attachment preview cleanup,
  // then the lease-release refresh below.
  const messageWindow = useMessageWindow({
    fetchOlderMessages,
    loadingOlder,
    messages: displayedMessages,
    olderAvailable,
    olderRequestVersionRef,
    persistedMessageCount: persistedMessages.length,
    sending,
    setThreadError,
    threadId,
  });
  const waitingStatus = useWaitingStatus(sending);
  const draft = useChatDraft(setThreadError);

  useEffect(() => {
    if (releaseVersion === 0) return;
    setSyncingThread(true);
    olderRequestVersionRef.current += 1;
    void refetchThread()
      .catch((refreshError) => {
        Sentry.logger.warn("chat thread refresh failed after lease release");
        console.error(
          "Chat thread refresh failed after lease release",
          refreshError,
        );
        setThreadError(
          "Your previous message finished, but the thread failed to refresh. Reload to see the latest.",
        );
      })
      .finally(() => {
        setSyncingThread(false);
      });
  }, [refetchThread, releaseVersion, setThreadError]);

  const handleSend = useChatSend({
    composerBusy,
    draft,
    olderRequestVersionRef,
    refetchThread,
    refreshSession,
    resetWindow: messageWindow.resetWindow,
    sendMessage,
    setMessages,
    setSending,
    setSyncingThread,
    setThreadError,
    startTurn,
  });
  const drawer = useProfileDrawer({
    athleteProfile,
    olderRequestVersionRef,
    refetchThread,
    resetWindow: messageWindow.resetWindow,
  });
  const composerPlaceholder = composerPlaceholderFor(
    messageWindow.visibleMessages,
    threadData.profile_complete,
  );

  return (
    <main className={styles.page}>
      <div className={styles.shell}>
        <div className={styles.frame}>
          <ChatTopbar
            coachingStatus={coachingStatusLabel(threadData.profile_complete)}
            onExport={() => exportChatHistoryJsonl(persistedMessages)}
            onOpenDrawer={() => {
              void drawer.openDrawer();
            }}
            profile={athleteProfile.profile}
          />

          <MessagesSection
            hiddenMessageCount={messageWindow.hiddenMessageCount}
            loadingOlder={loadingOlder}
            messageEndRef={messageWindow.messageEndRef}
            messages={messageWindow.visibleMessages}
            onPrefillStarter={draft.setComposer}
            olderAvailable={olderAvailable}
            onShowMore={messageWindow.showMore}
            profileComplete={threadData.profile_complete}
          />

          <Composer
            attachments={draft.attachments}
            composer={draft.composer}
            composerBusy={composerBusy}
            isMobile={isMobile}
            leaseInFlight={turnInFlight}
            onComposerChange={draft.setComposer}
            onFilesAdded={(files) => {
              void draft.handleFilesAdded(files);
            }}
            onSend={() => {
              void handleSend();
            }}
            placeholder={composerPlaceholder}
            sending={sending}
            syncingThread={syncingThread}
            threadError={threadError}
            waitingStatus={waitingStatus}
          />
        </div>
      </div>

      <ProfileDrawer
        onClose={drawer.closeDrawer}
        onSave={() => {
          void drawer.saveProfile();
        }}
        open={drawer.drawerOpen}
        profile={athleteProfile.profile}
        saving={athleteProfile.saving}
        setProfile={athleteProfile.setProfile}
        status={athleteProfile.status}
      />
    </main>
  );
}
