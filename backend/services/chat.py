from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any, Literal

from backend.config import settings
from backend.models.athlete import AthleteProfile
from backend.models.chat import (
    ChatMessage,
    ChatMessagePage,
    ChatModelState,
    ChatModelStateReplaceRequest,
    ChatThreadBootstrap,
    ChatTurnLeaseStatus,
    MessageAttachment,
    MessagePart,
)
from backend.repos.supabase_repo import RecordNotFoundError, SupabaseRepository
from backend.services.r2 import R2Service

CHAT_MESSAGE_PAGE_SIZE = 50


def _encode_message_cursor(message: ChatMessage) -> str:
    payload = json.dumps(
        {"created_at": message.created_at.isoformat(), "id": message.id},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode("ascii")


def _decode_message_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode()
        payload = json.loads(decoded)
        created_at = datetime.fromisoformat(payload["created_at"])
        message_id = payload["id"]
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("Invalid chat message cursor.") from exc
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("Invalid chat message cursor.")
    return created_at, message_id


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
            welcome_text = self._initial_welcome(profile)
            await self._repo.create_chat_message(
                thread_id=thread.id,
                user_id=user_id,
                role="assistant",
                parts=[{"type": "text", "text": welcome_text}],
                metadata={"message_kind": "welcome"},
            )
            thread = await self._repo.get_or_create_chat_thread(user_id)

        return ChatThreadBootstrap(
            attachments_enabled=self.attachments_enabled,
            next_cursor=(
                _encode_message_cursor(thread.messages[0])
                if len(thread.messages) == CHAT_MESSAGE_PAGE_SIZE
                else None
            ),
            profile_complete=self._profile_complete(profile),
            thread=thread,
        )

    async def persist_message(  # noqa: PLR0913
        self,
        user_id: str,
        *,
        role: Literal["user", "assistant"],
        parts: list[MessagePart],
        metadata: dict[str, Any] | None = None,
        attachments: list[MessageAttachment] | None = None,
        message_id: str | None = None,
    ) -> ChatMessage:
        thread = await self._repo.get_or_create_chat_thread(user_id)
        return await self._repo.create_chat_message(
            thread_id=thread.id,
            user_id=user_id,
            role=role,
            parts=parts,
            metadata=metadata or {},
            attachments=attachments,
            message_id=message_id,
        )

    async def list_messages(
        self,
        user_id: str,
        *,
        limit: int = CHAT_MESSAGE_PAGE_SIZE,
        before: str | None = None,
    ) -> ChatMessagePage:
        thread = await self._repo.get_or_create_chat_thread(user_id, include_messages=False)
        messages = await self._repo.list_chat_messages(
            thread.id,
            limit=limit,
            before=_decode_message_cursor(before) if before is not None else None,
        )
        # list_chat_messages queries DESC then reverses, so the slice is ascending
        # (oldest-first).  messages[0] is therefore the oldest row in the page and
        # is the correct "before" anchor for the next older page.
        next_cursor = _encode_message_cursor(messages[0]) if len(messages) == limit else None
        return ChatMessagePage(messages=messages, next_cursor=next_cursor)

    async def get_model_state(self, user_id: str) -> ChatModelState:
        thread = await self._repo.get_or_create_chat_thread(user_id, include_messages=False)
        return await self._repo.get_or_create_chat_model_state(thread_id=thread.id, user_id=user_id)

    async def get_turn_lease_status(self, user_id: str) -> ChatTurnLeaseStatus:
        thread = await self._repo.get_or_create_chat_thread(user_id, include_messages=False)
        return await self._repo.get_chat_turn_lease_status(thread_id=thread.id, user_id=user_id)

    async def replace_model_state(
        self,
        user_id: str,
        replacement: ChatModelStateReplaceRequest,
    ) -> ChatModelState:
        thread = await self._repo.get_or_create_chat_thread(user_id, include_messages=False)
        return await self._repo.replace_chat_model_state(
            thread_id=thread.id,
            user_id=user_id,
            replacement=replacement,
        )

    async def acquire_turn_lease(
        self, user_id: str, lease_id: str, *, ttl_seconds: int
    ) -> ChatModelState:
        thread = await self._repo.get_or_create_chat_thread(user_id, include_messages=False)
        return await self._repo.acquire_chat_turn_lease(
            thread_id=thread.id,
            user_id=user_id,
            lease_id=lease_id,
            ttl_seconds=ttl_seconds,
        )

    async def renew_turn_lease(
        self, user_id: str, lease_id: str, *, ttl_seconds: int
    ) -> ChatModelState:
        thread = await self._repo.get_or_create_chat_thread(user_id, include_messages=False)
        return await self._repo.renew_chat_turn_lease(
            thread_id=thread.id,
            user_id=user_id,
            lease_id=lease_id,
            ttl_seconds=ttl_seconds,
        )

    async def release_turn_lease(self, user_id: str, lease_id: str) -> ChatModelState:
        thread = await self._repo.get_or_create_chat_thread(user_id, include_messages=False)
        return await self._repo.release_chat_turn_lease(
            thread_id=thread.id, user_id=user_id, lease_id=lease_id
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
