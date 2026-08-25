#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <run|resume> <campaign-id> <target-generation> <gpu-device>" >&2
  exit 2
fi
MODE="$1"; CAMPAIGN_ID="$2"; TARGET="$3"; GPU_DEVICE="$4"
[[ "$MODE" == run || "$MODE" == resume ]] || { echo "ERROR: mode must be run or resume" >&2; exit 2; }
[[ "$CAMPAIGN_ID" =~ ^[a-z0-9][a-z0-9.-]{0,63}$ ]] || { echo "ERROR: invalid campaign-id" >&2; exit 2; }
[[ "$TARGET" =~ ^(2|3|5|10|20|30|50)$ ]] || { echo "ERROR: target must be 2, 3, 5, 10, 20, 30, or 50" >&2; exit 2; }
[[ "$GPU_DEVICE" =~ ^[0-9]+$ ]] || { echo "ERROR: invalid GPU device" >&2; exit 2; }
[[ "$MODE" == resume || "$TARGET" == 2 ]] || { echo "ERROR: a new Campaign must start with the 2-generation gate" >&2; exit 2; }

PERSIST_ROOT="${TRACE_G_PERSIST_ROOT:-/opt/trace-g-office-v2-step6}"
PROJECT_DIR="$PERSIST_ROOT/wp2-redteam"
CAMPAIGN_ROOT="$PROJECT_DIR/.trace-g-data/$CAMPAIGN_ID"
RESULT_ROOT="$PROJECT_DIR/reports/server-stage6/$CAMPAIGN_ID"
DB="$CAMPAIGN_ROOT/campaign.sqlite3"
PREFLIGHT="$PROJECT_DIR/reports/server-stage6/preflight/stage6-preflight.json"
GPU_RESIDENCY="$PROJECT_DIR/reports/server-stage6/preflight/stage6-gpu-residency.json"
cd "$PROJECT_DIR"
command -v flock >/dev/null || { echo "ERROR: flock is required" >&2; exit 1; }
mkdir -p "$PERSIST_ROOT/locks"
exec 9>"$PERSIST_ROOT/locks/gpu-$GPU_DEVICE.lock"
flock -n 9 || { echo "ERROR: Stage 6 GPU $GPU_DEVICE is already leased" >&2; exit 1; }
mapfile -t LOCK_IMAGES < <(python3 - .trace-g/stage6-model-lock.json <<'PY'
import json
import sys

lock = json.load(open(sys.argv[1], encoding="utf-8"))
roles = {item["role"]: item for item in lock["roles"]}
print(lock["controller_image_reference"])
print(roles["agent"]["image_reference"])
print(roles["mutator"]["image_reference"])
PY
)
[[ "${#LOCK_IMAGES[@]}" -eq 3 ]] || { echo "ERROR: incomplete Stage 6 image lock" >&2; exit 1; }
CONTROLLER_IMAGE="${LOCK_IMAGES[0]}"
AGENT_IMAGE="${LOCK_IMAGES[1]}"
MUTATOR_IMAGE="${LOCK_IMAGES[2]}"
controller() {
  local work_item_id="$1"
  shift
  TRACE_G_CONTROLLER_IMAGE="$CONTROLLER_IMAGE" TRACE_G_CONTROLLER_NETWORK=none \
    TRACE_G_CAMPAIGN_ID="$CAMPAIGN_ID" TRACE_G_WORK_ITEM_ID="$work_item_id" \
    TRACE_G_ATTEMPT=1 scripts/server_python.sh "$@"
}
controller controller.verify-source \
  scripts/verify_office_v2_stage6_install.py verify \
  --root "$PROJECT_DIR" --identity .trace-g/stage6-source-tree.json
controller controller.verify-chain \
  scripts/verify_office_v2_stage6_install.py chain \
  --model-lock .trace-g/stage6-model-lock.json \
  --repair-plan .trace-g/stage6-repair-plan.json \
  --receipt .trace-g/stage6-repair-application.json \
  --stage-record .trace-g/stage6-stage.json --root-stage ../stage.json
test -f "$PREFLIGHT" || { echo "ERROR: preflight missing" >&2; exit 1; }
test -f "$GPU_RESIDENCY" || { echo "ERROR: GPU residency evidence missing" >&2; exit 1; }
python3 - "$PREFLIGHT" "$GPU_RESIDENCY" .trace-g/stage6-model-lock.json <<'PY'
import json
import sys

