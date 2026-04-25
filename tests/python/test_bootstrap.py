from types import SimpleNamespace

import httpx
import pytest

from scripts.bootstrap import main as bootstrap_main
from scripts.bootstrap import supabase_client
from scripts.bootstrap.cloudflare_client import CloudflareClient
from scripts.bootstrap.supabase_client import SupabaseClient


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
    client._http = FakeHTTP()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="SUPABASE_ACCESS_TOKEN"):
        client._get("/projects/ref")


def test_existing_project_ref_skips_management_api_verification() -> None:
    client = SupabaseClient("", "")

    project_ref, db_password = client.ensure_project("preview", project_ref="existing-ref")
    client.close()

    assert project_ref == "existing-ref"
    assert db_password is None


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

        def ensure_r2_token(self, *_args, **_kwargs) -> dict:
            raise AssertionError("bootstrap should not auto-create R2 runtime credentials")

        def get_public_base_url(self, bucket_name: str) -> str:
            calls.append(f"get_public_base_url:{bucket_name}")
            return ""

        def endpoint_url(self) -> str:
            return "https://account-id.r2.cloudflarestorage.com"

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(bootstrap_main, "CloudflareClient", FakeCloudflareClient)

    with pytest.raises(RuntimeError, match="R2_ACCESS_KEY_ID_PREVIEW"):
        bootstrap_main._setup_r2(_settings(), "preview", {}, False)

    assert calls == ["ensure_bucket:preview", "close"]


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
    client._http = FakeHTTP()  # type: ignore[assignment]

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


def _settings(**overrides: str) -> SimpleNamespace:
    defaults = {
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
        "production_domain": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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
    assert body["SITE_URL"] == "https://example.vercel.app"
    assert body["MAILER_AUTOCONFIRM"] is True
    assert "https://example.vercel.app" in body["URI_ALLOW_LIST"]
    assert "https://*.vercel.app/**" in body["URI_ALLOW_LIST"]


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


def test_build_auth_site_url_preview_uses_vercel_domain() -> None:
    settings = _settings()
    url = bootstrap_main._build_auth_site_url(settings, "preview", "fitness-coach-agent.vercel.app")
    assert url == "https://fitness-coach-agent.vercel.app"


def test_build_auth_site_url_prod_prefers_custom_domain() -> None:
    settings = _settings(production_domain="app.example.com")
    url = bootstrap_main._build_auth_site_url(settings, "prod", "fitness-coach-agent.vercel.app")
    assert url == "https://app.example.com"


def test_build_auth_redirect_urls_preview_includes_wildcard() -> None:
    settings = _settings()
    domain = "fitness-coach-agent.vercel.app"
    urls = bootstrap_main._build_auth_redirect_urls(settings, "preview", domain)
    assert "https://*.vercel.app/**" in urls
    assert "http://localhost:3000/**" in urls


def test_build_auth_redirect_urls_prod_does_not_include_wildcard() -> None:
    settings = _settings(production_domain="app.example.com")
    domain = "fitness-coach-agent.vercel.app"
    urls = bootstrap_main._build_auth_redirect_urls(settings, "prod", domain)
    assert "https://*.vercel.app/**" not in urls
