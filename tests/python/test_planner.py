from backend.models.planning import AthleteProfile, CheckInInput
from backend.services.planner import PlannerService


def test_create_plan_returns_14_days() -> None:
    service = PlannerService()
    profile = AthleteProfile(user_id="test-user", cycling_ftp_watts=219)
    check_in = CheckInInput(user_id="test-user", raw_text="Felt good after travel.", image_count=1)

    plan = service.create_plan(profile, check_in)

    assert len(plan.days) == 14
    assert plan.days[12].focus == "Tempo run substitution"
