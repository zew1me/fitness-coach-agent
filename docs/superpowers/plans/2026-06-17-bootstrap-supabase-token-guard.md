# Bootstrap Supabase Token Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bootstrap warn before external operations when `SUPABASE_ACCESS_TOKEN` conflicts across the shell and `.env.bootstrap`, or when bootstrap is exposed to shell-only token use.

**Architecture:** Add one pure credential-source preflight in `scripts/bootstrap/config.py`, using `python-dotenv` to inspect the configured file without exposing secret values. Invoke it from `scripts/bootstrap/main.py` immediately after settings load, then document `.env.bootstrap` as the authoritative bootstrap credential source.

**Tech Stack:** Python 3.12, Pydantic Settings, python-dotenv, pytest, Bun scripts, GitHub pull requests, Vercel preview deployments.

---

### Task 1: Add credential-source regression tests

**Files:**

- Modify: `tests/python/test_bootstrap.py`

- [ ] **Step 1: Write failing tests for warning behavior**

Add imports for `scripts.bootstrap.config` and tests that call the wished-for helper:

```python
from scripts.bootstrap import config as bootstrap_config


def test_warns_when_shell_supabase_token_conflicts_with_env_file(
    monkeypatch, tmp_path, capsys
) -> None:
    env_file = tmp_path / ".env.bootstrap"
    env_file.write_text("SUPABASE_ACCESS_TOKEN=file-secret\n")
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "shell-secret")

    bootstrap_config.warn_about_supabase_token_source(env_file=env_file)

    warning = capsys.readouterr().err
    assert "conflicts" in warning
    assert ".env.bootstrap" in warning
    assert "unset SUPABASE_ACCESS_TOKEN" in warning
    assert "file-secret" not in warning
    assert "shell-secret" not in warning


def test_warns_when_supabase_token_is_shell_only(monkeypatch, tmp_path, capsys) -> None:
    env_file = tmp_path / ".env.bootstrap"
    env_file.write_text("SUPABASE_ACCESS_TOKEN=\n")
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "shell-secret")

    bootstrap_config.warn_about_supabase_token_source(env_file=env_file)

    warning = capsys.readouterr().err
    assert "shell-exported SUPABASE_ACCESS_TOKEN" in warning
    assert "move it to .env.bootstrap" in warning
    assert "shell-secret" not in warning


@pytest.mark.parametrize("shell_token", [None, "file-secret"])
def test_does_not_warn_for_safe_supabase_token_sources(
    monkeypatch, tmp_path, capsys, shell_token
) -> None:
    env_file = tmp_path / ".env.bootstrap"
    env_file.write_text("SUPABASE_ACCESS_TOKEN=file-secret\n")
    if shell_token is None:
        monkeypatch.delenv("SUPABASE_ACCESS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", shell_token)

    bootstrap_config.warn_about_supabase_token_source(env_file=env_file)

    assert capsys.readouterr().err == ""
```

- [ ] **Step 2: Write a failing entrypoint-order test**

```python
def test_run_warns_about_token_source_before_external_operations(monkeypatch) -> None:
    events: list[str] = []

    monkeypatch.setattr(
        bootstrap_main,
        "load_settings",
        lambda: (_settings(), "vercel-project", "vercel-team"),
    )
    monkeypatch.setattr(bootstrap_main, "load_state", lambda _env: {})
    monkeypatch.setattr(
        bootstrap_main,
        "warn_about_supabase_token_source",
        lambda: events.append("preflight"),
        raising=False,
    )

    class ExternalOperationReached(Exception):
        pass

    def stop_at_first_external_operation(*_args, **_kwargs):
        events.append("external")
        raise ExternalOperationReached

    monkeypatch.setattr(bootstrap_main, "_fetch_vercel_domain", stop_at_first_external_operation)

    with pytest.raises(ExternalOperationReached):
        bootstrap_main.run("prod", skip_migrations=False, dry_run=False)

    assert events == ["preflight", "external"]
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run pytest tests/python/test_bootstrap.py -k "token_source or external_operations" -v
```

