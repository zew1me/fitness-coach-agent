"""Bootstrap script for preview and production environments.

Usage:
    uv run python -m scripts.bootstrap.main --env preview
    uv run python -m scripts.bootstrap.main --env prod
    uv run python -m scripts.bootstrap.main --env preview --skip-migrations
    uv run python -m scripts.bootstrap.main --env preview --dry-run
"""

import argparse
import sys
from typing import Protocol

from scripts.bootstrap.cloudflare_client import CloudflareClient
from scripts.bootstrap.config import BootstrapSettings, load_settings
from scripts.bootstrap.state import get_or_generate_jwt_secret, load_state, save_state
from scripts.bootstrap.supabase_client import SupabaseClient
from scripts.bootstrap.vercel_client import VercelClient

_MASK_CHARS = 4


class VercelEnvSyncClient(Protocol):
    def remove_env_vars(self, target: list[str], keys: list[str]) -> None: ...

    def upsert_env_vars(self, target: list[str], vars: dict[str, str]) -> None: ...


def _mask(value: str) -> str:
    """Mask a secret value, showing only the last few characters."""
    if not value or len(value) <= _MASK_CHARS:
        return "****"
    return f"{'*' * (len(value) - _MASK_CHARS)}{value[-_MASK_CHARS:]}"


def _fetch_vercel_domain(
    settings: BootstrapSettings,
    vercel_project_id: str,
    vercel_team_id: str,
    dry_run: bool,
) -> str:
    """Fetch the Vercel project's stable production domain (shortest alias)."""
    vercel = VercelClient(settings.vercel_token, vercel_project_id, vercel_team_id, dry_run=dry_run)
    try:
        return vercel.get_production_domain()
    except Exception as exc:
        print(f"  Warning: could not fetch Vercel domain: {exc}")
        return ""
    finally:
        vercel.close()


def _build_auth_site_url(settings: BootstrapSettings, env: str, vercel_domain: str) -> str:
    """Return the primary site URL for Supabase auth configuration."""
    if env == "prod":
        if settings.production_domain:
            return f"https://{settings.production_domain}"
        return f"https://{vercel_domain}" if vercel_domain else ""
    return f"https://{vercel_domain}" if vercel_domain else ""


def _build_auth_redirect_urls(env: str, vercel_domain: str) -> list[str]:
    """Return extra allowed redirect URLs for Supabase auth configuration."""
    urls = ["http://localhost:3000/**", "http://localhost:3001/**"]
    if env == "preview":
        urls.append("https://fitness-coach-agent-*-nigel-stukes-projects.vercel.app/**")
        if vercel_domain:
            urls.append(f"https://{vercel_domain}/**")
    return urls


def _setup_supabase(  # noqa: PLR0913
    settings: BootstrapSettings,
    env: str,
    state: dict,
    skip_migrations: bool,
    dry_run: bool,
    vercel_domain: str = "",
) -> dict:
    """Provision Supabase project, fetch API keys, apply migrations. Returns keys dict."""
    print(f"\n[1/4] Supabase ({env})")
    sb = SupabaseClient(settings.supabase_access_token, settings.supabase_org_id, dry_run=dry_run)
    try:
        existing_ref = (
            settings.supabase_project_ref_preview
            if env == "preview"
            else settings.supabase_project_ref_prod
        )
        env_db_password = (
            settings.supabase_db_password_preview
            if env == "preview"
            else settings.supabase_db_password_prod
        )
        configured_keys = _configured_supabase_keys(settings, env, existing_ref)
        project_ref, db_pass = sb.ensure_project(env, project_ref=existing_ref)
        state["supabase_project_ref"] = project_ref
        # Persist the DB password immediately if this is a newly created project.
        # It cannot be retrieved again after creation, and later bootstrap steps can fail.
        if db_pass:
            state["supabase_db_password"] = db_pass
            if not dry_run:
                save_state(env, state)
            print("  DB password saved to state file.")

        sb.configure_auth_settings(
            project_ref,
            site_url=_build_auth_site_url(settings, env, vercel_domain),
            extra_redirect_urls=_build_auth_redirect_urls(env, vercel_domain),
        )
        migration_db_password = db_pass or state.get("supabase_db_password") or env_db_password
        keys = configured_keys or sb.get_api_keys(project_ref, use_cli=bool(existing_ref))
        print(f"  Project URL: {keys['url']}")
        if not skip_migrations:
            if not migration_db_password:
                env_var = (
                    "SUPABASE_DB_PASSWORD_PREVIEW"
                    if env == "preview"
                    else "SUPABASE_DB_PASSWORD_PROD"
                )
                raise RuntimeError(
                    "Supabase database password is required to apply migrations. "
                    f"Set {env_var} in .env.bootstrap, or reset the database password "
                    "in the Supabase dashboard and rerun bootstrap."
                )
            sb.apply_migrations(project_ref, str(migration_db_password))
        else:
            print("  Skipping migrations (--skip-migrations).")
    finally:
        sb.close()
    return {"ref": project_ref, **keys}


