import re
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
        self.create_calls: list[dict[str, Any]] = []

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
        self.create_calls.append(
            {
                "thread_id": thread_id,
                "user_id": user_id,
                "role": role,
                "content": content,
                "metadata": metadata,
                "attachments": list(attachments or []),
            }
        )
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
async def test_onboarding_welcome_asks_for_sport_and_goal_first() -> None:
    repo = OnboardingRepo()
    service = ChatService(repo=cast(Any, repo), r2_service=cast(Any, object()))

    bootstrap = await service.bootstrap_thread("athlete-1")

    assert len(repo.create_calls) == 1
    assert repo.create_calls[0]["role"] == "assistant"
    assert repo.create_calls[0]["metadata"] == {"message_kind": "welcome"}

    welcome = bootstrap.thread.messages[0].content
    welcome_lower = welcome.lower()
    assert "sport" in welcome_lower
    assert re.search(r"\b(coaching|goal|objective|help|improve)\b", welcome_lower) is not None
    assert re.search(r"\bage\b", welcome_lower) is None
    assert "nutrition" not in welcome_lower
    assert "equipment" not in welcome_lower
    assert "availability" not in welcome_lower
    assert "recent training" not in welcome_lower


@pytest.mark.asyncio
async def test_persist_message_writes_single_row_with_role_and_metadata() -> None:
    repo = OnboardingRepo()
    service = ChatService(repo=cast(Any, repo), r2_service=cast(Any, object()))

    message = await service.persist_message(
        "athlete-1",
        role="user",
        content="I train ~8 hours/week",
        metadata={"message_kind": "user_turn", "client_message_id": "abc-123"},
    )

    assert message.thread_id == "thread-1"
    assert len(repo.create_calls) == 1
    call = repo.create_calls[0]
    assert call["metadata"] == {"message_kind": "user_turn", "client_message_id": "abc-123"}
    assert call["attachments"] == []


@pytest.mark.asyncio
async def test_persist_message_defaults_metadata_to_empty_dict() -> None:
    repo = OnboardingRepo()
    service = ChatService(repo=cast(Any, repo), r2_service=cast(Any, object()))

    await service.persist_message("athlete-1", role="assistant", content="Got it.")

    assert repo.create_calls[0]["metadata"] == {}
