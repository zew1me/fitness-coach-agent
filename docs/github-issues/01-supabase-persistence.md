## Summary

Replace scaffold-only repository behavior with real Supabase-backed reads and writes.

## Current state

- `backend/repos/supabase_repo.py` now reads and upserts athlete profiles via Supabase.
- `POST /api/check-ins` now persists check-ins instead of returning a placeholder acceptance.
- `PUT /api/profile` now writes profile state through the repository layer.
- Initial SQL schema lives in `supabase/migrations/0001_initial_schema.sql`.

## Progress

- [x] Replace hardcoded profile reads with a Supabase-backed repository.
- [x] Persist check-ins and return stored records from the API.
- [x] Add an authenticated profile upsert endpoint.
- [x] Add missing-profile (`404`) and unconfigured-Supabase (`503`) handling.
- [x] Add initial schema SQL for `athlete_profiles` and `check_ins`.
- [x] Add repository and API test coverage for success and failure cases.
- [ ] Apply the migration in a real Supabase project.
- [ ] Decide whether generated plans should also be persisted.

## Acceptance criteria

- Profile reads come from Supabase instead of hardcoded defaults.
- Profile writes go through the API and repository layers.
- Check-ins are stored and linked to a user.
- Authenticated users cannot read or write another user's profile/check-ins.
- Tests cover happy paths plus missing-profile and missing-config failures.
