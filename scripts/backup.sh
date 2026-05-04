#!/usr/bin/env bash
set -euo pipefail
mkdir -p data/backups
if [[ ! -f data/instance/hardlink_manager.db ]]; then
  echo "Database not found: data/instance/hardlink_manager.db"
  exit 1
fi
TS=$(date +%Y%m%d-%H%M%S)
cp data/instance/hardlink_manager.db "data/backups/hardlink_manager-${TS}.db"
ls -1t data/backups/hardlink_manager-*.db | tail -n +8 | xargs -r rm -f
printf 'Backup created: %s\n' "data/backups/hardlink_manager-${TS}.db"
