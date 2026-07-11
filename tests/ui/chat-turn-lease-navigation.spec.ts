import { expect, test } from "@playwright/test";

import { mockAuthenticatedSession } from "./helpers/session";

const ASSISTANT_REPLY = "I have your update.";

function uiMessageStream(text: string): string {
  const chunks = [
    { type: "start", messageId: "assistant-turn-1" },
    { type: "start-step" },
    { type: "text-start", id: "text-1" },
    { type: "text-delta", id: "text-1", delta: text },
    { type: "text-end", id: "text-1" },
    { type: "finish-step" },
    { type: "finish" },
  ];
  return (
    chunks.map((chunk) => `data: ${JSON.stringify(chunk)}\n\n`).join("") +
    "data: [DONE]\n\n"
  );
}

test("blocks a resend after navigation until the abandoned turn's lease clears (#347)", async ({
  page,
}) => {
  await mockAuthenticatedSession(page);

  let activeLease = false;
  let chatRequests = 0;
  let releaseFirstChat: () => void = () => {};

  await page.route("**/api/calendar**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        activities: [],
        end: "2026-08-30",
        planned_workouts: [],
        start: "2026-05-18",
      }),
    }),
  );
  await page.route("**/api/chat/model-state/lease", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        expires_at: activeLease ? "2026-07-10T00:01:00Z" : null,
        in_flight: activeLease,
      }),
    }),
  );
  await page.route("**/api/chat", async (route) => {
    chatRequests += 1;
    if (chatRequests === 1) {
      activeLease = true;
      await new Promise<void>((resolve) => {
        releaseFirstChat = resolve;
      });
    }
    await route.fulfill({
      status: 200,
      headers: {
        "cache-control": "no-cache",
        "content-type": "text/event-stream",
        "x-vercel-ai-ui-message-stream": "v1",
      },
      body: uiMessageStream(ASSISTANT_REPLY),
    });
  });

  await page.goto("/");
  await page.locator("textarea").fill("I finished my intervals.");
  await page.getByRole("button", { name: "Send" }).click();
  await expect.poll(() => chatRequests).toBe(1);

  await page.getByTestId("chat-open-calendar").click();
  await expect(page).toHaveURL(/\/calendar$/);
  await expect(page.getByTestId("calendar-grid")).toBeVisible();

  await page.getByTestId("calendar-open-chat").click();
  await expect(page).toHaveURL(/\/$/);

  const sendButton = page.getByRole("button", { name: "Send" });
  await expect(sendButton).toBeDisabled();
  await page.locator("textarea").fill("Can I add another workout?");
  await expect(sendButton).toBeDisabled();
  expect(chatRequests).toBe(1);

  activeLease = false;
  releaseFirstChat();

  await expect(sendButton).toBeEnabled();
  await sendButton.click();
  await expect.poll(() => chatRequests).toBe(2);
});
