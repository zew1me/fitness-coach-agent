/**
 * Regression suite for issue #254 — null value in specialization_pct blows up
 * new multi-sport athlete profiles.
 *
 * The root cause: `specialization_pct` was NOT NULL in the DB schema with a
 * DEFAULT of 80.  When the AI omits the field for a multi-sport athlete the
 * column had no DEFAULT on drifted preview DBs, causing a constraint violation.
 * Migration 0005 drops NOT NULL + DEFAULT; NULL is now the correct sentinel.
 *
 * These tests verify the *client-side* contract:
 *
 *   1. The profile page renders without error when `specialization_pct` is null.
 *   2. The chat UI completes a multi-sport onboarding turn without an error
 *      toast, and a subsequent profile reload with null specialization_pct
 *      does not crash.
 *
 * The server-side constraint fix is covered by the `test:db` suite
 * (tests/python/test_supabase_db.py).  Run with:
 *   bun run test:ui
 *   BASE_URL=https://your-preview.vercel.app bun run test:ui
 */
import { expect, test } from "@playwright/test";

import { mockAuthenticatedSession, TEST_USER_ID } from "./helpers/session";

// ── shared fixtures ──────────────────────────────────────────────────────────

/** Minimal athlete-summary for a new multi-sport athlete (no specialization). */
const MULTI_SPORT_SUMMARY = {
  profile: {
    user_id: TEST_USER_ID,
    coaching_state: "onboarding",
    primary_sports: ["cycling", "running", "swimming"],
    specialization_pct: null,
    weekly_available_hours: null,
    display_name: null,
  },
  fitness_metrics: {
    sports: [],
    physiology: {},
    best_times: [],
    ctl_ceiling: null,
  },
};

/** Minimal athlete-summary for a brand-new user (no sports yet). */
const NEW_USER_SUMMARY = {
  profile: {
    user_id: TEST_USER_ID,
    coaching_state: "onboarding",
    primary_sports: [],
    specialization_pct: null,
    weekly_available_hours: null,
    display_name: null,
  },
  fitness_metrics: {
    sports: [],
    physiology: {},
    best_times: [],
    ctl_ceiling: null,
  },
};

/** AI SDK UI message stream for a single assistant text turn. */
function uiMessageStream(messageId: string, text: string): string {
  const chunks = [
    { type: "start", messageId },
    { type: "start-step" },
    { type: "text-start", id: "t0" },
    { type: "text-delta", id: "t0", delta: text },
    { type: "text-end", id: "t0" },
    { type: "finish-step" },
    { type: "finish" },
  ];
  return (
    chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") +
    "data: [DONE]\n\n"
  );
}

// ── 1. Profile page renders with null specialization_pct ─────────────────────

test.describe("profile page — null specialization_pct (#254)", () => {
  test("renders without error for multi-sport athlete with null specialization_pct", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page);

    // Override the get-athlete-summary response to mimic a triathlete
    // whose specialization_pct is null (post-migration correct state).
    await page.route("**/api/engine/get-athlete-summary", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MULTI_SPORT_SUMMARY),
      }),
    );

    await page.goto("/profile");

    // Page must load without crashing — no error banner, no JS exception.
    await expect(page.getByText(/Loading/i)).not.toBeVisible({ timeout: 5000 });
    await expect(page.locator("body")).not.toContainText(
      "null value in column",
    );
    await expect(page.locator("body")).not.toContainText("specialization_pct");

    // The page heading must appear — confirms full render, not a blank crash.
    await expect(
      page.getByRole("heading", { name: /fitness profile/i }),
    ).toBeVisible();
  });
});

// ── 2. Chat onboarding turn for multi-sport athlete ─────────────────────────

