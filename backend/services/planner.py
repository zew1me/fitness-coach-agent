from textwrap import dedent
from typing import TypedDict

from backend.models.planning import AdaptedPlan, AthleteProfile, CheckInInput, PlanDay

BASE_PROMPT = dedent(
    """
    You are a fitness expert and endurance coach. Adapt a 14-day training plan based on
    primary sport, mixed training habits, user availability, fatigue, travel, and rehab
    constraints while preserving realistic recovery and useful progression. Prioritize
    event-relevant quality over junk volume, keep clear weekend anchors, and use CTL, ATL,
    and TSB language when explaining substitutions or recovery spacing.
    """
).strip()

MASTERS_AGE = 45

CYCLING_PLANS = {
    "balanced": [
        ("Sweet Spot + endurance", "Open the block with quality rather than extra volume."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Threshold ladders", "Support repeatable bike-specific work."),
        ("Easy spin or rest", "Micro-recovery keeps the next hard day useful."),
        ("Power intervals", "Short high-quality work maintains top-end specificity."),
        ("Race simulation or hard group ride", "Use the weekend anchor for specificity."),
        ("Long aerobic ride", "Preserve durability without adding junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Sweet Spot progression", "Adjust duration before cutting intensity."),
        ("Easy endurance", "Rebound ATL if the previous sessions landed hard."),
        ("Over/unders", "Blend threshold control with surge tolerance."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Skills ride or cadence drills", "Choose skill quality over extra gym fatigue."),
        ("Aerobic endurance", "Close the block with sustainable volume."),
    ],
    "threshold": [
        ("Threshold primer", "Open with controlled work that builds FTP without heroics."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Threshold ladders", "FTP-oriented progressions stay smooth and repeatable."),
        ("Easy spin or rest", "Micro-recovery keeps the next hard day useful."),
        ("Power intervals", "Short high-quality work maintains bike specificity."),
        ("Race simulation or hard group ride", "Use the weekend anchor for specificity."),
        ("Long aerobic ride", "Preserve durability without adding junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Sweet Spot progression", "Adjust duration before cutting intensity."),
        ("Easy endurance", "Rebound ATL if the previous sessions landed hard."),
        ("Over/unders", "Blend threshold control with race-like surges."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Skills ride or cadence drills", "Choose quality over extra gym fatigue."),
        ("Aerobic endurance", "Close the block with sustainable volume."),
    ],
    "endurance": [
        ("Aerobic endurance opener", "Bias the opening days toward durability and low stress."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Tempo endurance", "Keep the aerobic engine honest without overreaching."),
        ("Easy spin or rest", "Micro-recovery keeps the next hard day useful."),
        ("Sweet Spot cadence work", "A smooth tempo dose supports endurance without junk volume."),
        ("Long aerobic ride", "Use the weekend anchor to expand durable volume."),
        ("Long aerobic ride", "Preserve durability without adding junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Progressive endurance", "Adjust duration before cutting intensity."),
        ("Easy endurance", "Rebound ATL if the previous sessions landed hard."),
        ("Steady over-unders", "Blend threshold control with long-course resilience."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Skills ride with endurance", "Choose handling quality over extra fatigue."),
        ("Aerobic endurance", "Close the block with sustainable volume."),
    ],
    "race": [
        ("Race opener", "Open the block with quality that supports event sharpness."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Threshold ladders", "Keep repeatability high for race surges."),
        ("Easy spin or rest", "Micro-recovery keeps the next hard day useful."),
        ("Short power intervals", "Punchy work sharpens the race-day engine."),
        ("Race simulation or hard group ride", "Practice repeated surges and positioning."),
        ("Long aerobic ride", "Preserve durability without adding junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Sweet Spot progression", "Adjust duration before cutting intensity."),
        ("Easy endurance", "Rebound ATL if the previous sessions landed hard."),
        ("Over/unders", "Blend threshold control with race-like surges."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Skills ride or race drills", "Choose skills over extra gym fatigue."),
        ("Aerobic endurance", "Close the block with sustainable volume."),
    ],
}

RUNNING_PLANS = {
    "balanced": [
        ("Steady tempo run", "Open the block with quality rather than junk mileage."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Threshold intervals", "Support repeatable running economy."),
        ("Easy run or rest", "Micro-recovery keeps the next hard day useful."),
        ("Hill repeats", "Short uphill work builds power without overstriding."),
        ("Long run with quality finish", "Use the weekend anchor for race durability."),
        ("Easy aerobic run", "Preserve durability without forcing extra intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Progression run", "Adjust duration before cutting intensity."),
        ("Easy endurance run", "Rebound ATL if the previous sessions landed hard."),
        ("Cruise intervals", "Blend threshold control with sustained aerobic work."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Drills + strides", "Keep mechanics sharp without adding junk volume."),
        ("Aerobic run", "Close the block with sustainable volume."),
    ],
    "threshold": [
        ("Tempo opener", "Open with controlled work that builds lactate threshold."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Threshold intervals", "Cruise the repeats smoothly instead of chasing hero splits."),
        ("Easy run or rest", "Micro-recovery keeps the next hard day useful."),
        ("Hill reps", "Support force production without overloading flat speed."),
        ("Long run with steady middle", "Use the weekend anchor for durable aerobic work."),
        ("Easy aerobic run", "Preserve durability without junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Tempo progression", "Adjust duration before cutting intensity."),
        ("Easy endurance run", "Rebound ATL if the previous sessions landed hard."),
        ("Lactate-shuttle intervals", "Convert threshold fitness into repeatable race rhythm."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Drills + strides", "Mechanical quality beats extra fatigue."),
        ("Aerobic run", "Close the block with sustainable volume."),
    ],
    "endurance": [
        ("Aerobic run opener", "Bias the opening days toward durability and low stress."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Steady aerobic run", "Keep the aerobic engine honest without overreaching."),
        ("Easy run or rest", "Micro-recovery keeps the next hard day useful."),
        ("Medium-long run", "Extend durable volume before the weekend anchor."),
        ("Long run", "Use the weekend anchor to expand event durability."),
        ("Recovery jog or cross-train", "Preserve durability without adding junk intensity."),
        ("Mobility + strength", "Reset fatigue before the second week."),
        ("Progression run", "Adjust duration before cutting intensity."),
        ("Easy endurance run", "Rebound ATL if the previous sessions landed hard."),
        ("Marathon-pace block", "Blend aerobic durability with goal-specific rhythm."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Drills + strides", "Choose quality mechanics over extra fatigue."),
        ("Aerobic run", "Close the block with sustainable volume."),
    ],
    "race": [
        ("Race-pace opener", "Open the block with quality that supports event sharpness."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Threshold intervals", "Keep repeatability high for race surges."),
        ("Easy run or rest", "Micro-recovery keeps the next hard day useful."),
        ("Hill sprints", "Punchy work sharpens the race-day engine."),
        ("Simulation workout or tune-up race", "Practice race rhythm under manageable fatigue."),
        ("Long aerobic run", "Preserve durability without junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Progression run", "Adjust duration before cutting intensity."),
        ("Easy endurance run", "Rebound ATL if the previous sessions landed hard."),
        ("Alternating-threshold set", "Blend control with race-like discomfort."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Drills + strides", "Keep mechanics and cadence sharp."),
        ("Aerobic run", "Close the block with sustainable volume."),
    ],
}

MULTISPORT_PLANS = {
    "balanced": [
        ("Bike tempo + cadence", "Open with quality while spreading stress across disciplines."),
        ("Technique swim + mobility", "Use low-impact skill work instead of complete passivity."),
        ("Threshold run intervals", "Give the run enough quality without monopolizing the block."),
        ("Easy spin or rest", "Micro-recovery keeps the next hard day useful."),
        ("Swim threshold set", "Short controlled work builds multisport repeatability."),
        ("Long ride", "Use the weekend anchor for durable aerobic load."),
        ("Brick run off the bike", "Practice discipline changes without junk fatigue."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Sweet Spot ride", "Adjust duration before cutting intensity."),
        ("Easy swim", "Rebound ATL while keeping technique engaged."),
        ("Tempo brick session", "Blend race rhythm across multiple sports."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Technique session + drills", "Skill quality beats extra fatigue."),
        ("Aerobic run or ride", "Close the block with sustainable volume."),
    ],
    "threshold": [
        ("Bike threshold primer", "Open with controlled work that builds durable power."),
        ("Technique swim + mobility", "Use low-impact skill work instead of complete passivity."),
        ("Threshold run intervals", "Run economy gets a real threshold slot."),
        ("Easy spin or rest", "Micro-recovery keeps the next hard day useful."),
        ("Swim threshold set", "Short controlled work supports multisport repeatability."),
        ("Long ride", "Use the weekend anchor for durable aerobic load."),
        ("Easy brick run", "Add frequency without overreaching."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Sweet Spot ride", "Adjust duration before cutting intensity."),
        ("Easy swim", "Rebound ATL while keeping technique engaged."),
        ("Tempo brick session", "Convert threshold fitness into race rhythm."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Technique session + drills", "Skill quality beats extra fatigue."),
        ("Aerobic run or ride", "Close the block with sustainable volume."),
    ],
    "endurance": [
        ("Aerobic ride opener", "Bias the opening days toward durability and low stress."),
        ("Technique swim + mobility", "Use low-impact skill work instead of complete passivity."),
        ("Steady endurance run", "Keep the aerobic engine honest without overreaching."),
        ("Easy spin or rest", "Micro-recovery keeps the next hard day useful."),
        ("Steady swim set", "Build aerobic load without extra orthopedic stress."),
        ("Long ride", "Use the weekend anchor to expand durable volume."),
        ("Long run off fresh legs", "Preserve the second endurance anchor."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Progressive ride", "Adjust duration before cutting intensity."),
        ("Easy swim", "Rebound ATL while keeping technique engaged."),
        ("Brick endurance session", "Blend durability across disciplines."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Technique session + drills", "Skill quality beats extra fatigue."),
        ("Aerobic run or ride", "Close the block with sustainable volume."),
    ],
    "race": [
        ("Race-pace bike set", "Open the block with quality that supports event sharpness."),
        ("Technique swim + mobility", "Use low-impact skill work instead of complete passivity."),
        ("Threshold run intervals", "Keep repeatability high across the race disciplines."),
        ("Easy spin or rest", "Micro-recovery keeps the next hard day useful."),
        ("Open-water or threshold swim", "Specificity stays front and center."),
        ("Simulation brick", "Practice race rhythm and transitions."),
        ("Long aerobic ride", "Preserve durability without junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Sweet Spot ride", "Adjust duration before cutting intensity."),
        ("Easy run", "Rebound ATL if the previous sessions landed hard."),
        ("Tempo brick session", "Blend control with race-day specificity."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Transition drills", "Choose skill work over extra fatigue."),
        ("Aerobic run or ride", "Close the block with sustainable volume."),
    ],
}

GENERIC_ENDURANCE_PLANS = {
    "balanced": [
        ("Primary-sport tempo", "Open with useful quality rather than generic volume."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Threshold intervals", "Repeatable aerobic power stays front and center."),
        ("Easy aerobic session or rest", "Micro-recovery keeps the next hard day useful."),
        ("Short power or hill session", "Keep the quality compact and sport-relevant."),
        ("Primary-sport simulation session", "Use the weekend anchor for specificity."),
        ("Long aerobic session", "Preserve durability without adding junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Progression session", "Adjust duration before cutting intensity."),
        ("Easy endurance", "Rebound ATL if the previous sessions landed hard."),
        ("Steady over-unders", "Blend control with event-relevant discomfort."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Technique or coordination work", "Skill quality beats extra fatigue."),
        ("Aerobic endurance", "Close the block with sustainable volume."),
    ],
    "threshold": [
        ("Threshold primer", "Open with controlled work that builds repeatable aerobic power."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Threshold intervals", "Repeatable power stays smooth and useful."),
        ("Easy aerobic session or rest", "Micro-recovery keeps the next hard day useful."),
        ("Short power or hill session", "Compact quality keeps the block specific."),
        ("Primary-sport simulation session", "Use the weekend anchor for specificity."),
        ("Long aerobic session", "Preserve durability without adding junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Progression session", "Adjust duration before cutting intensity."),
        ("Easy endurance", "Rebound ATL if the previous sessions landed hard."),
        ("Alternating-threshold set", "Blend control with repeatability."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Technique or coordination work", "Skill quality beats extra fatigue."),
        ("Aerobic endurance", "Close the block with sustainable volume."),
    ],
    "endurance": [
        ("Aerobic opener", "Bias the opening days toward durability and low stress."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Steady aerobic session", "Keep the aerobic engine honest without overreaching."),
        ("Easy aerobic session or rest", "Micro-recovery keeps the next hard day useful."),
        ("Medium-long session", "Extend durable volume before the weekend anchor."),
        ("Long aerobic session", "Use the weekend anchor to expand durable volume."),
        ("Easy recovery session", "Preserve durability without adding junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Progression session", "Adjust duration before cutting intensity."),
        ("Easy endurance", "Rebound ATL if the previous sessions landed hard."),
        ("Steady tempo block", "Blend aerobic durability with event relevance."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Technique or coordination work", "Skill quality beats extra fatigue."),
        ("Aerobic endurance", "Close the block with sustainable volume."),
    ],
    "race": [
        ("Race-pace opener", "Open the block with quality that supports event sharpness."),
        ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
        ("Threshold intervals", "Keep repeatability high for competitive efforts."),
        ("Easy aerobic session or rest", "Micro-recovery keeps the next hard day useful."),
        ("Short power session", "Punchy work sharpens the race-day engine."),
        ("Primary-sport simulation session", "Practice event rhythm under manageable fatigue."),
        ("Long aerobic session", "Preserve durability without adding junk intensity."),
        ("Mobility + core endurance", "Reset fatigue before the second week."),
        ("Progression session", "Adjust duration before cutting intensity."),
        ("Easy endurance", "Rebound ATL if the previous sessions landed hard."),
        ("Alternating-threshold set", "Blend control with race-like discomfort."),
        ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
        ("Technique or coordination work", "Skill quality beats extra fatigue."),
        ("Aerobic endurance", "Close the block with sustainable volume."),
    ],
}

PLAN_LIBRARY = {
    "cycling": CYCLING_PLANS,
    "running": RUNNING_PLANS,
    "multisport": MULTISPORT_PLANS,
    "generic": GENERIC_ENDURANCE_PLANS,
}

SPORT_KEYWORDS = {
    "cycling": ("bike", "cycling", "cyclocross", "cx", "road race", "gravel", "mtb", "velo"),
    "running": ("run", "running", "marathon", "half marathon", "trail", "5k", "10k"),
    "swim": ("swim", "swimming", "pool", "open water"),
    "strength": ("strength", "gym", "lifting", "weights"),
    "hike": ("hike", "hiking", "mountain day"),
}


class DaySpec(TypedDict):
    focus: str
    notes: list[str]


class PlannerService:
    """Compose a small typed plan artifact from baseline profile and the latest check-in."""

    def compose_prompt(self, profile: AthleteProfile, check_in: CheckInInput) -> str:
        goals = ", ".join(profile.goals) or "Maintain a durable training load"
        constraints = ", ".join(profile.constraints) or "No explicit constraints"
        injuries = ", ".join(profile.injuries_rehab) or "No active rehab constraints"
        notes = profile.notes or "No extra athlete notes"
        return (
            f"{BASE_PROMPT}\n\n"
            f"User ID: {profile.user_id}\n"
            f"Goals: {goals}\n"
            f"Constraints: {constraints}\n"
            f"Injuries/Rehab: {injuries}\n"
            f"Athlete notes: {notes}\n"
            f"FTP: {profile.cycling_ftp_watts or 'unknown'}\n"
            f"Check-in: {check_in.raw_text}\n"
            f"Image count: {check_in.image_count}\n"
        )

    def create_plan(self, profile: AthleteProfile, check_in: CheckInInput) -> AdaptedPlan:
        signal_text = self._signal_text(profile, check_in)
        goal_theme = self._goal_theme(profile)
        sport_theme = self._sport_theme(profile, signal_text)
        modalities = self._secondary_modalities(signal_text, sport_theme)
        fatigue = self._contains_any(
            signal_text,
            ("fatigue", "fatigued", "tired", "tiredness", "exhausted", "heavy legs", "low energy"),
        )
        travel = self._contains_any(
            signal_text,
            (
                "travel",
                "travelling",
                "traveling",
                "trip",
                "flight",
                "flying",
                "airport",
                "hotel",
                "road",
            ),
        )
        rehab = self._contains_any(
            signal_text,
            (
                "rehab",
                "injury",
                "injured",
                "ache",
                "pain",
                "sore",
                "achilles",
                "knee",
                "back",
                "calf",
                "hamstring",
            ),
        )
        image_signal = check_in.image_count > 0

        day_specs = self._build_day_specs(goal_theme, sport_theme)
        hours = 7.0

        if fatigue:
            self._apply_fatigue(day_specs, sport_theme)
            hours -= 1.25
        if travel:
            self._apply_travel(day_specs, sport_theme, rehab=rehab)
            hours -= 0.75
        if rehab:
            self._apply_rehab(day_specs, sport_theme, travel=travel)
            hours -= 0.5
        if image_signal:
            self._apply_image_recovery(day_specs, sport_theme)
            hours -= 0.25

        self._apply_goal_emphasis(day_specs, goal_theme, sport_theme, travel=travel)
        self._apply_age_adjustments(day_specs, profile.age)
        self._apply_modality_mix(day_specs, sport_theme, modalities)

        if not fatigue and goal_theme == "endurance":
            hours += 0.5
        elif not fatigue and goal_theme in {"threshold", "race"}:
            hours += 0.25
        if profile.age is not None and profile.age >= MASTERS_AGE:
            hours -= 0.25

        summary = self._build_summary(
            goal_theme=goal_theme,
            sport_theme=sport_theme,
            fatigue=fatigue,
            travel=travel,
            rehab=rehab,
            image_signal=image_signal,
            age=profile.age,
            modalities=modalities,
        )
        trend = self._build_trend(
            goal_theme=goal_theme,
            sport_theme=sport_theme,
            fatigue=fatigue,
            travel=travel,
            rehab=rehab,
            image_signal=image_signal,
            age=profile.age,
        )
        days = [
            PlanDay(
                day_index=day_index,
                focus=spec["focus"],
                notes=" ".join(spec["notes"]),
            )
            for day_index, spec in sorted(day_specs.items())
        ]
        return AdaptedPlan(
            user_id=profile.user_id,
            hours=max(4.5, round(hours, 1)),
            summary=summary,
            trend=trend,
            days=days,
        )

    def _build_day_specs(self, goal_theme: str, sport_theme: str) -> dict[int, DaySpec]:
        templates = PLAN_LIBRARY.get(sport_theme, GENERIC_ENDURANCE_PLANS).get(
            goal_theme,
            PLAN_LIBRARY.get(sport_theme, GENERIC_ENDURANCE_PLANS)["balanced"],
        )
        return {
            index: {"focus": focus, "notes": [notes]}
            for index, (focus, notes) in enumerate(templates, start=1)
        }

    def _apply_fatigue(self, day_specs: dict[int, DaySpec], sport_theme: str) -> None:
        self._set_day(
            day_specs,
            2,
            focus=self._recovery_focus(sport_theme),
            note="Freshness matters after a fatigue-heavy check-in.",
        )
        self._set_day(
            day_specs,
            4,
            note="Keep the fourth day easy so the next quality day stays useful.",
        )
        self._set_day(
            day_specs,
            8,
            focus="Full reset day",
            note="The second week starts with restoration before more load.",
        )
        self._set_day(
            day_specs,
            10,
            note="Maintain aerobic rhythm without forcing intensity.",
        )
        self._set_day(
            day_specs,
            12,
            note="A softer flex day protects recovery if the legs still feel cooked.",
        )

    def _apply_travel(
        self,
        day_specs: dict[int, DaySpec],
        sport_theme: str,
        *,
        rehab: bool,
    ) -> None:
        self._set_day(
            day_specs,
            5,
            focus=self._portable_session_focus(sport_theme, rehab=rehab),
            note="Keep the work compact and easy to execute while on the road.",
        )
        self._set_day(
            day_specs,
            12,
            focus="Travel buffer + mobility",
            note="Leave room for delays, airport time, and lower sleep quality.",
        )
        self._set_day(
            day_specs,
            13,
            focus=self._travel_substitution_focus(sport_theme),
            note=(
                "Portable aerobic quality replaces your usual sport-specific session during travel."
            ),
        )

    def _apply_rehab(
        self,
        day_specs: dict[int, DaySpec],
        sport_theme: str,
        *,
        travel: bool,
    ) -> None:
        self._set_day(
            day_specs,
            2,
            focus=self._recovery_focus(sport_theme) if travel else "Rehab circuit + mobility",
            note="Protect the injury with controlled movement and rehab work.",
        )
        self._set_day(
            day_specs,
            3,
            note=(
                "Keep the intensity smooth and mechanically controlled while "
                "the rehab issue settles."
            ),
        )
        self._set_day(
            day_specs,
            5,
            focus=self._portable_session_focus(sport_theme, rehab=True)
            if travel
            else self._rehab_quality_focus(sport_theme),
            note="Choose low-risk quality that keeps the rehab issue quiet.",
        )
        self._set_day(
            day_specs,
            11,
            note="Keep the quality controlled and mechanically efficient.",
        )
        self._set_day(
            day_specs,
            13,
            note="Choose skill or aerobic work that avoids flare-ups.",
        )

    def _apply_image_recovery(self, day_specs: dict[int, DaySpec], sport_theme: str) -> None:
        self._set_day(
            day_specs,
            2,
            focus="Image-informed recovery day",
            note=(
                "Uploaded evidence suggests preserving readiness before stacking "
                f"more {sport_theme}-specific intensity."
            ),
        )
        self._set_day(
            day_specs,
            8,
            note="Use the second-week reset to respond to the visual recovery signal as well.",
        )

    def _apply_goal_emphasis(
        self,
        day_specs: dict[int, DaySpec],
        goal_theme: str,
        sport_theme: str,
        *,
        travel: bool,
    ) -> None:
        if goal_theme == "threshold":
            day_1, day_3, day_11 = self._threshold_focuses(sport_theme)
            self._set_day(
                day_specs,
                1,
                focus=day_1,
                note="Start with controlled work that builds repeatable power.",
            )
            self._set_day(
                day_specs,
                3,
                focus=day_3,
                note="This is the main threshold-growth session in the block.",
            )
            self._set_day(
                day_specs,
                11,
                focus=day_11,
                note="Use race-like discomfort to convert threshold into durability.",
            )
        elif goal_theme == "endurance":
            day_1, day_7, day_14 = self._endurance_focuses(sport_theme)
            self._set_day(
                day_specs,
                1,
                focus=day_1,
                note="Bias the opening days toward durability and low stress.",
            )
            self._set_day(
                day_specs,
                7,
                focus=day_7,
                note="Stretch the longest session to support the endurance goal.",
            )
            self._set_day(
                day_specs,
                14,
                focus=day_14,
                note="Close the block with a volume-biased aerobic finish.",
            )
        elif goal_theme == "race":
            day_5, day_6, day_13 = self._race_focuses(sport_theme, travel=travel)
            self._set_day(
                day_specs, 5, focus=day_5, note="Punchy work sharpens the race-day engine."
            )
            self._set_day(
                day_specs,
                6,
                focus=day_6,
                note="Use the weekend anchor for sport-specific race rehearsal.",
            )
            self._set_day(
                day_specs,
                13,
                focus=day_13,
                note="Specificity stays high without adding junk fatigue.",
            )

    def _apply_age_adjustments(
        self,
        day_specs: dict[int, DaySpec],
        age: int | None,
    ) -> None:
        if age is None or age < MASTERS_AGE:
            return
        self._set_day(
            day_specs,
            4,
            note="Recovery spacing is widened a touch to keep quality sessions landing well.",
        )
        self._set_day(
            day_specs,
            10,
            note=(
                "A masters-friendly cadence means the aerobic work stays supportive, not draining."
            ),
        )
        self._set_day(
            day_specs,
            12,
            focus="Optional rest or mobility",
            note=(
                "Leave more room for restoration when the training age and life load are both high."
            ),
        )

    def _apply_modality_mix(
        self,
        day_specs: dict[int, DaySpec],
        sport_theme: str,
        modalities: list[str],
    ) -> None:
        if "strength" in modalities:
            self._set_day(
                day_specs,
                8,
                note=(
                    "Keep a short strength-maintenance touch so supporting work does not disappear."
                ),
            )
        if "swim" in modalities and sport_theme != "multisport":
            self._set_day(
                day_specs,
                2,
                note=(
                    "A light technique swim can replace some recovery volume if "
                    "that keeps compliance higher."
                ),
            )
        if "running" in modalities and sport_theme == "cycling":
            self._set_day(
                day_specs,
                13,
                note="A short transition run can fit here if mixed-modality consistency matters.",
            )
        if "hike" in modalities:
            self._set_day(
                day_specs,
                14,
                note=(
                    "A steady hike is a valid aerobic anchor if terrain and "
                    "family plans drive compliance."
                ),
            )

    def _build_summary(  # noqa: PLR0913
        self,
        *,
        goal_theme: str,
        sport_theme: str,
        fatigue: bool,
        travel: bool,
        rehab: bool,
        image_signal: bool,
        age: int | None,
        modalities: list[str],
    ) -> str:
        summary_parts = [
            (
                f"Adaptive 14-day plan built around {self._sport_label(sport_theme)} "
                "demands and realistic recovery."
            ),
        ]
        summary_parts.append(self._goal_summary_fragment(goal_theme))
        if modalities:
            summary_parts.append(
                "Supporting modalities stay in the mix through "
                f"{', '.join(modalities)} where they help compliance."
            )
        if fatigue:
            summary_parts.append(
                "Freshness is intentionally protected because the check-in reads as fatigued."
            )
        if travel:
            summary_parts.append("Midweek work is compressed so travel does not erase the block.")
        if rehab:
            summary_parts.append("Low-risk choices protect the rehab constraint.")
        if image_signal:
            summary_parts.append("Uploaded images push the block toward extra recovery protection.")
        if age is not None and age >= MASTERS_AGE:
            summary_parts.append(
                "Recovery spacing is widened slightly for a masters-friendly rhythm."
            )
        return " ".join(summary_parts)

    def _build_trend(  # noqa: PLR0913
        self,
        *,
        goal_theme: str,
        sport_theme: str,
        fatigue: bool,
        travel: bool,
        rehab: bool,
        image_signal: bool,
        age: int | None,
    ) -> str:
        trend_parts: list[str] = []
        if fatigue or rehab:
            trend_parts.append("ATL is eased back before the next quality block.")
        else:
            trend_parts.append("CTL stays steady-to-rising.")
        if travel:
            trend_parts.append(
                "Portable sessions keep stimulus high even when the schedule compresses."
            )
        if image_signal:
            trend_parts.append(
                "Recovery signals from uploaded evidence temper the load progression."
            )
        if age is not None and age >= MASTERS_AGE:
            trend_parts.append("TSB is given a little more room before the second-week quality.")
        trend_parts.append(self._goal_trend_fragment(goal_theme, sport_theme))
        return " ".join(trend_parts)

    def _goal_summary_fragment(self, goal_theme: str) -> str:
        if goal_theme == "threshold":
            return "Threshold density is the main adaptation driver."
        if goal_theme == "endurance":
            return "Aerobic durability gets the most emphasis."
        if goal_theme == "race":
            return "Race-specific rhythm and sharpness stay front and center."
        return "The block stays balanced across quality, durability, and recovery."

    def _goal_trend_fragment(self, goal_theme: str, sport_theme: str) -> str:
        if goal_theme == "threshold":
            return "Threshold density is the main growth lever."
        if goal_theme == "endurance":
            return f"{self._sport_label(sport_theme).capitalize()} durability drives the block."
        if goal_theme == "race":
            return f"Specific {self._sport_label(sport_theme)} rhythm keeps race readiness high."
        return "The block holds a balanced mix of aerobic work and intensity."

    def _sport_theme(self, profile: AthleteProfile, signal_text: str) -> str:
        if self._contains_any(signal_text, ("triathlon", "duathlon", "70.3", "ironman")) or (
            self._contains_any(signal_text, SPORT_KEYWORDS["cycling"])
            and self._contains_any(signal_text, SPORT_KEYWORDS["running"])
            and self._contains_any(signal_text, SPORT_KEYWORDS["swim"])
        ):
            return "multisport"
        if self._contains_any(signal_text, SPORT_KEYWORDS["running"]):
            return "running"
        if self._contains_any(signal_text, SPORT_KEYWORDS["cycling"]) or profile.cycling_ftp_watts:
            return "cycling"
        return "generic"

    def _secondary_modalities(self, signal_text: str, sport_theme: str) -> list[str]:
        modalities: list[str] = []
        if sport_theme != "multisport" and self._contains_any(signal_text, SPORT_KEYWORDS["swim"]):
            modalities.append("swim")
        if sport_theme != "running" and self._contains_any(signal_text, SPORT_KEYWORDS["running"]):
            modalities.append("running")
        if self._contains_any(signal_text, SPORT_KEYWORDS["strength"]):
            modalities.append("strength")
        if self._contains_any(signal_text, SPORT_KEYWORDS["hike"]):
            modalities.append("hike")
        return modalities

    def _signal_text(self, profile: AthleteProfile, check_in: CheckInInput) -> str:
        return " ".join(
            [
                profile.user_id,
                " ".join(profile.goals),
                " ".join(profile.constraints),
                " ".join(profile.injuries_rehab),
                profile.notes or "",
                check_in.raw_text,
            ]
        ).lower()

    def _goal_theme(self, profile: AthleteProfile) -> str:
        goal_text = " ".join([*profile.goals, profile.notes or ""]).lower()
        if self._contains_any(goal_text, ("ftp", "threshold", "power", "repeatability", "tempo")):
            return "threshold"
        if self._contains_any(
            goal_text,
            ("endurance", "aerobic", "base", "durability", "long ride", "long run"),
        ):
            return "endurance"
        if self._contains_any(
            goal_text,
            ("race", "cx", "cyclocross", "criterium", "competition", "sharpness", "marathon"),
        ):
            return "race"
        return "balanced"

    def _threshold_focuses(self, sport_theme: str) -> tuple[str, str, str]:
        mapping = {
            "cycling": ("Threshold primer", "Threshold ladders", "Over/unders"),
            "running": ("Tempo opener", "Threshold intervals", "Lactate-shuttle intervals"),
            "multisport": (
                "Bike threshold primer",
                "Threshold run intervals",
                "Tempo brick session",
            ),
            "generic": ("Threshold primer", "Threshold intervals", "Alternating-threshold set"),
        }
        return mapping[sport_theme]

    def _endurance_focuses(self, sport_theme: str) -> tuple[str, str, str]:
        mapping = {
            "cycling": ("Aerobic endurance opener", "Long aerobic ride", "Aerobic endurance"),
            "running": ("Aerobic run opener", "Recovery jog or cross-train", "Aerobic run"),
            "multisport": ("Aerobic ride opener", "Long run off fresh legs", "Aerobic run or ride"),
            "generic": ("Aerobic opener", "Easy recovery session", "Aerobic endurance"),
        }
        return mapping[sport_theme]

    def _race_focuses(self, sport_theme: str, *, travel: bool) -> tuple[str, str, str]:
        mapping = {
            "cycling": (
                "Short power intervals",
                "Race simulation or hard group ride",
                "Tempo run substitution" if travel else "Skills ride or race drills",
            ),
            "running": (
                "Hill sprints",
                "Simulation workout or tune-up race",
                "Portable aerobic session" if travel else "Drills + strides",
            ),
            "multisport": (
                "Open-water or threshold swim",
                "Simulation brick",
                "Portable aerobic session" if travel else "Transition drills",
            ),
            "generic": (
                "Short power session",
                "Primary-sport simulation session",
                "Portable aerobic session" if travel else "Technique or coordination work",
            ),
        }
        return mapping[sport_theme]

    def _recovery_focus(self, sport_theme: str) -> str:
        return {
            "cycling": "Recovery spin + mobility",
            "running": "Recovery jog + mobility",
            "multisport": "Easy swim or spin + mobility",
            "generic": "Recovery session + mobility",
        }[sport_theme]

    def _portable_session_focus(self, sport_theme: str, *, rehab: bool) -> str:
        if rehab:
            return {
                "cycling": "Low-torque portable intervals",
                "running": "Low-impact portable cardio",
                "multisport": "Portable low-impact quality",
                "generic": "Portable low-risk quality",
            }[sport_theme]
        return {
            "cycling": "Portable tempo session",
            "running": "Portable run workout",
            "multisport": "Portable brick alternative",
            "generic": "Portable aerobic quality",
        }[sport_theme]

    def _travel_substitution_focus(self, sport_theme: str) -> str:
        return {
            "cycling": "Tempo run substitution",
            "running": "Portable aerobic session",
            "multisport": "Portable aerobic session",
            "generic": "Portable aerobic session",
        }[sport_theme]

    def _rehab_quality_focus(self, sport_theme: str) -> str:
        return {
            "cycling": "Low-torque interval set",
            "running": "Controlled run-walk quality",
            "multisport": "Low-impact threshold set",
            "generic": "Controlled quality session",
        }[sport_theme]

    def _sport_label(self, sport_theme: str) -> str:
        return {
            "cycling": "cycling",
            "running": "running",
            "multisport": "multisport",
            "generic": "endurance-sport",
        }[sport_theme]

    @staticmethod
    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _set_day(
        day_specs: dict[int, DaySpec],
        day_index: int,
        *,
        focus: str | None = None,
        note: str | None = None,
    ) -> None:
        spec = day_specs[day_index]
        if focus is not None:
            spec["focus"] = focus
        if note is not None:
            spec["notes"].append(note)
