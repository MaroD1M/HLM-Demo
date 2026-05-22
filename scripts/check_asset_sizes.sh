#!/usr/bin/env bash
set -euo pipefail

# Guardrail for repository-friendly static assets.
# Default threshold: 2 MiB per file under static/.
MAX_BYTES=${ASSET_MAX_BYTES:-2097152}
SCAN_DIR=${ASSET_SCAN_DIR:-static}

if [[ ! -d "$SCAN_DIR" ]]; then
  echo "[asset-guard] scan dir not found: $SCAN_DIR"
  exit 0
fi

violations=0
while IFS= read -r -d '' f; do
  size=$(wc -c < "$f" | tr -d ' ')
  if [[ "$size" -gt "$MAX_BYTES" ]]; then
    mib=$(python3 - <<PY
s=$size
print(f"{s/1024/1024:.2f}")
PY
)
    echo "[asset-guard] too large: ${f} (${mib} MiB)"
    violations=$((violations + 1))
  fi
done < <(find "$SCAN_DIR" -type f -print0)

if [[ "$violations" -gt 0 ]]; then
  echo "[asset-guard] failed: ${violations} file(s) exceed ${MAX_BYTES} bytes"
  exit 1
fi

echo "[asset-guard] ok: all files under ${SCAN_DIR} <= ${MAX_BYTES} bytes"
