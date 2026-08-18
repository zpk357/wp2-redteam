#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: bash scripts/server_validate_trace_workspace.sh <campaign-id> <model-digest>" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
source scripts/server_env.sh

CAMPAIGN_ID="$1"
MODEL_DIGEST="$2"
trace_g_validate_campaign_id "$CAMPAIGN_ID"
[[ "$MODEL_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "ERROR: invalid model digest" >&2
  exit 1
}

ENV_FILE="${ENV_FILE:-deploy/.env.server}"
PYTHON="${PYTHON:-scripts/server_python.sh}"
MODEL_NETWORK="trace-g-model-internal"
RESULT_DIR="reports/server-real-model/$CAMPAIGN_ID/trace-workspace"
trace_g_load_server_env "$ENV_FILE"
: "${MODEL_NAME:?MODEL_NAME is required}"
: "${AGENT_IMAGE:?AGENT_IMAGE is required}"
mkdir -p "$RESULT_DIR"

MODEL_ARGS=(
  --model-provider ollama
  --model-name "$MODEL_NAME"
  --model-digest "$MODEL_DIGEST"
  --ollama-endpoint http://ollama:11434
  --model-network "$MODEL_NETWORK"
)
STORAGE_ARGS=(
  --output-dir data/trajectories
  --artifact-dir data/artifacts
  --manifest-dir data/replays
)

"$PYTHON" scripts/warm_ollama_model.py \
  --endpoint http://ollama:11434 \
  --model "$MODEL_NAME" \
  --timeout 180 \
  --keep-alive 15m \
  > "$RESULT_DIR/model-warmup.json"

for variant in clean injected; do
  "$PYTHON" -m sandbox.cli run \
    --case "trace-workspace-${variant}-001" \
    --image "$AGENT_IMAGE" \
    --execution-backend trace_react_v2 \
    "${MODEL_ARGS[@]}" \
    --output-dir data/trajectories \
    | tee "$RESULT_DIR/${variant}-run.json"
done

"$PYTHON" -m sandbox.cli record \
  --case trace-workspace-injected-001 \
  --image "$AGENT_IMAGE" \
  --execution-backend trace_react_v2 \
  "${MODEL_ARGS[@]}" "${STORAGE_ARGS[@]}" \
  | tee "$RESULT_DIR/injected-record.json"

REPLAY_ID="$("$PYTHON" - "$RESULT_DIR/injected-record.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if not manifest.get("recording_complete"):
    raise SystemExit("TRACE workspace recording did not complete")
print(manifest["replay_id"])
PY
)"

"$PYTHON" -m sandbox.cli replay \
  --replay-id "$REPLAY_ID" \
  --mode strict \
  "${MODEL_ARGS[@]}" "${STORAGE_ARGS[@]}" \
  | tee "$RESULT_DIR/injected-strict.json"

"$PYTHON" scripts/validate_trace_workspace_results.py \
  --result-dir "$RESULT_DIR" \
  --repository-root . \
  --model-name "$MODEL_NAME" \
  --model-digest "$MODEL_DIGEST" \
  --output "$RESULT_DIR/validation.json"

echo "TRACE workspace Qwen validation artifacts: $RESULT_DIR"
