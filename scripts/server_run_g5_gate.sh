#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: bash scripts/server_run_g5_gate.sh <run-id> <gpu-device>" >&2
  exit 2
fi
RUN_ID="$1"
GPU_DEVICE="$2"
[[ "$RUN_ID" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] || {
  echo "ERROR: run-id must contain only lowercase letters, digits, and hyphens" >&2
  exit 2
}
[[ "$GPU_DEVICE" =~ ^[0-9]+$ ]] || {
  echo "ERROR: gpu-device must be a non-negative integer" >&2
  exit 2
}

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
PERSIST_ROOT="${TRACE_G_PERSIST_ROOT:-/opt/trace-g-g5}"
STATE_DIR="$PERSIST_ROOT/deploy-state"
LOCK="$ROOT_DIR/.trace-g/g5-server-kit-lock.json"
OUTPUT_ROOT="$ROOT_DIR/reports/server-g5/$RUN_ID"
ARCHIVE="$ROOT_DIR/reports/trace-g-$RUN_ID-g5-results.tar.gz"
FAILURE_ARCHIVE="$ROOT_DIR/reports/trace-g-$RUN_ID-g5-failed.tar.gz"

save_failure() {
  local status=$?
  trap - EXIT
  mkdir -p "$OUTPUT_ROOT"
  printf '{"schema_version":"1.0","gate":"5.G5","passed":false,"exit_code":%d}\n' \
    "$status" > "$OUTPUT_ROOT/failure.json"
  docker ps -a --filter label=trace-g.component=agent-sandbox \
    --format '{{.ID}} {{.Image}} {{.Status}}' > "$OUTPUT_ROOT/failure-container-residue.txt" || true
  docker volume ls --filter label=trace-g.component=workspace-volume \
    --format '{{.Name}}' > "$OUTPUT_ROOT/failure-volume-residue.txt" || true
  tar -czf "$FAILURE_ARCHIVE" -C "$ROOT_DIR/reports/server-g5" "$RUN_ID" || true
  if [[ -f "$FAILURE_ARCHIVE" ]]; then
    sha256sum "$FAILURE_ARCHIVE" > "$FAILURE_ARCHIVE.sha256" || true
  fi
  echo "ERROR: G5 failed; preserve the *-g5-failed archive and do not treat it as passing." >&2
  exit "$status"
}

cd "$ROOT_DIR"
test -f "$LOCK" || { echo "ERROR: G5 lock is missing" >&2; exit 1; }
python3 - "$STATE_DIR/g5-stage.json" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
if state.get("gate") != "5.G5" or state.get("status") != "ready":
    raise SystemExit("G5 server staging is not ready")
PY
test ! -e "$OUTPUT_ROOT" || {
  echo "ERROR: output already exists; preserve it and choose a new run-id" >&2
  exit 1
}
test ! -e "$ARCHIVE" && test ! -e "$FAILURE_ARCHIVE" || {
  echo "ERROR: result archive already exists; preserve it and choose a new run-id" >&2
  exit 1
}
trap save_failure EXIT

AGENT_IMAGE="$(python3 - "$LOCK" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["agent_image"]["reference"])
PY
)"
AGENT_IMAGE_ID="$(python3 - "$LOCK" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["agent_image"]["image_id"])
PY
)"
MODEL_DIGEST="$(python3 - "$LOCK" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["model_digest"])
PY
)"

export TRACE_G_CONTROLLER_NETWORK=none
scripts/server_python.sh scripts/run_g4_local_acceptance.py \
  --gate 5.G5 \
  --run-id "$RUN_ID" \
  --output-root "$OUTPUT_ROOT" \
  --image "$AGENT_IMAGE" \
  --expected-image-id "$AGENT_IMAGE_ID" \
  --expected-model-digest "$MODEL_DIGEST" \
  --gpu-device "$GPU_DEVICE"

cp "$LOCK" "$OUTPUT_ROOT/g5-server-kit-lock.json"
nvidia-smi --id="$GPU_DEVICE" \
  --query-gpu=index,name,uuid,memory.total,driver_version \
  --format=csv,noheader,nounits > "$OUTPUT_ROOT/gpu-evidence.txt"
scripts/server_python.sh scripts/collect_g5_host_evidence.py \
  --lock "$LOCK" \
  --gpu-device "$GPU_DEVICE" \
  --gpu-evidence "$OUTPUT_ROOT/gpu-evidence.txt" \
  --output "$OUTPUT_ROOT/host-evidence.json"
scripts/server_python.sh scripts/validate_g5_server_results.py \
  --result-root "$OUTPUT_ROOT" \
  --lock "$LOCK" \
  --write-integrity \
  --output "$OUTPUT_ROOT/validation.json"

tar -czf "$ARCHIVE" -C "$ROOT_DIR/reports/server-g5" "$RUN_ID"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
trap - EXIT
echo "G5 result archive: $ARCHIVE"
echo "G5 result digest: $ARCHIVE.sha256"
echo "Copy both files back before releasing the server. This is the only passing archive."
