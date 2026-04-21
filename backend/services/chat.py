from __future__ import annotations

from backend.config import settings
from backend.models.athlete import AthleteProfile
from backend.models.chat import (
    ChatAttachmentInput,
    ChatSendResponse,
    ChatThreadBootstrap,
)
from backend.repos.supabase_repo import RecordNotFoundError, SupabaseRepository
from backend.services.r2 import R2Service


class ChatUnavailableError(RuntimeError):
    """Raised when the conversational coach cannot be used in the current environment."""


class ChatService:
    """Persist the athlete-facing coaching conversation.

    The LLM coaching layer now belongs in the TypeScript AI SDK route. This Python service
    keeps thread and attachment persistence available without direct model calls.
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

    async def send_message(
        self, user_id: str, content: str, attachments: list[ChatAttachmentInput]
    ) -> ChatSendResponse:
        thread = await self._repo.get_or_create_chat_thread(user_id)
        profile = await self._get_profile(user_id)

        cleaned_content = content.strip()
        if cleaned_content or attachments:
            await self._repo.create_chat_message(
                thread_id=thread.id,
                user_id=user_id,
                role="user",
                content=cleaned_content,
                metadata={"message_kind": "user_turn"},
                attachments=attachments,
            )

        await self._repo.create_chat_message(
            thread_id=thread.id,
            user_id=user_id,
            role="assistant",
            content=self._handoff_reply(cleaned_content, len(attachments)),
            metadata={"message_kind": "assistant_reply", "source": "python_persistence_shell"},
        )

        updated_thread = await self._repo.get_or_create_chat_thread(user_id)
        return ChatSendResponse(
            attachments_enabled=self.attachments_enabled,
            profile_complete=self._profile_complete(profile),
            thread=updated_thread,
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
                "Welcome. I'll ask a few questions to understand your background, but none "
                "of it is required. Tell me about your sports, goals, recent training, and "
                "schedule in normal language; I can gather multiple details from a single "
                "message."
            )
        return "Welcome back. Tell me what changed in training, recovery, schedule, or goals."

    @staticmethod
    def _handoff_reply(content: str, attachment_count: int) -> str:
        if attachment_count > 0:
            return (
                "I saved your message and attachment metadata. The streaming coaching layer will "
                "process uploaded files through the engine route next."
            )
        if content:
            return (
                "I saved your message. The streaming coaching layer will use this thread as "
                "context for the next response."
            )
        return "I saved the turn and am ready for the next update."