def _configured_supabase_keys(
    settings: BootstrapSettings,
    env: str,
    project_ref: str,
) -> dict | None:
    """Return dashboard-provided Supabase keys for an existing project, if configured."""
    if not project_ref:
        return None

    url = settings.supabase_url_preview if env == "preview" else settings.supabase_url_prod
    anon_key = (
        settings.supabase_anon_key_preview if env == "preview" else settings.supabase_anon_key_prod
    )
    service_role_key = (
        settings.supabase_service_role_key_preview
        if env == "preview"
        else settings.supabase_service_role_key_prod
    )
    if url and anon_key and service_role_key:
        print("  Using Supabase URL and API keys from .env.bootstrap.")
        return {
            "url": url,
            "anon_key": anon_key,
            "service_role_key": service_role_key,
        }
    if any((url, anon_key, service_role_key)):
        env_suffix = "PREVIEW" if env == "preview" else "PROD"
        raise RuntimeError(
            "Incomplete Supabase API key configuration. Set all of "
            f"SUPABASE_URL_{env_suffix}, SUPABASE_ANON_KEY_{env_suffix}, and "
            f"SUPABASE_SERVICE_ROLE_KEY_{env_suffix}, or leave all three blank "
            "to fetch keys via the Supabase CLI."
        )
    return None


def _setup_r2(
    settings: BootstrapSettings,
    env: str,
    state: dict,
    dry_run: bool,
) -> dict:
    """Provision R2 bucket and resolve runtime S3 credentials."""
    print(f"\n[2/4] Cloudflare R2 ({env})")
    cf = CloudflareClient(settings.cf_api_token, settings.cf_account_id, dry_run=dry_run)
    try:
        bucket_name = cf.ensure_bucket(env)
        print("  Skipping R2 CORS configuration; uploads use the backend proxy.")

        configured_creds = _configured_r2_credentials(settings, env)
        cached_creds = _cached_r2_credentials(state)
        if configured_creds:
            print("  Using R2 S3 credentials from .env.bootstrap.")
            r2_creds = configured_creds
        elif cached_creds:
            print("  Using R2 S3 credentials from state file.")
            r2_creds = cached_creds
        else:
            raise RuntimeError(
                "R2 bucket is ready, but runtime R2 S3 credentials are missing. "
                f"Create an account-level R2 API token scoped to bucket {bucket_name!r} "
                "with Object Read & Write permissions, then set "
                f"R2_ACCESS_KEY_ID_{env.upper()} and R2_SECRET_ACCESS_KEY_{env.upper()} "
                "in .env.bootstrap and rerun bootstrap."
            )

        state["r2_access_key_id"] = r2_creds["access_key_id"]
        state["r2_secret_access_key"] = r2_creds["secret_access_key"]
        if not dry_run:
            save_state(env, state)

        configured_public_base_url = (
            settings.r2_public_base_url_preview
            if env == "preview"
            else settings.r2_public_base_url_prod
        )
        if configured_public_base_url:
            print(f"  Using R2 public base URL from .env.bootstrap: {configured_public_base_url}")
            public_base_url = configured_public_base_url
        else:
            public_base_url = cf.get_public_base_url(bucket_name)
        endpoint_url = cf.endpoint_url()
    finally:
        cf.close()
    return {
        "bucket_name": bucket_name,
        "access_key_id": r2_creds["access_key_id"],
        "secret_access_key": r2_creds["secret_access_key"],
        "public_base_url": public_base_url,
        "endpoint_url": endpoint_url,
    }


