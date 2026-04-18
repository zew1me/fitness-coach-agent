# Login page shows raw internal errors and ignores Supabase hash-fragment error codes

## Summary

When a magic link is expired or otherwise invalid, the athlete lands on `/login` with two separate error signals — one in the query string, one in the URL hash — but the page handles neither well:

- The query param error (`?error=Missing+auth+code+from+Supabase.`) is a raw internal message that leaks implementation details to the user.
- The Supabase hash fragment (`#error=access_denied&error_code=otp_expired&error_description=...`) is **never read at all** — `useSearchParams()` only sees query params, not hash params.
- Error text has no visual distinction from success/status messages (same unstyled `<p>` tag).
- No recovery CTA is shown — the user is stuck with an error and no prompt to request a new link.

## Observed URL

```
/login?return_to=%2F&error=Missing+auth+code+from+Supabase.#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired
```

The user sees: **"Missing auth code from Supabase."** — confusing and unhelpful.

## All error cases to handle

### From the URL hash (Supabase-set, never currently read)

These arrive when the athlete clicks an email link that is invalid or already used:

| `error_code` | Meaning | Desired message |
|---|---|---|
| `otp_expired` | Magic link or OTP has expired | "Your sign-in link has expired. Enter your email to get a new one." |
| `access_denied` | Generic Supabase denial (often wraps `otp_expired`) | "Access was denied. Try signing in again." |
| `bad_otp` | Invalid OTP entered via the link token | "That sign-in link is not valid. Request a new one." |
| `email_not_confirmed` | Account exists but email never confirmed | "Please confirm your email address first, then try again." |

### From the query string (`?error=…`)

Set by `app/auth/callback/route.ts` when the code exchange fails:

| Current raw message | Desired message |
|---|---|
| `Missing auth code from Supabase.` | "Your sign-in link is missing or has already been used. Enter your email to get a new one." |
| `Unable to finish login.` | "Something went wrong completing your sign-in. Try again." |
| Supabase SDK error text (varies) | Sanitised generic fallback |

### From OTP verification (in-page, `setStatus`)

Already handled reasonably; just needs visual treatment.

## Expected behavior

1. On page load, read **both** `?error=` and `window.location.hash` (parsed client-side since hash is not server-visible).
2. Map known `error_code` values to friendly copy.
3. Render errors with a visually distinct error style (e.g., `className="error-hint"` or similar) so they don't look like status messages.
4. When the error indicates an expired/invalid link, **reset the form to email-entry mode** and pre-fill the email if it was passed or stored, so the user can immediately request a new link with one click.
5. Never surface raw SDK error strings or internal implementation messages to the user.

## Relevant files

- `app/login/page.tsx` — reads `searchParams.get("error")`, renders `<p>{status ?? authError}</p>` with no error styling
- `app/auth/callback/route.ts` — sets `?error=Missing+auth+code+from+Supabase.` when `code === null` (line 44); message should be sanitised here or mapped on the login page
- `lib/auth.ts` — `buildLoginRedirectPath` constructs the `?error=` redirect; consider using a structured error code param instead of a free-text message

## Fix ideas

- Add client-side `useEffect` in `LoginPageContent` to parse `window.location.hash` on mount and extract `error_code` / `error_description`.
- Create a small `classifyAuthError(errorCode: string): string` helper that maps known Supabase `error_code` values to user-safe copy.
- Pass a structured `error_code` query param from the callback route instead of a free-text `error` message, so the login page can map it consistently.
- Add an `error` CSS class or `data-type="error"` to the error `<p>` so it renders with distinct styling.
- When error indicates an expired/invalid link, call `setOtpSent(false)` so the email form is shown with a prompt to retry.
