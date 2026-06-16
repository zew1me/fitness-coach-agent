from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from scripts.bootstrap import main as bootstrap_main
from scripts.bootstrap import supabase_client
from scripts.bootstrap.cloudflare_client import CloudflareClient
from scripts.bootstrap.config import BootstrapSettings
from scripts.bootstrap.supabase_client import SupabaseClient
from scripts.bootstrap.vercel_client import VercelClient


def test_apply_migrations_links_project_then_pushes_with_password(monkeypatch, tmp_path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_schema.sql").write_text("select 1;")
    monkeypatch.setattr(supabase_client, "_MIGRATIONS_DIR", migrations_dir)

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(supabase_client.subprocess, "run", fake_run)

    client = SupabaseClient("token", "org")
    client.apply_migrations("project-ref", "db-secret")
    client.close()

    assert calls == [
        [
            "supabase",
            "link",
            "--project-ref",
            "project-ref",
            "--password",
            "db-secret",
            "--yes",
        ],
        ["supabase", "db", "push", "--password", "db-secret", "--yes"],
    ]
    assert "--project-ref" not in calls[1]


def test_supabase_api_unauthorized_mentions_access_token() -> None:
    class FakeHTTP:
        def get(self, _url: str) -> httpx.Response:
            request = httpx.Request("GET", "https://api.supabase.com/v1/projects/ref")
            return httpx.Response(401, request=request)

        def configure_auth_settings(self, *_args, **_kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    client = SupabaseClient("bad-token", "org")
    client._http = FakeHTTP()

    with pytest.raises(RuntimeError, match="SUPABASE_ACCESS_TOKEN"):
        client._get("/projects/ref")


def test_get_api_keys_can_use_supabase_cli(monkeypatch) -> None:
    def fake_run(command: list[str], **_kwargs):
        assert command == [
            "supabase",
            "projects",
            "api-keys",
            "--project-ref",
            "project-ref",
            "--output",
            "json",
        ]
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"name":"anon","api_key":"anon-key"},'
                '{"name":"service_role","api_key":"service-key"}]'
            ),
        )

    monkeypatch.setattr(supabase_client.subprocess, "run", fake_run)

    client = SupabaseClient("", "")
    keys = client.get_api_keys("project-ref", use_cli=True)
    client.close()

    assert keys == {
        "url": "https://project-ref.supabase.co",
        "anon_key": "anon-key",
        "service_role_key": "service-key",
    }


def test_supabase_cli_subprocesses_receive_access_token_in_env(monkeypatch, tmp_path) -> None:
    # The Supabase CLI authenticates via SUPABASE_ACCESS_TOKEN. The bootstrap
    # script must forward its own PAT to the CLI subprocess; otherwise the CLI
    # falls back to the operator's shell env, which may have no/stale token.
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_schema.sql").write_text("select 1;")
    monkeypatch.setattr(supabase_client, "_MIGRATIONS_DIR", migrations_dir)
    monkeypatch.delenv("SUPABASE_ACCESS_TOKEN", raising=False)

    captured_envs: list[dict[str, str]] = []

    def fake_run(_command: list[str], **kwargs):
        captured_envs.append(kwargs.get("env") or {})
        return SimpleNamespace(returncode=0, stdout="[]")

    monkeypatch.setattr(supabase_client.subprocess, "run", fake_run)

    client = SupabaseClient("sbp_test_token", "org")
    client._get_api_keys_from_cli("project-ref")
    client.apply_migrations("project-ref", "db-pw")
    client.close()

    assert len(captured_envs) == 3
    for env in captured_envs:
        assert env.get("SUPABASE_ACCESS_TOKEN") == "sbp_test_token"


def test_supabase_cli_env_falls_back_when_token_blank(monkeypatch) -> None:
    # When the bootstrap PAT is blank, do not stomp on an existing shell token —
    # the operator may rely on `supabase login` / shell-exported token.
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "shell-token")

    client = SupabaseClient("", "")
    env = client._cli_env()
    client.close()

    assert env["SUPABASE_ACCESS_TOKEN"] == "shell-token"