def _configured_r2_credentials(settings: BootstrapSettings, env: str) -> dict | None:
    """Return dashboard-created R2 S3 credentials, if configured."""
    access_key_id = (
        settings.r2_access_key_id_preview if env == "preview" else settings.r2_access_key_id_prod
    )
    secret_access_key = (
        settings.r2_secret_access_key_preview
        if env == "preview"
        else settings.r2_secret_access_key_prod
    )
    if access_key_id and secret_access_key:
        return {"access_key_id": access_key_id, "secret_access_key": secret_access_key}
    if access_key_id or secret_access_key:
        env_suffix = "PREVIEW" if env == "preview" else "PROD"
        raise RuntimeError(
            "Incomplete R2 credential configuration. Set both "
            f"R2_ACCESS_KEY_ID_{env_suffix} and R2_SECRET_ACCESS_KEY_{env_suffix}, "
            "or leave both blank to use cached state credentials."
        )
    return None


def _cached_r2_credentials(state: dict) -> dict | None:
    access_key_id = state.get("r2_access_key_id", "")
    secret_access_key = state.get("r2_secret_access_key", "")
    if access_key_id and secret_access_key:
        return {"access_key_id": access_key_id, "secret_access_key": secret_access_key}
    return None


def _resolve_app_base_url(settings: BootstrapSettings, env: str, vercel_domain: str) -> str:
    """Determine APP_BASE_URL for the given environment."""
    if env != "prod":
        # Preview: leave blank — Python backend falls back to VERCEL_URL at runtime,
        # Next.js auth callback falls back to request.nextUrl.origin.
        return ""
    if settings.production_domain:
        return f"https://{settings.production_domain}"
    url = f"https://{vercel_domain}" if vercel_domain else ""
    if url:
        print(f"  APP_BASE_URL (auto-detected): {url}")
    else:
        print(
            "  Warning: could not auto-detect production domain. "
            "Set PRODUCTION_DOMAIN in .env.bootstrap."
        )
    return url


def _build_env_vars(  # noqa: PLR0913
    env: str,
    app_base_url: str,
    jwt_secret: str,
    supabase: dict,
    r2: dict,
    settings: BootstrapSettings,
) -> dict:
    vars: dict[str, str] = {
        "APP_ENV": "production" if env == "prod" else "preview",
        "APP_JWT_SECRET": jwt_secret,
        "NEXT_PUBLIC_SUPABASE_URL": supabase["url"],
        "NEXT_PUBLIC_SUPABASE_ANON_KEY": supabase["anon_key"],
        "SUPABASE_URL": supabase["url"],
        "SUPABASE_SERVICE_ROLE_KEY": supabase["service_role_key"],
        "OPENAI_API_KEY": settings.openai_api_key,
        "TAVILY_API_KEY": settings.tavily_api_key,
        "R2_ACCOUNT_ID": settings.cf_account_id,
        "R2_ACCESS_KEY_ID": r2["access_key_id"],
        "R2_SECRET_ACCESS_KEY": r2["secret_access_key"],
        "R2_BUCKET": r2["bucket_name"],
        "R2_ENDPOINT_URL": r2["endpoint_url"],
        "R2_PUBLIC_BASE_URL": r2["public_base_url"],
    }
    if app_base_url:
        vars["APP_BASE_URL"] = app_base_url
    return vars


