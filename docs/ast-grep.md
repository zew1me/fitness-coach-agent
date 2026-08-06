# ast-grep agent guardrails

ast-grep complements ESLint, Ruff, and type checking with structural checks for repository-specific
contracts. It is intentionally small: a noisy rule teaches agents to work around safety tooling.

## Required workflow

1. Before changing unfamiliar code, use `bunx --no-install ast-grep run` to find the existing
   structural convention.
2. After editing, run `bun run ast-grep:changed`; before pushing, run `bun run ast-grep:check`.
3. When a review, regression, or incident exposes a recurring structural mistake, add a narrow rule
   and a valid/invalid test case in `.ast-grep/rule-tests/` in the same change.

## Useful discovery queries

```sh
# Locate model execution boundaries and inspect their input normalization.
bunx --no-install ast-grep run -l TypeScript \
  -p '$RUNNER.run($AGENT, $INPUT, $$$)' lib/agent

# Find model payload objects by their discriminant rather than text layout.
bunx --no-install ast-grep run -l TypeScript \
  -p '{ type: $TYPE, $$$ }' lib/agent app/api/chat

# Find API routes protected by the common FastAPI dependency.
bunx --no-install ast-grep run -l Python \
  -p 'async def $HANDLER($$$, $USER: UserContext = Depends(require_user_context), $$$): $$$' api

# Find the established repository boundaries for Supabase client construction.
bunx --no-install ast-grep run -l Python -p 'create_client($$$)' backend/repos
```

## Rule design

- Scope a rule with `files`/`ignores`; do not scan tests or legacy adapters unless they own the
  invariant.
- Start at `warning` while proving a rule, then promote it to `error` once valid/invalid examples
  show it is precise.
- Use `ast-grep test` to keep the examples and diagnostics stable.
- Add `fix:` only for a mechanical, reviewed migration, and apply with
  `ast-grep scan --interactive`.
