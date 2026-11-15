from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from fitness_coach_cli.credentials import Credentials, CredentialStore, default_credentials_path
from fitness_coach_cli.errors import CoachError
from fitness_coach_cli.urls import normalize_base_url, requires_confirmation


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_save_creates_private_dir_and_file(store: CredentialStore, creds_path: Path) -> None:
    store.save("https://a.example", Credentials("a", "r", 1.0, "u"))
    assert _mode(creds_path) == 0o600
    assert _mode(creds_path.parent) == 0o700
    assert not [p for p in creds_path.parent.iterdir() if p.name != creds_path.name]


def test_credentials_keyed_per_base_url(store: CredentialStore) -> None:
    store.save("https://a.example", Credentials("a1", "r1", 1.0, "u1"))
    store.save("https://b.example", Credentials("b1", "r2", 2.0, "u2"))
    loaded_a = store.load("https://a.example")
    assert loaded_a is not None
    assert loaded_a.access_token == "a1"
    loaded_b = store.load("https://b.example")
    assert loaded_b is not None
    assert loaded_b.user_id == "u2"
    assert store.load("https://c.example") is None
    assert store.delete("https://a.example")
    assert store.load("https://a.example") is None
    assert store.load("https://b.example") is not None


def test_delete_last_entry_removes_file(store: CredentialStore, creds_path: Path) -> None:
    store.save("https://a.example", Credentials("a", "r", 1.0, "u"))
    assert store.delete("https://a.example")
    assert not creds_path.exists()
    assert not store.delete("https://a.example")


def test_existing_parent_dir_is_not_chmodded(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    CredentialStore(shared / "c.json").save("https://a.example", Credentials("a", "r", 1.0, "u"))
    assert _mode(shared) == 0o755


def test_corrupt_file_is_reported(store: CredentialStore, creds_path: Path) -> None:
    creds_path.parent.mkdir(parents=True)
    creds_path.write_text("not json")
    with pytest.raises(CoachError) as info:
        store.load("https://a.example")
    assert info.value.error == "credentials_unreadable"


def test_file_content_has_no_stray_fields(store: CredentialStore, creds_path: Path) -> None:
    store.save("https://a.example", Credentials("a", "r", 5.0, "u"))
    document = json.loads(creds_path.read_text())
    assert document["servers"]["https://a.example"] == {
        "access_token": "a",
        "refresh_token": "r",
        "expires_at": 5.0,
        "user_id": "u",
    }


def test_default_path_and_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("COACH_CREDENTIALS_FILE", raising=False)
    assert default_credentials_path().parts[-3:] == (".config", "fitness-coach", "credentials.json")
    monkeypatch.setenv("COACH_CREDENTIALS_FILE", str(tmp_path / "x.json"))
    assert default_credentials_path() == tmp_path / "x.json"


def test_normalize_base_url_collapses_spellings() -> None:
    assert normalize_base_url("HTTPS://Foo.Vercel.app/") == "https://foo.vercel.app"
    assert normalize_base_url("http://localhost:3000/?x=1") == "http://localhost:3000"
    with pytest.raises(CoachError):
        normalize_base_url("ftp://x")


@pytest.mark.parametrize(
    ("url", "needs_yes"),
    [
        ("http://localhost:3000", False),
        ("http://127.0.0.1:8000", False),
        ("https://my-preview-git-x.vercel.app", True),  # vercel.app can serve production too
        ("http://[::1]:3000", False),
        ("https://coach.example.com", True),
        ("https://vercel.app.evil.com", True),
    ],
)
def test_requires_confirmation(url: str, needs_yes: bool) -> None:
    assert requires_confirmation(url) is needs_yes


@pytest.mark.parametrize(
    "raw", ["https://[::1", "https://example.com:bad", "https://example.com:99999"]
)
def test_malformed_base_url_is_a_coach_error(raw: str) -> None:
    with pytest.raises(CoachError) as info:
        normalize_base_url(raw)
    assert info.value.error == "invalid_base_url"
