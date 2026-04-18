# Frontend renders raw HTML when an API call returns a non-JSON error page

## Summary

When the homepage bootstrap request fails with an HTML response, the app surfaces the entire HTML document string inside the landing card. This makes the deployed site look catastrophically broken instead of showing a bounded error state.

## Observed behavior

- The latest preview shows raw markup text inside the main landing card.
- The dumped content includes `<!DOCTYPE html>`, Next.js error markup, and inlined script/style payloads.
- The page layout becomes unreadable because the error body is inserted directly into visible UI copy.

## Reproduction

1. Open the latest preview deployment.
2. Trigger the bootstrap path where `fetchBrowserToken()` fails.
3. Return an HTML error payload for `/api/oauth/browser-token` such as a Next.js `404` page.
4. Observe the full HTML body rendered as inline error text in the landing card.

## Expected behavior

- The UI should show a short, user-safe error message.
- Raw HTML responses should never be rendered verbatim in visible product copy.
- The page should stay visually intact even when the backend is misconfigured or unavailable.

## Likely cause

`readJson()` in `lib/coach-api.ts` calls `response.text()` for all non-OK responses and throws the entire response body as the error message. `CoachChat` then stores that string in `session.error`, and `LoggedOutLanding` renders it directly in the page.

Relevant flow:

- `lib/coach-api.ts`: `readJson()` uses `await response.text()`
- `lib/coach-api.ts`: `fetchBrowserToken()` throws that raw body on failure
- `components/coach-chat.tsx`: `bootstrap()` stores `error.message` in state
- `components/coach-chat.tsx`: `LoggedOutLanding` renders `{error}` directly

## Fix ideas

- Prefer a structured error parser that reads JSON when available and falls back to a short generic message for HTML or unknown content types.
- Clamp or sanitize any fallback error text before rendering.
- Add a regression test that returns an HTML error response from `/api/oauth/browser-token` and asserts that the UI shows a bounded message instead of the raw document.
