"""Bootstrap script for preview and production environments.

Usage:
    uv run python -m scripts.bootstrap.main --env preview
    uv run python -m scripts.bootstrap.main --env prod
    uv run python -m scripts.bootstrap.main --env preview --skip-migrations
    uv run python -m scripts.bootstrap.main --env preview --dry-run
"""

import argparse
import sys

from scripts.bootstrap.cloudflare_client import CloudflareClient
from scripts.bootstrap.config import BootstrapSettings, load_settings
from scripts.bootstrap.state import get_or_generate_jwt_secret, load_state, save_state
from scripts.bootstrap.supabase_client import SupabaseClient
from scripts.bootstrap.vercel_client import VercelClient

_MASK_CHARS = 4


def _mask(value: str) -> str:
    """Mask a secret value, showing only the last few characters."""
    if not value or len(value) <= _MASK_CHARS:
        return "****"
    return f"{'*' * (len(value) - _MASK_CHARS)}{value[-_MASK_CHARS:]}"


def _setup_supabase(
    settings: BootstrapSettings,
    env: str,
    state: dict,
    skip_migrations: bool,
    dry_run: bool,
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
        project_ref, db_pass = sb.ensure_project(env, project_ref=existing_ref)
        state["supabase_project_ref"] = project_ref
        # Persist the DB password if this is a newly created project — it cannot
        # be retrieved again after creation.
        if db_pass:
            state["supabase_db_password"] = db_pass
            print("  DB password saved to state file.")
        keys = sb.get_api_keys(project_ref)
        print(f"  Project URL: {keys['url']}")
        if not skip_migrations:
            sb.apply_migrations(project_ref)
        else:
            print("  Skipping migrations (--skip-migrations).")
    finally:
        sb.close()
    return {"ref": project_ref, **keys}


def _setup_r2(
    settings: BootstrapSettings,
    env: str,
    state: dict,
    cors_origins: list[str],
    dry_run: bool,
) -> dict:
    """Provision R2 bucket, CORS, and API token. Returns R2 credentials dict."""
    print(f"\n[2/4] Cloudflare R2 ({env})")
    cf = CloudflareClient(settings.cf_api_token, settings.cf_account_id, dry_run=dry_run)
    try:
        bucket_name = cf.ensure_bucket(env)
        cf.ensure_cors(bucket_name, allowed_origins=cors_origins)

        cached_secret = state.get("r2_secret_access_key", "")
        r2_creds = cf.ensure_r2_token(bucket_name, env, existing_secret=cached_secret)

        # Persist R2 secret immediately — it is only returned at token creation time.
        # Do this before any further API calls so it is never lost on a subsequent error.
        state["r2_access_key_id"] = r2_creds["access_key_id"]
        state["r2_secret_access_key"] = r2_creds["secret_access_key"]
        if not dry_run:
            save_state(env, state)

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


def _resolve_app_base_url(settings: BootstrapSettings, env: str, vercel: VercelClient) -> str:
    """Determine APP_BASE_URL for the given environment."""
    if env != "prod":
        # Preview: leave blank — Python backend falls back to VERCEL_URL at runtime,
        # Next.js auth callback falls back to request.nextUrl.origin.
        return ""
    if settings.production_domain:
        return f"https://{settings.production_domain}"
    domain = vercel.get_production_domain()
    url = f"https://{domain}" if domain else ""
    if url:
        print(f"  APP_BASE_URL (auto-detected): {url}")
    else:
        print(
            "  Warning: could not auto-detect production domain. "
            "Set PRODUCTION_DOMAIN in .env.bootstrap."
        )
    return url


def _build_cors_origins(settings: BootstrapSettings, vercel_domain: str) -> list[str]:
    """Build the CORS allowed origins list from known stable domains.

    Vercel preview URLs (per-commit hashes) are not predictable, so we scope CORS
    to the production domain and any stable preview alias returned by the Vercel API.
    Add CORS_EXTRA_ORIGINS to .env.bootstrap if you need additional origins.
    """
    origins: list[str] = []
    if settings.production_domain:
        origins.append(f"https://{settings.production_domain}")
    if vercel_domain and ".vercel.app" in vercel_domain:
        # Include both the exact alias and a wildcard for branch/PR previews
        # under the same project slug.
        slug = vercel_domain.split(".vercel.app")[0]
        origins.append(f"https://{vercel_domain}")
        origins.append(f"https://{slug}-*.vercel.app")
    elif vercel_domain:
        origins.append(f"https://{vercel_domain}")
    # Fallback: if we have no specific origins, allow all of vercel.app.
    # Document this clearly so operators know to tighten it once they have a stable domain.
    if not origins:
        print(
            "  Warning: no specific domains found for CORS. Allowing https://*.vercel.app. "
            "Set PRODUCTION_DOMAIN in .env.bootstrap to restrict this."
        )
        origins = ["https://*.vercel.app"]
    return origins


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

    # Fetch the Vercel production domain early so we can use it for CORS config
    # and APP_BASE_URL resolution without opening a second Vercel client later.
    vercel = VercelClient(settings.vercel_token, vercel_project_id, vercel_team_id, dry_run=dry_run)
    try:
        vercel_domain = vercel.get_production_domain()
    finally:
        vercel.close()

    supabase = _setup_supabase(settings, env, state, skip_migrations, dry_run)
    if not dry_run:
        save_state(env, state)

    cors_origins = _build_cors_origins(settings, vercel_domain)
    # _setup_r2 calls save_state internally after capturing the R2 secret.
    r2 = _setup_r2(settings, env, state, cors_origins, dry_run)

    print(f"\n[3/4] Generating stable secrets ({env})")
    jwt_secret = get_or_generate_jwt_secret(env, state)
    if not dry_run:
        save_state(env, state)
    print("  APP_JWT_SECRET: ready.")

    print(f"\n[4/4] Vercel environment variables ({env})")
    vercel = VercelClient(settings.vercel_token, vercel_project_id, vercel_team_id, dry_run=dry_run)
    try:
        app_base_url = _resolve_app_base_url(settings, env, vercel)
        vercel_target = ["production"] if env == "prod" else ["preview"]
        env_vars = _build_env_vars(env, app_base_url, jwt_secret, supabase, r2, settings)
        vercel.upsert_env_vars(vercel_target, env_vars)
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
