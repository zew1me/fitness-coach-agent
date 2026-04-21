import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_vercel_project_json() -> dict:
    path = Path(".vercel/project.json")
    if path.exists():
        return json.loads(path.read_text())
    return {}


class BootstrapSettings(BaseSettings):
    # Supabase Management API
    supabase_access_token: str
    supabase_org_id: str
    supabase_project_ref_preview: str = ""
    supabase_project_ref_prod: str = ""

    # Cloudflare R2
    cf_api_token: str
    cf_account_id: str

    # Vercel
    vercel_token: str
    production_domain: str = ""

    # App secrets passed through to Vercel env vars
    openai_api_key: str
    tavily_api_key: str

    model_config = SettingsConfigDict(
        env_file=".env.bootstrap",
        env_file_encoding="utf-8",
        extra="ignore",
    )


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
