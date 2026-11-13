# Environment Bootstrap

This document covers how to provision preview and production infrastructure for this project. Running the bootstrap script creates and configures Supabase projects, Cloudflare R2 buckets, and Vercel environment variables from a single command.

## How it works

`.env.bootstrap` holds your **platform admin credentials** — the tokens used to manage infrastructure. These are shared across environments. The `--env` flag determines which environment is being provisioned.

```text
.env.bootstrap          → who you are (admin tokens, shared app secrets)
--env preview|prod      → which environment to operate on
.state.{env}.json       → generated secrets for that env (Supabase DB password, R2 token, JWT secret)
```

The script is idempotent — safe to re-run. It will create resources that don't exist and update Vercel env vars in place without creating duplicates.

## Prerequisites

You need accounts on all three platforms and the following CLI tools installed:

- `uv` (Python package manager)
- `supabase` CLI (used for `db push`)
- `vercel` CLI (used to link the project)

The Vercel project must already be linked (`vercel link`) so that `.vercel/project.json` exists.
For existing Supabase projects, authenticate the Supabase CLI first:

```bash
supabase login
supabase init
supabase link --project-ref <project-ref>
```

## Step 1 — Get your admin tokens

| Token                   | Where to get it                                                                                                                                                                                                            |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SUPABASE_ACCESS_TOKEN` | supabase.com → Account → Access Tokens; only needed for auto-creating projects                                                                                                                                             |
| `SUPABASE_ORG_ID`       | Your org slug from the Supabase dashboard URL; only needed for auto-creating projects                                                                                                                                      |
| `CF_API_TOKEN`          | dash.cloudflare.com → My Profile → API Tokens → create with **Workers R2 Storage:Edit**, plus **API Tokens:Edit** if bootstrap should mint the R2 S3 runtime token (see [R2 runtime credentials](#r2-runtime-credentials)) |
| `CF_ACCOUNT_ID`         | Cloudflare dashboard right sidebar (any page)                                                                                                                                                                              |
| `OPENAI_API_KEY`        | platform.openai.com                                                                                                                                                                                                        |
| `TAVILY_API_KEY`        | tavily.com                                                                                                                                                                                                                 |
| `SENTRY_DSN`            | Sentry → Settings → Projects → `javascript-nextjs` → Client Keys (DSN); server-side key, optional                                                                                                                          |
| `SENTRY_PUBLIC_DSN`     | A second Sentry Client Key for the browser (public); optional                                                                                                                                                              |
| `SENTRY_AUTH_TOKEN`     | Sentry → Settings → Auth Tokens (scope `project:releases`); optional, build-time source-map upload                                                                                                                         |

Vercel authentication uses the local `vercel` CLI (`vercel login`); no token is needed in `.env.bootstrap`.

## Step 2 — Create `.env.bootstrap`

```bash
cp .env.bootstrap.example .env.bootstrap
```

Fill in all required values. This file is gitignored and never committed.

If your Supabase projects already exist, set `SUPABASE_PROJECT_REF_PREVIEW` and/or `SUPABASE_PROJECT_REF_PROD` to their project refs (found in the Supabase dashboard URL). Leave blank to auto-create new projects named `fitness-coach-agent-preview` and `fitness-coach-agent-prod`.

Existing projects can use your local Supabase CLI login to fetch API keys. If `supabase projects api-keys --project-ref <project-ref> --output json` returns `401`, copy the Supabase URL, anon key, and service role key from Project Settings -> API into `SUPABASE_URL_PREVIEW`, `SUPABASE_ANON_KEY_PREVIEW`, and `SUPABASE_SERVICE_ROLE_KEY_PREVIEW` (or the matching `_PROD` variables).

Automated migrations also need the database password for existing projects. Set `SUPABASE_DB_PASSWORD_PREVIEW` and/or `SUPABASE_DB_PASSWORD_PROD` in `.env.bootstrap` when using an existing project. For projects created by the bootstrap script, the generated password is captured in the state file.

Set `PRODUCTION_DOMAIN` if you have a custom domain (e.g. `app.example.com`). Leave blank to auto-detect from your Vercel project aliases.

Bootstrap intentionally leaves R2 bucket CORS unconfigured. Browser clients should upload files to the backend proxy, and the backend writes to R2 with bucket-scoped S3 credentials (see [R2 runtime credentials](#r2-runtime-credentials)). If browser-direct presigned uploads are reintroduced, track and configure exact R2 CORS origins separately; wildcard origins like `https://*.vercel.app` are not accepted by the Cloudflare R2 CORS API.

### Supabase auth redirect URLs

Bootstrap automatically configures the Supabase project's auth settings via the Management API when `SUPABASE_ACCESS_TOKEN` is set:

