#!/bin/bash
set -e
source "$(dirname "$0")/colima-docker-host.sh"
supabase db reset
