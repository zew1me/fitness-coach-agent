from textwrap import dedent

from backend.models.planning import AdaptedPlan, AthleteProfile, CheckInInput, PlanDay

BASE_PROMPT = dedent(
    """
    You are a fitness expert and cyclocross coach. Adapt a 14-day training plan based on
    user availability, fatigue, travel, and schedule while preserving high training load
    and realistic recovery. Prioritize intensity over junk volume, keep weekend anchors,
    and use CTL, ATL, and TSB to justify substitutions when relevant.
    """
).strip()


class PlannerService:
    """Compose a small typed plan artifact from baseline profile and the latest check-in."""

    def compose_prompt(self, profile: AthleteProfile, check_in: CheckInInput) -> str:
        goals = ", ".join(profile.goals) or "Maintain a durable training load"
        constraints = ", ".join(profile.constraints) or "No explicit constraints"
        return (
            f"{BASE_PROMPT}\n\n"
            f"User ID: {profile.user_id}\n"
            f"Goals: {goals}\n"
            f"Constraints: {constraints}\n"
            f"FTP: {profile.cycling_ftp_watts or 'unknown'}\n"
            f"Check-in: {check_in.raw_text}\n"
            f"Image count: {check_in.image_count}\n"
        )

    def create_plan(self, profile: AthleteProfile, check_in: CheckInInput) -> AdaptedPlan:
        summary = (
            "Adapted 14-day plan centered on cyclocross intensity, one long aerobic anchor, "
            "and recovery protection when fatigue or travel constraints appear."
        )
        trend = "Hold CTL steady-to-rising while keeping TSB out of the deep-red range."
        days = [
            PlanDay(
                day_index=1,
                focus="Sweet Spot + endurance",
                notes="Open the block with quality rather than extra volume.",
            ),
            PlanDay(
                day_index=2,
                focus="Mobility or yoga",
                notes="Use low-back rehab work instead of complete passivity.",
            ),
            PlanDay(
                day_index=3,
                focus="Threshold ladders",
                notes="Support cyclocross-specific repeatability.",
            ),
            PlanDay(
                day_index=4,
                focus="Easy spin or rest",
                notes="Micro-recovery keeps the next hard day useful.",
            ),
            PlanDay(
                day_index=5,
                focus="Power intervals",
                notes="Short high-quality work maintains CX specificity.",
            ),
            PlanDay(
                day_index=6,
                focus="Race simulation or hard group ride",
                notes="Use the weekend anchor for specificity.",
            ),
            PlanDay(
                day_index=7,
                focus="Long aerobic ride",
                notes="Preserve durability without adding junk intensity.",
            ),
            PlanDay(
                day_index=8,
                focus="Mobility + core endurance",
                notes="Reset fatigue before the second week.",
            ),
            PlanDay(
                day_index=9,
                focus="Sweet Spot progression",
                notes="Adjust duration before cutting intensity.",
            ),
            PlanDay(
                day_index=10,
                focus="Easy endurance",
                notes="Rebound ATL if the previous sessions landed hard.",
            ),
            PlanDay(
                day_index=11,
                focus="Over/unders",
                notes="Blend threshold control with race-like surges.",
            ),
            PlanDay(
                day_index=12,
                focus="Optional yoga or rest",
                notes="Travel or low-HRV days can stay restorative.",
            ),
            PlanDay(
                day_index=13,
                focus="CX practice or run-ups",
                notes="Choose skills over extra gym fatigue.",
            ),
            PlanDay(
                day_index=14,
                focus="Aerobic endurance",
                notes="Close the block with sustainable volume.",
            ),
        ]
        if check_in.image_count > 0:
            days[1] = PlanDay(
                day_index=2,
                focus="Image-informed recovery day",
                notes=(
                    "Uploaded evidence suggests preserving readiness before "
                    "stacking more intensity."
                ),
            )
        if "travel" in check_in.raw_text.lower():
            days[12] = PlanDay(
                day_index=13,
                focus="Tempo run substitution",
                notes=(
                    "Travel constraint replaces bike-specific work with portable "
                    "aerobic quality."
                ),
            )
        return AdaptedPlan(
            user_id=profile.user_id, hours=7.0, summary=summary, trend=trend, days=days
        )
