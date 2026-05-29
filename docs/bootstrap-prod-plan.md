# Bootstrap Vercel Production Env — Manual / Local-Agent / Cloud-Agent Split

## Context

You want to bootstrap the `production` environment on Vercel with every env var the app needs to run, and you want to do as much of it as possible from a cloud Claude Code session (this one). The repo already ships a complete bootstrap automation at `scripts/bootstrap/` driven by `bun run setup:prod`, documented in `BOOTSTRAP.md`. It provisions Supabase, Cloudflare R2, generates `APP_JWT_SECRET`, and upserts all required env vars into Vercel for the `production` target. The work is to (a) categorize each remaining step by where it must run and (b) identify the small set of changes that would let a cloud agent run the script end-to-end.

You answered: secrets will be pasted into this session, and you'll personally handle the `vercel link` step if needed.

**Update — MCP connectors are now in the picture.** You've added Supabase, Cloudflare, and Vercel MCP connectors to this environment. Current detection state in this session:

- **Cloudflare MCP** — ✅ authenticated. Exposes R2 bucket CRUD (`r2_bucket_create/get/list/delete`), account selection, D1/KV/Hyperdrive/Workers (unused), and CF docs search. Does **not** expose R2 API token creation, so the R2 two-pass remains.
- **Supabase MCP** — installed but not yet authenticated. Requires `/mcp` to complete OAuth in the user's browser (the server's host allowlist blocks the auto-OAuth path). Scoped to a **single project** via URL query string (`project_ref=psbteexygkspyotkyflc`). Identify that project after auth; if it isn't your prod project, the MCP must be re-installed pointing at the right ref.
- **Vercel MCP** — not visible in this session despite being added. The user should verify it's configured with URL `https://mcp.vercel.com`, transport `http`. If it stays absent we fall back to pasted `projectId`/`orgId`. Note: Vercel MCP is read-only at launch, so a `VERCEL_TOKEN` is still required for env var writes and redeploys regardless.

## Required env vars (target: Vercel "production" scope)

Every var the runtime reads — sources in parens. The bootstrap script writes all of them in one pass:

- `APP_ENV=production` (constant)
- `APP_BASE_URL=https://<your-prod-domain>` (your custom domain or canonical Vercel alias)
- `APP_JWT_SECRET` (generated, persisted in `scripts/bootstrap/.state.prod.json`)
- `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (Supabase prod project)
- `OPENAI_API_KEY` (OpenAI dashboard)
- `TAVILY_API_KEY` (Tavily dashboard)
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_ENDPOINT_URL`, `R2_PUBLIC_BASE_URL` (Cloudflare R2)
- `INVITE_CODE` (optional — not written by bootstrap; set manually in Vercel if you want gated signup)

`VERCEL_ENV` and `VERCEL_URL` are auto-injected by Vercel — do not set them.

## Prerequisite resources (one-time, must exist before bootstrap)

These are accounts and tokens. Account creation is inherently manual; once tokens exist they can be pasted into the cloud session.

1. **Supabase account** with an organization. You'll need:
   - `SUPABASE_ACCESS_TOKEN` from supabase.com → Account → Access Tokens
   - `SUPABASE_ORG_ID` (org slug from dashboard URL)
   - Either a pre-created production project ref, or let bootstrap auto-create `fitness-coach-agent-prod`
2. **Cloudflare account** with R2 enabled:
   - `CF_API_TOKEN` with **R2 bucket edit** permission (My Profile → API Tokens)
   - `CF_ACCOUNT_ID` (right sidebar)
   - After first bootstrap pass: **R2 S3 token** (account-level token scoped to the bucket with Object Read & Write) — provides `R2_ACCESS_KEY_ID_PROD` and `R2_SECRET_ACCESS_KEY_PROD`. Created in dashboard today; can be automated later.
3. **Vercel account** with the project created and (today) `.vercel/project.json` present:
   - `VERCEL_TOKEN` from vercel.com/account/tokens
   - `projectId` and `orgId` (in `.vercel/project.json` after `vercel link`)
   - `PRODUCTION_DOMAIN` (optional override if you have a custom domain)
