"""Vercel API client backed by the `vercel api` CLI subcommand.

Uses the local `vercel` CLI's stored authentication (from `vercel login`)
instead of a bearer token. The CLI must be installed and logged in.
"""

import json
import subprocess

# Environment variable keys that contain secrets and must be marked sensitive.
# Sensitive vars are write-only in the Vercel dashboard (values are never shown).
SENSITIVE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "APP_JWT_SECRET",
        "SUPABASE_SERVICE_ROLE_KEY",
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "SENTRY_DSN",
        "SENTRY_AUTH_TOKEN",
    }
)


class VercelClient:
    def __init__(self, project_id: str, team_id: str, dry_run: bool = False) -> None:
        self._project_id = project_id
        self._team_id = team_id
        self._dry_run = dry_run

    def _scoped_path(self, path: str) -> str:
        if not self._team_id:
            return path
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}teamId={self._team_id}"

    def _api(self, method: str, path: str, body: dict | None = None) -> dict:
        cmd = [
            "vercel",
            "api",
            self._scoped_path(path),
            "--method",
            method,
            "--raw",
            "--non-interactive",
        ]
        if method == "DELETE":
            cmd.append("--dangerously-skip-permissions")
        if body is not None:
            cmd.extend(["--input", "-"])
        result = subprocess.run(
            cmd,
            input=json.dumps(body) if body is not None else None,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"vercel api {method} {path} failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        out = result.stdout.strip()
        return json.loads(out) if out else {}

    def get_production_domain(self) -> str:
        """Return the canonical production domain for this project.

        Prefers a non-vercel.app custom domain; falls back to the vercel.app alias.
        """
        data = self._api("GET", f"/v10/projects/{self._project_id}")
        aliases: list[str] = [str(a.get("domain", "")) for a in data.get("alias", [])]
        custom = [a for a in aliases if a and ".vercel.app" not in a]
        if custom:
            return min(custom, key=lambda d: len(d))
        vercel_aliases = [a for a in aliases if ".vercel.app" in a]
        if vercel_aliases:
            return min(vercel_aliases, key=lambda d: len(d))
        return ""

    def _env_vars(self) -> list[dict]:
        return self._api("GET", f"/v10/projects/{self._project_id}/env").get("envs", [])

    def remove_env_vars(self, target: list[str], keys: list[str]) -> None:
        """Delete environment variables matching the given target scopes and keys."""
        existing_vars = self._env_vars()
        deleted = 0
        updated = 0

        for ev in existing_vars:
            env_key = ev.get("key")
            if env_key not in keys:
                continue
            for scope in target:
                env_targets = ev.get("target", [])
                if scope not in env_targets:
                    continue
                remaining_targets = [t for t in env_targets if t not in target]
                if self._dry_run:
                    action = "update" if remaining_targets else "delete"
                    print(f"  [dry-run] Would {action} {env_key} ({scope})")
                elif remaining_targets:
                    self._api(
                        "PATCH",
                        f"/v10/projects/{self._project_id}/env/{ev['id']}",
                        {"target": remaining_targets},
                    )
                    updated += 1
                else:
                    self._api("DELETE", f"/v10/projects/{self._project_id}/env/{ev['id']}")
                    deleted += 1
                break

        if deleted or updated:
            print(f"  Vercel env vars: {deleted} deleted, {updated} updated ({target}).")

    def upsert_env_vars(self, target: list[str], vars: dict[str, str]) -> None:
        """Create or update Vercel environment variables for the given target scopes.

        target should be ["preview"] or ["production"].
        Idempotent: fetches existing vars and PATCHes by ID rather than creating duplicates.
        Keys listed in SENSITIVE_ENV_KEYS are stored as type "sensitive" so their values
        are write-only in the Vercel dashboard.
        """
        existing_vars = self._env_vars()

        existing_lookup: dict[tuple[str, str], str] = {}
        for ev in existing_vars:
            for t in ev.get("target", []):
                existing_lookup[(ev["key"], t)] = ev["id"]

        created = 0
        updated = 0

        for key, value in vars.items():
            var_type = "sensitive" if key in SENSITIVE_ENV_KEYS else "encrypted"
            for scope in target:
                env_id = existing_lookup.get((key, scope))
                if env_id:
                    if self._dry_run:
                        print(f"  [dry-run] Would update {key} ({scope}) [{var_type}]")
                    else:
                        self._api(
                            "PATCH",
                            f"/v10/projects/{self._project_id}/env/{env_id}",
                            {"value": value, "target": [scope], "type": var_type},
                        )
                    updated += 1
                else:
                    if self._dry_run:
                        print(f"  [dry-run] Would create {key} ({scope}) [{var_type}]")
                    else:
                        self._api(
                            "POST",
                            f"/v10/projects/{self._project_id}/env",
                            {
                                "key": key,
                                "value": value,
                                "target": [scope],
                                "type": var_type,
                            },
                        )
                    created += 1

        print(f"  Vercel env vars: {created} created, {updated} updated ({target}).")

    def close(self) -> None:
        pass
