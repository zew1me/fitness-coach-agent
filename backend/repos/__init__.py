"""Repository adapters."""

from backend.repos.intervals_repo import (
    IntervalsRepository,
    IntervalsRepositoryNotConfiguredError,
)
from backend.repos.oauth_repo import OAuthRepository, OAuthRepositoryNotConfiguredError
from backend.repos.supabase_repo import (
    RecordNotFoundError,
    RepositoryNotConfiguredError,
    SupabaseRepository,
)

__all__ = [
    "IntervalsRepository",
    "IntervalsRepositoryNotConfiguredError",
    "OAuthRepository",
    "OAuthRepositoryNotConfiguredError",
    "RecordNotFoundError",
    "RepositoryNotConfiguredError",
    "SupabaseRepository",
]
