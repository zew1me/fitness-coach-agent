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

The API now expects two Postgres tables:

- `athlete_profiles`
- `check_ins`

An initial schema is included at `supabase/migrations/0001_initial_schema.sql`.

Configure:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Current backend behavior:

- `POST /api/profile` reads the athlete profile from Supabase.
- `PUT /api/profile` upserts the athlete profile in Supabase.
- `POST /api/check-ins` persists a check-in row and returns the stored record.
- `POST /api/plans/generate` reads the athlete profile from Supabase before composing a plan.

If Supabase is not configured, those endpoints return `503`.
If a profile is missing, profile and plan generation return `404`.

## Notes

- The current implementation is still a scaffold in product terms, but profile reads and check-in writes now have a concrete persistence path.
- OAuth consent flows still need durable grants, real user-session binding, and provider secrets before deployment.
- GitHub issue filing is currently blocked locally until a remote is configured and `gh auth login` is refreshed.
- Once GitHub access is configured, run `scripts/create_github_issues.sh` to file the prepared issue set from `docs/github-issues/`.
