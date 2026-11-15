from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fitness_coach_cli.client import CLIENT_ID, CoachClient
from fitness_coach_cli.credentials import Credentials, CredentialStore
from fitness_coach_cli.errors import CoachError


def test_request_otp_sends_invite_code(api: Any, client: CoachClient, h: Any) -> None:
    api.on("POST", "/api/auth/request-otp", json={"status": "otp_sent"})
    client.request_otp("a@b.c", "inv")
    assert h.body(api.requests[0]) == {"email": "a@b.c", "inviteCode": "inv"}
    assert "authorization" not in api.requests[0].headers


def test_invite_required_is_surfaced(api: Any, client: CoachClient, h: Any) -> None:
    api.on(
        "POST",
        "/api/auth/request-otp",
        status=409,
        json={"error": "invite_required", "message": "Invite code required."},
    )
    with pytest.raises(CoachError) as info:
        client.request_otp("a@b.c")
    assert (info.value.error, info.value.status) == ("invite_required", 409)
    assert info.value.detail == "Invite code required."


def test_verify_otp_stores_credentials(
    api: Any, client: CoachClient, store: CredentialStore, h: Any
) -> None:
    api.on("POST", "/api/auth/cli/verify-otp", json=h.bundle())
    client.verify_otp("a@b.c", "123456")
    assert h.body(api.requests[0]) == {"email": "a@b.c", "token": "123456"}
    saved = store.load(h.BASE)
    assert saved is not None
    assert saved.expires_at == h.NOW + 3600
    assert saved.refresh_token == "ref-1"


def test_verify_otp_bad_code(api: Any, client: CoachClient, store: CredentialStore, h: Any) -> None:
    api.on(
        "POST", "/api/auth/cli/verify-otp", status=401, json={"detail": "Invalid or expired code."}
    )
    with pytest.raises(CoachError) as info:
        client.verify_otp("a@b.c", "000000")
    assert info.value.status == 401
    assert info.value.detail == "Invalid or expired code."
    assert store.load(h.BASE) is None


def test_bundle_with_expires_at_iso(
    api: Any, client: CoachClient, store: CredentialStore, h: Any
) -> None:
    payload = h.bundle()
    del payload["expires_in"]
    payload["expires_at"] = "2027-01-15T08:00:00Z"
    api.on("POST", "/api/auth/cli/verify-otp", json=payload)
    client.verify_otp("a@b.c", "1")
    saved = store.load(h.BASE)
    assert saved is not None
    assert saved.expires_at == 1_800_000_000.0


def test_no_credentials_means_not_logged_in(client: CoachClient, h: Any) -> None:
    with pytest.raises(CoachError) as info:
        client.whoami()
    assert info.value.error == "not_logged_in"


def test_whoami_sends_bearer(api: Any, client: CoachClient, store: CredentialStore, h: Any) -> None:
    h.seed(store)
    api.on("GET", "/api/auth/whoami", json={"user_id": "user-1", "client_id": CLIENT_ID})
    assert client.whoami()["user_id"] == "user-1"
    assert api.requests[0].headers["authorization"] == "Bearer acc-1"


def test_proactive_refresh_when_near_expiry(
    api: Any, client: CoachClient, store: CredentialStore, h: Any
) -> None:
    h.seed(store, expires_at=h.NOW + 30)
    api.on("POST", "/api/oauth/token", json=h.bundle("acc-2", "ref-2") | {"user_id": None})
    api.on("GET", "/api/auth/whoami", json={"user_id": "user-1"})
    client.whoami()
    assert h.body(api.requests[0]) == {
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": "ref-1",
    }
    assert api.requests[1].headers["authorization"] == "Bearer acc-2"
    saved = store.load(h.BASE)
    assert saved is not None
    assert (saved.access_token, saved.refresh_token, saved.user_id) == ("acc-2", "ref-2", "user-1")


def test_refresh_on_401_persists_rotation_and_retries(
    api: Any, client: CoachClient, store: CredentialStore, h: Any
) -> None:
    h.seed(store)
    api.on("GET", "/api/auth/whoami", status=401, json={"detail": "expired"})
    api.on("GET", "/api/auth/whoami", json={"user_id": "user-1"})
    api.on("POST", "/api/oauth/token", json=h.bundle("acc-2", "ref-2"))
    assert client.whoami() == {"user_id": "user-1"}
    assert [r.headers.get("authorization") for r in api.to("/api/auth/whoami")] == [
        "Bearer acc-1",
        "Bearer acc-2",
    ]
    saved = store.load(h.BASE)
    assert saved is not None
    assert saved.refresh_token == "ref-2"


