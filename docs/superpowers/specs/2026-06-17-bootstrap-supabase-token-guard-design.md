# Bootstrap Supabase Token Guard Design

## Context

Production bootstrap previously allowed an exported `SUPABASE_ACCESS_TOKEN` to override the
correct value in `.env.bootstrap`. The resulting stale credential was not visible until a later
Supabase Management API request failed with `401 Unauthorized`. The precedence fix makes
`.env.bootstrap` authoritative and forwards the resolved credential to Supabase CLI subprocesses,
but operators still receive no early warning about conflicting shell state or shell-only token use.

## Behavior

`bun run setup:prod` and `bun run setup:preview` will run a credential-source preflight immediately
after loading configuration and before any Vercel, Supabase, or Cloudflare request.

- If the shell and `.env.bootstrap` both contain non-empty, different tokens, print a warning to
  stderr that `.env.bootstrap` is authoritative and recommend unsetting the shell variable.
- If the shell contains a token and `.env.bootstrap` does not contain a non-empty token, print a
  warning to stderr that bootstrap is relying on the risky shell-export pattern and recommend
  moving the token into `.env.bootstrap` and unsetting the shell variable.
- If both sources contain the same non-empty token, or only `.env.bootstrap` contains one, print no
  warning.
- Never print, partially reveal, hash, or otherwise expose either token.
- Warnings are non-fatal. Existing bootstrap behavior and precedence remain unchanged.

## Implementation Boundary

Add a small, pure warning helper in `scripts/bootstrap/config.py`. It will accept the loaded settings,
the raw shell environment, and the configured dotenv path so its behavior is deterministic and easy
to test. `scripts/bootstrap/main.py` will invoke it immediately after `load_settings()` returns.

The helper will inspect `.env.bootstrap` using the dotenv parser already supplied through the
settings dependency rather than implementing a second ad hoc parser. It will distinguish absent or
blank file values from non-empty values without logging credentials.

## Documentation

Update `.env.bootstrap.example` and the production bootstrap instructions to make
`.env.bootstrap` the required source for bootstrap's Supabase personal access token. Clarify that
shell exports are suitable for direct CLI work but should be unset when running bootstrap.

## Tests

Focused unit tests will cover:

1. Different non-empty shell and file tokens produce the conflict warning.
2. A shell-only token produces the risky-pattern warning.
3. Matching shell and file tokens produce no warning.
4. A file-only token produces no warning.
5. The bootstrap entrypoint performs the preflight before its first external operation.

Tests will assert messages and source names only, and will explicitly verify that token values are
absent from captured output.
