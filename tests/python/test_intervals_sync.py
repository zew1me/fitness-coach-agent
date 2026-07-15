import base64
from datetime import UTC, datetime

import pytest

from backend.models.intervals import IntervalsConnectionCreate, IntervalsConnectionRecord
from backend.services.intervals import (
    IntervalsConfigurationError,
    IntervalsNotConnectedError,
    IntervalsOAuthService,
    TokenCipher,
)


class InMemoryIntervalsRepository:
    def __init__(self) -> None:
        self.rows: list[IntervalsConnectionRecord] = []

    def get_active_connection(self, user_id: str) -> IntervalsConnectionRecord | None:
        return next(
            (
                row
                for row in reversed(self.rows)
                if row.user_id == user_id and row.revoked_at is None
            ),
            None,
        )

    def replace_connection(
        self, connection: IntervalsConnectionCreate
    ) -> IntervalsConnectionRecord:
        now = datetime.now(UTC)
        row = IntervalsConnectionRecord(
            id=f"connection-{len(self.rows) + 1}",
            user_id=connection.user_id,
            intervals_athlete_id=connection.intervals_athlete_id,
            intervals_athlete_name=connection.intervals_athlete_name,
            scopes=connection.scopes,
            access_token_ciphertext=connection.access_token_ciphertext,
            token_type=connection.token_type,
            connected_at=now,
            updated_at=now,
            revoked_at=None,
        )
        self.rows.append(row)
        return row

    def revoke_active_connection(self, user_id: str) -> bool:
        row = self.get_active_connection(user_id)
        if row is None:
            return False
        row.revoked_at = datetime.now(UTC)
        return True


@pytest.fixture(autouse=True)
def configured_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_token_encryption_secret",
        "encryption-secret-123",
    )
    monkeypatch.setattr("backend.services.intervals.settings.intervals_dev_api_key", "")
    monkeypatch.setattr("backend.services.intervals.settings.intervals_dev_athlete_id", "")
    monkeypatch.delenv("VERCEL_URL", raising=False)


def test_dev_bypass_resolves_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_api_key",
        "local-api-key",
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_athlete_id",
        "i135168",
    )

    auth = IntervalsOAuthService(repository=InMemoryIntervalsRepository()).resolve_auth(
        "any-logged-in-user"
    )

    expected = base64.b64encode(b"API_KEY:local-api-key").decode()
    assert auth.athlete_id == "i135168"
    assert auth.auth_header == {"Authorization": f"Basic {expected}"}
    assert auth.mode == "dev_api_key"
    assert "using local Intervals API-key bypass athlete_id=i135168" in caplog.text


def test_dev_bypass_is_disabled_on_vercel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_api_key",
        "local-api-key",
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_athlete_id",
        "i135168",
    )
    monkeypatch.setenv("VERCEL_URL", "coach-preview.vercel.app")

    with pytest.raises(IntervalsNotConnectedError):
        _ = IntervalsOAuthService(repository=InMemoryIntervalsRepository()).resolve_auth("user-1")


@pytest.mark.parametrize(
    ("api_key", "athlete_id"),
    [("local-api-key", ""), ("", "i135168")],
)
def test_half_configured_dev_bypass_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str,
    athlete_id: str,
) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_api_key",
        api_key,
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_athlete_id",
        athlete_id,
    )

    with pytest.raises(IntervalsConfigurationError, match="must both be configured"):
        _ = IntervalsOAuthService(repository=InMemoryIntervalsRepository()).resolve_auth("user-1")


def test_oauth_auth_decrypts_stored_token() -> None:
    repo = InMemoryIntervalsRepository()
    _ = repo.replace_connection(
        IntervalsConnectionCreate(
            user_id="user-1",
            intervals_athlete_id="i135168",
            intervals_athlete_name="Nigel",
            scopes=["ACTIVITY:READ"],
            access_token_ciphertext=TokenCipher("encryption-secret-123").encrypt(
                "oauth-access-token"
            ),
            token_type="Bearer",
        )
    )

    auth = IntervalsOAuthService(repository=repo).resolve_auth("user-1")

    assert auth.athlete_id == "i135168"
    assert auth.auth_header == {"Authorization": "Bearer oauth-access-token"}
    assert auth.mode == "oauth"


def test_oauth_auth_requires_active_connection() -> None:
    with pytest.raises(IntervalsNotConnectedError, match="not connected"):
        _ = IntervalsOAuthService(repository=InMemoryIntervalsRepository()).resolve_auth("user-1")