def test_bootstrap_settings_env_file_overrides_shell_env(monkeypatch, tmp_path) -> None:
    # A stale SUPABASE_ACCESS_TOKEN in the shell must not silently mask the
    # value in .env.bootstrap. This is the bug that caused production OTP setup
    # to PATCH the Supabase Management API with the wrong token and 401.
    env_file = tmp_path / ".env.bootstrap"
    env_file.write_text(
        "SUPABASE_ACCESS_TOKEN=sbp_from_file\n"
        "CF_API_TOKEN=cf-required\n"
        "CF_ACCOUNT_ID=acct-required\n"
        "OPENAI_API_KEY=openai-required\n"
        "TAVILY_API_KEY=tavily-required\n"
    )
    monkeypatch.setenv("SUPABASE_ACCESS_TOKEN", "stale_from_shell")

    settings = BootstrapSettings(_env_file=str(env_file))  # type: ignore[call-arg]

    assert settings.supabase_access_token == "sbp_from_file"


def test_setup_supabase_saves_new_db_password_before_migration_failure(monkeypatch) -> None:
    saved_states: list[dict] = []

    class FakeSupabaseClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def ensure_project(self, _env: str, project_ref: str = "") -> tuple[str, str]:
            assert project_ref == ""
            return "new-ref", "generated-password"

        def get_api_keys(self, ref: str, use_cli: bool = False) -> dict:
            assert ref == "new-ref"
            assert use_cli is False
            return {
                "url": "https://new-ref.supabase.co",
                "anon_key": "anon",
                "service_role_key": "svc",
            }

        def apply_migrations(self, ref: str, db_password: str) -> None:
            assert ref == "new-ref"
            assert db_password == "generated-password"
            raise RuntimeError("migration failed")

        def configure_auth_settings(self, *_args, **_kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(bootstrap_main, "SupabaseClient", FakeSupabaseClient)
    monkeypatch.setattr(
        bootstrap_main,
        "save_state",
        lambda env, state: saved_states.append({"env": env, **state}),
    )

    settings = _settings()
    state: dict = {}

    with pytest.raises(RuntimeError, match="migration failed"):
        bootstrap_main._setup_supabase(settings, "preview", state, False, False)

    assert saved_states == [
        {
            "env": "preview",
            "supabase_project_ref": "new-ref",
            "supabase_db_password": "generated-password",
        }
    ]


def test_setup_supabase_requires_db_password_for_existing_project(monkeypatch) -> None:
    class FakeSupabaseClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def ensure_project(self, _env: str, project_ref: str = "") -> tuple[str, None]:
            assert project_ref == "existing-ref"
            return "existing-ref", None

        def get_api_keys(self, ref: str, use_cli: bool = False) -> dict:
            assert ref == "existing-ref"
            assert use_cli is True
            return {
                "url": "https://existing-ref.supabase.co",
                "anon_key": "anon",
                "service_role_key": "svc",
            }

        def apply_migrations(self, _ref: str, _db_password: str) -> None:
            raise AssertionError("apply_migrations should not be called without a DB password")

        def configure_auth_settings(self, *_args, **_kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(bootstrap_main, "SupabaseClient", FakeSupabaseClient)

    settings = _settings(supabase_project_ref_preview="existing-ref")

    with pytest.raises(RuntimeError, match="SUPABASE_DB_PASSWORD_PREVIEW"):
        bootstrap_main._setup_supabase(settings, "preview", {}, False, False)


def test_setup_supabase_uses_configured_keys_for_existing_project(monkeypatch) -> None:
    applied: list[tuple[str, str]] = []

    class FakeSupabaseClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def ensure_project(self, _env: str, project_ref: str = "") -> tuple[str, None]:
            return project_ref, None

        def get_api_keys(self, _ref: str, use_cli: bool = False) -> dict:
            raise AssertionError("dashboard-provided keys should skip CLI key lookup")

        def apply_migrations(self, ref: str, db_password: str) -> None:
            applied.append((ref, db_password))

        def configure_auth_settings(self, *_args, **_kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(bootstrap_main, "SupabaseClient", FakeSupabaseClient)

    settings = _settings(
        supabase_project_ref_preview="existing-ref",
        supabase_db_password_preview="db-password",
        supabase_url_preview="https://existing-ref.supabase.co",
        supabase_anon_key_preview="anon",
        supabase_service_role_key_preview="service-role",
    )

    supabase = bootstrap_main._setup_supabase(settings, "preview", {}, False, False)

    assert supabase == {
        "ref": "existing-ref",
        "url": "https://existing-ref.supabase.co",
        "anon_key": "anon",
        "service_role_key": "service-role",
    }
    assert applied == [("existing-ref", "db-password")]


def test_setup_supabase_rejects_partial_configured_keys() -> None:
    settings = _settings(
        supabase_project_ref_preview="existing-ref",
        supabase_url_preview="https://existing-ref.supabase.co",
    )

    with pytest.raises(RuntimeError, match="Incomplete Supabase API key configuration"):
        bootstrap_main._configured_supabase_keys(settings, "preview", "existing-ref")


def test_setup_supabase_uses_state_db_password_for_existing_project(monkeypatch) -> None:
    applied: list[tuple[str, str]] = []

    class FakeSupabaseClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def ensure_project(self, _env: str, project_ref: str = "") -> tuple[str, None]:
            assert project_ref == "existing-ref"
            return "existing-ref", None

        def get_api_keys(self, ref: str, use_cli: bool = False) -> dict:
            assert ref == "existing-ref"
            assert use_cli is True
            return {
                "url": "https://existing-ref.supabase.co",
                "anon_key": "anon",
                "service_role_key": "svc",
            }

        def apply_migrations(self, ref: str, db_password: str) -> None:
            applied.append((ref, db_password))

        def configure_auth_settings(self, *_args, **_kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(bootstrap_main, "SupabaseClient", FakeSupabaseClient)

    settings = _settings(
        supabase_project_ref_preview="existing-ref",
        supabase_db_password_preview="env-password",
    )
    state = {"supabase_db_password": "state-password"}

    bootstrap_main._setup_supabase(settings, "preview", state, False, False)

    assert applied == [("existing-ref", "state-password")]


def test_setup_r2_creates_bucket_then_requests_runtime_credentials(monkeypatch) -> None:
    calls: list[str] = []

    class FakeCloudflareClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def ensure_bucket(self, env: str) -> str:
            calls.append(f"ensure_bucket:{env}")
            return "fitness-coach-agent-preview"

        def ensure_cors(self, *_args, **_kwargs) -> None:
            raise AssertionError("bootstrap should not configure R2 CORS")

        def ensure_r2_token(self, bucket_name: str, env: str, **_kwargs) -> dict:
            calls.append(f"ensure_r2_token:{bucket_name}:{env}")
            return {"access_key_id": "minted-id", "secret_access_key": "minted-secret"}

        def get_public_base_url(self, bucket_name: str) -> str:
            calls.append(f"get_public_base_url:{bucket_name}")
            return ""

        def endpoint_url(self) -> str:
            return "https://account-id.r2.cloudflarestorage.com"

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(bootstrap_main, "CloudflareClient", FakeCloudflareClient)

    result = bootstrap_main._setup_r2(_settings(), "preview", {}, False)

    assert result["access_key_id"] == "minted-id"
    assert result["secret_access_key"] == "minted-secret"
    assert calls == [
        "ensure_bucket:preview",
        "ensure_r2_token:fitness-coach-agent-preview:preview",
        "get_public_base_url:fitness-coach-agent-preview",
        "close",
    ]


def test_setup_r2_uses_configured_credentials_without_creating_token(monkeypatch) -> None:
    calls: list[str] = []

    class FakeCloudflareClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def ensure_bucket(self, env: str) -> str:
            calls.append(f"ensure_bucket:{env}")
            return "fitness-coach-agent-preview"

        def ensure_r2_token(self, *_args, **_kwargs) -> dict:
            raise AssertionError("dashboard-provided credentials should skip token creation")

        def get_public_base_url(self, bucket_name: str) -> str:
            calls.append(f"get_public_base_url:{bucket_name}")
            return ""

        def endpoint_url(self) -> str:
            return "https://account-id.r2.cloudflarestorage.com"

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(bootstrap_main, "CloudflareClient", FakeCloudflareClient)

    r2 = bootstrap_main._setup_r2(
        _settings(
            r2_access_key_id_preview="configured-access",
            r2_secret_access_key_preview="configured-secret",
        ),
        "preview",
        {},
        False,
    )

    assert calls == [
        "ensure_bucket:preview",
        "get_public_base_url:fitness-coach-agent-preview",
        "close",
    ]
    assert r2 == {
        "bucket_name": "fitness-coach-agent-preview",
        "access_key_id": "configured-access",
        "secret_access_key": "configured-secret",
        "public_base_url": "",
        "endpoint_url": "https://account-id.r2.cloudflarestorage.com",
    }


def test_setup_r2_rejects_partial_configured_credentials() -> None:
    with pytest.raises(RuntimeError, match="Incomplete R2 credential configuration"):
        bootstrap_main._configured_r2_credentials(
            _settings(r2_access_key_id_preview="configured-access"),
            "preview",
        )


def test_cloudflare_r2_token_uses_user_token_api_and_hashes_token_value() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    class FakeHTTP:
        def get(self, url: str, **_kwargs) -> httpx.Response:
            calls.append(("GET", url, None))
            request = httpx.Request("GET", url)
            if url.endswith("/user/tokens"):
                return httpx.Response(200, json={"result": []}, request=request)
            if url.endswith("/user/tokens/permission_groups"):
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "id": "write-group",
                                "name": "Workers R2 Storage Bucket Item Write",
                            },
                            {
                                "id": "read-group",
                                "name": "Workers R2 Storage Bucket Item Read",
                            },
                        ]
                    },
                    request=request,
                )
            raise AssertionError(f"unexpected GET {url}")

        def post(self, url: str, json: dict) -> httpx.Response:
            calls.append(("POST", url, json))
            request = httpx.Request("POST", url)
            assert url.endswith("/user/tokens")
            return httpx.Response(
                200,
                json={"result": {"id": "access-key-id", "value": "plain-token-value"}},
                request=request,
            )

        def configure_auth_settings(self, *_args, **_kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    client = CloudflareClient("token", "account-id")
    client._http = FakeHTTP()

    creds = client.ensure_r2_token("bucket-name", "preview")
    client.close()

    assert creds == {
        "access_key_id": "access-key-id",
        "secret_access_key": ("c80226c1c8a33783f2e85578e53b8e77b9334cf9dc68cab45968af4eba4bf259"),
    }
    assert calls == [
        ("GET", "https://api.cloudflare.com/client/v4/user/tokens", None),
        (
            "GET",
            "https://api.cloudflare.com/client/v4/user/tokens/permission_groups",
            None,
        ),
        (
            "POST",
            "https://api.cloudflare.com/client/v4/user/tokens",
            {
                "name": "fitness-coach-agent-preview",
                "policies": [
                    {
                        "effect": "allow",
                        "resources": {
                            "com.cloudflare.edge.r2.bucket.account-id_default_bucket-name": "*"
                        },
                        "permission_groups": [
                            {"id": "write-group"},
                            {"id": "read-group"},
                        ],
                    }
                ],
            },
        ),
    ]


def test_vercel_remove_env_vars_deletes_matching_preview_key(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    fake_run = _make_fake_vercel_run(
        calls,
        {
            ("GET", "/v10/projects/project-id/env?teamId=team-id"): {
                "envs": [
                    {"id": "env-preview", "key": "APP_BASE_URL", "target": ["preview"]},
                    {"id": "env-prod", "key": "APP_BASE_URL", "target": ["production"]},
                ]
            },
            ("DELETE", "/v10/projects/project-id/env/env-preview?teamId=team-id"): {},
        },
    )
    monkeypatch.setattr("scripts.bootstrap.vercel_client.subprocess.run", fake_run)

    client = VercelClient("project-id", "team-id")
    client.remove_env_vars(["preview"], ["APP_BASE_URL"])
    client.close()

    assert calls == [
        ("GET", "/v10/projects/project-id/env?teamId=team-id"),
        ("DELETE", "/v10/projects/project-id/env/env-preview?teamId=team-id"),
    ]


def test_vercel_remove_env_vars_dry_run_only_reports(monkeypatch, capsys) -> None:
    calls: list[tuple[str, str]] = []
    fake_run = _make_fake_vercel_run(
        calls,
        {
            ("GET", "/v10/projects/project-id/env?teamId=team-id"): {
                "envs": [
                    {"id": "env-preview", "key": "APP_BASE_URL", "target": ["preview"]},
                ]
            },
        },
    )
    monkeypatch.setattr("scripts.bootstrap.vercel_client.subprocess.run", fake_run)

    client = VercelClient("project-id", "team-id", dry_run=True)
    client.remove_env_vars(["preview"], ["APP_BASE_URL"])
    client.close()

    delete_calls = [c for c in calls if c[0] == "DELETE"]
    assert delete_calls == []
    assert "Would delete APP_BASE_URL (preview)" in capsys.readouterr().out


def test_vercel_remove_env_vars_preserves_other_targets(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    fake_run = _make_fake_vercel_run(
        calls,
        {
            ("GET", "/v10/projects/project-id/env?teamId=team-id"): {
                "envs": [
                    {
                        "id": "env-shared",
                        "key": "APP_BASE_URL",
                        "target": ["preview", "production"],
                    },
                ]
            },
            ("PATCH", "/v10/projects/project-id/env/env-shared?teamId=team-id"): {},
        },
        capture_body=True,
    )
    monkeypatch.setattr("scripts.bootstrap.vercel_client.subprocess.run", fake_run)

    client = VercelClient("project-id", "team-id")
    client.remove_env_vars(["preview"], ["APP_BASE_URL"])
    client.close()

    assert calls == [
        ("GET", "/v10/projects/project-id/env?teamId=team-id", None),
        (
            "PATCH",
            "/v10/projects/project-id/env/env-shared?teamId=team-id",
            {"target": ["production"]},
        ),
    ]


def test_sync_vercel_env_vars_removes_preview_app_base_url_before_upsert() -> None:
    calls: list[tuple[str, list[str], dict | list[str]]] = []

    class FakeVercel:
        def remove_env_vars(self, target: list[str], keys: list[str]) -> None:
            calls.append(("remove", target, keys))

        def upsert_env_vars(self, target: list[str], vars: dict[str, str]) -> None:
            calls.append(("upsert", target, vars))

    bootstrap_main._sync_vercel_env_vars(FakeVercel(), ["preview"], {"APP_ENV": "preview"})

    assert calls == [
        ("remove", ["preview"], ["APP_BASE_URL"]),
        ("upsert", ["preview"], {"APP_ENV": "preview"}),
    ]


def _settings(**overrides: str) -> BootstrapSettings:
    defaults: dict[str, Any] = {
        "supabase_access_token": "token",
        "supabase_org_id": "org",
        "supabase_project_ref_preview": "",
        "supabase_project_ref_prod": "",
        "supabase_db_password_preview": "",
        "supabase_db_password_prod": "",
        "supabase_url_preview": "",
        "supabase_url_prod": "",
        "supabase_anon_key_preview": "",
        "supabase_anon_key_prod": "",
        "supabase_service_role_key_preview": "",
        "supabase_service_role_key_prod": "",
        "cf_api_token": "cf-token",
        "cf_account_id": "account-id",
        "r2_access_key_id_preview": "",
        "r2_access_key_id_prod": "",
        "r2_secret_access_key_preview": "",
        "r2_secret_access_key_prod": "",
        "openai_api_key": "openai-key",
        "tavily_api_key": "tavily-key",
        "production_domain": "",
        "smtp_host": "smtp.resend.com",
        "smtp_port": 465,
        "smtp_user": "resend",
        "smtp_pass": "",
        "smtp_admin_email": "",
        "smtp_sender_name": "",
    }
    defaults.update(overrides)
    return BootstrapSettings.model_construct(**defaults)


def _make_fake_vercel_run(
    calls: list,
    responses: dict[tuple[str, str], Any],
    *,
    capture_body: bool = False,
):
    """Stub for subprocess.run mimicking `vercel api` invocations.

    `responses` maps (method, path) -> dict to return as JSON stdout.
    When `capture_body` is True, calls record (method, path, body_dict).
    """

    def fake_run(command: list[str], **kwargs):
        path = command[2]
        method = command[command.index("--method") + 1]
        body = kwargs.get("input")
        body_dict = None
        if body is not None:
            import json as _json

            body_dict = _json.loads(body)
        if capture_body:
            calls.append((method, path, body_dict))
        else:
            calls.append((method, path))
        payload = responses.get((method, path), {})
        import json as _json

        return SimpleNamespace(returncode=0, stdout=_json.dumps(payload), stderr="")

    return fake_run


def test_configure_auth_settings_patches_management_api(monkeypatch) -> None:
    patched: list[tuple[str, dict]] = []

    def fake_patch(self, path: str, body: dict) -> dict:
        patched.append((path, body))
        return {}

    monkeypatch.setattr(SupabaseClient, "_patch", fake_patch)

    client = SupabaseClient("my-access-token", "my-org")
    client.configure_auth_settings(
        "proj-ref",
        site_url="https://example.vercel.app",
        extra_redirect_urls=["https://*.vercel.app/**", "http://localhost:3000/**"],
    )
    client.close()

    assert len(patched) == 1
    path, body = patched[0]
    assert path == "/projects/proj-ref/config/auth"
    assert body["site_url"] == "https://example.vercel.app"
    assert body["mailer_autoconfirm"] is True
    assert body["mailer_otp_length"] == 6
    assert "https://example.vercel.app" in body["uri_allow_list"]
    assert "https://*.vercel.app/**" in body["uri_allow_list"]
    assert "{{ .Token }}" in body["mailer_templates_magic_link_content"]
    assert "{{ .ConfirmationURL }}" in body["mailer_templates_magic_link_content"]
    assert "{{ .Token }}" in body["mailer_templates_confirmation_content"]
    assert "{{ .ConfirmationURL }}" in body["mailer_templates_confirmation_content"]


def test_configure_auth_settings_prints_instructions_when_no_token(capsys) -> None:
    client = SupabaseClient("", "")
    client.configure_auth_settings(
        "proj-ref",
        site_url="https://example.vercel.app",
        extra_redirect_urls=["https://*.vercel.app/**"],
    )
    client.close()

    out = capsys.readouterr().out
    assert "manually" in out
    assert "URL Configuration" in out
    assert "Confirm email" in out


def test_configure_auth_settings_dry_run(monkeypatch, capsys) -> None:
    def fail_patch(self, path: str, body: dict) -> dict:
        raise AssertionError("_patch should not be called in dry-run mode")

    monkeypatch.setattr(SupabaseClient, "_patch", fail_patch)

    client = SupabaseClient("my-token", "my-org", dry_run=True)
    client.configure_auth_settings(
        "proj-ref",
        site_url="https://prod.example.com",
        extra_redirect_urls=["http://localhost:3000/**"],
    )
    client.close()

    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "https://prod.example.com" in out


def test_build_auth_redirect_urls_preview_includes_scoped_wildcard_and_domain() -> None:
    domain = "fitness-coach-agent.vercel.app"
    urls = bootstrap_main._build_auth_redirect_urls(_settings(), "preview", domain)
    assert "https://fitness-coach-agent-*-nigel-stukes-projects.vercel.app/**" in urls
    assert "https://fitness-coach-agent.vercel.app/**" in urls
    assert "http://localhost:3000/**" in urls


def test_build_auth_redirect_urls_prod_includes_site_origin_wildcard() -> None:
    # Regression for issue #172: production must allow-list its own /auth/callback
    # path via a /** wildcard, or Supabase drops redirect_to and the magic link
    # arrives without the auth code. The Vercel-assigned alias is allowed too.
    domain = "fitness-coach-agent-phi.vercel.app"
    urls = bootstrap_main._build_auth_redirect_urls(
        _settings(production_domain="coach.example.com"), "prod", domain
    )
    assert "https://coach.example.com/**" in urls
    assert "https://fitness-coach-agent-phi.vercel.app/**" in urls
    assert "http://localhost:3000/**" in urls


def test_build_auth_redirect_urls_prod_without_custom_domain_uses_vercel_alias() -> None:
    domain = "fitness-coach-agent-phi.vercel.app"
    urls = bootstrap_main._build_auth_redirect_urls(_settings(), "prod", domain)
    # Only one wildcard for the origin — no duplicate when site URL == alias.
    assert urls.count("https://fitness-coach-agent-phi.vercel.app/**") == 1


def test_build_auth_redirect_urls_prod_raises_when_origin_unresolved() -> None:
    # With neither PRODUCTION_DOMAIN nor a Vercel domain, fail loud instead of
    # writing a localhost-only allow-list that would break production sign-in.
    with pytest.raises(RuntimeError, match="production auth origin"):
        bootstrap_main._build_auth_redirect_urls(_settings(), "prod", "")


def test_build_smtp_settings_returns_none_without_credentials() -> None:
    assert bootstrap_main._build_smtp_settings(_settings()) is None
    # Sender address alone is not enough — the password (Resend key) is required.
    assert (
        bootstrap_main._build_smtp_settings(_settings(smtp_admin_email="login@example.com")) is None
    )


def test_build_smtp_settings_returns_resend_config_when_present() -> None:
    smtp = bootstrap_main._build_smtp_settings(
        _settings(smtp_pass="re_secret", smtp_admin_email="login@example.com")
    )
    assert smtp == {
        "host": "smtp.resend.com",
        "port": 465,
        "user": "resend",
        "pass": "re_secret",
        "admin_email": "login@example.com",
        "sender_name": "",
    }


def test_configure_auth_settings_includes_smtp_when_provided(monkeypatch) -> None:
    patched: list[tuple[str, dict]] = []

    def fake_patch(self, path: str, body: dict) -> dict:
        patched.append((path, body))
        return {}

    monkeypatch.setattr(SupabaseClient, "_patch", fake_patch)

    client = SupabaseClient("my-access-token", "my-org")
    client.configure_auth_settings(
        "proj-ref",
        site_url="https://example.vercel.app",
        extra_redirect_urls=["https://example.vercel.app/**"],
        smtp={
            "host": "smtp.resend.com",
            "port": 465,
            "user": "resend",
            "pass": "re_secret",
            "admin_email": "login@example.com",
            "sender_name": "Coach",
        },
    )
    client.close()

    _, body = patched[0]
    assert body["smtp_host"] == "smtp.resend.com"
    assert body["smtp_port"] == 465
    assert body["smtp_user"] == "resend"
    assert body["smtp_pass"] == "re_secret"
    assert body["smtp_admin_email"] == "login@example.com"
    assert body["smtp_sender_name"] == "Coach"


def test_configure_auth_settings_omits_smtp_when_absent(monkeypatch) -> None:
    patched: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        SupabaseClient, "_patch", lambda self, path, body: patched.append((path, body)) or {}
    )

    client = SupabaseClient("my-access-token", "my-org")
    client.configure_auth_settings(
        "proj-ref",
        site_url="https://example.vercel.app",
        extra_redirect_urls=["https://example.vercel.app/**"],
    )
    client.close()

    _, body = patched[0]
    assert not any(key.startswith("smtp_") for key in body)
