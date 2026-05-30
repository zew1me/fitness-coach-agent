from __future__ import annotations

from typing import Any, Literal

from backend.config import settings
from backend.models.athlete import AthleteProfile
from backend.models.chat import (
    ChatAttachmentInput,
    ChatMessage,
    ChatThreadBootstrap,
)
from backend.repos.supabase_repo import RecordNotFoundError, SupabaseRepository
from backend.services.r2 import R2Service


class ChatUnavailableError(RuntimeError):
    """Raised when the conversational coach cannot be used in the current environment."""


class ChatService:
    """Persist the athlete-facing coaching conversation.

    The LLM coaching layer lives in the TypeScript AI SDK route; this Python service is the
    single source of truth for thread and message persistence in Supabase.
    """

    def __init__(
        self,
        repo: SupabaseRepository | None = None,
        r2_service: R2Service | None = None,
    ) -> None:
        self._repo = repo or SupabaseRepository()
        self._r2_service = r2_service or R2Service()

    async def bootstrap_thread(self, user_id: str) -> ChatThreadBootstrap:
        thread = await self._repo.get_or_create_chat_thread(user_id)
        profile = await self._get_profile(user_id)

        if not thread.messages:
            await self._repo.create_chat_message(
                thread_id=thread.id,
                user_id=user_id,
                role="assistant",
                content=self._initial_welcome(profile),
                metadata={"message_kind": "welcome"},
            )
            thread = await self._repo.get_or_create_chat_thread(user_id)

        return ChatThreadBootstrap(
            attachments_enabled=self.attachments_enabled,
            profile_complete=self._profile_complete(profile),
            thread=thread,
        )

    async def persist_message(  # noqa: PLR0913
        self,
        user_id: str,
        *,
        role: Literal["user", "assistant"],
        content: str,
        metadata: dict[str, Any] | None = None,
        attachments: list[ChatAttachmentInput] | None = None,
        message_id: str | None = None,
    ) -> ChatMessage:
        thread = await self._repo.get_or_create_chat_thread(user_id)
        return await self._repo.create_chat_message(
            thread_id=thread.id,
            user_id=user_id,
            role=role,
            content=content,
            metadata=metadata or {},
            attachments=attachments,
            message_id=message_id,
        )

    @property
    def attachments_enabled(self) -> bool:
        return all(
            (
                settings.r2_access_key_id,
                settings.r2_secret_access_key,
                settings.r2_bucket,
                settings.r2_account_id or settings.r2_endpoint_url,
            )
        )

    async def _get_profile(self, user_id: str) -> AthleteProfile:
        try:
            return await self._repo.get_athlete_profile(user_id)
        except RecordNotFoundError:
            return AthleteProfile(user_id=user_id)

    @staticmethod
    def _profile_complete(profile: AthleteProfile) -> bool:
        return profile.coaching_state != "onboarding"

    @staticmethod
    def _initial_welcome(profile: AthleteProfile) -> str:
        if profile.coaching_state == "onboarding":
            return (
                "Welcome. Let's start with just two things: what sport or sports are you "
                "training for, and what would you like coaching around?"
            )
        return "Welcome back. Tell me what changed in training, recovery, schedule, or goals."
