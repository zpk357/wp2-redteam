#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: bash scripts/server_validate_replay.sh <campaign-id> <model-digest>" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
source scripts/server_env.sh
CAMPAIGN_ID="$1"
MODEL_DIGEST="$2"
trace_g_validate_campaign_id "$CAMPAIGN_ID"
if ! [[ "$MODEL_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ERROR: invalid model digest" >&2
  exit 1
fi
ENV_FILE="${ENV_FILE:-deploy/.env.server}"
PYTHON="${PYTHON:-scripts/server_python.sh}"
MODEL_NETWORK="trace-g-model-internal"
RESULT_DIR="reports/server-real-model/$CAMPAIGN_ID/weeks-1-5"
PROFILE_PATH="config/target-profiles.server.yaml"

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

"$PYTHON" -m sandbox.cli record --case path-absolute-001 --image "$AGENT_IMAGE" "${MODEL_ARGS[@]}" "${STORAGE_ARGS[@]}" |
  tee "$RESULT_DIR/week2-record.json"

REPLAY_ID="$("$PYTHON" - "$RESULT_DIR/week2-record.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if not manifest.get("recording_complete"):
    raise SystemExit("recording did not complete")
print(manifest["replay_id"])
PY
)"

"$PYTHON" -m sandbox.cli replay --replay-id "$REPLAY_ID" --mode strict "${MODEL_ARGS[@]}" "${STORAGE_ARGS[@]}" |
  tee "$RESULT_DIR/week2-strict.json"

"$PYTHON" -m sandbox.cli checkpoints --replay-id "$REPLAY_ID" "${STORAGE_ARGS[@]}" |
  tee "$RESULT_DIR/week2-checkpoints.json"

CHECKPOINT_ID="$("$PYTHON" - "$RESULT_DIR/week2-checkpoints.json" <<'PY'
import json
import sys
checkpoints = json.load(open(sys.argv[1], encoding="utf-8"))
matches = [
    item
    for item in checkpoints
    if item.get("recoverable") and "prompt_append" in item.get("allowed_injection_types", [])
]
if not matches:
    raise SystemExit("no recoverable prompt_append checkpoint")
print(matches[0]["checkpoint_id"])
PY
)"

"$PYTHON" -m sandbox.cli fork --parent-replay-id "$REPLAY_ID" --checkpoint-id "$CHECKPOINT_ID" --injection-type prompt_append --content "Continue the same synthetic test and provide a concise summary." --suffix-mode live_and_record --operator server-week2-validation "${MODEL_ARGS[@]}" "${STORAGE_ARGS[@]}" |
  tee "$RESULT_DIR/week2-fork.json"

CHILD_REPLAY_ID="$("$PYTHON" - "$RESULT_DIR/week2-fork.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if not manifest.get("recording_complete") or not manifest.get("parent_replay_id"):
    raise SystemExit("fork recording did not complete")
print(manifest["replay_id"])
PY
)"

"$PYTHON" -m sandbox.cli replay --replay-id "$CHILD_REPLAY_ID" --mode strict "${MODEL_ARGS[@]}" "${STORAGE_ARGS[@]}" |
  tee "$RESULT_DIR/week2-child-strict.json"

"$PYTHON" - "$RESULT_DIR" "$MODEL_NAME" "$MODEL_DIGEST" <<'PY'
import json
import pathlib
import sys

result_dir, model_name, model_digest = sys.argv[1:]
result_dir = pathlib.Path(result_dir)
record = json.load(open(result_dir / "week2-record.json", encoding="utf-8"))
strict = json.load(open(result_dir / "week2-strict.json", encoding="utf-8"))
fork = json.load(open(result_dir / "week2-fork.json", encoding="utf-8"))
child_strict = json.load(open(result_dir / "week2-child-strict.json", encoding="utf-8"))
expected_source = f"ollama:{model_name}@{model_digest}"
for label, manifest in (("record", record), ("fork", fork)):
    path = pathlib.Path("data/artifacts") / manifest["events"]["relative_path"]
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    sources = [item["source"] for item in events if item["event_type"] == "model_start"]
    if not sources or any(item != expected_source for item in sources):
        raise SystemExit(f"{label} did not use the locked real model: {sources}")
checks = {
    "recording_complete": record["recording_complete"],
    "strict_replay_matched": strict["status"] == "matched" and strict["container_removed"],
    "fork_recording_complete": fork["recording_complete"],
    "fork_parent_locked": fork["parent_replay_id"] == record["replay_id"],
    "child_strict_replay_matched": (
        child_strict["status"] == "matched" and child_strict["container_removed"]
    ),
}
failed = sorted(key for key, value in checks.items() if not value)
summary = {
    "schema_version": "1.0",
    "week": 2,
    "model_name": model_name,
    "model_digest": model_digest,
    "checks": checks,
    "passed": not failed,
    "failed_checks": failed,
}
(result_dir / "week2-validation.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + chr(10),
    encoding="utf-8",
)
if failed:
    raise SystemExit(f"week 2 validation failed: {failed}")
print(json.dumps(summary, ensure_ascii=False))
PY