4. **OpenAI account** — `OPENAI_API_KEY`
5. **Tavily account** — `TAVILY_API_KEY`
6. **Local tools** (only needed for the local-CLI fallback paths): `uv`, `bun`, `supabase` CLI, `vercel` CLI.

## Phase 1a — Bootstrap with MCPs authenticated (the new happy path)

Once Supabase MCP OAuth and Vercel MCP connection are completed, the manual surface shrinks meaningfully. Updated split:

### What you must do manually in a browser (one-time)

1. **Create accounts** on Supabase, Cloudflare, Vercel, OpenAI, Tavily (skip any you already have).
2. **OpenAI API key** — https://platform.openai.com/api-keys → Create new secret key. Paste into chat as `OPENAI_API_KEY`.
3. **Tavily API key** — https://app.tavily.com/home → copy from dashboard. Paste as `TAVILY_API_KEY`.
4. **Vercel personal token (write scope)** — https://vercel.com/account/tokens → Create Token (Full Account). Paste as `VERCEL_TOKEN`. Still required even with Vercel MCP, because MCP is read-only.
5. **Vercel production project** — create via dashboard if it doesn't exist (`Add New → Project → import the repo`). Vercel MCP can confirm it afterward; nothing to copy by hand.
6. **Supabase MCP authentication** — run `/mcp` in this session, complete the Supabase OAuth in your browser. After auth I'll read the project name behind `project_ref=psbteexygkspyotkyflc` and confirm it's your intended prod project. If it isn't, re-install the Supabase MCP with the correct `?project_ref=<prod-ref>` in the URL.
7. **R2 S3 runtime token** — *after* the cloud agent creates the bucket: https://dash.cloudflare.com → R2 → Manage R2 API Tokens → Create API Token → Object Read & Write → scope to the bootstrap-created bucket. Paste the Access Key ID and Secret Access Key into chat. (This step survives because no MCP exposes R2 token creation.)
8. **Post-deploy ChatGPT GPT OAuth config** — in the GPT builder, set Authorization/Token URLs to `https://<prod-domain>/api/oauth/{authorize,token}` and scope to `profile:read profile:write plans:read plans:write metrics:write`.

### What the cloud agent (this session) does

Driven by the three MCPs plus the pasted `VERCEL_TOKEN`, OpenAI key, and Tavily key:

