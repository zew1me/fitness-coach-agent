import { expect, test } from "@playwright/test";

import { mockAuthenticatedSession, TEST_USER_ID } from "./helpers/session";

const WELCOME_TEXT =
  "Welcome. Let's start with just two things: what sport or sports are you training for, and what would you like coaching around?";

test.describe("first-time chat onboarding (#156)", () => {
  test("matches the empty state and starter prompts to the welcome ask", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page);
    await page.route("**/api/chat/thread", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          attachments_enabled: false,
          profile_complete: false,
          thread: {
            id: "thread-test-1",
            user_id: TEST_USER_ID,
            state: { pending_profile_field: "goals" },
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
            messages: [
              {
                id: "welcome-1",
                role: "assistant",
                thread_id: "thread-test-1",
                user_id: TEST_USER_ID,
                created_at: "2026-01-01T00:00:00Z",
                metadata: { message_kind: "welcome" },
                attachments: [],
                parts: [{ type: "text", text: WELCOME_TEXT }],
              },
            ],
          },
        }),
      }),
    );

    await page.goto("/");

    await expect(
      page.getByRole("heading", { name: /Start with your sport and goal/i }),
    ).toBeVisible();
    await expect(page.getByText(/A short answer is enough/i)).toBeVisible();
    await expect(
      page.getByText(/what sport or sports are you training for/i),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Running base and consistency/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Generate next plan/i }),
    ).toHaveCount(0);
    await expect(page.locator("textarea")).toHaveAttribute(
      "placeholder",
      "Tell your coach your sport and goal...",
    );
  });
});
