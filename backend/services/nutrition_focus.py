"""Derive a per-phase fueling focus for a training plan (issue #53).

The endurance coach already reasons about nutrition conversationally; this module
gives the *plan itself* a short, deterministic fueling emphasis per periodization
phase so the same guidance rides on ``training_plans.phases`` (surfaced to the
coach and the calendar) rather than being re-invented each turn.

Kept intentionally boring and reproducible — same phase focus + same dietary
restrictions in, same string out — to match the plan composer's contract.
"""

from __future__ import annotations

# Evidence-based, phase-appropriate fueling emphasis. Keyed by the periodization
# ``focus`` value carried on each ``PhasePlan`` (see backend/engine/periodization).
_FOCUS_BY_PHASE: dict[str, str] = {
    "base": (
        "Aerobic base: keep everyday carbohydrate moderate and protein steady "
        "(~1.6 g/kg/day). Use long sessions to rehearse relaxed, consistent fueling."
    ),
    "build": (
        "Rising intensity: fuel quality sessions with carbohydrate before and during, "
        "and prioritise post-session protein plus carbohydrate to support recovery."
    ),
    "peak": (
        "Race-specific sharpening: practise event-day fuelling at goal intake rates "
        "(carbohydrate g/hr) so nothing is new on race day."
    ),
    "taper": (
        "Freshness phase: hold carbohydrate intake up even as volume drops — "
        "under-fuelling now blunts the benefit of the taper."
    ),
    "recovery": (
        "Recovery week: emphasise nutrient-dense whole foods and protein; "
        "carbohydrate needs ease with the lighter load."
    ),
}

_DEFAULT_FOCUS = _FOCUS_BY_PHASE["base"]


def derive_nutrition_focus(focus: str, dietary_restrictions: list[str] | None = None) -> str:
    """Return a short fueling emphasis for a phase, tailored to the athlete.

    ``focus`` is the periodization phase focus (``base``/``build``/``peak``/
    ``taper``/``recovery``); unknown values fall back to the base emphasis.
    Non-empty ``dietary_restrictions`` are appended so the guidance stays
    compatible with the athlete's stated needs.
    """
    base = _FOCUS_BY_PHASE.get(focus.lower().strip(), _DEFAULT_FOCUS)
    cleaned = [r.strip() for r in (dietary_restrictions or []) if r and r.strip()]
    if cleaned:
        return f"{base} Keep choices compatible with: {', '.join(cleaned)}."
    return base
