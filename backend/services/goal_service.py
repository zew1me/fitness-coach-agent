from pydantic import ValidationError

from backend.models.training import Goal, GoalCreatePayload, GoalUpdatePayload
from backend.repos.supabase_repo import SupabaseRepository


class InvalidGoalPayloadError(ValueError):
    def __init__(self, errors: list) -> None:
        self.errors = errors
        super().__init__(str(errors))


class UnknownGoalActionError(ValueError):
    pass


def _payload_fields(payload: GoalCreatePayload | GoalUpdatePayload) -> dict[str, object]:
    # exclude_unset uses Pydantic's model_fields_set to retain omitted-vs-null semantics.
    fields = payload.model_dump(mode="json", exclude_unset=True)
    if "course_profile_notes" in fields and "course_profile" not in fields:
        notes = fields.pop("course_profile_notes")
        if notes is not None:
            fields["course_profile"] = {"notes": notes}
    else:
        fields.pop("course_profile_notes", None)
    return fields


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
            try:
                payload = GoalCreatePayload.model_validate(goal)
                goal_dict = _payload_fields(payload)
                goal_dict.update({"user_id": user_id, "status": "active"})
                validated_goal = Goal.model_validate(goal_dict)
            except ValidationError as exc:
                raise InvalidGoalPayloadError(exc.errors()) from exc
            return await repo.create_goal(validated_goal)
        if action == "update":
            try:
                payload = GoalUpdatePayload.model_validate(goal)
            except ValidationError as exc:
                raise InvalidGoalPayloadError(exc.errors()) from exc

            merge_profile_notes = (
                payload.course_profile_notes is not None
                and "course_profile" not in payload.model_fields_set
            )
            goal_dict = _payload_fields(payload)
            if merge_profile_notes and goal_id:
                existing_goal = await repo.get_goal(goal_id, user_id)
                goal_dict["course_profile"] = {
                    **(existing_goal.course_profile or {}),
                    "notes": payload.course_profile_notes,
                }
            # Explicit null remains a no-op for every partial update field.
            goal_dict = {key: value for key, value in goal_dict.items() if value is not None}
            if not goal_dict:
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
        status_map = {"complete": "completed", "abandon": "abandoned"}
        if status := status_map.get(action):
            return await repo.update_goal(goal_id or "", user_id, {"status": status})
        raise UnknownGoalActionError(f"Unknown action: {action}")