def _sync_vercel_env_vars(
    vercel: VercelEnvSyncClient, vercel_target: list[str], env_vars: dict[str, str]
) -> None:
    """Apply Vercel env vars and clear preview-only vars that must fall back at runtime."""
    if vercel_target == ["preview"]:
        vercel.remove_env_vars(vercel_target, ["APP_BASE_URL"])
    vercel.upsert_env_vars(vercel_target, env_vars)


def _print_summary(
    env: str, supabase: dict, r2: dict, app_base_url: str, vercel_target: list
) -> None:
    print(f"\n{'=' * 60}")
    print(f"Bootstrap complete for {env.upper()}")
    print(f"{'=' * 60}")
    print(f"  Supabase project ref : {supabase['ref']}")
    print(f"  Supabase URL         : {supabase['url']}")
    print(f"  Supabase anon key    : {_mask(supabase['anon_key'])}")
    print(f"  Supabase svc role    : {_mask(supabase['service_role_key'])}")
    print(f"  R2 bucket            : {r2['bucket_name']}")
    print(f"  R2 access key ID     : {_mask(r2['access_key_id'])}")
    print(f"  R2 public base URL   : {r2['public_base_url'] or '(not configured)'}")
    print(f"  APP_BASE_URL         : {app_base_url or '(unset — falls back to VERCEL_URL)'}")
    print(f"  Vercel target        : {vercel_target}")
    print()
    print("Next steps:")
    print("  • Run `vercel env ls` to confirm vars are set.")
    print(
        "  • If R2 public URL is empty, enable 'Public Access' on the bucket in the\n"
        "    Cloudflare dashboard, then re-run to update R2_PUBLIC_BASE_URL."
    )
    print("  • Redeploy to pick up the new env vars: `vercel deploy` (or push a commit).")
    if env == "preview":
        print("  • For production, run: bun run setup:prod")


def run(env: str, skip_migrations: bool, dry_run: bool) -> None:
    if env not in ("preview", "prod"):
        print("--env must be 'preview' or 'prod'", file=sys.stderr)
        sys.exit(1)

    if dry_run:
        print("=== DRY RUN — no changes will be made ===\n")

    print("Loading configuration…")
    settings, vercel_project_id, vercel_team_id = load_settings()
    state = load_state(env)

    vercel_domain = _fetch_vercel_domain(settings, vercel_project_id, vercel_team_id, dry_run)
    supabase = _setup_supabase(settings, env, state, skip_migrations, dry_run, vercel_domain)
    if not dry_run:
        save_state(env, state)

    # _setup_r2 calls save_state internally after capturing the R2 secret.
    r2 = _setup_r2(settings, env, state, dry_run)

    print(f"\n[3/4] Generating stable secrets ({env})")
    jwt_secret = get_or_generate_jwt_secret(env, state)
    if not dry_run:
        save_state(env, state)
    print("  APP_JWT_SECRET: ready.")

    print(f"\n[4/4] Vercel environment variables ({env})")
    vercel = VercelClient(settings.vercel_token, vercel_project_id, vercel_team_id, dry_run=dry_run)
    try:
        app_base_url = _resolve_app_base_url(settings, env, vercel_domain)
        vercel_target = ["production"] if env == "prod" else ["preview"]
        env_vars = _build_env_vars(env, app_base_url, jwt_secret, supabase, r2, settings)
        _sync_vercel_env_vars(vercel, vercel_target, env_vars)
    finally:
        vercel.close()

    _print_summary(env, supabase, r2, app_base_url, vercel_target)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap preview/production infrastructure.")
    parser.add_argument("--env", required=True, choices=["preview", "prod"])
    parser.add_argument("--skip-migrations", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.env, args.skip_migrations, args.dry_run)


if __name__ == "__main__":
    main()
