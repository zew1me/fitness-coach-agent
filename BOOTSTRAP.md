# Environment Bootstrap

This document covers how to provision preview and production infrastructure for this project. Running the bootstrap script creates and configures Supabase projects, Cloudflare R2 buckets, and Vercel environment variables from a single command.

## How it works

`.env.bootstrap` holds your **platform admin credentials** — the tokens used to manage infrastructure. These are shared across environments. The `--env` flag determines which environment is being provisioned.

```
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

| Token | Where to get it |
|---|---|
| `SUPABASE_ACCESS_TOKEN` | supabase.com → Account → Access Tokens; only needed for auto-creating projects |
| `SUPABASE_ORG_ID` | Your org slug from the Supabase dashboard URL; only needed for auto-creating projects |
| `CF_API_TOKEN` | dash.cloudflare.com → My Profile → API Tokens → create with R2 bucket edit permissions |
| `CF_ACCOUNT_ID` | Cloudflare dashboard right sidebar (any page) |
| `VERCEL_TOKEN` | vercel.com/account/tokens |
| `OPENAI_API_KEY` | platform.openai.com |
| `TAVILY_API_KEY` | tavily.com |

## Step 2 — Create `.env.bootstrap`

```bash
cp .env.bootstrap.example .env.bootstrap
```

Fill in all required values. This file is gitignored and never committed.

If your Supabase projects already exist, set `SUPABASE_PROJECT_REF_PREVIEW` and/or `SUPABASE_PROJECT_REF_PROD` to their project refs (found in the Supabase dashboard URL). Leave blank to auto-create new projects named `fitness-coach-agent-preview` and `fitness-coach-agent-prod`.

Existing projects can use your local Supabase CLI login to fetch API keys. If `supabase projects api-keys --project-ref <project-ref> --output json` returns `401`, copy the Supabase URL, anon key, and service role key from Project Settings -> API into `SUPABASE_URL_PREVIEW`, `SUPABASE_ANON_KEY_PREVIEW`, and `SUPABASE_SERVICE_ROLE_KEY_PREVIEW` (or the matching `_PROD` variables).

Automated migrations also need the database password for existing projects. Set `SUPABASE_DB_PASSWORD_PREVIEW` and/or `SUPABASE_DB_PASSWORD_PROD` in `.env.bootstrap` when using an existing project. For projects created by the bootstrap script, the generated password is captured in the state file.

Set `PRODUCTION_DOMAIN` if you have a custom domain (e.g. `app.example.com`). Leave blank to auto-detect from your Vercel project aliases.

Bootstrap intentionally leaves R2 bucket CORS unconfigured. Browser clients should upload files to the backend proxy, and the backend writes to R2 with bucket-scoped S3 credentials from `.env.bootstrap`. If browser-direct presigned uploads are reintroduced, track and configure exact R2 CORS origins separately; wildcard origins like `https://*.vercel.app` are not accepted by the Cloudflare R2 CORS API.

### Supabase auth redirect URLs

Bootstrap automatically configures the Supabase project's auth settings via the Management API when `SUPABASE_ACCESS_TOKEN` is set:

- **Site URL** — set to your production domain or the Vercel project's stable alias.
- **Redirect URLs** — `https://fitness-coach-agent-*-nigel-stukes-projects.vercel.app/**` and the Vercel project's exact stable alias are added for preview environments; `http://localhost:3000/**` and `http://localhost:3001/**` are included for local development.
- **Email confirmation** — disabled (`MAILER_AUTOCONFIRM=true`) so first-time signups receive a 6-digit OTP code directly, matching the login page UI.

If `SUPABASE_ACCESS_TOKEN` is not set (e.g. existing projects using CLI login), bootstrap prints the values to configure manually in the Supabase dashboard:

- **Authentication → URL Configuration**: set Site URL and add redirect URL patterns
- **Authentication → Providers → Email**: disable *Confirm email* so OTP codes are sent directly

R2 runtime credentials have a two-pass setup:

1. Run bootstrap once so it creates or verifies the bucket.
2. In the Cloudflare dashboard, go to R2 -> Manage API tokens and create an account-level token scoped to that bucket with Object Read & Write.
3. Copy the generated Access Key ID and Secret Access Key into `R2_ACCESS_KEY_ID_PREVIEW` / `R2_SECRET_ACCESS_KEY_PREVIEW` or the matching `_PROD` values.
4. Rerun bootstrap so it writes those runtime credentials to Vercel.

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
3. Reads bucket-scoped R2 S3 credentials from `.env.bootstrap` for backend-proxy uploads
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
- Existing R2 buckets and tokens are detected and reused (secret read from state file)
- Vercel env vars are updated in place (no duplicates)

To skip migrations on a re-run (e.g. when only updating env vars):

```bash
bun run setup:prod -- --skip-migrations
```

To preview what would happen without making any changes:

```bash
bun run setup:prod -- --dry-run
```

## APP_BASE_URL behavior

| Environment | Value | Why |
|---|---|---|
| Production | Set to your domain | Required for OAuth issuer, JWT audience, and redirect URLs |
| Preview | Not set | Python backend falls back to `VERCEL_URL` (auto-set by Vercel on every deployment); Next.js auth callback falls back to `request.nextUrl.origin` |
| Local dev | Falls back to `http://localhost:3000` | Same `VERCEL_URL` fallback logic — returns localhost when not on Vercel |
