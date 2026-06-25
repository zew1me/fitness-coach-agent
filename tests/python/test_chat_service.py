from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.models.athlete import AthleteProfile
from backend.models.chat import ChatMessage, ChatModelState, ChatPersistRequest, ChatThread
from backend.services.chat import ChatService, _decode_message_cursor, _encode_message_cursor


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
        parts: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        message_id: str | None = None,
    ) -> ChatMessage:
        self.create_calls.append(
            {
                "thread_id": thread_id,
                "user_id": user_id,
                "role": role,
                "parts": list(parts),
                "metadata": dict(metadata or {}),
                "attachments": list(attachments or []),
                "message_id": message_id,
            }
        )
        content = "".join(str(part.get("text", "")) for part in parts if part.get("type") == "text")
        message = ChatMessage(
            attachments=list(attachments or []),
            content=content,
            created_at=datetime(2026, 4, 19, tzinfo=UTC),
            id=f"message-{len(self.thread.messages) + 1}",
            metadata=metadata or {},
            parts=list(parts),
            role=cast(Any, role),
            thread_id=thread_id,
            user_id=user_id,
        )
        self.thread.messages.append(message)
        return message


@pytest.mark.asyncio
async def test_onboarding_welcome_writes_assistant_welcome_message() -> None:
    repo = OnboardingRepo()
    service = ChatService(repo=cast(Any, repo), r2_service=cast(Any, object()))

    bootstrap = await service.bootstrap_thread("athlete-1")

    assert len(repo.create_calls) == 1
    assert repo.create_calls[0]["role"] == "assistant"
    assert repo.create_calls[0]["metadata"] == {"message_kind": "welcome"}

    assert len(bootstrap.thread.messages) == 1
    assert bootstrap.thread.messages[0].role == "assistant"
    welcome_parts = bootstrap.thread.messages[0].parts
    assert welcome_parts and welcome_parts[0]["type"] == "text"
    assert welcome_parts[0]["text"].strip()


@pytest.mark.asyncio
async def test_persist_message_writes_parts_and_metadata() -> None:
    repo = OnboardingRepo()
    service = ChatService(repo=cast(Any, repo), r2_service=cast(Any, object()))

    parts = [
        {
            "type": "file",
            "filename": "long-run.gpx",
            "mediaType": "application/gpx+xml",
            "url": "https://r2.example/users/athlete-1/long-run.gpx",
        },
        {"type": "text", "text": "I train ~8 hours/week"},
    ]
    message = await service.persist_message(
        "athlete-1",
        role="user",
        parts=parts,
        metadata={"message_kind": "user_turn", "client_message_id": "abc-123"},
    )

    assert message.thread_id == "thread-1"
    assert message.parts == parts
    assert len(repo.create_calls) == 1
    call = repo.create_calls[0]
    assert call["metadata"] == {"message_kind": "user_turn", "client_message_id": "abc-123"}
    assert call["parts"] == parts
    assert call["attachments"] == []
    assert call["message_id"] is None


@pytest.mark.asyncio
async def test_persist_message_threads_caller_message_id_to_repository() -> None:
    repo = OnboardingRepo()
    service = ChatService(repo=cast(Any, repo), r2_service=cast(Any, object()))
    message_id = "63ff9606-9158-43d7-a82b-d31ef9788b7d"

    await service.persist_message(
        "athlete-1",
        role="user",
        parts=[{"type": "text", "text": "I train ~8 hours/week"}],
        message_id=message_id,
    )

    assert repo.create_calls[0]["message_id"] == message_id


@pytest.mark.asyncio
async def test_persist_message_round_trips_mixed_part_kinds() -> None:
    """A mixed text / file / tool / reasoning parts array survives persistence verbatim.

    This is the contract that prevents the lossy translation bug (issue #149):
    if the AI SDK's UIMessage parts can't round-trip, tool-call pills and inline
    images disappear on reload.
    """
    repo = OnboardingRepo()
    service = ChatService(repo=cast(Any, repo), r2_service=cast(Any, object()))

    parts = [
        {"type": "text", "text": "Reviewing your screenshot..."},
        {
            "type": "tool-analyze_screenshot",
            "toolCallId": "call-1",
            "state": "output-available",
            "output": {"screenshot_type": "garmin_summary"},
        },
        {
            "type": "file",
            "filename": "garmin.png",
            "mediaType": "image/png",
            "url": "https://r2.example/users/athlete-1/garmin.png",
        },
        {
            "type": "reasoning",
            "text": "User uploaded a Garmin summary; explain fitness vs. fatigue.",
        },
        {"type": "text", "text": "Your fitness is trending up nicely."},
    ]

    message = await service.persist_message("athlete-1", role="assistant", parts=parts)

    assert message.parts == parts
    persisted = repo.thread.messages[-1]
    assert persisted.parts == parts


