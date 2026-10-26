import { expect, test } from "@playwright/test";

import { mockAuthenticatedSession } from "./helpers/session";

// A chat POST that fails before any stream starts (lease conflict, a 503 from
// the route's catch-all) must surface its message and give the athlete their
// draft back. The AI SDK's sendMessage resolves instead of throwing on these,
// so without explicit handling the message vanished silently (#346).

const LEASE_BUSY_MESSAGE =
  "Coach is still replying to your previous message. Please try again in a minute.";

test("surfaces a lease-conflict 409 and restores the draft (#346)", async ({
  page,
}) => {
  await mockAuthenticatedSession(page);
  await page.route("**/api/chat", (route) =>
    route.fulfill({
      status: 409,
      contentType: "text/plain",
      body: LEASE_BUSY_MESSAGE,
    }),
  );

  await page.goto("/");
  const composer = page.locator("textarea");
  await composer.fill("I finished my intervals.");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText(LEASE_BUSY_MESSAGE)).toBeVisible();
  await expect(composer).toHaveValue("I finished my intervals.");
  // The optimistic user bubble is dropped; only the restored draft remains.
  await expect(page.getByTestId("chat-bubble")).toHaveCount(0);
});

test("shows the error field of a JSON error body, not the raw JSON", async ({
  page,
}) => {
  await mockAuthenticatedSession(page);
  await page.route("**/api/chat", (route) =>
    route.fulfill({
      status: 413,
      contentType: "application/json",
      body: JSON.stringify({
        error: "Turn exceeds the 256 KiB request limit.",
      }),
    }),
  );

  await page.goto("/");
  await page.locator("textarea").fill("hello");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(
    page.getByText("Turn exceeds the 256 KiB request limit."),
  ).toBeVisible();
  await expect(page.getByText('{"error"')).toHaveCount(0);
});
