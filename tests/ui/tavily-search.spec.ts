/**
 * Tavily web-search smoke test — verifies the coach can trigger a Tavily
 * search tool call and surface a real answer in the UI.
 *
 * Skipped automatically when TAVILY_API_KEY is not set so it never blocks
 * CI environments without the key.  Run locally with:
 *
 *   TAVILY_API_KEY=tvly-... bun run test:ui --grep "web search"
 *
 * Unlike the rest of the UI suite this test does NOT mock /api/chat — it lets
 * the real route handler call Tavily and OpenAI so the full code path is
 * exercised.  Everything else (auth, athlete context) is still mocked so the
 * test remains hermetic with respect to Supabase/R2.
 */
import { expect, test } from "@playwright/test";

import { mockAuthenticatedSession } from "./helpers/session";

const TAVILY_API_KEY = process.env["TAVILY_API_KEY"];

test.describe("Tavily web search", { tag: "@tavily" }, () => {
  test.skip(
    !TAVILY_API_KEY,
    "TAVILY_API_KEY not set — skipping Tavily integration",
  );

  test("coach looks up upcoming Indianapolis marathons and returns a result", async ({
    page,
  }) => {
    await mockAuthenticatedSession(page);

    // Let /api/chat hit the real server (no route intercept).
    // Mock persist endpoints to keep the test hermetic.
    await page.route("**/api/chat/messages", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "{}",
      }),
    );

    await page.goto("/");

    // Wait for the chat input to be ready
    const input = page.getByRole("textbox", { name: /message/i });
    await expect(input).toBeVisible({ timeout: 10_000 });

    await input.fill(
      "Can you look up the next major marathon happening in the Indianapolis area?",
    );
    await input.press("Enter");

    // The coach should produce a non-empty assistant bubble within 60 s
    // (Tavily + OpenAI roundtrip can be slow).
    const assistantBubble = page
      .locator('[data-role="assistant"]')
      .or(page.locator(".assistant-message"))
      .last();

    await expect(assistantBubble).toBeVisible({ timeout: 60_000 });

    const text = await assistantBubble.textContent();
    expect(text?.length ?? 0).toBeGreaterThan(20);

    // The response should mention Indianapolis or marathon in some form
    const lower = text?.toLowerCase() ?? "";
    expect(
      lower.includes("indianapolis") ||
        lower.includes("marathon") ||
        lower.includes("race") ||
        lower.includes("indy"),
    ).toBe(true);
  });
});
