# Exercise Training Plan GPT

ChatGPT-first endurance coaching app scaffold with:

- Next.js + TypeScript on Vercel
- Python API functions on Vercel
- Supabase Auth + Postgres
- Cloudflare R2 for raw uploads
- Strict static analysis across both languages

## Layout

- `app/`: Next.js routes and browser UI
- `components/`: shared React components
- `lib/`: frontend configuration and schemas
- `api/index.py`: Vercel Python entrypoint
- `backend/`: Python domain, services, and repository glue
- `tests/web/`: TypeScript tests
- `tests/python/`: Python tests

## Tooling

- JavaScript package manager: `bun`
- Python package manager: `uv`
- TypeScript linting: `eslint`
- Python lint/format: `ruff`
- Python type checking: `ty`

## Commands

### Web

```bash
bun install
bun run dev
bun run lint
bun run typecheck
bun run test
```

### Python

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

## R2 Uploads

`POST /api/files/presign-upload` now returns a presigned `PUT` URL plus the final object key and
public URL. Configure:

- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET`
- `R2_ENDPOINT_URL` (optional; defaults from `R2_ACCOUNT_ID`)
- `R2_PUBLIC_BASE_URL` (optional; used for public object URLs)

## Supabase Persistence

Database schema changes live in `supabase/migrations/`. See
`docs/supabase-migration-history.md` for migration ordering and Supabase Preview
history repair notes.

Configure:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`

Recommended local setup:

- Put app secrets in `.env`.
- Put your Supabase personal access token in `.envrc` as `SUPABASE_ACCESS_TOKEN=...` if you want
  to run Supabase CLI or management API commands locally.
- Replace the placeholder `APP_JWT_SECRET` with a strong random value before any shared or deployed use.

Environment contract:

- Standardize on Vercel's built-in environment names: `development`, `preview`, and `production`.
- Set `APP_ENV` to match the active deployment environment: `development`, `preview`, or `production`.
- Use a separate Supabase project and database for each environment.
- Never point `preview` or `development` at the production database.
- Keep `.env` for local-only work. Shared and deployed environments should get values from Vercel and CI secrets.

Required runtime variables for all three environments:

- `APP_ENV`
- `APP_BASE_URL`
- `APP_JWT_SECRET`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `OPENAI_API_KEY` when plan generation is enabled
- `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET` when uploads are enabled

Vercel deployment checklist:

1. Create one Vercel project for this repo and leave the root directory at the repository root.
2. Keep the default Next.js build settings for the frontend and keep `vercel.json` so `api/index.py` stays on the `python3.12` runtime.
3. Add the required environment variables separately for Vercel `Development`, `Preview`, and `Production`.
4. Set `APP_ENV=development` in Vercel Development, `APP_ENV=preview` in Preview, and `APP_ENV=production` in Production.
5. Set `APP_BASE_URL` to the matching public origin for each environment.
6. Point each environment at its own Supabase project by setting both the server-side and `NEXT_PUBLIC_` Supabase variables.
7. Generate a strong `APP_JWT_SECRET` for each shared environment instead of reusing the local placeholder secret from `.env`.
8. Add `OPENAI_API_KEY` only if plan generation is enabled in that environment.
9. Add the R2 variables only if upload support is enabled in that environment.
10. Redeploy after secrets are added so both the Next.js app and the Python function receive the new values.

If the login page says the Supabase browser client is not configured, the current environment is
missing `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, or both.

Ownership rules:

- Local development: `.env` and optional `.envrc`
- Vercel `Development`: points to the development Supabase project
- Vercel `Preview`: points to the preview Supabase project
- Vercel `Production`: points to the production Supabase project
- GitHub Actions environments: `development`, `preview`, and `production` should each hold the matching CI secrets

CI/CD promotion rules:

- Commit schema changes as migration files in `supabase/migrations/`.
- Validate migrations and integration tests against `development` first.
- Promote the same migration set to `preview` before any production release.
- Apply production migrations from CI/CD, not from a developer machine.
- Deploy the app to each environment only with that environment's matching secrets and database.

Suggested mapping:

- `development`: integration testing and shared non-production validation
- `preview`: pre-production validation with production-like configuration
- `production`: live traffic only

Current backend behavior:

- `POST /api/profile` reads the athlete profile from Supabase.
- `PUT /api/profile` upserts the athlete profile in Supabase.
- `POST /api/check-ins` persists a check-in row and returns the stored record.
- `POST /api/plans/generate` reads the athlete profile from Supabase before composing a plan.

If Supabase is not configured, those endpoints return `503`.
If a profile is missing, profile and plan generation return `404`.

## Notes

- The current implementation is still a scaffold in product terms, but profile reads and check-in writes now have a concrete persistence path.
- OAuth consent now persists durable grants and binds authorization to a browser session; real provider secrets are still required before deployment.
