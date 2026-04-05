# GitHub Issues Progress

GitHub issue filing is currently blocked locally because this repo has no configured remote and
`gh auth status` reports an invalid token. The issue titles and bodies were drafted in the working
session, and this file tracks implementation progress until those issues can be created upstream.

## Wire Supabase persistence for athlete profiles and check-ins

Status: in progress

Completed:
- Replaced the hardcoded profile repository path with a Supabase-backed adapter.
- Added an authenticated profile upsert API path backed by Supabase.
- Added typed check-in persistence and API responses for stored check-ins.
- Added `404` handling for missing athlete profiles.
- Added `503` handling when Supabase credentials are not configured.
- Added API-side user scoping so authenticated users cannot read or write other users' profile/check-in resources.
- Added initial schema SQL for `athlete_profiles` and `check_ins`.
- Added Python tests for repository behavior and protected API paths.

Remaining:
- Apply the migration in a real Supabase project.
- Decide whether plan generation should also persist generated plans or derive from recent check-ins.

## Replace placeholder OAuth flow with durable consent and token handling

Status: not started

## Make PlannerService generate materially adaptive 14-day plans

Status: not started

## Build the end-to-end user flow in the Next.js app

Status: not started

## Expand automated coverage for API, auth, and planner behavior

Status: partially started

Completed:
- Added API tests for protected-route auth enforcement.
- Added repository and check-in API coverage around persistence.

## Establish product branding, paired theme system, and icon direction

Status: drafted and implemented locally

Completed:
- Drafted the GitHub issue body in `docs/github-issues/06-branding-theme-system.md`.
- Added paired light and dark theme tokens with persisted `Light / Dark / System` selection.
- Updated the app shell, homepage, login, consent, and profile surfaces to the new brand palette.
- Added three simple mountain-based brand mark candidates under `public/brand/`.
- Installed the missing `@supabase/ssr` package so lint, typecheck, and tests run cleanly in this worktree.

Remaining:
- Create the upstream GitHub issue once `gh auth login -h github.com` has been refreshed.
- Choose one of the three initial icon directions and iterate on stroke / snow accent details.
