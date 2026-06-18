import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def _read_vercel_project_json() -> dict:
    path = Path(".vercel/project.json")
    if path.exists():
        return json.loads(path.read_text())
    return {}


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


class BootstrapSettings(BaseSettings):
    # Supabase Management API. Only required when auto-creating projects.
    supabase_access_token: str = ""
    supabase_org_id: str = ""
    supabase_project_ref_preview: str = ""
    supabase_project_ref_prod: str = ""
    supabase_db_password_preview: str = ""
    supabase_db_password_prod: str = ""
    supabase_url_preview: str = ""
    supabase_url_prod: str = ""
    supabase_anon_key_preview: str = ""
    supabase_anon_key_prod: str = ""
    supabase_service_role_key_preview: str = ""
    supabase_service_role_key_prod: str = ""

    # Cloudflare R2
    cf_api_token: str
    cf_account_id: str
    r2_access_key_id_preview: str = ""
    r2_access_key_id_prod: str = ""
    r2_secret_access_key_preview: str = ""
    r2_secret_access_key_prod: str = ""
    # Optional manual overrides for the R2 public base URL per environment.
    # Bootstrap auto-detects these from the Cloudflare API; set these only if
    # auto-detection fails or you're using a custom domain on your R2 bucket.
    r2_public_base_url_preview: str = ""
    r2_public_base_url_prod: str = ""

    # Vercel — authentication comes from the local `vercel` CLI; no token needed.
    production_domain: str = ""

    # Custom SMTP (Resend) for auth emails. Applied identically to preview and
    # production so magic-link / OTP delivery is at parity across environments.
    # Leave smtp_pass and smtp_admin_email blank to keep Supabase's built-in
    # email sender (rate-limited, not for production). smtp_pass is the Resend
    # API key; the Resend SMTP username is the literal string "resend".
    smtp_host: str = "smtp.resend.com"
    smtp_port: int = 465
    smtp_user: str = "resend"
    smtp_pass: str = ""
    smtp_admin_email: str = ""
    smtp_sender_name: str = ""

    # App secrets passed through to Vercel env vars
    openai_api_key: str
    tavily_api_key: str

    model_config = SettingsConfigDict(
        env_file=".env.bootstrap",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # .env.bootstrap is authoritative. Otherwise a stale SUPABASE_ACCESS_TOKEN
        # exported in the shell silently masks a corrected value in the file and
        # causes confusing 401s on the Supabase Management API.
        del settings_cls
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)


def load_settings() -> tuple[BootstrapSettings, str, str]:
    """Load bootstrap settings and Vercel project metadata.

    Returns (settings, vercel_project_id, vercel_team_id).
    """
    settings = BootstrapSettings()  # type: ignore[call-arg]
    vercel_meta = _read_vercel_project_json()
    project_id = vercel_meta.get("projectId", "")
    team_id = vercel_meta.get("orgId", "")
    if not project_id:
        raise RuntimeError(
            ".vercel/project.json not found or missing projectId. "
            "Run `vercel link` first to connect this directory to a Vercel project."
        )
    return settings, project_id, team_id
