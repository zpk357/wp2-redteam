#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT_DIR"
source scripts/server_env.sh

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/server_export_incomplete.sh <campaign-id>" >&2
  exit 2
fi

CAMPAIGN_ID="$1"
trace_g_validate_campaign_id "$CAMPAIGN_ID"
RESULT_DIR="reports/server-real-model/$CAMPAIGN_ID"
ARCHIVE="reports/trace-g-$CAMPAIGN_ID-incomplete.tar.gz"
OLLAMA_CONTAINER="trace-g-ollama"

command -v tar >/dev/null
command -v sha256sum >/dev/null
test -d "$RESULT_DIR" || {
  echo "ERROR: no result directory exists for $CAMPAIGN_ID" >&2
  exit 1
}

cat > "$RESULT_DIR/INCOMPLETE-EXPORT-WARNING.txt" <<'EOF'
This is a diagnostic rescue archive from an incomplete or failed run.
It is not a passing Weeks 1-5 acceptance result and not a golden set.
Because a complete Campaign export was unavailable, global trajectory,
replay, and artifact stores are included. Use this only on a fresh,
dedicated rental instance and review the archive before sharing it.
EOF

if docker inspect "$OLLAMA_CONTAINER" >/dev/null 2>&1; then
  docker inspect "$OLLAMA_CONTAINER" > "$RESULT_DIR/ollama-rescue-inspect.json" 2>/dev/null || true
  docker logs "$OLLAMA_CONTAINER" > "$RESULT_DIR/ollama-rescue.log" 2>&1 || true
  docker stop "$OLLAMA_CONTAINER" > "$RESULT_DIR/ollama-rescue-stop.txt" 2>&1 || true
fi

archive_paths=("$RESULT_DIR")
for candidate in   "data/fuzzing/$CAMPAIGN_ID"   "data/coverage/$CAMPAIGN_ID"   "data/mutations/$CAMPAIGN_ID"   data/trajectories   data/replays   data/artifacts   config/risk-taxonomy.yaml   config/risk-scope-server-qwen3.yaml   config/mutation-operators.yaml   config/target-profiles.server.yaml; do
  if [[ -e "$candidate" ]]; then
    archive_paths+=("$candidate")
  fi
done

tar -czf "$ARCHIVE" "${archive_paths[@]}"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

echo "Incomplete diagnostic archive: $ROOT_DIR/$ARCHIVE"
echo "SHA256 file: $ROOT_DIR/$ARCHIVE.sha256"
echo "Copy both files back before releasing the instance."