Expected: failures because `warn_about_supabase_token_source` does not exist and `run()` does not invoke it.

### Task 2: Implement the preflight guard

**Files:**

- Modify: `scripts/bootstrap/config.py`
- Modify: `scripts/bootstrap/main.py`
- Test: `tests/python/test_bootstrap.py`

- [ ] **Step 1: Add the minimal warning helper**

Add these imports and function to `scripts/bootstrap/config.py`:

```python
import os
import sys
from collections.abc import Mapping

from dotenv import dotenv_values


def warn_about_supabase_token_source(
    *,
    env_file: Path = Path(".env.bootstrap"),
    environ: Mapping[str, str] | None = None,
) -> None:
    environment = os.environ if environ is None else environ
    shell_token = environment.get("SUPABASE_ACCESS_TOKEN", "").strip()
    file_token = (dotenv_values(env_file).get("SUPABASE_ACCESS_TOKEN") or "").strip()

    if not shell_token:
        return
    if file_token and shell_token != file_token:
        print(
            "Warning: shell SUPABASE_ACCESS_TOKEN conflicts with .env.bootstrap; "
            ".env.bootstrap is authoritative. Run `unset SUPABASE_ACCESS_TOKEN` "
            "before bootstrap to avoid using the stale token in other commands.",
            file=sys.stderr,
        )
    elif not file_token:
        print(
            "Warning: bootstrap detected a shell-exported SUPABASE_ACCESS_TOKEN but no "
            "token in .env.bootstrap. This shell-only pattern is risky; move it to "
            ".env.bootstrap and run `unset SUPABASE_ACCESS_TOKEN` before bootstrap.",
            file=sys.stderr,
        )
```

- [ ] **Step 2: Invoke the helper before external operations**

Import `warn_about_supabase_token_source` in `scripts/bootstrap/main.py`, then call it directly after `load_settings()`:

```python
settings, vercel_project_id, vercel_team_id = load_settings()
warn_about_supabase_token_source()
state = load_state(env)
```

- [ ] **Step 3: Run focused tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run pytest tests/python/test_bootstrap.py -k "token_source or external_operations" -v
```

Expected: all selected tests pass and neither token value appears in captured warnings.

- [ ] **Step 4: Run bootstrap test module**

Run:

```bash
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run pytest tests/python/test_bootstrap.py
```

Expected: all bootstrap tests pass.

### Task 3: Update the operator contract

**Files:**

- Modify: `.env.bootstrap.example`
- Modify: `README.md`
- Modify: `docs/supabase-otp-parity.md`

- [ ] **Step 1: Update `.env.bootstrap.example`**

Replace the Supabase token comment with:

```dotenv
# Put the bootstrap PAT here; `.env.bootstrap` is authoritative for bootstrap.
# Before `bun run setup:preview` or `bun run setup:prod`, unset any shell-exported
# SUPABASE_ACCESS_TOKEN so other Supabase commands cannot use a stale credential.
SUPABASE_ACCESS_TOKEN=
```

- [ ] **Step 2: Clarify README shell-export scope**

State that `.envrc` exports are for direct Supabase CLI or Management API commands only, and that bootstrap operators must put the PAT in `.env.bootstrap` and unset the shell variable.

- [ ] **Step 3: Clarify production bootstrap instructions**

In `docs/supabase-otp-parity.md`, require the PAT in `.env.bootstrap`, add `unset SUPABASE_ACCESS_TOKEN` before `bun run setup:prod`, and explain the immediate warnings for conflicts and shell-only usage.

- [ ] **Step 4: Run formatting and documentation checks**

Run:

```bash
bunx prettier --check .env.bootstrap.example README.md docs/supabase-otp-parity.md docs/superpowers/specs/2026-06-17-bootstrap-supabase-token-guard-design.md docs/superpowers/plans/2026-06-17-bootstrap-supabase-token-guard.md
git diff --check
```

Expected: Prettier and whitespace checks pass.

### Task 4: Remove unavailable coach tools

**Files:**

- Modify: `lib/agent/tools.ts`
- Modify: `lib/agent/coach-tools.ts`
- Modify: `lib/agent/orchestration-types.ts`
- Modify: `tests/web/agent-tools.test.ts`
- Modify: `tests/web/agent-specialist-report.test.ts`

- [ ] **Step 1: Write failing registry tests**

Change the expected registry in `tests/web/agent-tools.test.ts` to the eight executable tools and add
an assertion that the seven issue-tracked names are absent. Update specialist schema tests so
unavailable write tools are rejected and supported `update_athlete_profile` inputs still enforce the
no-`user_id` rule.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
bun run test tests/web/agent-tools.test.ts tests/web/agent-specialist-report.test.ts
```

