import pytest
from pydantic import ValidationError

from backend.config import Settings


def test_settings_rejects_placeholder_jwt_secret_when_strava_enabled() -> None:
    with pytest.raises(ValidationError, match="APP_JWT_SECRET must not use the placeholder"):
        Settings(strava_integration_enabled=True, app_jwt_secret=" replace-me ")


def test_settings_accepts_non_placeholder_jwt_secret_when_strava_enabled() -> None:
    settings = Settings(strava_integration_enabled=True, app_jwt_secret="a-real-jwt-secret")

    assert settings.app_jwt_secret == "a-real-jwt-secret"


def test_settings_allows_placeholder_jwt_secret_when_strava_is_disabled() -> None:
    settings = Settings(strava_integration_enabled=False, app_jwt_secret="replace-me")

    assert settings.app_jwt_secret == "replace-me"