preflight = json.load(open(sys.argv[1], encoding="utf-8"))
gpu = json.load(open(sys.argv[2], encoding="utf-8"))
lock = json.load(open(sys.argv[3], encoding="utf-8"))
roles = {item["role"]: item for item in lock["roles"]}
if (
    preflight.get("schema_version") != "office-v2-stage6-preflight-v1"
    or preflight.get("passed") is not True
    or preflight.get("model_name") != lock.get("model_name")
    or preflight.get("model_digest") != lock.get("manifest_digest")
    or preflight.get("model_lock_digest") != lock.get("lock_digest")
    or preflight.get("controller_image_reference") != lock.get("controller_image_reference")
    or preflight.get("controller_image_id") != lock.get("controller_image_id")
    or preflight.get("agent_image_reference") != roles["agent"].get("image_reference")
    or preflight.get("agent_image_id") != roles["agent"].get("image_id")
    or preflight.get("mutator_image_reference") != roles["mutator"].get("image_reference")
    or preflight.get("mutator_image_id") != roles["mutator"].get("image_id")
    or preflight.get("mutator_completed_before_agent") is not True
    or preflight.get("agent_successful_tool_exchange_count", 0) < 1
    or preflight.get("agent_model_decision_count", 0) < 2
    or preflight.get("agent_post_tool_decision_proved") is not True
):
    raise SystemExit("ERROR: preflight identity does not match the active model lock")
full_residency = gpu.get("full_residency", {})
if (
    gpu.get("passed") is not True
    or full_residency.get("agent") is not True
    or full_residency.get("mutator") is not True
    or gpu.get("residual_observed_model_process_pids")
):
    raise SystemExit("ERROR: GPU residency evidence is incomplete")
PY
mkdir -p "$CAMPAIGN_ROOT" "$RESULT_ROOT"
RUN_ATTEMPT_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"

archive_campaign() {
  local outcome="$1"
  local suffix="complete"
  [[ "$outcome" == failure ]] && suffix="failed"
  controller "controller.archive-$outcome-$RUN_ATTEMPT_ID" \
    scripts/audit_office_v2_stage6_campaign.py archive \
    --campaign-id "$CAMPAIGN_ID" --outcome "$outcome" \
    --campaign-root "$CAMPAIGN_ROOT" --result-root "$RESULT_ROOT" \
    --model-lock .trace-g/stage6-model-lock.json \
    --bootstrap .trace-g/stage6-bootstrap.json --preflight "$PREFLIGHT" \
    --repair-plan .trace-g/stage6-repair-plan.json \
    --repair-receipt .trace-g/stage6-repair-application.json \
    --stage-record .trace-g/stage6-stage.json \
    --source-tree-identity .trace-g/stage6-source-tree.json \
    --server-host "$PROJECT_DIR/reports/server-stage6/preflight/stage6-server-host.json" \
    --gpu-residency "$GPU_RESIDENCY" \
    --output "$PROJECT_DIR/reports/server-stage6/${CAMPAIGN_ID}-to-${TARGET}-${suffix}-${RUN_ATTEMPT_ID}.tar.gz"
}

archive_failure() {
  local status=$?
  trap - EXIT
  printf '{"schema_version":"office-v2-stage6-failure-v1","exit_code":%d}\n' "$status" > "$RESULT_ROOT/failure.json"
  docker ps -a --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" \
    --format '{{.ID}} {{.Image}} {{.Status}}' > "$RESULT_ROOT/container-residue.txt" || true
  docker volume ls --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" \
    --format '{{.Name}}' > "$RESULT_ROOT/volume-residue.txt" || true
  archive_campaign failure || true
  exit "$status"
}
trap archive_failure EXIT

if [[ "$MODE" == run ]]; then
  test ! -e "$DB" || { echo "ERROR: run requires a new Campaign" >&2; exit 1; }
  CLI_COMMAND=real-run
else
  test -f "$DB" || { echo "ERROR: resume requires an existing Campaign" >&2; exit 1; }
  CLI_COMMAND=real-resume
fi

