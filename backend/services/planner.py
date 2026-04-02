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

BALANCED_PLAN = [
    ("Sweet Spot + endurance", "Open the block with quality rather than extra volume."),
    ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
    ("Threshold ladders", "Support cyclocross-specific repeatability."),
    ("Easy spin or rest", "Micro-recovery keeps the next hard day useful."),
    ("Power intervals", "Short high-quality work maintains CX specificity."),
    ("Race simulation or hard group ride", "Use the weekend anchor for specificity."),
    ("Long aerobic ride", "Preserve durability without adding junk intensity."),
    ("Mobility + core endurance", "Reset fatigue before the second week."),
    ("Sweet Spot progression", "Adjust duration before cutting intensity."),
    ("Easy endurance", "Rebound ATL if the previous sessions landed hard."),
    ("Over/unders", "Blend threshold control with race-like surges."),
    ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
    ("CX practice or run-ups", "Choose skills over extra gym fatigue."),
    ("Aerobic endurance", "Close the block with sustainable volume."),
]

THRESHOLD_PLAN = [
    ("Threshold primer", "Open with controlled work that builds FTP without heroics."),
    ("Mobility or yoga", "Use low-back rehab work instead of complete passivity."),
    ("Threshold ladders", "FTP-oriented progressions stay smooth and repeatable."),
    ("Easy spin or rest", "Micro-recovery keeps the next hard day useful."),
    ("Power intervals", "Short high-quality work maintains CX specificity."),
    ("Race simulation or hard group ride", "Use the weekend anchor for specificity."),
    ("Long aerobic ride", "Preserve durability without adding junk intensity."),
    ("Mobility + core endurance", "Reset fatigue before the second week."),
    ("Sweet Spot progression", "Adjust duration before cutting intensity."),
    ("Easy endurance", "Rebound ATL if the previous sessions landed hard."),
    ("Over/unders", "Blend threshold control with race-like surges."),
    ("Optional yoga or rest", "Travel or low-HRV days can stay restorative."),
    ("CX practice or run-ups", "Choose skills over extra gym fatigue."),
    ("Aerobic endurance", "Close the block with sustainable volume."),
]

ENDURANCE_PLAN = [
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
    ("CX practice with endurance", "Choose skills over extra gym fatigue."),
    ("Aerobic endurance", "Close the block with sustainable volume."),
]