@pytest.mark.asyncio
async def test_persist_message_defaults_metadata_to_empty_dict() -> None:
    repo = OnboardingRepo()
    service = ChatService(repo=cast(Any, repo), r2_service=cast(Any, object()))

    await service.persist_message(
        "athlete-1",
        role="assistant",
        parts=[{"type": "text", "text": "Got it."}],
    )

    assert repo.create_calls[0]["metadata"] == {}


def test_message_cursor_round_trips_message_ids_with_delimiters() -> None:
    created_at = datetime(2026, 4, 19, 12, 30, tzinfo=UTC)
    message = ChatMessage(
        content="Older message",
        created_at=created_at,
        id="message|with|pipes",
        metadata={},
        parts=[{"type": "text", "text": "Older message"}],
        role="assistant",
        thread_id="thread-1",
        user_id="athlete-1",
    )

    cursor = _encode_message_cursor(message)

    assert _decode_message_cursor(cursor) == (created_at, "message|with|pipes")


class ModelStateRepo(OnboardingRepo):
    def __init__(self) -> None:
        super().__init__()
        now = datetime(2026, 6, 20, tzinfo=UTC)
        self.model_state = ChatModelState(
            created_at=now,
            thread_id="thread-1",
            updated_at=now,
            user_id="athlete-1",
        )

    async def get_or_create_chat_model_state(self, *, thread_id: str, user_id: str):
        assert thread_id == "thread-1"
        assert user_id == "athlete-1"
        return self.model_state

    async def replace_chat_model_state(self, **kwargs):
        assert kwargs["expected_version"] == self.model_state.version
        self.model_state = self.model_state.model_copy(
            update={
                "items": kwargs["items"],
                "coaching_memory": kwargs["coaching_memory"],
                "compaction_metadata": kwargs["compaction_metadata"],
                "version": self.model_state.version + 1,
            }
        )
        return self.model_state

    async def acquire_chat_turn_lease(self, **kwargs):
        self.model_state = self.model_state.model_copy(update={"lease_id": kwargs["lease_id"]})
        return self.model_state

    async def release_chat_turn_lease(self, **kwargs):
        assert kwargs["lease_id"] == self.model_state.lease_id
        self.model_state = self.model_state.model_copy(update={"lease_id": None})
        return self.model_state


@pytest.mark.asyncio
async def test_model_state_service_keeps_private_state_outside_thread_bootstrap() -> None:
    repo = ModelStateRepo()
    service = ChatService(repo=cast(Any, repo), r2_service=cast(Any, object()))

    state = await service.get_model_state("athlete-1")
    updated = await service.replace_model_state(
        "athlete-1",
        expected_version=state.version,
        lease_id="lease-1",
        items=[{"role": "user", "content": "hello"}],
        coaching_memory=[],
        compaction_metadata={"reason": "seed"},
    )

    assert updated.items == [{"role": "user", "content": "hello"}]
    bootstrap = await service.bootstrap_thread("athlete-1")
    assert "model_state" not in bootstrap.model_dump(mode="json")


@pytest.mark.asyncio
async def test_model_state_service_acquires_and_releases_turn_lease() -> None:
    repo = ModelStateRepo()
    service = ChatService(repo=cast(Any, repo), r2_service=cast(Any, object()))

    leased = await service.acquire_turn_lease("athlete-1", "lease-1", ttl_seconds=60)
    released = await service.release_turn_lease("athlete-1", "lease-1")

    assert leased.lease_id == "lease-1"
    assert released.lease_id is None


def test_chat_persist_request_accepts_uuid_message_id() -> None:
    payload = ChatPersistRequest.model_validate(
        {
            "id": "63ff9606-9158-43d7-a82b-d31ef9788b7d",
            "role": "user",
            "parts": [{"type": "text", "text": "I train ~8 hours/week"}],
        }
    )

    assert payload.id == UUID("63ff9606-9158-43d7-a82b-d31ef9788b7d")


def test_chat_persist_request_rejects_invalid_message_id() -> None:
    with pytest.raises(ValidationError):
        ChatPersistRequest.model_validate(
            {"id": "not-a-uuid", "role": "user", "parts": [{"type": "text", "text": "Bad id"}]}
        )
