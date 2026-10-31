#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${HLM_DB_PATH:-$ROOT_DIR/data/instance/hardlink_manager.db}"
BACKUP_DIR="${HLM_BACKUP_DIR:-$ROOT_DIR/data/backups}"
mkdir -p "$BACKUP_DIR"
if [[ ! -f "$DB_PATH" ]]; then
  echo "Database not found: $DB_PATH"
  exit 1
fi

TS=$(date +%Y%m%d-%H%M%S)
OUT="$BACKUP_DIR/hardlink_manager-${TS}.db"

if command -v python3 >/dev/null 2>&1; then
  python3 - "$DB_PATH" "$OUT" <<'PY'
import datetime
import hashlib
import json
import pathlib
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()

backup = pathlib.Path(sys.argv[2])
digest = hashlib.sha256(backup.read_bytes()).hexdigest()
manifest = {
    '__meta': {
        'format_version': 1,
        'kind': 'sqlite_backup',
        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    },
    'backup': {
        'source_path': sys.argv[1],
        'backup_path': str(backup),
        'backup_name': backup.name,
        'backup_size': backup.stat().st_size,
        'backup_sha256': digest,
        'keep_last': 7,
    },
}
backup.with_suffix(backup.suffix + '.json').write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
)
PY
elif command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" ".backup '$OUT'"
else
  echo "python3 or sqlite3 is required for a WAL-safe backup"
  exit 1
fi
chmod 600 "$OUT" 2>/dev/null || true
chmod 600 "$OUT.json" 2>/dev/null || true
while IFS= read -r old_backup; do
  rm -f "$old_backup" "$old_backup.json"
done < <(ls -1t "$BACKUP_DIR"/hardlink_manager-*.db | tail -n +8)
printf 'Backup created: %s\n' "$OUT"