Expected: registry and specialist-schema assertions fail because the pending tools are still exposed.

- [ ] **Step 3: Remove the pending definitions and schemas**

Remove `get_compliance_summary`, `save_activity_from_text`, `save_recovery_data`, `update_schedule`,
`update_goals`, `adjust_plan`, and `recalibrate_thresholds` from `coachToolDefinitions` and
`proposedWriteToolNameSchema`. Delete schemas used only by those definitions. Replace the
`pending_implementation` fallback in `executeCoachTool` with:

```typescript
throw new Error(`Unknown coach tool: ${name}`);
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
bun run test tests/web/agent-tools.test.ts tests/web/agent-specialist-report.test.ts
```

Expected: both test files pass.

- [ ] **Step 5: Confirm the fallback is gone**

Run:

```bash
rg -n "pending_implementation|get_compliance_summary|save_activity_from_text|save_recovery_data|update_schedule|update_goals|adjust_plan|recalibrate_thresholds" lib tests/web
```

Expected: no runtime registry references; issue-oriented documentation may still contain the names.

### Task 5: Verify and self-review

**Files:**

- Review: all files changed from `origin/main`

- [ ] **Step 1: Run relevant repository checks**

Run:

```bash
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run pytest tests/python/test_bootstrap.py
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run ruff check scripts/bootstrap tests/python/test_bootstrap.py
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run ruff format --check scripts/bootstrap tests/python/test_bootstrap.py
UV_CACHE_DIR=/private/tmp/codex-uv-cache uv run ty check scripts/bootstrap
```

Expected: every command exits zero.

- [ ] **Step 2: Self-review the final diff**

Run `git diff origin/main...HEAD` plus the uncommitted diff and inspect for:

- token leakage in messages or test failures;
- warning timing after any external operation;
- behavior drift from the existing dotenv-over-shell precedence;
- false positives for matching or file-only tokens;
- documentation that still recommends shell exports for bootstrap.

Fix any finding through another RED/GREEN cycle, then rerun Task 4 Step 1.

- [ ] **Step 3: Commit implementation**

```bash
git add scripts/bootstrap/config.py scripts/bootstrap/main.py tests/python/test_bootstrap.py .env.bootstrap.example README.md docs/supabase-otp-parity.md docs/superpowers/plans/2026-06-17-bootstrap-supabase-token-guard.md
git commit -m "fix(bootstrap): warn about risky Supabase token sources"
```

### Task 6: Publish PR and verify deployment

**Files:**

- Publish: current branch and GitHub pull request

- [ ] **Step 1: Create a `codex/` branch if still detached, then push it**

```bash
git switch -c codex/bootstrap-supabase-token-guard
git push -u origin codex/bootstrap-supabase-token-guard
```

- [ ] **Step 2: Open a draft PR with incident rationale**

The PR body must explain the stale shell token incident, why `.env.bootstrap` remains authoritative, the new early warning behavior, and the verification performed.

- [ ] **Step 3: Verify PR checks and Vercel deployment**

Inspect PR checks until the Vercel preview deployment is successful. Open the deployment URL and verify it responds successfully. If the repository does not create previews for these non-UI files, explicitly trigger the repository's supported Vercel preview flow and verify the resulting deployment.

- [ ] **Step 4: Perform final PR self-review**

Review the exact GitHub PR diff after push, confirm it matches the locally reviewed commit, and leave no unresolved self-review findings. Report the PR URL, deployment URL/status, review result, and exact test evidence.
