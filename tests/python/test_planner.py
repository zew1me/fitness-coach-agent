from backend.models.planning import AthleteProfile, CheckInInput
from backend.services.planner import PlannerService


def test_create_plan_returns_14_days() -> None:
    service = PlannerService()
    profile = AthleteProfile(user_id="test-user", cycling_ftp_watts=219)
    check_in = CheckInInput(
        user_id="test-user",
        raw_text="Felt good after an easy week.",
        image_count=0,
    )

    plan = service.create_plan(profile, check_in)

    assert len(plan.days) == 14
    assert plan.user_id == "test-user"
    assert "cycling" in plan.summary.lower()
    assert "steady-to-rising" in plan.trend.lower()


def test_create_plan_compresses_for_fatigue_and_travel() -> None:
    service = PlannerService()
    profile = AthleteProfile(
        user_id="test-user",
        cycling_ftp_watts=219,
        goals=["Raise FTP for road racing"],
        constraints=["Wednesday travel"],
    )
    check_in = CheckInInput(
        user_id="test-user",
        raw_text="Feeling fatigued after travel with heavy legs.",
        image_count=1,
    )

    plan = service.create_plan(profile, check_in)

    assert plan.hours == 4.8
    assert "fatigued" in plan.summary.lower()
    assert "travel" in plan.summary.lower()
    assert "images" in plan.summary.lower()
    assert "threshold" in plan.summary.lower()
    assert plan.days[1].focus == "Image-informed recovery day"
    assert plan.days[4].focus == "Portable tempo session"
    assert plan.days[12].focus == "Tempo run substitution"


def test_create_plan_protects_rehab_and_emphasizes_endurance_goal() -> None:
    service = PlannerService()
    profile = AthleteProfile(
        user_id="test-user",
        goals=["Build aerobic endurance"],
        injuries_rehab=["Achilles rehab"],
        notes="Keep the load sustainable.",
    )
    check_in = CheckInInput(user_id="test-user", raw_text="Normal energy today.", image_count=0)

    plan = service.create_plan(profile, check_in)

    assert plan.hours == 7.0
    assert "durability" in plan.summary.lower()
    assert "endurance-sport durability" in plan.trend.lower()
    assert plan.days[0].focus == "Aerobic opener"
    assert plan.days[6].focus == "Easy recovery session"
    assert plan.days[13].focus == "Aerobic endurance"
    assert any("flare-ups" in day.notes for day in plan.days)


def test_create_plan_uses_image_signal_without_other_flags() -> None:
    service = PlannerService()
    profile = AthleteProfile(user_id="test-user", goals=["Race sharpness"])
    check_in = CheckInInput(user_id="test-user", raw_text="Feeling decent.", image_count=2)

    plan = service.create_plan(profile, check_in)

    assert plan.hours == 7.0
    assert plan.days[1].focus == "Image-informed recovery day"
    assert "uploaded images" in plan.summary.lower()


def test_create_plan_builds_running_specific_schedule() -> None:
    service = PlannerService()
    profile = AthleteProfile(
        user_id="runner-1",
        age=37,
        goals=["Half marathon race sharpness"],
        notes="Primary sport is running with hill work and strides.",
    )
    check_in = CheckInInput(
        user_id="runner-1",
        raw_text="Running feels good and I want a tune-up race soon.",
        image_count=0,
    )

    plan = service.create_plan(profile, check_in)

    assert "running" in plan.summary.lower()
    assert "race-specific rhythm" in plan.summary.lower()
    assert plan.days[0].focus == "Race-pace opener"
    assert plan.days[4].focus == "Hill sprints"
    assert plan.days[5].focus == "Simulation workout or tune-up race"
    assert plan.days[12].focus == "Drills + strides"


def test_create_plan_builds_multisport_base_and_keeps_supporting_modalities() -> None:
    service = PlannerService()
    profile = AthleteProfile(
        user_id="tri-1",
        age=46,
        goals=["Build aerobic endurance for triathlon"],
        notes=(
            "Primary sport is triathlon with swimming, cycling, running, and light strength work."
        ),
    )
    check_in = CheckInInput(
        user_id="tri-1",
        raw_text="Training is steady and I can handle a balanced week.",
        image_count=0,
    )

    plan = service.create_plan(profile, check_in)

    assert plan.hours == 7.2
    assert "multisport" in plan.summary.lower()
    assert "strength" in plan.summary.lower()
    assert "masters-friendly" in plan.summary.lower()
    assert "tsb is given a little more room" in plan.trend.lower()
    assert plan.days[0].focus == "Aerobic ride opener"
    assert plan.days[1].focus == "Technique swim + mobility"
    assert plan.days[6].focus == "Long run off fresh legs"
    assert any("strength-maintenance" in day.notes for day in plan.days)