RACE_PLAN = [
    ("CX opener", "Open the block with quality that supports race-day sharpness."),
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
    ("CX practice or run-ups", "Choose skills over extra gym fatigue."),
    ("Aerobic endurance", "Close the block with sustainable volume."),
]


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
        goal_theme = self._goal_theme(profile)
        signal_text = self._signal_text(profile, check_in)
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

        day_specs = self._build_day_specs(goal_theme)
        hours = 7.0

        if fatigue:
            self._apply_fatigue(day_specs)
            hours -= 1.25
        if travel:
            self._apply_travel(day_specs, rehab=rehab)
            hours -= 0.75
        if rehab:
            self._apply_rehab(day_specs, travel=travel)
            hours -= 0.5
        if image_signal:
            self._apply_image_recovery(day_specs)
            hours -= 0.25
        self._apply_goal_emphasis(day_specs, goal_theme, travel=travel)

        if not fatigue and goal_theme == "endurance":
            hours += 0.5
        elif not fatigue and goal_theme in {"threshold", "race"}:
            hours += 0.25

        summary = self._build_summary(
            goal_theme=goal_theme,
            fatigue=fatigue,
            travel=travel,
            rehab=rehab,
            image_signal=image_signal,
        )
        trend = self._build_trend(
            goal_theme=goal_theme,
            fatigue=fatigue,
            travel=travel,
            rehab=rehab,
            image_signal=image_signal,
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

    def _build_day_specs(self, goal_theme: str) -> dict[int, dict[str, list[str] | str]]:
        templates = {
            "threshold": THRESHOLD_PLAN,
            "endurance": ENDURANCE_PLAN,
            "race": RACE_PLAN,
        }.get(goal_theme, BALANCED_PLAN)
        return {
            index: {"focus": focus, "notes": [notes]}
            for index, (focus, notes) in enumerate(templates, start=1)
        }

    def _apply_fatigue(self, day_specs: dict[int, dict[str, list[str] | str]]) -> None:
        self._set_day(
            day_specs,
            2,
            focus="Recovery spin + mobility",
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
        day_specs: dict[int, dict[str, list[str] | str]],
        *,
        rehab: bool,
    ) -> None:
        self._set_day(
            day_specs,
            5,
            focus="Low-torque portable intervals" if rehab else "Portable tempo session",
            note="Keep the work compact and trainer-friendly while on the road.",
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
            focus="Tempo run substitution",
            note="Portable aerobic quality replaces bike-specific work during travel.",
        )

    def _apply_rehab(
        self,
        day_specs: dict[int, dict[str, list[str] | str]],
        *,
        travel: bool,
    ) -> None:
        self._set_day(
            day_specs,
            2,
            focus="Recovery spin + mobility" if travel else "Rehab circuit + mobility",
            note="Protect the injury with controlled movement and rehab work.",
        )
        self._set_day(
            day_specs,
            3,
            note="Avoid aggressive torque; keep cadence smooth and controlled.",
        )
        self._set_day(
            day_specs,
            5,
            focus="Low-torque portable intervals" if travel else "Low-torque interval set",
            note="Stay seated and smooth so the rehab issue stays quiet.",
        )
        self._set_day(
            day_specs,
            11,
            note="Keep threshold work controlled and low-torque.",
        )
        self._set_day(
            day_specs,
            13,
            note="Choose skills or aerobic work that avoids flare-ups.",
        )

    def _apply_image_recovery(self, day_specs: dict[int, dict[str, list[str] | str]]) -> None:
        self._set_day(
            day_specs,
            2,
            focus="Image-informed recovery day",
            note="Uploaded evidence suggests preserving readiness before stacking more intensity.",
        )
        self._set_day(
            day_specs,
            8,
            note="Use the second-week reset to respond to the visual recovery signal as well.",
        )

    def _apply_goal_emphasis(
        self,
        day_specs: dict[int, dict[str, list[str] | str]],
        goal_theme: str,
        *,
        travel: bool,
    ) -> None:
        if goal_theme == "threshold":
            self._set_day(
                day_specs,
                1,
                focus="Threshold primer",
                note="Start with controlled work that builds FTP without overreaching.",
            )
            self._set_day(
                day_specs,
                3,
                focus="Threshold ladders",
                note="The FTP goal gets the most attention here.",
            )
            self._set_day(
                day_specs,
                11,
                focus="Over/unders",
                note="Use race-like surges to convert threshold into repeatability.",
            )
        elif goal_theme == "endurance":
            self._set_day(
                day_specs,
                1,
                focus="Aerobic endurance opener",
                note="Bias the opening days toward durability and low stress.",
            )
            self._set_day(
                day_specs,
                7,
                focus="Long aerobic ride",
                note="Stretch the longest session to support the endurance goal.",
            )
            self._set_day(
                day_specs,
                14,
                focus="Aerobic endurance",
                note="Close the block with a volume-biased aerobic finish.",
            )
        elif goal_theme == "race":
            self._set_day(
                day_specs,
                5,
                focus="Short power intervals",
                note="Punchy work sharpens the race-day engine.",
            )
            self._set_day(
                day_specs,
                6,
                focus="Race simulation or hard group ride",
                note="Practice repeated surges and positioning.",
            )
            self._set_day(
                day_specs,
                13,
                focus="Tempo run substitution" if travel else "CX practice or run-ups",
                note="Skill work keeps race specificity high without junk volume.",
            )

    def _build_summary(
        self,
        *,
        goal_theme: str,
        fatigue: bool,
        travel: bool,
        rehab: bool,
        image_signal: bool,
    ) -> str:
        summary_parts = [
            "Adaptive 14-day plan built around cyclocross intensity and realistic recovery.",
        ]
        summary_parts.append(self._goal_summary_fragment(goal_theme))
        if fatigue:
            summary_parts.append(
                "Freshness is intentionally protected because the check-in reads as fatigued."
            )
        if travel:
            summary_parts.append("Midweek work is compressed so travel does not erase the block.")
        if rehab:
            summary_parts.append("Low-torque choices protect the rehab constraint.")
        if image_signal:
            summary_parts.append("Uploaded images push the block toward extra recovery protection.")
        return " ".join(summary_parts)

    def _build_trend(
        self,
        *,
        goal_theme: str,
        fatigue: bool,
        travel: bool,
        rehab: bool,
        image_signal: bool,
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
        trend_parts.append(self._goal_trend_fragment(goal_theme))
        return " ".join(trend_parts)

    def _goal_summary_fragment(self, goal_theme: str) -> str:
        if goal_theme == "threshold":
            return "FTP and threshold density are the main adaptation drivers."
        if goal_theme == "endurance":
            return "Aerobic durability gets the most emphasis."
        if goal_theme == "race":
            return "Race-specific surges and skills stay front and center."
        return "Cyclocross-specific work stays balanced across intensity and recovery."

    def _goal_trend_fragment(self, goal_theme: str) -> str:
        if goal_theme == "threshold":
            return "Threshold density is the main growth lever."
        if goal_theme == "endurance":
            return "Long aerobic anchors drive the block."
        if goal_theme == "race":
            return "Repeated surges and skills keep race specificity high."
        return "The block holds a balanced mix of aerobic and intensity work."

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
        if self._contains_any(goal_text, ("ftp", "threshold", "power", "repeatability")):
            return "threshold"
        if self._contains_any(
            goal_text,
            ("endurance", "aerobic", "base", "durability", "long ride"),
        ):
            return "endurance"
        if self._contains_any(
            goal_text,
            ("race", "cx", "cyclocross", "criterium", "competition"),
        ):
            return "race"
        return "balanced"

    @staticmethod
    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _set_day(
        day_specs: dict[int, dict[str, list[str] | str]],
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