append_progress() {
  local progress_mode="$1" requested_target="$2" report_path="$3"
  python3 - "$CAMPAIGN_ID" "$progress_mode" "$requested_target" "$report_path" \
    "$RESULT_ROOT/stage6-campaign-progress.jsonl" "$RUN_ATTEMPT_ID" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

report = json.load(open(sys.argv[4], encoding="utf-8"))
payload = json.dumps({
    "schema_version": "office-v2-stage6-progress-v1",
    "campaign_id": sys.argv[1],
    "mode": sys.argv[2],
    "requested_target": int(sys.argv[3]),
    "observed_generation": report["generation_index"],
    "completion_status": report["completion_status"],
    "report_digest": report["report_digest"],
    "run_attempt_id": sys.argv[6],
    "recorded_at": datetime.now(timezone.utc).isoformat(),
}, sort_keys=True).encode() + b"\n"
descriptor = os.open(sys.argv[5], os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
try:
    os.write(descriptor, payload)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
}

if [[ "$MODE" == run ]]; then
  controller controller.run-to-1 -m sandbox.fuzzer.v2_cli real-run \
    --db "$DB" --campaign-id "$CAMPAIGN_ID" \
    --bootstrap .trace-g/stage6-bootstrap.json \
    --model-lock .trace-g/stage6-model-lock.json \
    --agent-image "$AGENT_IMAGE" --mutator-image "$MUTATOR_IMAGE" \
    --data-root "$CAMPAIGN_ROOT" --generations 1 --gpu-device "$GPU_DEVICE" \
    --progress-dir "$RESULT_ROOT/generation-snapshots" \
    > "$RESULT_ROOT/run-to-1.json" 2> "$RESULT_ROOT/run-to-1.stderr.log"
  controller controller.report-to-1 -m sandbox.fuzzer.v2_cli report \
    --db "$DB" --campaign-id "$CAMPAIGN_ID" \
    --output "$RESULT_ROOT/campaign-report-to-1.json"
  append_progress run 1 "$RESULT_ROOT/campaign-report-to-1.json"
  CLI_COMMAND=real-resume
fi
controller "controller.$MODE-to-$TARGET" -m sandbox.fuzzer.v2_cli "$CLI_COMMAND" \
  --db "$DB" --campaign-id "$CAMPAIGN_ID" \
  --bootstrap .trace-g/stage6-bootstrap.json \
  --model-lock .trace-g/stage6-model-lock.json \
  --agent-image "$AGENT_IMAGE" \
  --mutator-image "$MUTATOR_IMAGE" \
  --data-root "$CAMPAIGN_ROOT" --generations "$TARGET" --gpu-device "$GPU_DEVICE" \
  --progress-dir "$RESULT_ROOT/generation-snapshots" \
  > "$RESULT_ROOT/run-to-${TARGET}.json" 2> "$RESULT_ROOT/run-to-${TARGET}.stderr.log"
controller controller.report-to-$TARGET -m sandbox.fuzzer.v2_cli report \
  --db "$DB" --campaign-id "$CAMPAIGN_ID" --output "$RESULT_ROOT/campaign-report.json"
controller controller.milestone-to-$TARGET \
  scripts/audit_office_v2_stage6_campaign.py milestone-gate \
  --db "$DB" --campaign-id "$CAMPAIGN_ID" --target-generation "$TARGET" \
  --output "$RESULT_ROOT/milestone-to-${TARGET}.json"
append_progress "$([[ "$MODE" == run ]] && echo resume || echo "$MODE")" \
  "$TARGET" "$RESULT_ROOT/campaign-report.json"
docker ps -a --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" \
  --format '{{.ID}} {{.Image}} {{.Status}}' > "$RESULT_ROOT/container-residue.txt"
docker volume ls --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" \
  --format '{{.Name}}' > "$RESULT_ROOT/volume-residue.txt"
test ! -s "$RESULT_ROOT/container-residue.txt"
test ! -s "$RESULT_ROOT/volume-residue.txt"
if (( TARGET >= 2 )); then
  controller controller.two-generation-gate \
    scripts/audit_office_v2_stage6_campaign.py \
    two-generation-gate --db "$DB" --campaign-id "$CAMPAIGN_ID" \
    --output "$RESULT_ROOT/two-generation-gate.json"
fi
REPLAY_ID="$(python3 - "$CAMPAIGN_ROOT/replays" <<'PY'
import json
import sys
from pathlib import Path

items = []
for path in Path(sys.argv[1]).glob("*/manifest.json"):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("recording_complete") is True:
        items.append((payload.get("created_at", ""), payload["replay_id"]))
if not items:
    raise SystemExit("ERROR: no sealed replay is available for the Stage 6 replay gate")
print(max(items)[1])
PY
)"
controller controller.strict-replay \
  -m sandbox.cli replay --replay-id "$REPLAY_ID" --mode strict \
  --campaign-id "$CAMPAIGN_ID" --work-item-id replay.strict-gate --attempt 1 \
  --output-dir "$CAMPAIGN_ROOT/strict-replay-trajectories" \
  --artifact-dir "$CAMPAIGN_ROOT/artifacts" --manifest-dir "$CAMPAIGN_ROOT/replays" \
  > "$RESULT_ROOT/stage6-replay-report.json" \
  2> "$RESULT_ROOT/stage6-replay.stderr.log"
docker ps -a --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" \
  --format '{{.ID}} {{.Image}} {{.Status}}' > "$RESULT_ROOT/container-residue.txt"
docker volume ls --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" \
  --format '{{.Name}}' > "$RESULT_ROOT/volume-residue.txt"
test ! -s "$RESULT_ROOT/container-residue.txt"
test ! -s "$RESULT_ROOT/volume-residue.txt"
archive_campaign success
trap - EXIT
MILESTONE_RESULT="$(python3 - "$RESULT_ROOT/milestone-to-${TARGET}.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["result_kind"])
PY
)"
echo "Campaign $CAMPAIGN_ID milestone result: $MILESTONE_RESULT (target $TARGET)"
