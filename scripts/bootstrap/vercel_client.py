"""Vercel REST API client for environment variable management."""

import httpx

_VERCEL_BASE = "https://api.vercel.com"

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
    }
)


class VercelClient:
    def __init__(self, token: str, project_id: str, team_id: str, dry_run: bool = False) -> None:
        self._project_id = project_id
        self._team_id = team_id
        self._dry_run = dry_run
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

    def _params(self) -> dict:
        return {"teamId": self._team_id} if self._team_id else {}

    def get_production_domain(self) -> str:
        """Return the canonical production domain for this project.

        Prefers a non-vercel.app custom domain; falls back to the vercel.app alias.
        """
        r = self._http.get(
            f"{_VERCEL_BASE}/v10/projects/{self._project_id}",
            params=self._params(),
        )
        r.raise_for_status()
        data = r.json()
        aliases = [a.get("domain", "") for a in data.get("alias", [])]
        # Prefer shortest custom domain (not *.vercel.app)
        custom = [a for a in aliases if a and ".vercel.app" not in a]
        if custom:
            return min(custom, key=len)
        # Fall back to the vercel.app alias
        vercel_aliases = [a for a in aliases if ".vercel.app" in a]
        if vercel_aliases:
            return min(vercel_aliases, key=len)
        return ""

    def _env_vars(self) -> list[dict]:
        r = self._http.get(
            f"{_VERCEL_BASE}/v10/projects/{self._project_id}/env",
            params=self._params(),
        )
        r.raise_for_status()
        return r.json().get("envs", [])

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
                    patch = self._http.patch(
                        f"{_VERCEL_BASE}/v10/projects/{self._project_id}/env/{ev['id']}",
                        json={"target": remaining_targets},
                        params=self._params(),
                    )
                    patch.raise_for_status()
                    updated += 1
                else:
                    delete = self._http.delete(
                        f"{_VERCEL_BASE}/v10/projects/{self._project_id}/env/{ev['id']}",
                        params=self._params(),
                    )
                    delete.raise_for_status()
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

        # Build lookup: (key, first_target) -> env var ID
        existing_lookup: dict[tuple[str, str], str] = {}
        for ev in existing_vars:
            for t in ev.get("target", []):
                existing_lookup[(ev["key"], t)] = ev["id"]

        created = 0
        updated = 0

        for key, value in vars.items():
            is_sensitive = key in SENSITIVE_ENV_KEYS
            var_type = "sensitive" if is_sensitive else "encrypted"
            for scope in target:
                env_id = existing_lookup.get((key, scope))
                if env_id:
                    if self._dry_run:
                        label = "sensitive" if is_sensitive else "encrypted"
                        print(f"  [dry-run] Would update {key} ({scope}) [{label}]")
                    else:
                        patch = self._http.patch(
                            f"{_VERCEL_BASE}/v10/projects/{self._project_id}/env/{env_id}",
                            json={"value": value, "target": [scope], "type": var_type},
                            params=self._params(),
                        )
                        patch.raise_for_status()
                    updated += 1
                else:
                    if self._dry_run:
                        label = "sensitive" if is_sensitive else "encrypted"
                        print(f"  [dry-run] Would create {key} ({scope}) [{label}]")
                    else:
                        post = self._http.post(
                            f"{_VERCEL_BASE}/v10/projects/{self._project_id}/env",
                            json={
                                "key": key,
                                "value": value,
                                "target": [scope],
                                "type": var_type,
                            },
                            params=self._params(),
                        )
                        post.raise_for_status()
                    created += 1

        print(f"  Vercel env vars: {created} created, {updated} updated ({target}).")

    def close(self) -> None:
        self._http.close()
