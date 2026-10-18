"""The public OAuth repository entry points must not retain a synchronous adapter.

Persistence and gateway-retry behavior is covered in test_async_oauth_repo.py.
"""

from inspect import iscoroutinefunction

from backend.repos import OAuthRepository as ExportedOAuthRepository
from backend.repos.async_oauth_repo import AsyncOAuthRepository
from backend.repos.oauth_repo import OAuthRepository, OAuthRepositoryNotConfiguredError
from backend.repos.oauth_repo_base import OAuthRepositoryNotConfiguredError as BaseConfigError
from backend.services.auth import AuthService


def test_public_repository_exports_only_async_implementation() -> None:
    assert ExportedOAuthRepository is OAuthRepository is AsyncOAuthRepository
    assert OAuthRepositoryNotConfiguredError is BaseConfigError


async def test_auth_service_defaults_to_async_persistence() -> None:
    service = AuthService()
    assert isinstance(service._oauth_repo, OAuthRepository)
    assert iscoroutinefunction(service._oauth_repo.upsert_grant)
    await service.aclose()
