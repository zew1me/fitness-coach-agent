"""Supabase Management API client for project provisioning and migrations."""

import subprocess
import time
from pathlib import Path

import httpx

_BASE = "https://api.supabase.com/v1"
_MIGRATIONS_DIR = Path("supabase/migrations")
_POLL_INTERVAL = 5  # seconds
_POLL_MAX = 60  # attempts (~5 minutes)


class SupabaseClient:
    def __init__(self, access_token: str, org_id: str, dry_run: bool = False) -> None:
        self._token = access_token
        self._org_id = org_id
        self._dry_run = dry_run
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )

    def _get(self, path: str) -> dict:
        r = self._http.get(f"{_BASE}{path}")
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self._http.post(f"{_BASE}{path}", json=body)
        r.raise_for_status()
        return r.json()

    def ensure_project(self, env: str, project_ref: str = "") -> tuple[str, str | None]:
        """Return (project_ref, db_password).

        db_password is only available when a new project is created; it is None
        for pre-existing projects. Callers should persist it to the state file.
        """
        name = f"fitness-coach-agent-{env}"

        if project_ref:
            print(f"  Verifying existing project ref {project_ref!r}…")
            data = self._get(f"/projects/{project_ref}")
            if data.get("organization_id") and data.get("status"):
                print(f"  Found: {data['name']} [{data['status']}]")
                self._wait_healthy(project_ref)
                return project_ref, None
            raise RuntimeError(f"Project {project_ref!r} not found or not accessible.")

        # Check if a project with this name already exists in the org
        projects = self._get("/projects")
        for p in projects:
            if p.get("organization_id") == self._org_id and p.get("name") == name:
                ref = p["id"]
                print(f"  Found existing project {name!r} → {ref}")
                self._wait_healthy(ref)
                return ref, None

        # Create it
        print(f"  Creating Supabase project {name!r}…")
        if self._dry_run:
            print("  [dry-run] Would create project — skipping")
            return "dry-run-ref", None

        import secrets as _secrets

        db_pass = _secrets.token_urlsafe(24)
        data = self._post(
            "/projects",
            {
                "name": name,
                "organization_id": self._org_id,
                "region": "us-east-1",
                "db_pass": db_pass,
                "plan": "free",
            },
        )
        ref = data["id"]
        print(f"  Created project {name!r} → {ref} (provisioning…)")
        self._wait_healthy(ref)
        return ref, db_pass

    def _wait_healthy(self, ref: str) -> None:
        for attempt in range(_POLL_MAX):
            data = self._get(f"/projects/{ref}")
            status = data.get("status", "")
            if status == "ACTIVE_HEALTHY":
                return
            if attempt == 0:
                print(f"  Waiting for ACTIVE_HEALTHY (status: {status})…", end="", flush=True)
            else:
                print(".", end="", flush=True)
            time.sleep(_POLL_INTERVAL)
        print()
        raise RuntimeError(f"Project {ref!r} did not become ACTIVE_HEALTHY within timeout.")

    def get_api_keys(self, ref: str) -> dict:
        """Return {url, anon_key, service_role_key} for a project."""
        keys_data = self._get(f"/projects/{ref}/api-keys")
        # Keys list: [{name: "anon", api_key: "..."}, {name: "service_role", ...}]
        keys = {k["name"]: k["api_key"] for k in keys_data}
        return {
            "url": f"https://{ref}.supabase.co",
            "anon_key": keys.get("anon", ""),
            "service_role_key": keys.get("service_role", ""),
        }

    def apply_migrations(self, ref: str) -> None:
        """Apply all pending migrations using the Supabase CLI."""
        migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
        if not migration_files:
            print("  No migration files found — skipping.")
            return

        print(f"  Applying migrations to {ref} via supabase CLI…")
        if self._dry_run:
            for f in migration_files:
                print(f"  [dry-run] Would apply {f.name}")
            return

        result = subprocess.run(
            ["supabase", "db", "push", "--project-ref", ref],
            capture_output=False,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"supabase db push failed (exit {result.returncode})")

    def close(self) -> None:
        self._http.close()