- **Vercel MCP** → fetch `projectId` and `orgId` from your project; write `.vercel/project.json` so the bootstrap script's `load_settings` succeeds. List the production domain and existing env vars.
- **Cloudflare MCP** → list accounts (`accounts_list`), pick the right one (`set_active_account`), create the R2 bucket if missing (`r2_bucket_create`), read bucket details (`r2_bucket_get`). Skip the `CF_API_TOKEN`-based path in the bootstrap script's R2 step by populating `.env.bootstrap` with values that map directly to API credentials *after* you paste the R2 S3 token from step 7.
- **Supabase MCP** → confirm the project, fetch the project URL + anon key + service_role key, apply migrations directly (the Supabase MCP typically exposes `apply_migration`/`execute_sql` tools; we'll discover the exact tool names after OAuth and either feed them into the script or run them directly, then call `bun run setup:prod -- --skip-migrations`).
- **Bootstrap script** → run `bun run setup:prod -- --skip-migrations` (or full, depending on what Supabase MCP exposes for migrations). The script still does JWT generation, builds the env var dict, and upserts everything to Vercel via `VERCEL_TOKEN`. Then trigger a redeploy.

### Variables you no longer need to paste

With Supabase MCP authed and Vercel MCP visible, the following `.env.bootstrap` entries become unnecessary:

- `SUPABASE_ACCESS_TOKEN` (Management API access subsumed by MCP)
- `SUPABASE_ORG_ID` (we won't auto-create projects from this session)
- `SUPABASE_DB_PASSWORD_PROD` (only needed by `supabase db push`; MCP can apply migrations another way)
- `CF_API_TOKEN` (for bucket ops — MCP does it; CF token is still required if you want bootstrap's existing CloudflareClient path, but we can bypass it)
- `CF_ACCOUNT_ID` (MCP exposes `accounts_list`)
- `SUPABASE_PROJECT_REF_PROD` / `SUPABASE_URL_PROD` / `*_ANON_KEY_PROD` / `*_SERVICE_ROLE_KEY_PROD` (we fetch via MCP and feed straight into `setup:prod`'s env, or write `.env.bootstrap` programmatically)

You still must paste: `VERCEL_TOKEN`, `OPENAI_API_KEY`, `TAVILY_API_KEY`, and (after step 7) the R2 S3 key id + secret.

### Cloud-agent blockers that remain

- **R2 S3 runtime token** — no MCP creates it. Phase 2 item #2 (CF API call to mint the token) still pays off here.
- **Migrations via Supabase MCP** — depends on the exact tool surface exposed post-OAuth. If the MCP only offers SQL execution (not full migration tracking), we either (a) feed each unapplied migration through `execute_sql` ourselves, or (b) keep the `supabase db push` path and install the CLI. We'll know after OAuth completes.

## Phase 1b — Bootstrap with what exists today (MCPs not used)

The split below assumes the bootstrap automation **as currently written**. Most steps are doable from this cloud session if you paste the tokens; a few require you to run something locally because of interactive CLI auth.

### Manual (must be a human in a browser)

- Create accounts on Supabase, Cloudflare, Vercel, OpenAI, Tavily.
- Mint admin tokens at each provider (the seven tokens listed above).
- Create the **Vercel project** (one-time): either via dashboard, or `vercel link` locally. The cloud session cannot do this because `vercel link` is interactive and the Vercel CLI is not installed here.
- Create the **R2 S3 runtime token** in the Cloudflare dashboard after the bucket exists (today this is the only step the bootstrap script does *not* automate — see `BOOTSTRAP.md` step "R2 runtime credentials have a two-pass setup").
- After production deploys, configure the **ChatGPT GPT** OAuth client in the GPT builder with the deployed URLs (`/api/oauth/authorize`, `/token`, `/revoke`). No registry on our side — anything that follows the PKCE flow with an HTTPS redirect URI is accepted.

### Local agent (your machine, has shell + browser)

Use a local Claude Code session for the steps that need interactive auth or a CLI not installed in the cloud:

- `vercel link` (interactive — pick the project; writes `.vercel/project.json`).
- `supabase login` + `supabase link --project-ref <ref>` (interactive — opens a browser).
- After those two CLI logins, the local agent can also just run `bun run setup:prod` end-to-end if you don't want to use the cloud agent at all.

### Cloud agent (this session)

Once you paste the tokens and tell me your Vercel `projectId` + `orgId` (from `.vercel/project.json`), this session can:

- Write `.env.bootstrap` from the values you paste.
- Write `.vercel/project.json` directly with `{projectId, orgId}` — avoids needing the Vercel CLI.
- Run `bun run setup:prod` — this performs all four bootstrap steps non-interactively because:
  - `SupabaseClient` uses the **Management API** with your `SUPABASE_ACCESS_TOKEN` (no `supabase login` needed if the project already exists *and* you provide `SUPABASE_PROJECT_REF_PROD` + `SUPABASE_DB_PASSWORD_PROD`, or all three of `SUPABASE_URL_PROD`/`SUPABASE_ANON_KEY_PROD`/`SUPABASE_SERVICE_ROLE_KEY_PROD`).
  - `CloudflareClient` uses the CF API.
  - `VercelClient` uses the Vercel API.
- Apply migrations remotely via `supabase db push` (requires `SUPABASE_DB_PASSWORD_PROD` — bootstrap will error clearly if missing).
- Verify env vars landed (`scripts/bootstrap` already prints a summary; we can also hit Vercel's API to list env vars).
- Commit nothing secret — `.env.bootstrap` and `scripts/bootstrap/.state.prod.json` are gitignored.
- Open the final PR for any phase 2 code changes (see below).

**Cloud-agent blockers today**:
- The R2 S3 runtime token must be created in the Cloudflare dashboard first — bootstrap can verify the bucket but cannot mint that token. You paste the resulting key id + secret into the session.
- The Supabase CLI is not installed in this container; `supabase db push` runs through a CLI subprocess (`SupabaseClient.apply_migrations`). If the CLI isn't on PATH the migrations step will fail. We need to either install `supabase` in the session (via `npm i -g supabase` or the official install script) or skip migrations in cloud and run them locally with `bun run setup:prod -- --skip-migrations`.

## Phase 2 — Changes to push more onto cloud agents

Small, well-scoped tweaks that would let the cloud agent own the entire bootstrap (post-account-creation) without any local CLI step:

1. **`scripts/bootstrap/vercel_client.py` — project lookup by name.** Add a `find_project_by_name(name)` method that hits `GET /v9/projects` and resolves `projectId`/`orgId` from a project name when `.vercel/project.json` is missing. In `config.py:load_settings`, fall back to that lookup (read `VERCEL_PROJECT_NAME` from `.env.bootstrap`) before raising the "`.vercel/project.json` not found" error. Removes the `vercel link` prerequisite for cloud agents entirely.

2. **`scripts/bootstrap/cloudflare_client.py` — automate the R2 S3 token.** Cloudflare's API supports creating account-level R2 S3 tokens (`POST /accounts/{account_id}/r2/tokens`) scoped to a single bucket. Add `ensure_r2_s3_token(bucket_name)` that creates the token if missing, persists the secret to `.state.prod.json`, and returns `{access_key_id, secret_access_key}`. Removes the two-pass dashboard step. Edit `_setup_r2` in `main.py` to call this instead of erroring when `.env.bootstrap` lacks the runtime creds.

3. **`scripts/bootstrap/supabase_client.py` — `apply_migrations` without local CLI.** Two options, pick one:
   - **(a) Auto-install the CLI** if not on PATH — small wrapper that runs the official install script on first use. Cheap; matches current code paths.
   - **(b) Replace the CLI subprocess** with a direct `psql`/`asyncpg` migration runner that connects to the project's pooler (`db.{ref}.supabase.co:6543`) with the DB password and applies pending files from `supabase/migrations/`. More code, but removes the CLI dependency entirely.
   Recommended: (a) for minimal change.

4. **`SessionStart` hook for cloud sessions.** Add a `.claude/` SessionStart hook that pre-installs `supabase` CLI in the container so future cloud bootstrap sessions don't need to do it. (Use the `session-start-hook` skill.)

5. **`BOOTSTRAP.md` update.** Document a "Cloud-agent bootstrap" section describing the new flow: paste tokens → cloud agent writes `.env.bootstrap` → runs `bun run setup:prod` → reports back.

## Critical files

- `scripts/bootstrap/main.py` — orchestrator; `_setup_r2` and `_setup_supabase` are the change points for items 2 and 3 above.
- `scripts/bootstrap/config.py` — add `VERCEL_PROJECT_NAME` setting and lookup fallback (item 1).
- `scripts/bootstrap/vercel_client.py` — add `find_project_by_name` (item 1).
- `scripts/bootstrap/cloudflare_client.py` — add `ensure_r2_s3_token` (item 2).
- `scripts/bootstrap/supabase_client.py` — auto-install CLI or replace with direct DB connection (item 3).
- `BOOTSTRAP.md` — document cloud-agent flow (item 5).
- `.env.bootstrap.example` — add `VERCEL_PROJECT_NAME` placeholder.

## Verification

Run these from inside the cloud session after the bootstrap completes:

1. `bun run setup:prod -- --dry-run` first to surface any missing inputs before making real changes.
2. After the real run, the summary printed by `_print_summary` shows project ref, masked keys, R2 bucket, and APP_BASE_URL.
3. Hit Vercel's API to list env vars on the `production` target and assert all 14 expected keys are present and non-empty.
4. Trigger a Vercel redeploy (`POST /v13/deployments` with `gitSource` set to the production branch, or push a no-op commit).
5. Smoke-test the deployed app:
   - `GET https://<prod-domain>/health` → 200.
   - `GET https://<prod-domain>/.well-known/oauth-authorization-server` → discovery JSON with the prod issuer.
   - Magic-link login from `/login` end-to-end (requires Supabase email config — bootstrap configures Site URL and redirect URLs automatically when `SUPABASE_ACCESS_TOKEN` is set; otherwise the script prints the values to set in the Supabase dashboard).
6. (After ChatGPT GPT config) connect the GPT and complete the OAuth consent flow once to confirm `/api/oauth/*` endpoints work end-to-end.
