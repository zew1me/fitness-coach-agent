#!/usr/bin/env bash

set -euo pipefail

PROJECT_REF="${SUPABASE_PROJECT_REF:-}"
ACCESS_TOKEN="${SUPABASE_ACCESS_TOKEN:-}"
SQL_FILE="${1:-supabase/migrations/0001_initial_schema.sql}"

if [[ -z "${PROJECT_REF}" ]]; then
  echo "SUPABASE_PROJECT_REF is required." >&2
  exit 1
fi

if [[ -z "${ACCESS_TOKEN}" ]]; then
  echo "SUPABASE_ACCESS_TOKEN is required." >&2
  exit 1
fi

if [[ ! -f "${SQL_FILE}" ]]; then
  echo "SQL file not found: ${SQL_FILE}" >&2
  exit 1
fi

QUERY_JSON="$(jq -Rs . < "${SQL_FILE}")"

curl --fail-with-body --silent --show-error \
  -X POST "https://api.supabase.com/v1/projects/${PROJECT_REF}/database/query" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"query\":${QUERY_JSON},\"read_only\":false}"
