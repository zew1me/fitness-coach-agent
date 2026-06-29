# AI Agent Based Endurance Coaching

This file provides guidance to agents when working with code in this repository.

## What This App Is

A ChatGPT like experience for endurance coaching. Athletes log in via magic link, manage their fitness profiles, and submit check-ins, and get plans.

Two runtimes:

- **Next.js 15 frontend** (TypeScript, Supabase Auth, App Router)
- **Python FastAPI backend** running on Vercel Functions (`api/index.py` entrypoint)

## Commands

**Package manager: Bun**

```bash
# Local dev (local Supabase + Next.js in one command)
bun run dev:local   # starts local Supabase if needed, then next dev against it
bun run db:start    # start local Supabase stack only
bun run db:stop     # stop local Supabase stack
bun run db:reset    # wipe + replay all migrations locally

# Remote dev (uses .env.local which points to the hosted Supabase project)
bun dev          # Next.js dev server
bun run build    # Production build
bun run lint     # ESLint (zero warnings)
bun run typecheck  # tsc + Next.js typegen
bun run test     # Vitest unit tests (tests/web/)
bun run check    # lint + typecheck + test

# Python backend
uv run pytest tests/python/          # all Python tests
uv run pytest tests/python/test_api.py  # single test file
uv run pytest -k "test_name"         # single test
uv run ruff check .                  # lint
uv run ruff format .                 # format
```

## Architecture

### Frontend (`app/`, `components/`, `lib/`)

Next.js 15 App Router. Key pages:

- `app/login/page.tsx` — magic link OTP via Supabase
- `app/auth/callback/page.tsx` — Supabase auth callback
- `app/consent/page.tsx` — OAuth consent UI (POSTs to `/api/oauth/authorize/decision`)
- `app/profile/page.tsx` — athlete profile management

`lib/` contains shared TS utilities: Supabase browser client (`supabase.ts`), Zod schemas (`schemas.ts`), TypeScript types (`types.ts`), and site config (`site.ts`).

### Python Backend (`api/`, `backend/`)

`api/index.py` is the Vercel Functions entrypoint (FastAPI app). Domain logic lives in `backend/`:

- `backend/config.py` — Pydantic Settings (reads env vars)
- `backend/models/` — Pydantic models: `auth.py` (OAuth + JWT), `planning.py` (plans), `storage.py` (R2)
- `backend/repos/` — Supabase persistence: `oauth_repo.py` (grants/codes/refresh tokens), `supabase_repo.py` (athlete profiles + check-ins)
- `backend/services/` — Business logic: `auth.py` (PKCE OAuth flow, JWT issuance), `planner.py` (14-day plan composition), `r2.py` (Cloudflare R2 presigned URLs)

### OAuth Flow

The app implements OAuth 2.0 PKCE as a provider (not consumer). ChatGPT is the OAuth client. Key endpoints:

- `GET /.well-known/oauth-authorization-server` — discovery
- `GET /api/oauth/authorize` — start flow (redirects to login if unauthenticated)
- `POST /api/oauth/authorize/decision` — user approves/denies consent
- `POST /api/oauth/token` — exchange code for tokens
- `POST /api/oauth/revoke` — revoke grant

Consent grants are durable (persisted to `oauth_grants` table). The `require_user_context()` FastAPI dependency validates bearer JWTs on protected endpoints.

### Database (Supabase + Postgres)

Migrations in `supabase/migrations/`:

- `0001_schema.sql` — initial schema (athlete profiles, check-ins, OAuth tables, chat tables, etc.)
- `0002_nutrition.sql` — nutrition tracking
- `0003_fitness_thresholds.sql` — sport-specific threshold source metadata
- `0004_chat_messages_parts.sql` — adopts AI SDK `UIMessage.parts` JSON shape on `chat_messages` (see "Chat persistence" below)

When introducing a new migration, update `docs/supabase-migration-history.md`
in the same change so migration ordering and any environment-specific repair
notes stay current.

Use separate Supabase projects per environment (development (local) / preview / production).

### Chat persistence

