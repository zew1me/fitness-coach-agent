import pytest

from tests.python.local_db import DEFAULT_LOCAL_DB_URL, direct_session, local_db_url


def test_local_db_url_defaults_to_the_local_supabase_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCAL_SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)

    assert local_db_url() == DEFAULT_LOCAL_DB_URL


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:secret@db.abcdefgh.supabase.co:5432/postgres",
        "postgresql://postgres:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
        "postgresql://postgres:secret@10.0.0.5:54322/postgres",
        "postgresql://postgres:secret@/postgres",
        # libpq connects to hostaddr rather than host when both are set.
        "postgresql://postgres:secret@localhost/postgres?hostaddr=203.0.113.5",
    ],
)
def test_local_db_url_refuses_non_loopback_hosts(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("LOCAL_SUPABASE_DB_URL", url)

    with pytest.raises(RuntimeError, match="non-local host"):
        local_db_url()


def test_local_db_url_refuses_a_non_loopback_pghostaddr(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin the URL: a hostaddr inside an ambient LOCAL_SUPABASE_DB_URL would override PGHOSTADDR.
    monkeypatch.setenv("LOCAL_SUPABASE_DB_URL", DEFAULT_LOCAL_DB_URL)
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.5")

    with pytest.raises(RuntimeError, match="non-local host"):
        local_db_url()


@pytest.mark.parametrize(
    ("url", "pgservice"),
    [
        ("postgresql://postgres:secret@127.0.0.1:54322/postgres?service=remote", None),
        (DEFAULT_LOCAL_DB_URL, "remote"),
    ],
)
def test_local_db_url_refuses_a_service_file_configuration(
    monkeypatch: pytest.MonkeyPatch, url: str, pgservice: str | None
) -> None:
    monkeypatch.setenv("LOCAL_SUPABASE_DB_URL", url)
    if pgservice is None:
        monkeypatch.delenv("PGSERVICE", raising=False)
    else:
        monkeypatch.setenv("PGSERVICE", pgservice)

    with pytest.raises(RuntimeError, match="pg_service.conf"):
        local_db_url()


def test_direct_session_refuses_a_remote_host_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_SUPABASE_DB_URL", "postgresql://postgres:x@db.example.com:5432/p")

    with pytest.raises(RuntimeError, match="non-local host"), direct_session():
        raise AssertionError("a session must not open for a remote host")


def test_direct_session_rejects_unknown_roles() -> None:
    with pytest.raises(ValueError, match="Unsupported role"), direct_session(role="postgres"):
        raise AssertionError("a session must not open for an unsupported role")
