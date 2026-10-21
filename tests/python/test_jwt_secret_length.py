"""Guard for issue #261: the suite must sign JWTs with a key PyJWT considers long enough."""

from backend.config import settings

# RFC 7518 §3.2: an HS256 key must be at least as long as the hash output (32 bytes).
_MIN_HS256_KEY_BYTES = 32


def test_suite_jwt_secret_meets_hs256_minimum_length() -> None:
    assert len(settings.app_jwt_secret.encode()) >= _MIN_HS256_KEY_BYTES
