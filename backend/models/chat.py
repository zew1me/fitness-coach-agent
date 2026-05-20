from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatAttachmentInput(BaseModel):
    content_type: str
    filename: str
    object_key: str
    public_url: str | None = None


class ChatAttachmentRecord(ChatAttachmentInput):
    created_at: datetime
    id: str
    message_id: str
    user_id: str


class ChatMessage(BaseModel):
    attachments: list[ChatAttachmentRecord] = Field(default_factory=list)
    content: str
    created_at: datetime
    id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
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
    attachments: list[ChatAttachmentInput] = Field(default_factory=list)
    content: str = Field(default="", max_length=32000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    role: Literal["user", "assistant"]
