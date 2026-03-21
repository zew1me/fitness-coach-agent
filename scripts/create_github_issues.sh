#!/usr/bin/env bash

set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required." >&2
  exit 1
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "Git remote 'origin' is not configured." >&2
  exit 1
fi

gh auth status >/dev/null 2>&1 || {
  echo "GitHub auth is not configured. Run: gh auth login -h github.com" >&2
  exit 1
}

declare -a TITLES=(
  "Wire Supabase persistence for athlete profiles and check-ins"
  "Replace placeholder OAuth flow with durable consent and token handling"
  "Make PlannerService generate materially adaptive 14-day plans"
  "Build the end-to-end user flow in the Next.js app"
  "Expand automated coverage for API, auth, and planner behavior"
)

declare -a BODIES=(
  "docs/github-issues/01-supabase-persistence.md"
  "docs/github-issues/02-oauth-durable-consent.md"
  "docs/github-issues/03-adaptive-planner.md"
  "docs/github-issues/04-frontend-user-flow.md"
  "docs/github-issues/05-test-coverage.md"
)

for i in "${!TITLES[@]}"; do
  echo "Creating issue: ${TITLES[$i]}"
  gh issue create --title "${TITLES[$i]}" --body-file "${BODIES[$i]}"
done