`chat_messages` stores each turn as a `parts jsonb` array matching the AI SDK [`UIMessage`](https://sdk.vercel.ai) shape — text parts, file parts, tool-call parts, and reasoning parts ride together on one row. This mirrors vercel/chatbot's `Message_v2` table.

The legacy columns `chat_messages.content` (denormalized text mirror) and the separate `chat_attachments` table are still present for one release window; a follow-up migration will drop them once readers have fully cut over.

### Storage — current reality vs. intent

**Intent.** Cloudflare R2 via S3-compatible API. `POST /api/files/presign-upload` and `POST /api/chat/attachments/presign` mint presigned upload URLs; the client uploads directly (or via the `POST /api/chat/attachments/upload` proxy). The returned `public_url` should be the canonical reference used everywhere downstream.

**Current reality.** Chat image attachments are now persisted to `chat_messages.parts` as R2 `public_url` references (issue #163 fix). The client refuses to send a message whose attachment lacks a `public_url`, so any base64 `data:` URL in `parts[i].url` is a stale row from before the fix landed. Whether R2 is referenced by anything else in the running app is **issue #164**.

### Unsupported file attachments → text (do not send to the model as `input_file`)

OpenAI's Responses API only ingests images and PDFs. Athletes attach activity files — `.fit` (`application/vnd.garmin.fit`) and `.gpx` (`application/gpx+xml`) — which it **cannot** ingest, and it rejects a `filename` sent alongside a `file_url`/`file_id` reference (`400 Mutually exclusive parameters … 'file_id' or 'filename'`). A single rejected content part aborts the stream, surfacing as an **empty assistant bubble**.

Contract: every non-image file part is converted to an `input_text` description that **preserves the link** (`public_url` / `object_key`) so the coach can still resolve it — files are never dropped. This is centralized at the `toAgentInputItems` chokepoint (`lib/agent/agent-input.ts`), reusing `convertUnsupportedFilePartsToText` (`lib/agent/message-context.ts`). Never reintroduce a path that emits `input_file` for these types.

**Durable session caveat.** The durable model state (`chat_model_states.items`, used when `COACH_CONTEXT_STRATEGY` ≠ `full_history`) replays every stored item each turn, so one poisoned `input_file` breaks the thread permanently. Two defenses, both required:

- `SupabaseAgentSession.prepareHistoryItemForModelInput` (`lib/agent/supabase-agent-session.ts`) strips `input_image` and rewrites any already-stored `input_file` → link-preserving text. The SDK applies this to history before every model turn, so old threads self-heal on the run path.
- The delegation planner reads `getItems()` **raw**, so legacy poisoned rows also need a one-time data rewrite of `chat_model_states.items` (per environment — preview and production are separate Supabase projects).

## Environment Variables

See `.env.example`. Required:

- `APP_ENV`, `APP_BASE_URL`, `APP_JWT_SECRET`
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `OPENAI_API_KEY` — plan generation
- `R2_*` — file uploads

## Tests

- Web tests: `tests/web/` — Vitest with `environment: "node"` by default; DOM-based component tests use `@vitest-environment jsdom` directive for React rendering and testing-library interactions
- Python tests: `tests/python/` — pytest with `asyncio_mode = "auto"`
- Python test config: `conftest.py` for fixtures

## Local hooks (lefthook)

`bun install` automatically installs git hooks via the `prepare` script — no extra step needed after cloning.

### Pre-commit (fast, autofix)

Runs in parallel on staged files only. Fixes are re-staged automatically (`stage_fixed: true`):

| Hook             | What it does                                                                                                    |
| ---------------- | --------------------------------------------------------------------------------------------------------------- |
| `eslint-fix`     | ESLint `--fix` on staged `*.{ts,tsx,js,mjs,cjs}`                                                                |
| `prettier-write` | Prettier on staged `*.{ts,tsx,js,mjs,cjs,json,md,yml,yaml}`                                                     |
| `ruff-fix`       | `ruff check --fix` on staged `*.py`                                                                             |
| `ruff-format`    | `ruff format` on staged `*.py`                                                                                  |
| `actionlint`     | Lints `.github/workflows/` files (only when they are staged); gracefully skips if `actionlint` is not installed |

Install actionlint once with `brew install actionlint` to get GitHub Actions workflow linting on commit.

### Pre-push (full CI gate)

Runs sequentially and mirrors every check in `.github/workflows/ci.yml`, plus three additional static checks:

| Step                | Command                                                                       |
| ------------------- | ----------------------------------------------------------------------------- |
| `lint`              | `bun run lint`                                                                |
| `ruff-check`        | `uv run ruff check .`                                                         |
| `ruff-format-check` | `uv run ruff format --check .`                                                |
| `typecheck`         | `bun run typecheck`                                                           |
| `ty`                | `uv run ty check`                                                             |
| `vitest`            | `bun run test`                                                                |
| `pytest`            | `uv run pytest`                                                               |
| `cpd`               | Copy-paste detection across `app/`, `components/`, `lib/`, `api/`, `backend/` |
| `knip`              | Dead-code / unused-export detection (production code only)                    |
| `playwright`        | Full Playwright UI suite                                                      |

### Changes that don't impact the UI layer

Playwright is slow (~2–3 min). Only in cases where you are absolutely sure a change does not impact
the UI layer or interface UI layer interacts with or anything that will manifest only in rendering
lever type of tests, it may be skipped. Skip it on a given push using **either** method:

```bash
# 1. Keyword in the last commit subject
git commit -m "fix: tweak layout lefthook-skip-ui"
git push

# 2. One-off env var (no commit amend needed)
LEFTHOOK_SKIP_UI=1 git push
```

All other pre-push checks still run when Playwright is skipped.

### Bypass hooks entirely

You MUST pass hooks all of the time!

## Code Conventions

- TypeScript strict mode; ESLint zero-warnings policy
- Python: Ruff linting + formatting, 100-char line length, `ty` for type checking
- Zod schemas in `lib/schemas.ts` for all API validation boundaries
- All Python async handlers use `async def`
- Bearer token auth: clients pass `Authorization: Bearer <jwt>`; server validates via `require_user_context()`

<!-- lean-ctx -->

## lean-ctx

lean-ctx is active — the MCP tools replace native equivalents.
Full rules: LEAN-CTX.md (open on demand — do not auto-load).

<!-- /lean-ctx -->
