#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
source scripts/server_env.sh

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/server_export_results.sh <campaign-id>" >&2
  exit 2
fi

CAMPAIGN_ID="$1"
trace_g_validate_campaign_id "$CAMPAIGN_ID"
ENV_FILE="${ENV_FILE:-deploy/.env.server}"
RESULT_DIR="reports/server-real-model/$CAMPAIGN_ID"
ARCHIVE="reports/trace-g-$CAMPAIGN_ID-results.tar.gz"
OLLAMA_CONTAINER="trace-g-ollama"
PYTHON="${PYTHON:-scripts/server_python.sh}"
export TRACE_G_CONTROLLER_NETWORK=none

test -d "$RESULT_DIR" || {
  echo "ERROR: validation result directory does not exist: $RESULT_DIR" >&2
  exit 1
}
test -f "$RESULT_DIR/campaign-validation.json" || {
  echo "ERROR: campaign validation did not complete" >&2
  exit 1
}
test -f "$RESULT_DIR/learning/golden-set-candidate-manifest.json" || {
  echo "ERROR: learning dataset export did not complete" >&2
  exit 1
}
test -f "$RESULT_DIR/weeks-1-5-validation.json" || {
  echo "ERROR: Weeks 1-5 validation did not complete" >&2
  exit 1
}
test -x "$PYTHON" || {
  echo "ERROR: controller Python wrapper is missing" >&2
  exit 1
}
"$PYTHON" - "$RESULT_DIR/campaign-validation.json" "$RESULT_DIR/weeks-1-5-validation.json" <<'PY'
import json
import sys

summary = json.load(open(sys.argv[1], encoding="utf-8"))
weeks = json.load(open(sys.argv[2], encoding="utf-8"))
if not summary.get("passed"):
    raise SystemExit(f"campaign validation is not passing: {summary.get('failed_checks')}")
if not weeks.get("passed"):
    raise SystemExit(f"Weeks 1-5 validation is not passing: {weeks.get('failed_checks')}")
PY

mkdir -p "$RESULT_DIR/host"
if command -v nvidia-smi >/dev/null; then
  nvidia-smi > "$RESULT_DIR/host/nvidia-smi-final.txt" 2>&1 || true
else
  echo "nvidia-smi unavailable during export" > "$RESULT_DIR/host/nvidia-smi-final.txt"
fi
docker version > "$RESULT_DIR/host/docker-version.txt"
docker compose version > "$RESULT_DIR/host/docker-compose-version.txt"
uname -a > "$RESULT_DIR/host/uname.txt"
"$PYTHON" --version > "$RESULT_DIR/host/python-version.txt" 2>&1
"$PYTHON" -m pip freeze > "$RESULT_DIR/host/python-freeze.txt"
git rev-parse HEAD > "$RESULT_DIR/host/git-revision.txt" 2>/dev/null || true
git status --short > "$RESULT_DIR/host/git-status.txt" 2>/dev/null || true
docker image inspect trace-redteam-agent:server \
  > "$RESULT_DIR/host/agent-image-inspect.json"
docker image inspect trace-redteam-controller:server \
  > "$RESULT_DIR/host/controller-image-inspect.json"
if docker inspect "$OLLAMA_CONTAINER" >/dev/null 2>&1; then
  docker inspect "$OLLAMA_CONTAINER" > "$RESULT_DIR/host/ollama-container-inspect.json"
  docker logs "$OLLAMA_CONTAINER" > "$RESULT_DIR/host/ollama-container.log" 2>&1
fi
cp config/target-profiles.server.yaml "$RESULT_DIR/target-profiles.server.yaml"
cp config/risk-taxonomy.yaml "$RESULT_DIR/risk-taxonomy.yaml"
cp config/risk-scope-server-qwen3.yaml "$RESULT_DIR/risk-scope-server-qwen3.yaml"
cp config/mutation-operators.yaml "$RESULT_DIR/mutation-operators.yaml"
cp config/fuzzer-server.example.yaml "$RESULT_DIR/fuzzer-server-smoke.yaml"
cp config/fuzzer-server-data.example.yaml "$RESULT_DIR/fuzzer-server-data.yaml"
cp config/golden-label-schema-v1.yaml "$RESULT_DIR/golden-label-schema-v1.yaml"

"$PYTHON" scripts/stage_server_results.py \
  --campaign-id "$CAMPAIGN_ID" \
  --result-dir "$RESULT_DIR" \
  --output-dir "$RESULT_DIR/raw-data" |
  tee "$RESULT_DIR/raw-data-summary.json"

archive_paths=("$RESULT_DIR")

"$PYTHON" scripts/verify_server_results.py \
  --campaign-id "$CAMPAIGN_ID" \
  --result-dir "$RESULT_DIR" \
  --output "$RESULT_DIR/result-integrity.json" |
  tee "$RESULT_DIR/result-integrity-summary.json"

tar -czf "$ARCHIVE" "${archive_paths[@]}"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

if [[ -f "$ENV_FILE" ]]; then
  docker compose --env-file "$ENV_FILE" \
    -f deploy/docker-compose.server.yaml down
fi

echo "Result archive: $ROOT_DIR/$ARCHIVE"
echo "SHA256 file: $ROOT_DIR/$ARCHIVE.sha256"
echo "Copy both files back to Windows before deleting the instance."
