#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Please edit SECRET_KEY and path variables."
fi

mkdir -p data/instance data/backups

echo "Checking compose config..."
docker compose config >/dev/null

echo "Bootstrap done. Next: docker compose up -d"
