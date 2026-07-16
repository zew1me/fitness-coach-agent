#!/bin/bash
set -e
source "$(dirname "$0")/colima-docker-host.sh"

# Load optional keys (OPENAI_API_KEY, R2_*, etc.) from .env.local if present.
# Local Supabase vars set below take precedence.
if [ -f .env.local ]; then
  set -a
  # shellcheck disable=SC1091
  source .env.local
  set +a
fi

# `vercel env pull` writes VERCEL_URL to .env.local. This command is always a
# local run, and the Intervals API-key bypass deliberately refuses Vercel
# requests, so do not let a pulled deployment variable disable it.
unset VERCEL_URL

# Start Supabase if not already running
if ! supabase status > /dev/null 2>&1; then
  echo "Starting local Supabase..."
  supabase start
fi

# Local Supabase always uses these fixed demo credentials
LOCAL_SUPABASE_URL="http://127.0.0.1:54321"
LOCAL_ANON_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0"
LOCAL_SERVICE_ROLE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU"
PYTHON_API_PORT=8001

# Start Python FastAPI backend in background
SUPABASE_URL="$LOCAL_SUPABASE_URL" \
SUPABASE_SERVICE_ROLE_KEY="$LOCAL_SERVICE_ROLE_KEY" \
APP_BASE_URL="http://localhost:3000" \
APP_ENV="development" \
APP_JWT_SECRET="super-secret-jwt-token-with-at-least-32-characters-long" \
OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
uv run uvicorn api.index:app --host 127.0.0.1 --port "$PYTHON_API_PORT" &
UVICORN_PID=$!
trap "kill $UVICORN_PID 2>/dev/null" EXIT

echo "Python backend started on port $PYTHON_API_PORT (pid $UVICORN_PID)"

PYTHON_API_URL="http://127.0.0.1:$PYTHON_API_PORT" \
NEXT_PUBLIC_SUPABASE_URL="$LOCAL_SUPABASE_URL" \
NEXT_PUBLIC_SUPABASE_ANON_KEY="$LOCAL_ANON_KEY" \
SUPABASE_URL="$LOCAL_SUPABASE_URL" \
SUPABASE_SERVICE_ROLE_KEY="$LOCAL_SERVICE_ROLE_KEY" \
APP_BASE_URL="http://localhost:3000" \
APP_ENV="development" \
APP_JWT_SECRET="super-secret-jwt-token-with-at-least-32-characters-long" \
next dev
