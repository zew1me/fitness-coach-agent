# CLI tool briefs

This directory is the progressive-disclosure reference for project CLI tools. `AGENTS.md`
contains the catalog and shared quick-reference rules; load the relevant brief before using an
unfamiliar, destructive, externally visible, or organization-specific tool.

| Tool        | Use it for                                                                 | Brief                        |
| ----------- | -------------------------------------------------------------------------- | ---------------------------- |
| `vercel`    | Deployments, runtime logs, project settings, and environment variables     | [vercel.md](vercel.md)       |
| `supabase`  | Local services, migrations, database checks, and hosted project operations | [supabase.md](supabase.md)   |
| `sentry`    | Issues, events, traces, logs, releases, and Sentry API access              | [sentry.md](sentry.md)       |
| `codegraph` | Local structural navigation and change-impact analysis                     | [codegraph.md](codegraph.md) |

## Loading order

1. Select the tool from the table above; do not load every brief preemptively.
2. Read that tool's brief.
3. Run `<tool> --help`, then `<tool> <group> --help` or the exact command's `--help` when needed.
4. Consult upstream documentation only if the brief and installed CLI help are insufficient.

The installed CLI is the authority for its available commands and flags. Prefer bounded,
machine-readable output and never print, persist, or commit credentials.
