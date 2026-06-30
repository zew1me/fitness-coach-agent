from pydantic import ValidationError

from backend.models.training import Goal
from backend.repos.supabase_repo import SupabaseRepository


class InvalidGoalPayloadError(ValueError):
    def __init__(self, errors: list) -> None:
        self.errors = errors
        super().__init__(str(errors))


class UnknownGoalActionError(ValueError):
    pass


def _normalize_goal_fields(d: dict[str, object]) -> dict[str, object]:
    result = dict(d)
    if "course_profile_notes" in result and "course_profile" not in result:
        result["course_profile"] = result.pop("course_profile_notes")
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
        goal_dict = _normalize_goal_fields(goal)
        if action == "create":
            goal_dict.update({"user_id": user_id, "status": "active"})
            try:
                validated_goal = Goal.model_validate(goal_dict)
            except ValidationError as exc:
                raise InvalidGoalPayloadError(exc.errors()) from exc
            return await repo.create_goal(validated_goal)
        if action in ("update", "complete", "abandon"):
            goal_dict = _sanitize_goal_update_fields(goal_dict)
            status_map = {"complete": "completed", "abandon": "abandoned"}
            if s := status_map.get(action):
                goal_dict["status"] = s
            return await repo.update_goal(goal_id or "", user_id, goal_dict)
        raise UnknownGoalActionError(f"Unknown action: {action}")
