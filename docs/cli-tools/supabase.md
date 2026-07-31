# Supabase CLI

Use `supabase` for the Supabase stack, migrations, database checks, and explicitly requested
hosted-project operations. This repository's local configuration is `supabase/config.toml`.
Development, preview, and production are separate projects; never infer that a linked remote is the
intended target.

## Start here

```sh
command -v supabase
supabase --version
supabase --help
supabase <group> --help
supabase <group> <command> --help
```

The CLI changes frequently, so discover commands and flags from the installed version rather than
guessing. Prefer `--output-format json`, command-specific JSON output, and explicit `--local` or
`--linked` selectors when supported.

## Prefer repository commands locally

```sh
bun run db:start
bun run db:stop
bun run db:reset
bun run dev:local
supabase status -o json
```

The Bun scripts encode this repository's startup and environment behavior. `db:reset` destroys and
recreates the **local** database; use it only when discarding local data is acceptable.

## Migration workflow

1. Create migration files with `supabase migration new <descriptive_name>`; do not invent filenames.
2. Put the reviewed SQL in the generated file under `supabase/migrations/`.
3. Update `docs/supabase-migration-history.md` in the same change.
4. Validate locally with the repository reset/tests appropriate to the change.
5. Run local security and performance advisors when applicable:

   ```sh
   supabase db advisors --local --type security
   supabase db advisors --local --type performance
   ```

6. Verify migration state with `supabase migration list --local`.

Follow the RLS and schema guidance in `AGENTS.md`. Every table exposed through the Data API must have
an intentional access model and RLS where required.

## Queries and remote operations

```sh
supabase db query --local --output json '<read-only SQL>'
supabase db advisors --linked --type security
supabase migration list --linked
```

- Use `--local` by default. Before `--linked`, verify which project is linked and which environment
  it represents.
- Treat `db push`, `migration repair`, remote SQL writes, secrets changes, branch deletion, and
  project configuration changes as shared-state mutations. Confirm the target and intent first.
- Never run `db reset` against a hosted database.
- A pull request should normally contain migration files; do not apply them to preview or production
  without explicit interactive review with the user.
