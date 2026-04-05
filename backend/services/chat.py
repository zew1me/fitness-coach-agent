from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from backend.config import settings
from backend.models.chat import (
    ChatAttachmentInput,
    ChatMessage,
    ChatSendResponse,
    ChatThreadBootstrap,
)
from backend.models.planning import AdaptedPlan, AthleteProfile, CheckInInput
from backend.repos.supabase_repo import RecordNotFoundError, SupabaseRepository
from backend.services.planner import PlannerService
from backend.services.r2 import R2Service


class ChatUnavailableError(RuntimeError):
    """Raised when the conversational coach cannot be used in the current environment."""


class ChatService:
    """Persist and orchestrate the athlete-facing coaching conversation."""

    _profile_field_order = (
        "goals",
        "cycling_ftp_watts",
        "weight_kg",
        "age",
        "constraints",
        "injuries_rehab",
        "notes",
    )

    def __init__(
        self,
        repo: SupabaseRepository | None = None,
        planner_service: PlannerService | None = None,
        r2_service: R2Service | None = None,
    ) -> None:
        self._repo = repo or SupabaseRepository()
        self._planner_service = planner_service or PlannerService()
        self._r2_service = r2_service or R2Service()

    async def bootstrap_thread(self, user_id: str) -> ChatThreadBootstrap:
        self._require_openai()
        thread = await self._repo.get_or_create_chat_thread(user_id)
        profile = await self._get_profile(user_id)
        if not thread.messages:
            next_field = self._next_missing_profile_field(profile)
            await self._repo.create_chat_message(
                thread_id=thread.id,
                user_id=user_id,
                role="assistant",
                content=self._initial_welcome(next_field),
                metadata={
                    "message_kind": "welcome",
                    "pending_profile_field": next_field,
                },
            )
            await self._repo.update_chat_thread_state(
                thread.id,
                {
                    "pending_profile_field": next_field,
                },
            )
            thread = await self._repo.get_or_create_chat_thread(user_id)

        return ChatThreadBootstrap(
            attachments_enabled=self.attachments_enabled,
            profile_complete=self._next_missing_profile_field(profile) is None,
            thread=thread,
        )

    async def send_message(
        self, user_id: str, content: str, attachments: list[ChatAttachmentInput]
    ) -> ChatSendResponse:
        self._require_openai()
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

        pending_field = self._pending_profile_field(thread.state)
        if pending_field is not None:
            assistant_content, state_update, profile = await self._handle_profile_answer(
                user_id=user_id,
                thread_id=thread.id,
                profile=profile,
                field_name=pending_field,
                content=cleaned_content,
            )
            await self._repo.create_chat_message(
                thread_id=thread.id,
                user_id=user_id,
                role="assistant",
                content=assistant_content,
                metadata={
                    "message_kind": "onboarding",
                    "pending_profile_field": state_update.get("pending_profile_field"),
                },
            )
            await self._repo.update_chat_thread_state(thread.id, state_update)
        elif self._next_missing_profile_field(profile) is not None:
            next_field = self._next_missing_profile_field(profile)
            await self._repo.create_chat_message(
                thread_id=thread.id,
                user_id=user_id,
                role="assistant",
                content=self._question_for_field(next_field),
                metadata={
                    "message_kind": "onboarding",
                    "pending_profile_field": next_field,
                },
            )
            await self._repo.update_chat_thread_state(
                thread.id, {"pending_profile_field": next_field}
            )
        else:
            if cleaned_content and self._looks_like_check_in(cleaned_content, attachments):
                await self._repo.create_check_in(
                    CheckInInput(
                        user_id=user_id,
                        raw_text=cleaned_content,
                        image_count=len(attachments),
                        effective_date=self._extract_date(cleaned_content),
                    )
                )

            metadata: dict[str, Any] = {"message_kind": "assistant_reply"}
            assistant_content = await self._coach_reply(
                profile=profile,
                thread_id=thread.id,
                latest_user_message=cleaned_content,
                attachments=attachments,
                metadata=metadata,
            )
            await self._repo.create_chat_message(
                thread_id=thread.id,
                user_id=user_id,
                role="assistant",
                content=assistant_content,
                metadata=metadata,
            )
            await self._repo.update_chat_thread_state(
                thread.id,
                {"pending_profile_field": None},
            )

        updated_thread = await self._repo.get_or_create_chat_thread(user_id)
        current_profile = await self._get_profile(user_id)
        return ChatSendResponse(
            attachments_enabled=self.attachments_enabled,
            profile_complete=self._next_missing_profile_field(current_profile) is None,
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

    async def _coach_reply(
        self,
        *,
        profile: AthleteProfile,
        thread_id: str,
        latest_user_message: str,
        attachments: list[ChatAttachmentInput],
        metadata: dict[str, Any],
    ) -> str:
        wants_plan = self._wants_plan(latest_user_message)
        plan: AdaptedPlan | None = None
        if wants_plan:
            check_in = CheckInInput(
                user_id=profile.user_id,
                raw_text=latest_user_message or "Please generate my next 14-day plan.",
                image_count=len(attachments),
            )
            plan = self._planner_service.create_plan(profile, check_in)
            metadata["message_kind"] = "plan"
            metadata["plan"] = plan.model_dump(mode="json")

        recent_messages = await self._repo.list_chat_messages(thread_id)
        prompt = self._build_prompt(
            profile=profile,
            recent_messages=recent_messages[-8:],
            latest_user_message=latest_user_message,
            attachments=attachments,
            plan=plan,
        )

        try:
            return await self._create_openai_reply(prompt)
        except Exception:
            return self._fallback_reply(profile, latest_user_message, len(attachments), plan)

    async def _create_openai_reply(self, prompt: str) -> str:
        response = await httpx.AsyncClient(timeout=60.0).post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4.1-mini",
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}],
                    }
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        text_parts: list[str] = []
        for item in payload.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = content.get("text", "")
                    if isinstance(text, str) and text.strip():
                        text_parts.append(text.strip())
        if not text_parts:
            raise RuntimeError("OpenAI response did not include assistant text.")
        return "\n\n".join(text_parts)

    async def _handle_profile_answer(
        self,
        *,
        user_id: str,
        thread_id: str,
        profile: AthleteProfile,
        field_name: str,
        content: str,
    ) -> tuple[str, dict[str, Any], AthleteProfile]:
        parsed_value = self._parse_profile_field(field_name, content)
        if parsed_value is None:
            question = self._question_for_field(field_name)
            return (
                f"I still need your {self._field_label(field_name)}. {question}",
                {"pending_profile_field": field_name},
                profile,
            )

        updated_profile = profile.model_copy(deep=True)
        setattr(updated_profile, field_name, parsed_value)
        updated_profile = await self._repo.upsert_athlete_profile(updated_profile)
        next_field = self._next_missing_profile_field(updated_profile)
        if next_field is not None:
            question = self._question_for_field(next_field)
            return (
                f"Saved your {self._field_label(field_name)}. {question}",
                {"pending_profile_field": next_field},
                updated_profile,
            )

        prompt = self._build_prompt(
            profile=updated_profile,
            recent_messages=await self._repo.list_chat_messages(thread_id),
            latest_user_message=content,
            attachments=[],
            plan=None,
        )
        try:
            follow_up = await self._create_openai_reply(
                "You are finishing an onboarding flow for a cycling coach chat app. "
                "Thank the athlete for completing setup, summarize what is now known in one "
                "sentence, and invite them to share today's training update or ask for a "
                f"14-day plan.\n\n{prompt}"
            )
        except Exception:
            follow_up = (
                "Your setup is complete. I've saved your athlete profile, and I'm ready for "
                "today's "
                "training update whenever you are."
            )
        return (follow_up, {"pending_profile_field": None}, updated_profile)

    async def _get_profile(self, user_id: str) -> AthleteProfile:
        try:
            return await self._repo.get_athlete_profile(user_id)
        except RecordNotFoundError:
            return AthleteProfile(user_id=user_id)

    def _build_prompt(
        self,
        *,
        profile: AthleteProfile,
        recent_messages: list[ChatMessage],
        latest_user_message: str,
        attachments: list[ChatAttachmentInput],
        plan: AdaptedPlan | None,
    ) -> str:
        profile_summary = (
            f"user_id={profile.user_id}; goals={profile.goals or ['unknown']}; "
            f"constraints={profile.constraints or ['none']}; "
            f"injuries={profile.injuries_rehab or ['none']}; "
            f"ftp={profile.cycling_ftp_watts or 'unknown'}; "
            f"weight_kg={profile.weight_kg or 'unknown'}; "
            f"age={profile.age or 'unknown'}; notes={profile.notes or 'none'}"
        )
        transcript = "\n".join(
            f"{message.role}: {message.content}"
            for message in recent_messages
            if message.content.strip()
        )
        attachment_hint = (
            f"The latest user turn includes {len(attachments)} uploaded photo attachment(s)."
            if attachments
            else "The latest user turn has no photo attachments."
        )
        plan_hint = (
            "A structured 14-day plan has already been generated for this turn. "
            f"Plan summary: {plan.summary}. Trend: {plan.trend}. Hours: {plan.hours}."
            if plan is not None
            else "No plan object was generated for this turn."
        )
        return (
            "You are an endurance coach inside a minimal chat app. "
            "Be concise, practical, and warm. Ask at most one follow-up question. "
            "If the athlete asked for a plan, summarize the 14-day structure conversationally "
            "and mention that the detailed plan cards are attached below the message. "
            "Do not mention internal APIs or hidden forms.\n\n"
            f"Athlete profile: {profile_summary}\n"
            f"Recent conversation:\n{transcript or 'No earlier messages.'}\n\n"
            f"Latest user message: {latest_user_message or '[attachment only]'}\n"
            f"{attachment_hint}\n"
            f"{plan_hint}"
        )

    def _fallback_reply(
        self,
        profile: AthleteProfile,
        latest_user_message: str,
        attachment_count: int,
        plan: AdaptedPlan | None,
    ) -> str:
        if plan is not None:
            return (
                f"I mapped out your next 14 days around {plan.summary.lower()}. "
                f"Expect about {plan.hours} hours total with a {plan.trend.lower()} trend. "
                "Use the day-by-day cards below as your working block, and tell me what changes "
                "if travel, fatigue, or soreness shows up."
            )
        if attachment_count > 0:
            return (
                "I saved your update and noted the attached photo evidence. "
                "Give me a quick read on how the session felt, and I'll adjust the next block "
                "from there."
            )
        if latest_user_message:
            return (
                f"I've logged that update for {profile.user_id}. "
                "Tell me how your legs feel today, what session you just did, or ask for your "
                "next 14-day plan."
            )
        return (
            "I'm here and ready whenever you want to log a training update or ask for your next "
            "plan."
        )

    def _initial_welcome(self, next_field: str | None) -> str:
        opening = (
            "Welcome back. This is your coach chat, so you can treat it like a normal "
            "conversation: "
            "tell me how training is going, attach photos, or ask for your next 14-day plan."
        )
        if next_field is None:
            return (
                f"{opening} I already have the basics I need, so start with today's check-in or "
                "ask me to build your next block."
            )
        return f"{opening} To personalize things, {self._question_for_field(next_field)}"

    def _next_missing_profile_field(self, profile: AthleteProfile) -> str | None:
        for field_name in self._profile_field_order:
            value = getattr(profile, field_name)
            if value is None:
                return field_name
            if isinstance(value, list) and not value:
                return field_name
            if isinstance(value, str) and not value.strip():
                return field_name
        return None

    @staticmethod
    def _pending_profile_field(state: dict[str, Any]) -> str | None:
        pending = state.get("pending_profile_field")
        return pending if isinstance(pending, str) and pending else None

    @staticmethod
    def _field_label(field_name: str) -> str:
        return {
            "goals": "goals",
            "cycling_ftp_watts": "current FTP",
            "weight_kg": "weight",
            "age": "age",
            "constraints": "constraints",
            "injuries_rehab": "injuries or rehab notes",
            "notes": "extra training notes",
        }[field_name]

    def _question_for_field(self, field_name: str) -> str:
        return {
            "goals": "what are your main goals for the next training block?",
            "cycling_ftp_watts": "what's your current cycling FTP in watts?",
            "weight_kg": "what's your current weight in kilograms?",
            "age": "how old are you?",
            "constraints": "what schedule or life constraints should I coach around?",
            "injuries_rehab": (
                "are there any injuries, rehab considerations, or sore spots I should know about?"
            ),
            "notes": (
                "is there anything else about your training background or preferences that I "
                "should keep in mind?"
            ),
        }[field_name]

    def _parse_profile_field(  # noqa: PLR0911
        self, field_name: str, content: str
    ) -> Any | None:
        cleaned = content.strip()
        if not cleaned:
            return None
        if field_name in {"goals", "constraints", "injuries_rehab"}:
            lowered = cleaned.lower()
            if lowered in {"none", "no", "nothing", "n/a"}:
                return []
            return [
                part.strip(" -.")
                for part in cleaned.replace("\n", ",").split(",")
                if part.strip(" -.")
            ]
        if field_name == "notes":
            return cleaned
        if field_name == "age":
            digits = "".join(character for character in cleaned if character.isdigit())
            return int(digits) if digits else None
        if field_name == "cycling_ftp_watts":
            digits = "".join(character for character in cleaned if character.isdigit())
            return int(digits) if digits else None
        if field_name == "weight_kg":
            compact = "".join(
                character for character in cleaned if character.isdigit() or character == "."
            )
            return float(compact) if compact else None
        return None

    @staticmethod
    def _wants_plan(content: str) -> bool:
        lowered = content.lower()
        return any(
            phrase in lowered
            for phrase in (
                "14-day plan",
                "14 day plan",
                "next plan",
                "training plan",
                "two week",
                "2 week",
                "build my plan",
            )
        )

    @staticmethod
    def _looks_like_check_in(content: str, attachments: list[ChatAttachmentInput]) -> bool:
        lowered = content.lower()
        if attachments:
            return True
        return any(
            token in lowered
            for token in (
                "ride",
                "run",
                "session",
                "workout",
                "legs",
                "fatigue",
                "tired",
                "sore",
                "recovery",
                "travel",
                "sleep",
                "today",
                "felt",
            )
        )

    @staticmethod
    def _extract_date(_: str) -> date | None:
        return None

    @staticmethod
    def _require_openai() -> None:
        if settings.openai_api_key is None or not settings.openai_api_key.strip():
            raise ChatUnavailableError(
                "Chat coaching is not configured. Set OPENAI_API_KEY to enable the post-login "
                "coach."
            )
