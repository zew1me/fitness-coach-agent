from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# `parts` and `attachments` are stored verbatim as the AI SDK UIMessage shape.
# We deliberately keep them as opaque dicts so the LLM/AI SDK schema can evolve
# (new tool-* part types, new media kinds) without backend churn.
MessagePart = dict[str, Any]
MessageAttachment = dict[str, Any]


class ChatAttachmentInput(BaseModel):
    """Legacy attachment shape used only by the deprecated chat_attachments table.

    New code should put attachments inside `parts` as `{type: "file", ...}` entries
    or in the message-level `attachments` JSON. Kept for migration-window backfill
    compatibility only.
    """

    content_type: str
    filename: str
    object_key: str
    public_url: str | None = None


class ChatMessage(BaseModel):
    attachments: list[MessageAttachment] = Field(default_factory=list)
    content: str = ""
    created_at: datetime
    id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    parts: list[MessagePart] = Field(default_factory=list)
    role: Literal["user", "assistant"]
    thread_id: str
    user_id: str


class ChatThread(BaseModel):
    created_at: datetime
    id: str
    messages: list[ChatMessage] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_id: str


class ChatThreadBootstrap(BaseModel):
    attachments_enabled: bool
    profile_complete: bool
    thread: ChatThread


class ChatPersistRequest(BaseModel):
    id: UUID | None = None
    attachments: list[MessageAttachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    parts: list[MessagePart] = Field(default_factory=list)
    role: Literal["user", "assistant"]
