from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.config import settings
from backend.models.chat import (
    ChatAttachmentInput,
    ChatAttachmentRecord,
    ChatMessage,
    ChatThread,
)
from backend.models.planning import AthleteProfile, CheckInInput, CheckInRecord
from supabase import Client, create_client


class RepositoryNotConfiguredError(RuntimeError):
    """Raised when database-backed operations are requested without Supabase config."""


class RecordNotFoundError(LookupError):
    """Raised when a requested record is absent in persistence."""


class SupabaseRepository:
    """Supabase-backed adapter for athlete profile and check-in persistence."""

    def __init__(  # noqa: PLR0913
        self,
        client: Any | None = None,
        *,
        athlete_profiles_table: str = "athlete_profiles",
        chat_attachments_table: str = "chat_attachments",
        chat_messages_table: str = "chat_messages",
        chat_threads_table: str = "chat_threads",
        check_ins_table: str = "check_ins",
    ) -> None:
        self._client = client or self._build_client()
        self._athlete_profiles_table = athlete_profiles_table
        self._chat_attachments_table = chat_attachments_table
        self._chat_messages_table = chat_messages_table
        self._chat_threads_table = chat_threads_table
        self._check_ins_table = check_ins_table

    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        client = self._require_client()
        response = (
            client.table(self._athlete_profiles_table).select("*").eq("user_id", user_id).execute()
        )
        rows = response.data or []
        if not rows:
            raise RecordNotFoundError(f"No athlete profile found for user '{user_id}'.")
        return self._parse_athlete_profile(rows[0])

    async def upsert_athlete_profile(self, profile: AthleteProfile) -> AthleteProfile:
        client = self._require_client()
        payload = profile.model_dump(mode="python")
        response = (
            client.table(self._athlete_profiles_table)
            .upsert(payload, on_conflict="user_id")
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the upserted athlete profile row.")
        return self._parse_athlete_profile(rows[0])

    async def create_check_in(self, check_in: CheckInInput) -> CheckInRecord:
        client = self._require_client()
        payload: dict[str, Any] = {
            "id": str(uuid4()),
            "user_id": check_in.user_id,
            "raw_text": check_in.raw_text,
            "image_count": check_in.image_count,
            "effective_date": (
                check_in.effective_date.isoformat() if check_in.effective_date is not None else None
            ),
            "created_at": datetime.now(UTC).isoformat(),
        }
        response = client.table(self._check_ins_table).insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the inserted check-in row.")
        return self._parse_check_in_record(rows[0])

    async def get_or_create_chat_thread(self, user_id: str) -> ChatThread:
        client = self._require_client()
        response = (
            client.table(self._chat_threads_table).select("*").eq("user_id", user_id).execute()
        )
        rows = response.data or []
        if rows:
            thread = self._parse_chat_thread(rows[0])
        else:
            payload = {
                "id": str(uuid4()),
                "user_id": user_id,
                "state": {},
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            }
            created = client.table(self._chat_threads_table).insert(payload).execute()
            created_rows = created.data or []
            if not created_rows:
                raise RuntimeError("Supabase did not return the inserted chat thread row.")
            thread = self._parse_chat_thread(created_rows[0])
        messages = await self.list_chat_messages(thread.id)
        return thread.model_copy(update={"messages": messages})

    async def update_chat_thread_state(self, thread_id: str, state: dict[str, Any]) -> ChatThread:
        client = self._require_client()
        response = (
            client.table(self._chat_threads_table)
            .update({"state": state})
            .eq("id", thread_id)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the updated chat thread row.")
        thread = self._parse_chat_thread(rows[0])
        messages = await self.list_chat_messages(thread.id)
        return thread.model_copy(update={"messages": messages})

    async def list_chat_messages(self, thread_id: str) -> list[ChatMessage]:
        client = self._require_client()
        response = (
            client.table(self._chat_messages_table)
            .select("*")
            .eq("thread_id", thread_id)
            .order("created_at")
            .execute()
        )
        rows = response.data or []
        messages = [self._parse_chat_message(row) for row in rows]
        if not messages:
            return []
        attachments_response = (
            client.table(self._chat_attachments_table)
            .select("*")
            .in_("message_id", [message.id for message in messages])
            .order("created_at")
            .execute()
        )
        attachment_rows = attachments_response.data or []
        attachments_by_message: dict[str, list[ChatAttachmentRecord]] = {}
        for row in attachment_rows:
            attachment = self._parse_chat_attachment(row)
            attachments_by_message.setdefault(attachment.message_id, []).append(attachment)
        return [
            message.model_copy(update={"attachments": attachments_by_message.get(message.id, [])})
            for message in messages
        ]

    async def create_chat_message(  # noqa: PLR0913
        self,
        *,
        thread_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        attachments: list[ChatAttachmentInput] | None = None,
    ) -> ChatMessage:
        client = self._require_client()
        message_id = str(uuid4())
        payload = {
            "id": message_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.now(UTC).isoformat(),
        }
        response = client.table(self._chat_messages_table).insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("Supabase did not return the inserted chat message row.")
        message = self._parse_chat_message(rows[0])
        if attachments:
            attachment_payloads = [
                {
                    "id": str(uuid4()),
                    "message_id": message_id,
                    "user_id": user_id,
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "object_key": attachment.object_key,
                    "public_url": attachment.public_url,
                    "created_at": datetime.now(UTC).isoformat(),
                }
                for attachment in attachments
            ]
            client.table(self._chat_attachments_table).insert(attachment_payloads).execute()
        messages = await self.list_chat_messages(thread_id)
        for existing in messages:
            if existing.id == message.id:
                return existing
        return message

    def _build_client(self) -> Client | None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            return None
        return create_client(settings.supabase_url, settings.supabase_service_role_key)

    def _require_client(self) -> Any:
        if self._client is None:
            raise RepositoryNotConfiguredError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        return self._client

    @staticmethod
    def _parse_athlete_profile(row: object) -> AthleteProfile:
        if not isinstance(row, dict):
            raise TypeError("Supabase athlete profile rows must be objects.")
        return AthleteProfile.model_validate(row)

    @staticmethod
    def _parse_check_in_record(row: object) -> CheckInRecord:
        if not isinstance(row, dict):
            raise TypeError("Supabase check-in rows must be objects.")
        return CheckInRecord.model_validate(row)

    @staticmethod
    def _parse_chat_thread(row: object) -> ChatThread:
        if not isinstance(row, dict):
            raise TypeError("Supabase chat thread rows must be objects.")
        return ChatThread.model_validate(row)

    @staticmethod
    def _parse_chat_message(row: object) -> ChatMessage:
        if not isinstance(row, dict):
            raise TypeError("Supabase chat message rows must be objects.")
        return ChatMessage.model_validate(row)

    @staticmethod
    def _parse_chat_attachment(row: object) -> ChatAttachmentRecord:
        if not isinstance(row, dict):
            raise TypeError("Supabase chat attachment rows must be objects.")
        return ChatAttachmentRecord.model_validate(row)