- **Site URL** — set to your production domain or the Vercel project's stable alias.
- **Redirect URLs** — `https://*.vercel.app/**` is added for preview environments; `http://localhost:3000/**` and `http://localhost:3001/**` are included for local development.
- **Email confirmation** — disabled (`MAILER_AUTOCONFIRM=true`) so first-time signups receive the passwordless login email instead of the default signup confirmation.
- **Email templates** — Magic Link and Confirm Signup use `supabase/templates/email-otp.html`: `{{ .Token }}` for the 6-digit code and a direct app callback containing `{{ .TokenHash }}` for the sign-in link (issue #524). The callback verifies the hash server-side; it does not require a PKCE cookie from the requesting browser.
- **Preview base URL** — `APP_BASE_URL` is removed from the Vercel Preview environment so preview deployments fall back to Vercel's per-deployment `VERCEL_URL`.

If `SUPABASE_ACCESS_TOKEN` is not set (e.g. existing projects using CLI login), bootstrap prints the values to configure manually in the Supabase dashboard:

- **Authentication → URL Configuration**: set Site URL and add redirect URL patterns
- **Authentication → Providers → Email**: disable _Confirm email_ so OTP codes are sent directly
- **Authentication → Email Templates**: copy `supabase/templates/email-otp.html` into both Magic Link and Confirm Signup (deploy the updated callback first)
- **Vercel → Project Settings → Environment Variables**: remove `APP_BASE_URL` from Preview; keep it only for Production

### Roll out the email-link fix (#524)

Apply this separately to preview and production; their Supabase projects and Vercel settings
are independent. No database migration or new secret is needed.

1. Deploy the app with the updated `/auth/callback` before changing email templates. Until
   then, use the six-digit code; old `ConfirmationURL` links still use the incompatible
   implicit flow and are not repaired by deploying the new callback.
2. In each project's **Authentication → Email Templates**, replace the bodies of **Magic
   Link** and **Confirm Signup** with `supabase/templates/email-otp.html`. Bootstrap uses
   this same file for future runs, and local Supabase uses it via `supabase/config.toml`.
   Do not run the full infrastructure bootstrap merely to update two templates.
3. In **Authentication → URL Configuration**, verify the Site URL and allowed redirect
   URLs for that environment. The app sends `emailRedirectTo` as
   `https://<request-origin>/auth/callback?return_to=<encoded-local-path>`. The template
   uses `RedirectTo`, not `SiteURL`, to stay on the deployment that requested the email.
   Its `&amp;token_hash=...` suffix depends on the existing `?return_to=` query: other
   OTP senders must follow that contract. Keep production redirect allowlists restricted
   to trusted hosts; avoid project-wide preview wildcards on production.
4. Request a **fresh** email. Verify its link lands on the requesting deployment and
   signs in without an originating browser session (test another browser), and that a
   separate fresh email still supports six-digit code entry. Also test `/profile` and
   the consent return path. The link and code are alternatives for the same single-use
   credential; after one succeeds, reuse of either must fail. Test an expired link too.
   If email verification succeeds but app browser-session setup is unavailable, the
   callback preserves the Supabase session and displays **Retry sign-in**. Retry uses
   the authenticated cookie session, not the consumed link; an expired session still
   requires a fresh email.
5. If this fails, check **Authentication → Logs** in the same Supabase project and the
   app's `auth callback: email verification failed` log. Confirm Vercel's server and
   public Supabase settings refer to that same project. Never share raw links, tokens,
   session cookies, or service-role keys; share only the host/path, query parameter
   names, timestamp, and error code. Email security scanners can consume single-use
   links, invalidating that email's code too. Request a fresh email and enter its code
   without opening the link. If scanners consume every new email automatically, use
   the OTP-only template mitigation below; a scanner-safe confirmation interstitial
   is a separate enhancement.

If an immediate mitigation is needed before deployment, temporarily remove the link from
both hosted email templates and keep `{{ .Token }}`. Re-enable the new hash link only after
the callback is deployed. Do not re-enable `{{ .ConfirmationURL }}`.

### R2 runtime credentials

The backend needs bucket-scoped R2 S3 credentials. Each run resolves them from the first source that has them:

1. **`.env.bootstrap`** — `R2_ACCESS_KEY_ID_PREVIEW` / `R2_SECRET_ACCESS_KEY_PREVIEW` (or the matching `_PROD` values). Set both or neither; one without the other aborts the run.
2. **The state file** — credentials saved by an earlier run (see [State files](#state-files)).
3. **Minted through the Cloudflare API** — bootstrap creates a token named `fitness-coach-agent-{env}` scoped to the bucket and saves it to the state file. This is the only path that needs **API Tokens:Edit** on `CF_API_TOKEN`.

So leave the `R2_*` values blank to have bootstrap mint the credentials. To keep `CF_API_TOKEN` limited to **Workers R2 Storage:Edit**, supply them yourself in two passes:

1. Run bootstrap once so it creates or verifies the bucket. Without **API Tokens:Edit** this run fails at the minting step, after the bucket exists.
2. In the Cloudflare dashboard, go to R2 -> Manage API tokens and create an account-level token scoped to that bucket with Object Read & Write.
3. Copy the generated Access Key ID and Secret Access Key into `R2_ACCESS_KEY_ID_PREVIEW` / `R2_SECRET_ACCESS_KEY_PREVIEW` or the matching `_PROD` values.
4. Rerun bootstrap so it writes those runtime credentials to Vercel.

### Sentry observability (optional)

Set `SENTRY_DSN`, `SENTRY_PUBLIC_DSN`, and `SENTRY_AUTH_TOKEN` in `.env.bootstrap` to provision Sentry. All are optional — leave any blank to skip that var. Use **two distinct DSNs** (separate Client Keys) so the public browser key can never expose the server one; Sentry separates environments via the `environment` tag. Bootstrap writes:

| Vercel env var           | Source              | Runtime                                                                       |
| ------------------------ | ------------------- | ----------------------------------------------------------------------------- |
| `SENTRY_DSN`             | `SENTRY_DSN`        | server, edge, Python backend — stored sensitive (write-only)                  |
| `NEXT_PUBLIC_SENTRY_DSN` | `SENTRY_PUBLIC_DSN` | browser — public, must be `NEXT_PUBLIC_` to be inlined into the client bundle |
| `SENTRY_AUTH_TOKEN`      | `SENTRY_AUTH_TOKEN` | build-time source-map upload; stored as a sensitive (write-only) var          |

The `environment` tag comes from `APP_ENV` (server/edge/Python) and `NEXT_PUBLIC_VERCEL_ENV` (browser). The latter is injected automatically by Vercel **only if** the project's _Automatically expose System Environment Variables_ setting is on (the default) — if a production client event shows up tagged `development`, check that setting first.

## Step 3 — Run the bootstrap

```bash
# Preview environment
bun run setup:preview

# Production environment
bun run setup:prod
```

Each run:

1. Creates (or verifies) the Supabase project and applies all pending migrations via `supabase db push`
2. Creates (or verifies) the R2 bucket
3. Resolves bucket-scoped R2 S3 credentials for backend-proxy uploads (see [R2 runtime credentials](#r2-runtime-credentials))
4. Generates a stable `APP_JWT_SECRET` (kept consistent across re-runs)
5. Upserts all environment variables into the correct Vercel target scope

After it completes, run `vercel env ls` to confirm the vars are set, then redeploy.

## State files

Generated secrets that are only available at creation time (Supabase DB password, R2 token secret, JWT secret) are persisted to `scripts/bootstrap/.state.{env}.json`. These files are gitignored. Keep them safe. If an R2 token secret is lost, re-run the script with the relevant token deleted in the Cloudflare dashboard so a new one is created and captured.

If a bootstrap run created a Supabase project but failed before writing the state file, reset that project's database password in the Supabase dashboard, then set `SUPABASE_PROJECT_REF_PREVIEW`/`SUPABASE_PROJECT_REF_PROD` and the matching `SUPABASE_DB_PASSWORD_PREVIEW`/`SUPABASE_DB_PASSWORD_PROD` in `.env.bootstrap` before rerunning. If Supabase API key lookup also fails with `401`, set the matching `SUPABASE_URL_*`, `SUPABASE_ANON_KEY_*`, and `SUPABASE_SERVICE_ROLE_KEY_*` values from the dashboard.

## Re-running

The script is safe to re-run at any time:

- Existing Supabase projects are detected by name and reused
- Migrations already applied are skipped by the Supabase CLI
- Existing R2 buckets are detected and reused; R2 credentials come from `.env.bootstrap` or the state file before a new token is minted
- Vercel env vars are updated in place (no duplicates)

If Supabase reports `Remote migration versions not found in local migrations directory`,
stop and compare migration history before retrying bootstrap. See
`docs/supabase-migration-history.md`.

To skip migrations on a re-run (e.g. when only updating env vars):

```bash
bun run setup:prod -- --skip-migrations
```

To preview what would happen without making any changes:

```bash
bun run setup:prod -- --dry-run
```

## APP_BASE_URL behavior

| Environment | Value                                 | Why                                                                                                                                              |
| ----------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Production  | Set to your domain                    | Required for OAuth issuer, JWT audience, and redirect URLs                                                                                       |
| Preview     | Not set                               | Python backend falls back to `VERCEL_URL` (auto-set by Vercel on every deployment); Next.js auth callback falls back to `request.nextUrl.origin` |
| Local dev   | Falls back to `http://localhost:3000` | Same `VERCEL_URL` fallback logic — returns localhost when not on Vercel                                                                          |
