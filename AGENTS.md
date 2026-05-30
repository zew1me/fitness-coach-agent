# AI Agent Based Endurance Coaching

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This App Is

A ChatGPT-first endurance coaching app. Athletes log in via magic link, manage their fitness profiles, and submit check-ins. ChatGPT (or any OAuth client) connects via a durable OAuth 2.0 PKCE consent flow to read athlete data and request adaptive 14-day training plans.

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

Use separate Supabase projects per environment (development / preview / production).

### Chat persistence

`chat_messages` stores each turn as a `parts jsonb` array matching the AI SDK [`UIMessage`](https://sdk.vercel.ai) shape — text parts, file parts, tool-call parts, and reasoning parts ride together on one row. This mirrors vercel/chatbot's `Message_v2` table and eliminates the lossy translation that used to drop inline images and tool-call status on reload (issue #149).

The legacy columns `chat_messages.content` (denormalized text mirror) and the separate `chat_attachments` table are still present for one release window; a follow-up migration will drop them once readers have fully cut over.

### Storage — current reality vs. intent

**Intent.** Cloudflare R2 via S3-compatible API. `POST /api/files/presign-upload` and `POST /api/chat/attachments/presign` mint presigned upload URLs; the client uploads directly (or via the `POST /api/chat/attachments/upload` proxy). The returned `public_url` should be the canonical reference used everywhere downstream.

**Current reality (as of #149 landing).** Chat image attachments are being written into Postgres as inline base64 `data:image/png;base64,…` URLs inside `chat_messages.parts`, **not** as R2 URLs. Row sizes for a single-image message reach ~250KB. The R2 presign + upload calls *do* happen and the bytes land in R2, but the `public_url` returned by the upload proxy isn't being threaded back into the `LocalAttachment` state before submit, so `uploadedFileParts` falls through to its `attachment.dataUrl` (base64) fallback (`components/coach-chat.tsx:237`). Tracked in **issue #163**; whether R2 is referenced by anything else in the running app is **issue #164**.

Anyone reasoning about storage costs, backup size, or LLM context bloat should treat chat images as Postgres rows today, not R2 objects.

## Environment Variables

See `.env.example`. Required:
- `APP_ENV`, `APP_BASE_URL`, `APP_JWT_SECRET`
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`

Optional (features degrade gracefully without):
- `OPENAI_API_KEY` — plan generation
- `R2_*` — file uploads

## Tests

- Web tests: `tests/web/` — Vitest, `environment: "node"`
- Python tests: `tests/python/` — pytest with `asyncio_mode = "auto"`
- Python test config: `conftest.py` for fixtures

## Code Conventions

- TypeScript strict mode; ESLint zero-warnings policy
- Python: Ruff linting + formatting, 100-char line length, `ty` for type checking
- Zod schemas in `lib/schemas.ts` for all API validation boundaries
- All Python async handlers use `async def`
- Bearer token auth: clients pass `Authorization: Bearer <jwt>`; server validates via `require_user_context()`
