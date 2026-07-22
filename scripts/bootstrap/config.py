import json
from pathlib import Path

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def _read_vercel_project_json() -> dict:
    path = Path(".vercel/project.json")
    if path.exists():
        return json.loads(path.read_text())
    return {}


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
    intervals_client_id: str = ""
    intervals_client_secret: str = ""
    intervals_token_encryption_secret: str = ""
    strava_integration_enabled: bool = False
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_token_encryption_secret: str = ""
    strava_authorization_version: str = ""

    # Sentry observability. Two distinct DSNs (separate Sentry Client Keys):
    #   sentry_dsn        — server/edge/python (SENTRY_DSN); treated as a secret.
    #   sentry_public_dsn — browser (NEXT_PUBLIC_SENTRY_DSN); inlined into the client
    #                       bundle, so it is public and not sensitive.
    # sentry_auth_token is a build-time secret used by withSentryConfig to upload
    # source maps. Leave any blank to skip provisioning that var.
    sentry_dsn: str = ""
    sentry_public_dsn: str = ""
    sentry_auth_token: str = ""

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
