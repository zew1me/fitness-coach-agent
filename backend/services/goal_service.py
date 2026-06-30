from datetime import date

from pydantic import ValidationError

from backend.models.training import Goal
from backend.repos.supabase_repo import SupabaseRepository


class InvalidGoalPayloadError(ValueError):
    def __init__(self, errors: list) -> None:
        self.errors = errors
        super().__init__(str(errors))


class UnknownGoalActionError(ValueError):
    pass


def _normalize_goal_fields(
    d: dict[str, object],
    *,
    existing_course_profile: dict[str, object] | None = None,
) -> dict[str, object]:
    result = dict(d)
    if "course_profile_notes" in result and "course_profile" not in result:
        notes = result.pop("course_profile_notes")
        if notes is not None:
            result["course_profile"] = {
                **(existing_course_profile or {}),
                "notes": notes,
            }
    target_date = result.get("target_date")
    if isinstance(target_date, str):
        try:
            date.fromisoformat(target_date)
        except ValueError as exc:
            raise InvalidGoalPayloadError(
                [
                    {
                        "loc": ("goal", "target_date"),
                        "msg": "target_date must be an ISO date (YYYY-MM-DD).",
                        "type": "value_error.date",
                    }
                ]
            ) from exc
    return result


def _sanitize_goal_update_fields(fields: dict[str, object]) -> dict[str, object]:
    result = dict(fields)
    for immutable_field in ("id", "user_id", "created_at", "updated_at"):
        result.pop(immutable_field, None)
    return result


class GoalService:
    async def apply_action(
        self,
        user_id: str,
        action: str,
        goal: dict[str, object],
        goal_id: str | None,
        *,
        repo: SupabaseRepository,
    ) -> Goal:
        if action == "create":
            goal_dict = _normalize_goal_fields(goal)
            goal_dict.update({"user_id": user_id, "status": "active"})
            try:
                validated_goal = Goal.model_validate(goal_dict)
            except ValidationError as exc:
                raise InvalidGoalPayloadError(exc.errors()) from exc
            return await repo.create_goal(validated_goal)
        if action in ("update", "complete", "abandon"):
            existing_course_profile = None
            if goal.get("course_profile_notes") is not None and goal_id:
                existing_goal = await repo.get_goal(goal_id, user_id)
                existing_course_profile = existing_goal.course_profile
            goal_dict = _normalize_goal_fields(
                goal,
                existing_course_profile=existing_course_profile,
            )
            goal_dict = _sanitize_goal_update_fields(goal_dict)
            status_map = {"complete": "completed", "abandon": "abandoned"}
            if s := status_map.get(action):
                goal_dict["status"] = s
            if action == "update" and not goal_dict:
                raise InvalidGoalPayloadError(
                    [
                        {
                            "loc": ("goal",),
                            "msg": "No goal fields provided to update.",
                            "type": "value_error",
                        }
                    ]
                )
            return await repo.update_goal(goal_id or "", user_id, goal_dict)
        raise UnknownGoalActionError(f"Unknown action: {action}")
