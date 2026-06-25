"""Supabase Management API client for project provisioning and migrations."""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

_BASE = "https://api.supabase.com/v1"
_MIGRATIONS_DIR = Path("supabase/migrations")
_POLL_INTERVAL = 5  # seconds
_POLL_MAX = 60  # attempts (~5 minutes)
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_EMAIL_OTP_TEMPLATE = """<h2>Your sign-in code</h2>
<p>Enter this code to sign in:</p>
<p><strong>{{ .Token }}</strong></p>
<p>Or use this link:</p>
<p><a href="{{ .ConfirmationURL }}">Sign in</a></p>"""


def _print_auth_config_instructions(
    site_url: str, extra_redirect_urls: list[str], smtp: dict[str, Any] | None = None
) -> None:
    all_urls = ([site_url] if site_url else []) + extra_redirect_urls
    redirect_list = ", ".join(all_urls) if all_urls else "(your app URL)/**"
    print(
        "  Supabase auth redirect URLs must be set manually.\n"
        "  Dashboard → Authentication → URL Configuration:\n"
        f"    Site URL:      {site_url or '(your app URL)'}\n"
        f"    Redirect URLs: {redirect_list}\n"
        "  Dashboard → Authentication → Providers → Email:\n"
        "    Disable 'Confirm email' so first-time signups receive OTP codes.\n"
        "  Dashboard → Authentication → Email Templates:\n"
        "    Include {{ .Token }} and {{ .ConfirmationURL }} in Magic Link and Confirm Signup."
    )
    if smtp:
        print(
            "  Dashboard → Authentication → Emails → SMTP Settings:\n"
            f"    Host: {smtp['host']}  Port: {smtp['port']}  Username: {smtp['user']}\n"
            f"    Sender email: {smtp['admin_email']}  Sender name: {smtp['sender_name']}\n"
            "    Password: <your Resend API key>"
        )


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
        self._raise_for_status(r)
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self._http.post(f"{_BASE}{path}", json=body)
        self._raise_for_status(r)
        return r.json()

    def _patch(self, path: str, body: dict) -> dict:
        r = self._http.patch(f"{_BASE}{path}", json=body)
        self._raise_for_status(r)
        return r.json()

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == _HTTP_UNAUTHORIZED:
                raise RuntimeError(
                    "Supabase Management API returned 401 Unauthorized. "
                    "Check SUPABASE_ACCESS_TOKEN in .env.bootstrap; create a new "
                    "Supabase personal access token for a user with access to this "
                    "organization/project, then rerun bootstrap."
                ) from exc
            if status == _HTTP_FORBIDDEN:
                raise RuntimeError(
                    "Supabase Management API returned 403 Forbidden. "
                    "The token is valid but does not have access to this "
                    "organization/project."
                ) from exc
            raise RuntimeError(
                f"Supabase Management API request failed with HTTP {status}."
            ) from exc

    def ensure_project(self, env: str, project_ref: str = "") -> tuple[str, str | None]:
        """Return (project_ref, db_password).

        db_password is only available when a new project is created; it is None
        for pre-existing projects. Callers should persist it to the state file.
        """
        name = f"fitness-coach-agent-{env}"

        if project_ref:
            print(f"  Using existing project ref {project_ref!r}.")
            print("  Supabase CLI will verify access when fetching keys and applying migrations.")
            return project_ref, None

        if not self._token or not self._org_id:
            raise RuntimeError(
                "SUPABASE_ACCESS_TOKEN and SUPABASE_ORG_ID are required when "
                "auto-creating Supabase projects. Set SUPABASE_PROJECT_REF_PREVIEW/"
                "SUPABASE_PROJECT_REF_PROD to use an existing project via the "
                "Supabase CLI login instead."
            )

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

    def get_api_keys(self, ref: str, use_cli: bool = False) -> dict:
        """Return {url, anon_key, service_role_key} for a project."""
        keys_data = (
            self._get_api_keys_from_cli(ref) if use_cli else self._get(f"/projects/{ref}/api-keys")
        )
        # Keys list: [{name: "anon", api_key: "..."}, {name: "service_role", ...}]
        keys = {k["name"]: k.get("api_key") or k.get("key", "") for k in keys_data}
        return {
            "url": f"https://{ref}.supabase.co",
            "anon_key": keys.get("anon", ""),
            "service_role_key": keys.get("service_role", ""),
        }

    def _cli_env(self) -> dict[str, str]:
        """Subprocess env for the Supabase CLI: forwards the bootstrap PAT.

        The CLI reads SUPABASE_ACCESS_TOKEN from its environment. Without this,
        the CLI inherits only the parent shell — so if the operator's shell has
        no token (or a stale one), CLI commands fail even when the script itself
        has a valid PAT loaded from .env.bootstrap.
        """
        env = os.environ.copy()
        if self._token:
            env["SUPABASE_ACCESS_TOKEN"] = self._token
        return env

    def _get_api_keys_from_cli(self, ref: str) -> list[dict]:
        result = subprocess.run(
            ["supabase", "projects", "api-keys", "--project-ref", ref, "--output", "json"],
            capture_output=True,
            text=True,
            check=False,
            env=self._cli_env(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                "supabase projects api-keys failed. Run `supabase login`, confirm "
                f"your CLI account can access project {ref}, then rerun bootstrap."
            )
        data = json.loads(result.stdout)
        if isinstance(data, list):
            return data
        keys = data.get("api_keys") or data.get("apiKeys") or data.get("keys")
        if isinstance(keys, list):
            return keys
        raise RuntimeError("Could not parse Supabase CLI API keys output.")

    def apply_migrations(self, ref: str, db_password: str) -> None:
        """Apply all pending migrations using the Supabase CLI."""
        migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
        if not migration_files:
            print("  No migration files found — skipping.")
            return

        print(f"  Applying migrations to {ref} via supabase CLI...")
        if self._dry_run:
            print(f"  [dry-run] Would link Supabase project {ref}")
            for f in migration_files:
                print(f"  [dry-run] Would apply {f.name}")
            return

        link_result = subprocess.run(
            [
                "supabase",
                "link",
                "--project-ref",
                ref,
                "--password",
                db_password,
                "--yes",
            ],
            capture_output=False,
            text=True,
            check=False,
            env=self._cli_env(),
        )
        if link_result.returncode != 0:
            raise RuntimeError(f"supabase link failed (exit {link_result.returncode})")

        result = subprocess.run(
            ["supabase", "db", "push", "--password", db_password, "--yes"],
            capture_output=False,
            text=True,
            check=False,
            env=self._cli_env(),
        )
        if result.returncode != 0:
            raise RuntimeError(f"supabase db push failed (exit {result.returncode})")

    def configure_auth_settings(
        self,
        ref: str,
        site_url: str,
        extra_redirect_urls: list[str],
        smtp: dict[str, Any] | None = None,
    ) -> None:
        """Configure Supabase auth redirect URLs, OTP behavior, and (optional) SMTP.

        Sets site_url, uri_allow_list, and mailer_autoconfirm=True so that magic-link
        emails point to the right host and first-time signups receive OTP codes rather
        than email-confirmation links. When ``smtp`` is provided, the project's custom
        SMTP sender (e.g. Resend) is configured in the same call so email delivery is
        identical across environments rather than left as a manual, drift-prone step.
        Requires SUPABASE_ACCESS_TOKEN; prints manual instructions if the token is
        absent or the call is rejected.
        """
        all_urls = ([site_url] if site_url else []) + extra_redirect_urls
        allow_list = ",".join(all_urls)

        body: dict = {
            "mailer_autoconfirm": True,
            "mailer_otp_length": 6,
            "mailer_templates_confirmation_content": _EMAIL_OTP_TEMPLATE,
            "mailer_templates_magic_link_content": _EMAIL_OTP_TEMPLATE,
        }
        if site_url:
            body["site_url"] = site_url
        if allow_list:
            body["uri_allow_list"] = allow_list
        if smtp:
            body.update(
                {
                    "smtp_host": smtp["host"],
                    "smtp_port": smtp["port"],
                    "smtp_user": smtp["user"],
                    "smtp_pass": smtp["pass"],
                    "smtp_admin_email": smtp["admin_email"],
                    "smtp_sender_name": smtp["sender_name"],
                }
            )

        if not self._token:
            _print_auth_config_instructions(site_url, extra_redirect_urls, smtp)
            return

        if self._dry_run:
            smtp_note = f", smtp_host={smtp['host']!r}" if smtp else ""
            print(
                f"  [dry-run] Would configure auth: site_url={site_url!r}, "
                f"mailer_autoconfirm=True, uri_allow_list={allow_list!r}{smtp_note}"
            )
            return

        try:
            self._patch(f"/projects/{ref}/config/auth", body)
            smtp_note = f", smtp_host={smtp['host']!r}" if smtp else ""
            print(
                f"  Auth settings configured: site_url={site_url!r}, "
                f"mailer_autoconfirm=True{smtp_note}"
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            print(f"  Warning: could not configure auth settings via API: {exc}")
            _print_auth_config_instructions(site_url, extra_redirect_urls, smtp)

    def close(self) -> None:
        self._http.close()
