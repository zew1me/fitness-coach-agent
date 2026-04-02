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
    assert "cyclocross" in plan.summary.lower()
    assert "steady-to-rising" in plan.trend.lower()


def test_create_plan_compresses_for_fatigue_and_travel() -> None:
    service = PlannerService()
    profile = AthleteProfile(
        user_id="test-user",
        cycling_ftp_watts=219,
        goals=["Raise FTP for cyclocross"],
        constraints=["Wednesday travel"],
    )
    check_in = CheckInInput(
        user_id="test-user",
        raw_text="Feeling fatigued after travel with heavy legs.",
        image_count=1,
    )

    plan = service.create_plan(profile, check_in)

    assert plan.hours == 5.0
    assert "fatigued" in plan.summary.lower()
    assert "travel" in plan.summary.lower()
    assert "threshold" in plan.summary.lower()
    assert plan.days[1].focus == "Recovery spin + mobility"
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
    assert "aerobic" in plan.trend.lower()
    assert plan.days[0].focus == "Aerobic endurance opener"
    assert plan.days[6].focus == "Long aerobic ride"
    assert plan.days[13].focus == "Aerobic endurance"
    assert any("flare-ups" in day.notes for day in plan.days)
