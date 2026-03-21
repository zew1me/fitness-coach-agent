from backend.models.planning import AthleteProfile


class SupabaseRepository:
    """Thin placeholder adapter for future Supabase-backed persistence."""

    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        return AthleteProfile(
            user_id=user_id,
            cycling_ftp_watts=219,
            goals=[
                "Maintain high training load",
                "Adapt 14-day plan around fatigue and constraints",
            ],
            constraints=["Cyclocross-specific focus", "Preserve recovery realism"],
            notes="Default scaffold profile until database persistence is wired.",
        )
