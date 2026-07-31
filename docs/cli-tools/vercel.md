# Vercel CLI

Use `vercel` to inspect this project's deployments, logs, settings, and environment variables.
The repository contains `vercel.json`; the ignored `.vercel/` directory holds the local project
link and must not be committed.

## Start here

```sh
command -v vercel
vercel --version
vercel --help
vercel <command> --help
```

The CLI defaults to non-interactive behavior when it detects an agent. Still pass explicit
arguments and scope filters so commands are deterministic.

## Preferred read workflows

```sh
vercel list --limit 10 --json
vercel inspect <deployment-url-or-id> --json
vercel inspect <deployment-url-or-id> --logs
vercel logs --level error --since 1h --limit 100 --json
vercel logs --branch "$(git branch --show-current)" --limit 100 --json
```

- Let the existing `.vercel/` link select the project. Run `vercel link` only when the project is
  unlinked, and verify the account, team, and project before proceeding.
- Bound lists and logs with `--limit`, `--since`, `--branch`, or a deployment identifier.
- Prefer `--json` when output will be filtered or parsed.
- Use `vercel inspect` and `vercel logs` before reaching for raw API calls.

## Environment variables

```sh
vercel env list <development|preview|production>
vercel env run -- <command>
vercel env pull .env.local
```

`vercel env pull` writes secrets to disk. Keep the destination ignored, never display its contents,
and never commit it. Adding, updating, or removing a variable changes shared cloud state: verify the
target environment and obtain confirmation before doing so.

## Deployments and safety

- `vercel deploy` creates an externally visible deployment. Run it only when deployment is part of
  the request, and report the resulting URL.
- Treat `--prod`, `promote`, `rollback`, `redeploy`, aliases, domains, and project settings as
  production-affecting operations. Confirm the target and intent first.
- Confirm before `vercel remove` or any other destructive command.
- Do not pass tokens on the command line or include authentication output in logs or documentation.
- Prefer Git-driven preview and production deployments when the task only asks for a pull request.
