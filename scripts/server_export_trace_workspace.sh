#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT_DIR"
source scripts/server_env.sh

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/server_export_trace_workspace.sh <campaign-id>" >&2
  exit 2
fi

CAMPAIGN_ID="$1"
trace_g_validate_campaign_id "$CAMPAIGN_ID"
ENV_FILE="${ENV_FILE:-deploy/.env.server}"
RESULT_DIR="reports/server-real-model/$CAMPAIGN_ID"
TRACE_RESULT_DIR="$RESULT_DIR/trace-workspace"
STAGED_DIR="$TRACE_RESULT_DIR/trace-workspace-data"
ARCHIVE="reports/trace-g-$CAMPAIGN_ID-trace-workspace-results.tar.gz"
OLLAMA_CONTAINER="trace-g-ollama"
PYTHON="${PYTHON:-scripts/server_python.sh}"
export TRACE_G_CONTROLLER_NETWORK=none

test -f "$TRACE_RESULT_DIR/validation.json" || {
  echo "ERROR: TRACE workspace validation result is missing" >&2
  exit 1
}
test -x "$PYTHON" || {
  echo "ERROR: controller Python wrapper is missing" >&2
  exit 1
}

"$PYTHON" - "$TRACE_RESULT_DIR/validation.json" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
if summary.get("passed") is not True:
    raise SystemExit(f"TRACE workspace validation is not passing: {summary.get('failed_checks')}")
PY

"$PYTHON" scripts/stage_trace_workspace_results.py \
  --campaign-id "$CAMPAIGN_ID" \
  --result-dir "$TRACE_RESULT_DIR" \
  --repository-root . \
  --output-dir "$STAGED_DIR" |
  tee "$TRACE_RESULT_DIR/staging-summary.json"

mkdir -p "$TRACE_RESULT_DIR/locks" "$TRACE_RESULT_DIR/host"
for lock in \
  .trace-g/image-locks.json \
  .trace-g/image-verification.json \
  .trace-g/model-lock.json \
  .trace-g/source-archive.sha256 \
  config/target-profiles.server.yaml; do
  if [[ -f "$lock" ]]; then
    cp "$lock" "$TRACE_RESULT_DIR/locks/$(basename "$lock")"
  fi
done
docker version > "$TRACE_RESULT_DIR/host/docker-version.txt"
docker compose version > "$TRACE_RESULT_DIR/host/docker-compose-version.txt"
uname -a > "$TRACE_RESULT_DIR/host/uname.txt"
"$PYTHON" --version > "$TRACE_RESULT_DIR/host/python-version.txt" 2>&1
if command -v nvidia-smi >/dev/null; then
  nvidia-smi > "$TRACE_RESULT_DIR/host/nvidia-smi-final.txt" 2>&1 || true
fi
if docker inspect "$OLLAMA_CONTAINER" >/dev/null 2>&1; then
  docker inspect "$OLLAMA_CONTAINER" > "$TRACE_RESULT_DIR/host/ollama-container-inspect.json"
  docker logs "$OLLAMA_CONTAINER" > "$TRACE_RESULT_DIR/host/ollama-container.log" 2>&1
fi

"$PYTHON" - "$TRACE_RESULT_DIR" "$TRACE_RESULT_DIR/result-integrity.json" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
output = pathlib.Path(sys.argv[2]).resolve()
files = {}
for path in sorted(root.rglob("*")):
    if path.is_file() and path.resolve() != output:
        raw = path.read_bytes()
        files[path.relative_to(root).as_posix()] = {
            "bytes": len(raw),
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }
payload = {
    "schema_version": "1.0",
    "file_count": len(files),
    "total_bytes": sum(item["bytes"] for item in files.values()),
    "files": files,
}
output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

tar -czf "$ARCHIVE" "$RESULT_DIR"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

if [[ -f "$ENV_FILE" ]]; then
  docker compose --env-file "$ENV_FILE" \
    -f deploy/docker-compose.server.yaml down
fi

echo "TRACE workspace result archive: $ROOT_DIR/$ARCHIVE"
echo "SHA256 file: $ROOT_DIR/$ARCHIVE.sha256"
echo "Copy both files back before releasing the server."
