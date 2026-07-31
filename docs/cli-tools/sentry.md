# Sentry CLI

Use `sentry` to investigate issues, events, traces, spans, logs, releases, and other Sentry resources.
The CLI normally discovers authentication and project context from local configuration, DSNs, and
source. Let auto-detection run first; specify `<org>/<project>` only when detection fails or selects
the wrong target.

## Start here

```sh
command -v sentry
sentry --version
sentry --help
sentry <group> <command> --help
```

Do not pre-authenticate routinely. Run the intended read command first; if authentication is needed,
the CLI will report it. Never print or store the token.

## Investigation workflow

```sh
sentry issue list --query 'is:unresolved' --limit 10 \
  --json --fields shortId,title,level,status,lastSeen,permalink
sentry issue view <PROJECT-ID> --json \
  --fields shortId,title,status,culprit,event,trace,permalink
sentry trace list --period 1h --limit 10 --json
sentry trace view <trace-id> --json
sentry log list --period 1h --limit 100 --json
```

- Prefer a dedicated command (`issue view`, `trace view`, `log list`, and so on) over `sentry api`.
- Use issue short IDs such as `PROJECT-ABC` when available.
- Bound searches with `--period`, `--limit`, and a Sentry `--query` filter.
- Use `--json --fields ...` to keep output small and machine-readable.
- Use `--fresh` only when cached discovery or results are unsuitable.
- `-w`/`--web` opens a resource in the browser when a dashboard view is useful.

For an issue, inspect the issue and latest event before using `sentry issue explain` or
`sentry issue plan`; AI analysis supplements rather than replaces the underlying evidence.

## API and schema fallback

```sh
sentry schema <resource>
sentry api <endpoint> --json
```

Use `sentry schema` to discover endpoints and `sentry api` only when no dedicated command exposes
the required data. Keep responses bounded and select fields where supported.

## Mutations and safety

- Resolving, archiving, merging, or otherwise changing issues modifies shared triage state. Verify
  the issue and project before proceeding.
- Confirm before project deletion, release deletion, trial activation, alert changes, dashboard
  changes, or other destructive/billing-affecting operations.
- Release versions must exactly match the value configured in `Sentry.init`; verify before creating,
  finalizing, deploying, or associating commits.
- Do not send test events to a hosted project unless requested. For local-only investigation, prefer
  `sentry local run -- <command>` when appropriate.
