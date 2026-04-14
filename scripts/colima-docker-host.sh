#!/bin/bash
# Exported by scripts that need Docker. Detects Colima and sets DOCKER_HOST so
# the Supabase CLI can reach the daemon without requiring a /var/run/docker.sock symlink.
COLIMA_SOCK="$HOME/.colima/default/docker.sock"
if [ -S "$COLIMA_SOCK" ]; then
  export DOCKER_HOST="unix://$COLIMA_SOCK"
fi
