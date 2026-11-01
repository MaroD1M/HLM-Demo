#!/usr/bin/env bash
set -euo pipefail

# The application no longer supports in-container git pull / dependency sync.
# Runtime configuration is managed by the app UI and the mounted instance directory.
export HLM_INSTANCE_PATH="${HLM_INSTANCE_PATH:-/app/instance}"

exec gunicorn \
  --workers "${GUNICORN_WORKERS:-1}" \
  --threads "${GUNICORN_THREADS:-8}" \
  --timeout "${GUNICORN_TIMEOUT:-300}" \
  --graceful-timeout 30 \
  --bind 0.0.0.0:5000 \
  --error-logfile - \
  wsgi:application
