# Environment Bootstrap

This document covers how to provision preview and production infrastructure for this project. Running the bootstrap script creates and configures Supabase projects, Cloudflare R2 buckets, and Vercel environment variables from a single command.

## How it works

`.env.bootstrap` holds your **platform admin credentials** — the tokens used to manage infrastructure. These are shared across environments. The `--env` flag determines which environment is being provisioned.

```
.env.bootstrap          → who you are (admin tokens, shared app secrets)
--env preview|prod      → which environment to operate on
.state.{env}.json       → generated secrets for that env (R2 token, JWT secret)
```

The script is idempotent — safe to re-run. It will create resources that don't exist and update Vercel env vars in place without creating duplicates.

## Prerequisites

You need accounts on all three platforms and the following CLI tools installed:

- `uv` (Python package manager)
- `supabase` CLI (used for `db push`)
- `vercel` CLI (used to link the project)

The Vercel project must already be linked (`vercel link`) so that `.vercel/project.json` exists.

## Step 1 — Get your admin tokens

| Token | Where to get it |
|---|---|
| `SUPABASE_ACCESS_TOKEN` | supabase.com → Account → Access Tokens |
| `SUPABASE_ORG_ID` | Your org slug from the Supabase dashboard URL |
| `CF_API_TOKEN` | dash.cloudflare.com → My Profile → API Tokens → create with "R2 Storage: Edit" |
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

Set `PRODUCTION_DOMAIN` if you have a custom domain (e.g. `app.example.com`). Leave blank to auto-detect from your Vercel project aliases.

## Step 3 — Run the bootstrap

```bash
# Preview environment
bun run setup:preview

# Production environment
bun run setup:prod
```

Each run:
1. Creates (or verifies) the Supabase project and applies all pending migrations via `supabase db push`
2. Creates (or verifies) the R2 bucket and configures CORS for presigned browser uploads
3. Creates a scoped R2 API token for that bucket
4. Generates a stable `APP_JWT_SECRET` (kept consistent across re-runs)
5. Upserts all environment variables into the correct Vercel target scope

After it completes, run `vercel env ls` to confirm the vars are set, then redeploy.

## State files

Generated secrets that are only available at creation time (R2 token secret, JWT secret) are persisted to `scripts/bootstrap/.state.{env}.json`. These files are gitignored. Keep them safe — if lost, re-run the script with the relevant token deleted in the Cloudflare dashboard so a new one is created and captured.

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
