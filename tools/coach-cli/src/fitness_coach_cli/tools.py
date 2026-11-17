"""Catalogue of the ``/api/engine`` operations, for ``coach tools list``.

The server stays the source of truth: ``coach call`` accepts any well-formed operation name.
"""

from __future__ import annotations

import re

from fitness_coach_cli.errors import CoachError

ENGINE_OPERATIONS: dict[str, str] = {
    "adjust-plan": "Recompose the remaining weeks of the active plan {plan_id, reason}.",
    "analyze-screenshot": "Extract workout details from an uploaded screenshot {image_url}.",
    "calculate-zones": "Compute training zones from thresholds.",
    "compute-tss": "Compute training stress score for a workout.",
    "confirm-profile-metric": "Confirm a pending profile metric value.",
    "confirm-threshold": "Confirm a pending threshold value.",
    "estimate-thresholds": "Estimate FTP / threshold pace from recent activities.",
    "find-plan-workout": "Find planned workouts matching a date or description.",
    "generate-plan-structure": "Create a new training plan, superseding the active one.",
    "get-athlete-summary": "Read the athlete profile, goals, thresholds and schedule.",
    "get-compliance-summary": "Read plan compliance for the active plan.",
    "get-recent-activities": "List recent recorded activities {sport?, limit?}.",
    "process-uploaded-file": "Parse an uploaded .fit/.gpx/.tcx into an activity or course.",
    "process-uploaded-zip": "Unpack an uploaded zip of activity files.",
    "recalibrate-thresholds": "Propose threshold recalibration from recent data.",
    "recalibration-candidate-decision": "Accept or reject a recalibration candidate.",
    "recompute-load": "Recompute CTL/ATL/TSB load snapshots.",
    "resolve-plan-workout": "Match a planned workout to an activity.",
    "save-activity-from-text": "Log an activity described in text.",
    "save-recovery-data": "Save daily recovery entries {entries: [...]}.",
    "update-athlete-profile": "Update athlete profile fields.",
    "update-goals": "Replace or edit the athlete's goals.",
    "update-schedule": "Update the weekly availability pattern and dated overrides.",
}

_OPERATION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def validate_operation(operation: str) -> str:
    """Reject anything that could escape the ``/api/engine/`` path segment."""
    if not _OPERATION_PATTERN.fullmatch(operation):
        raise CoachError("invalid_operation", f"Not a valid operation name: {operation!r}")
    return operation
