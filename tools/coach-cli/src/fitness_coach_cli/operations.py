"""The coach operations ``coach call`` reaches: every ``POST /api/engine/<name>`` route.

The CLI carries no domain logic; each name is forwarded to the same endpoint the web coach's
tools call. A root test (tests/python/test_coach_cli_contract.py) fails when this catalog and
the FastAPI engine routes drift apart.
"""

from __future__ import annotations

OPERATIONS: dict[str, str] = {
    "adjust-plan": "Recompose the remaining weeks of the active plan in place.",
    "analyze-screenshot": "Extract structured data from an uploaded screenshot.",
    "calculate-zones": "Calculate training zone boundaries from thresholds.",
    "compute-tss": "Compute TSS from power, pace, heart rate or RPE (nothing is saved).",
    "confirm-profile-metric": "Mark an estimated max_hr or weight as user-confirmed.",
    "confirm-threshold": "Mark the active threshold for a sport as user-confirmed.",
    "estimate-thresholds": "Estimate LT1/LT2 thresholds from tests, races or maximal efforts.",
    "find-plan-workout": "Look up planned workouts on a date (optionally by sport).",
    "generate-plan-structure": "Generate and persist a new plan, superseding the active one.",
    "get-athlete-summary": "Read profile, thresholds, goals, load, recovery and active plan.",
    "get-compliance-summary": "Summarize planned versus completed workouts for the active plan.",
    "get-recent-activities": "Load recent normalized activities.",
    "process-uploaded-file": "Process an uploaded GPX, FIT, TCX or screenshot file.",
    "process-uploaded-zip": "Process an uploaded .zip of activity recordings.",
    "recalibrate-thresholds": "Re-estimate thresholds from recent activities (non-overwriting).",
    "recalibration-candidate-decision": "Accept or reject a pending threshold recalibration.",
    "recompute-load": "Rebuild training-load history from stored activities.",
    "resolve-plan-workout": "Mark a planned workout completed or skipped.",
    "save-activity-from-text": "Persist an activity described in natural language.",
    "save-recovery-data": "Persist recovery and wellness observations with their source.",
    "update-athlete-profile": "Update athlete profile fields.",
    "update-goals": "Create, update, complete or abandon goals.",
    "update-schedule": "Update weekly availability or dated overrides.",
}
