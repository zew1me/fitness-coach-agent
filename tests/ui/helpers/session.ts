/**
 * Shared Playwright helpers for hermetic, authenticated chat UI tests.
 *
 * `mockAuthenticatedSession` intercepts every network call the chat surface
 * makes on first paint so specs run without a live Supabase / R2 / OpenAI:
 *   - GET  /api/oauth/browser-token        → a fake browser session token
 *   - GET  /api/chat/thread                → an empty thread (override per-spec)
 *   - GET  /api/engine/get-athlete-summary → a minimal onboarding profile
 *   - POST /api/chat/attachments/presign   → a presign that points at example.com
 *   - PUT  https://example.com/upload      → 200 (hermetic upload sink)
 *
 * Specs that need a populated thread or a streaming turn should layer
 * additional `page.route(...)` calls AFTER calling this helper — later routes
 * registered for the same URL take precedence in Playwright.
 */
import type { Page } from "@playwright/test";

export const TEST_USER_ID = "test-user-123";

export async function mockAuthenticatedSession(page: Page): Promise<void> {
  await page.route("**/api/oauth/browser-token", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        user_id: TEST_USER_ID,
        access_token: "fake-token-for-tests",
      }),
    }),
  );

  await page.route("**/api/chat/thread", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        thread: {
          id: "thread-test-1",
          messages: [],
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      }),
    }),
  );

  await page.route("**/api/engine/get-athlete-summary", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        profile: {
          user_id: TEST_USER_ID,
          coaching_state: "onboarding",
          primary_sports: [],
        },
      }),
    }),
  );

  await page.route("**/api/chat/attachments/presign", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        upload_url: "https://example.com/upload",
        object_key: "test-file-key",
        public_url: "https://example.com/test-image.png",
        method: "PUT",
        headers: {},
      }),
    }),
  );

  // Intercept the actual file upload so tests are hermetic.
  await page.route("https://example.com/upload", (route) =>
    route.fulfill({ status: 200 }),
  );
}

/** A real 1×1 transparent PNG, base64-encoded — handy for attachment specs. */
export const ONE_BY_ONE_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
