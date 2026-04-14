#!/bin/bash
set -e
source "$(dirname "$0")/colima-docker-host.sh"

# Start Supabase if not already running
if ! supabase status > /dev/null 2>&1; then
  echo "Starting local Supabase..."
  supabase start
fi

# Local Supabase always uses these fixed demo credentials
LOCAL_SUPABASE_URL="http://127.0.0.1:54321"
LOCAL_ANON_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0"
LOCAL_SERVICE_ROLE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU"

NEXT_PUBLIC_SUPABASE_URL="$LOCAL_SUPABASE_URL" \
NEXT_PUBLIC_SUPABASE_ANON_KEY="$LOCAL_ANON_KEY" \
SUPABASE_URL="$LOCAL_SUPABASE_URL" \
SUPABASE_SERVICE_ROLE_KEY="$LOCAL_SERVICE_ROLE_KEY" \
APP_BASE_URL="http://localhost:3000" \
APP_ENV="development" \
APP_JWT_SECRET="super-secret-jwt-token-with-at-least-32-characters-long" \
exec next dev --port 3000
