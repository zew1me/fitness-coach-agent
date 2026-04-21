from datetime import UTC, datetime
from typing import Any, cast

import pytest

from backend.models.athlete import AthleteProfile
from backend.models.chat import ChatMessage, ChatThread
from backend.services.chat import ChatService


class OnboardingRepo:
    def __init__(self) -> None:
        now = datetime(2026, 4, 19, tzinfo=UTC)
        self.thread = ChatThread(
            created_at=now,
            id="thread-1",
            messages=[],
            updated_at=now,
            user_id="athlete-1",
        )

    async def get_or_create_chat_thread(self, user_id: str) -> ChatThread:
        return self.thread

    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        return AthleteProfile(user_id=user_id, coaching_state="onboarding")

    async def create_chat_message(
        self,
        *,
        thread_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any],
        attachments: list[Any] | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            attachments=[],
            content=content,
            created_at=datetime(2026, 4, 19, tzinfo=UTC),
            id=f"message-{len(self.thread.messages) + 1}",
            metadata=metadata,
            role=cast(Any, role),
            thread_id=thread_id,
            user_id=user_id,
        )
        self.thread.messages.append(message)
        return message


@pytest.mark.asyncio
async def test_onboarding_welcome_sets_optional_conversational_expectations() -> None:
    service = ChatService(repo=cast(Any, OnboardingRepo()), r2_service=cast(Any, object()))

    bootstrap = await service.bootstrap_thread("athlete-1")

    welcome = bootstrap.thread.messages[0].content
    assert "none of it is required" in welcome
    assert "normal language" in welcome
    assert "sports, goals, recent training, and schedule" in welcome
