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

## Notes

- The current implementation is a scaffold with typed route contracts, prompt composition, and a minimal plan-generation path.
- Supabase persistence and OAuth consent flows are structured for production work, but still need provider secrets and database migrations before deployment.
