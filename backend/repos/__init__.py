"""Repository adapters."""

from backend.repos.oauth_repo import OAuthRepository, OAuthRepositoryNotConfiguredError
from backend.repos.supabase_repo import (
    RecordNotFoundError,
    RepositoryNotConfiguredError,
    SupabaseRepository,
)

__all__ = [
    "OAuthRepository",
    "OAuthRepositoryNotConfiguredError",
    "RecordNotFoundError",
    "RepositoryNotConfiguredError",
    "SupabaseRepository",
]
