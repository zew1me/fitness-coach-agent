# Magic-link / OTP login parity (preview ↔ production)

Tracking issue: [#172](https://github.com/zew1me/fitness-coach-agent/issues/172)
— "log in magic link from email doesn't work" in production:

> Your sign-in link is missing or has already been used. Enter your email to
> get a new one.

Preview works; production does not. Same code runs in both, so this is Supabase
**project configuration drift**, not an app bug.

## How the flow works

1. `POST /api/auth/request-otp` (`app/api/auth/request-otp/route.ts`) calls
   Supabase `signInWithOtp` with
   `emailRedirectTo = https://<deployment-origin>/auth/callback?return_to=…`.
2. Supabase emails a link to `…/auth/v1/verify?token=…&redirect_to=<callback>`.
3. Clicking it verifies the token and **redirects to `redirect_to` with `?code=`
   appended**.
4. `app/auth/callback/route.ts` exchanges `code` for a session. If `code` is
   missing it redirects to `/login?error=Missing auth code…`, which
   `app/login/page.tsx` renders as the message above.

The breakage is at step 3: Supabase only honours a `redirect_to` whose **full
path** matches an entry in the project's redirect allow-list (`uri_allow_list`).
A bare `site_url` origin does **not** cover its own sub-paths — a `/**` wildcard
is required. When `redirect_to` isn't allow-listed, Supabase drops it and bounces
to the site root **without `?code`**.

## Root causes

1. **Missing production redirect wildcard.** `scripts/bootstrap/main.py`
   previously added `https://<domain>/**` to the allow-list **only for preview**.
   Production got only the bare `site_url` origin plus `localhost`, so the
   production `/auth/callback` redirect was never allow-listed. Fixed in
   `_build_auth_redirect_urls` (now adds the `/**` wildcard for the production
   origin and the Vercel-assigned alias).
2. **SMTP configured by hand.** Bootstrap configured redirect URLs, OTP, and
   templates but never the SMTP sender, so Resend was wired up manually per
   project — a classic drift source. `configure_auth_settings` now also applies
   custom SMTP from `.env.bootstrap` so both environments get an identical
   sender.

## Applying parity to production

With a populated `.env.bootstrap` (see `.env.bootstrap.example`), run:

```bash
bun run setup:prod   # uv run python -m scripts.bootstrap.main --env prod
```

This requires `SUPABASE_ACCESS_TOKEN` (a Supabase personal access token) so the
Management API call can patch the project. Add `SMTP_PASS` (your Resend API key)
and `SMTP_ADMIN_EMAIL` (an address on a Resend-verified domain) to also push the
SMTP sender; leave them blank to keep Supabase's built-in, rate-limited sender.

The expected production values (canonical alias `fitness-coach-agent-phi.vercel.app`,
or your custom `PRODUCTION_DOMAIN`):

| Setting                        | Value                                                                                                                               |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| Site URL                       | `https://<prod-domain>`                                                                                                             |
| Redirect URLs                  | `https://<prod-domain>/**`, `https://fitness-coach-agent-phi.vercel.app/**`, `http://localhost:3000/**`, `http://localhost:3001/**` |
| Confirm email                  | Disabled (`mailer_autoconfirm = true`)                                                                                              |
| OTP length                     | 6                                                                                                                                   |
| Magic Link / Confirm templates | include `{{ .Token }}` and `{{ .ConfirmationURL }}`                                                                                 |
| SMTP host / port               | `smtp.resend.com` / `465`                                                                                                           |
| SMTP user                      | `resend`                                                                                                                            |
| SMTP password                  | Resend API key                                                                                                                      |
| SMTP sender email              | address on a Resend-verified domain                                                                                                 |

### Verifying by hand (Dashboard)

Authentication → URL Configuration, Authentication → Providers → Email,
Authentication → Email Templates, and Authentication → Emails → SMTP Settings.
Confirm the production project matches the table above and mirrors preview.