test.describe("chat onboarding — multi-sport null specialization_pct (#254)", () => {
  test("triathlete onboarding turn completes without error toast", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page);

    const ASSISTANT_ID = "asst-multisport-onboarding";
    const COACH_REPLY =
      "Great — noted that you're training for triathlon (cycling, running, and swimming). Let's build your profile from there.";

    // Initial thread: brand-new user, no messages yet.
    let threadCalls = 0;
    let capturedUserId: string | null = null;

    await page.route("**/api/chat/thread", async (route) => {
      threadCalls += 1;
      const messages =
        threadCalls === 1 || capturedUserId === null
          ? []
          : [
              {
                id: capturedUserId,
                role: "user",
                thread_id: "thread-test-1",
                user_id: TEST_USER_ID,
                created_at: "2026-01-01T00:00:01Z",
                metadata: { message_kind: "user_turn" },
                attachments: [],
                parts: [
                  {
                    type: "text",
                    text: "I'm a triathlete — cycling, running, swimming",
                  },
                ],
              },
              {
                id: ASSISTANT_ID,
                role: "assistant",
                thread_id: "thread-test-1",
                user_id: TEST_USER_ID,
                created_at: "2026-01-01T00:00:02Z",
                metadata: { message_kind: "assistant_reply" },
                attachments: [],
                parts: [{ type: "text", text: COACH_REPLY }],
              },
            ];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          attachments_enabled: false,
          profile_complete: false,
          thread: {
            id: "thread-test-1",
            user_id: TEST_USER_ID,
            state: { pending_profile_field: "sports" },
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
            messages,
          },
        }),
      });
    });

    // The chat turn — agent internally POSTs to /api/engine/update-athlete-profile
    // on the server side, which we can't intercept from the browser.  We mock the
    // SSE stream so no real network call leaves the test.
    await page.route("**/api/chat", async (route) => {
      const body = route.request().postDataJSON() as {
        messages?: Array<{ id?: string; role?: string }>;
      };
      const lastUser = [...(body.messages ?? [])]
        .reverse()
        .find((m) => m.role === "user");
      capturedUserId = lastUser?.id ?? null;

      await route.fulfill({
        status: 200,
        headers: {
          "content-type": "text/event-stream",
          "x-vercel-ai-ui-message-stream": "v1",
          "cache-control": "no-cache",
        },
        body: uiMessageStream(ASSISTANT_ID, COACH_REPLY),
      });
    });

    await page.route("**/api/chat/messages", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: capturedUserId ?? ASSISTANT_ID }),
      }),
    );

    // After the turn the chat surface reloads the athlete summary; return the
    // post-update profile with specialization_pct = null (triathlete, no
    // single-sport specialization).  This is the state that existed only as a
    // 500 before migration 0005.
    await page.route("**/api/engine/get-athlete-summary", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          threadCalls <= 1 ? NEW_USER_SUMMARY : MULTI_SPORT_SUMMARY,
        ),
      }),
    );

    await page.goto("/");
    await expect(page.getByTestId("composer-row")).toBeVisible();

    // New user types their sport — the key onboarding message that would have
    // triggered the NOT NULL constraint violation pre-migration-0005.
    await page
      .locator("textarea")
      .fill("I'm a triathlete — cycling, running, swimming");
    await page.getByRole("button", { name: /send/i }).click();

    // Coach reply must appear — confirms the turn completed without a 500.
    await expect(page.getByText(COACH_REPLY)).toBeVisible();

    // No error toast or error text must be visible.
    // Exclude the Next.js route announcer which always has role=alert.
    await expect(
      page.locator('[role="alert"]:not(#__next-route-announcer__)'),
    ).not.toBeVisible();
    await expect(page.locator("body")).not.toContainText(
      "Unable to update athlete profile",
    );
    await expect(page.locator("body")).not.toContainText(
      "null value in column",
    );

    // Exactly one assistant bubble — no duplicate caused by a reload mismatch.
    await expect(
      page.locator('[data-testid="chat-bubble"][data-role="assistant"]'),
    ).toHaveCount(1);
  });
});