def test_second_401_is_not_retried_again(
    api: Any, client: CoachClient, store: CredentialStore, h: Any
) -> None:
    h.seed(store)
    api.on("GET", "/api/auth/whoami", status=401, json={"detail": "nope"})
    api.on("POST", "/api/oauth/token", json=h.bundle("acc-2", "ref-2"))
    with pytest.raises(CoachError) as info:
        client.whoami()
    assert info.value.status == 401
    assert len(api.to("/api/oauth/token")) == 1


def test_refresh_failure_asks_for_login(
    api: Any, client: CoachClient, store: CredentialStore, h: Any
) -> None:
    h.seed(store, expires_at=h.NOW - 5)
    api.on("POST", "/api/oauth/token", status=400, json={"detail": "Invalid refresh token."})
    with pytest.raises(CoachError) as info:
        client.whoami()
    assert info.value.error == "session_expired"
    assert f"coach --base-url {h.BASE} login" in str(info.value.detail)


def test_network_error_is_wrapped(store: CredentialStore, h: Any) -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    h.seed(store)
    offline = CoachClient(h.BASE, store, transport=httpx.MockTransport(boom), clock=lambda: h.NOW)
    with pytest.raises(CoachError) as info:
        offline.whoami()
    assert info.value.error == "network_error"


def test_logout_revokes_and_deletes(
    api: Any, client: CoachClient, store: CredentialStore, creds_path: Path, h: Any
) -> None:
    h.seed(store)
    api.on("POST", "/api/oauth/revoke", json={"revoked": True})
    assert client.logout() is True
    bodies = [h.body(r) for r in api.to("/api/oauth/revoke")]
    assert bodies == [
        {"client_id": CLIENT_ID, "token": "ref-1", "token_type_hint": "refresh_token"},
        {"client_id": CLIENT_ID, "token": "acc-1"},
    ]
    assert not creds_path.exists()


def test_logout_deletes_even_if_revoke_fails(
    api: Any, client: CoachClient, store: CredentialStore, creds_path: Path, h: Any
) -> None:
    h.seed(store)
    api.on("POST", "/api/oauth/revoke", status=503, json={"detail": "down"})
    assert client.logout() is False
    assert not creds_path.exists()


def test_call_rejects_path_injection(client: CoachClient, store: CredentialStore, h: Any) -> None:
    h.seed(store)
    with pytest.raises(CoachError) as info:
        client.call("../oauth/token", {})
    assert info.value.error == "invalid_operation"


def test_not_logged_in_hint_puts_root_option_before_subcommand(client: CoachClient, h: Any) -> None:
    with pytest.raises(CoachError) as info:
        client.whoami()
    assert f"coach --base-url {h.BASE} login" in str(info.value.detail)


def test_refresh_reuses_credentials_rotated_by_another_process(
    api: Any, client: CoachClient, store: CredentialStore, h: Any
) -> None:
    stale = h.seed(store, expires_at=h.NOW - 5)
    # Another process rotated and saved between our load and our refresh.
    store.save(h.BASE, Credentials("acc-2", "ref-2", h.NOW + 3600, "user-1"))
    assert client._refresh(stale).refresh_token == "ref-2"
    assert api.to("/api/oauth/token") == []


def test_refresh_rejected_after_concurrent_rotation_uses_stored_credentials(
    api: Any, client: CoachClient, store: CredentialStore, h: Any
) -> None:
    stale = h.seed(store, expires_at=h.NOW - 5)

    def rejected(_req: httpx.Request) -> httpx.Response:
        # The server revoked ref-1 because another process rotated it; that process saved.
        store.save(h.BASE, Credentials("acc-2", "ref-2", h.NOW + 3600, "user-1"))
        return httpx.Response(400, json={"detail": "Invalid refresh token."})

    # Simulate a writer outside the lock: the store still holds `stale` when the lock is taken.
    api.on("POST", "/api/oauth/token", handler=rejected)
    assert client._refresh_locked(stale).access_token == "acc-2"


def test_refresh_rejected_with_unchanged_store_is_session_expired(
    api: Any, client: CoachClient, store: CredentialStore, h: Any
) -> None:
    stale = h.seed(store, expires_at=h.NOW - 5)
    api.on("POST", "/api/oauth/token", status=401, json={"detail": "revoked"})
    with pytest.raises(CoachError) as info:
        client._refresh(stale)
    assert info.value.error == "session_expired"


def test_refresh_lock_file_is_private_and_serialises(
    store: CredentialStore, creds_path: Path
) -> None:
    with store.refresh_lock():
        lock = creds_path.with_name(f"{creds_path.name}.lock")
        assert lock.exists()
        assert lock.stat().st_mode & 0o777 == 0o600
