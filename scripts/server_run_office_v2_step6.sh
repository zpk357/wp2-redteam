#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <run|resume> <campaign-id> <target-generation> <gpu-device>" >&2
  exit 2
fi
MODE="$1"; CAMPAIGN_ID="$2"; TARGET="$3"; GPU_DEVICE="$4"
[[ "$MODE" == run || "$MODE" == resume ]] || { echo "ERROR: mode must be run or resume" >&2; exit 2; }
[[ "$CAMPAIGN_ID" =~ ^[a-z0-9][a-z0-9.-]{0,63}$ ]] || { echo "ERROR: invalid campaign-id" >&2; exit 2; }
[[ "$TARGET" =~ ^(2|10|20|30|50)$ ]] || { echo "ERROR: target must be 2, 10, 20, 30, or 50" >&2; exit 2; }
[[ "$GPU_DEVICE" =~ ^[0-9]+$ ]] || { echo "ERROR: invalid GPU device" >&2; exit 2; }

PERSIST_ROOT="${TRACE_G_PERSIST_ROOT:-/opt/trace-g-office-v2-step6}"
PROJECT_DIR="$PERSIST_ROOT/wp2-redteam"
CAMPAIGN_ROOT="$PROJECT_DIR/.trace-g-data/$CAMPAIGN_ID"
RESULT_ROOT="$PROJECT_DIR/reports/server-stage6/$CAMPAIGN_ID"
DB="$CAMPAIGN_ROOT/campaign.sqlite3"
PREFLIGHT="$PROJECT_DIR/reports/server-stage6/preflight/stage6-preflight.json"
cd "$PROJECT_DIR"
test -f "$PREFLIGHT" || { echo "ERROR: preflight missing" >&2; exit 1; }
python3 - "$PREFLIGHT" .trace-g/stage6-model-lock.json <<'PY'
import json
import sys

preflight = json.load(open(sys.argv[1], encoding="utf-8"))
lock = json.load(open(sys.argv[2], encoding="utf-8"))
if (
    preflight.get("schema_version") != "office-v2-stage6-preflight-v1"
    or preflight.get("passed") is not True
    or preflight.get("model_name") != lock.get("model_name")
    or preflight.get("model_digest") != lock.get("manifest_digest")
):
    raise SystemExit("ERROR: preflight identity does not match the active model lock")
PY
mkdir -p "$CAMPAIGN_ROOT" "$RESULT_ROOT"

archive_campaign() {
  local outcome="$1"
  local suffix="complete"
  [[ "$outcome" == failure ]] && suffix="failed"
  TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh \
    scripts/audit_office_v2_stage6_campaign.py archive \
    --campaign-id "$CAMPAIGN_ID" --outcome "$outcome" \
    --campaign-root "$CAMPAIGN_ROOT" --result-root "$RESULT_ROOT" \
    --model-lock .trace-g/stage6-model-lock.json \
    --bootstrap .trace-g/stage6-bootstrap.json --preflight "$PREFLIGHT" \
    --output "$PROJECT_DIR/reports/server-stage6/${CAMPAIGN_ID}-${suffix}.tar.gz"
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
TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh -m sandbox.fuzzer.v2_cli "$CLI_COMMAND" \
  --db "$DB" --campaign-id "$CAMPAIGN_ID" \
  --bootstrap .trace-g/stage6-bootstrap.json \
  --model-lock .trace-g/stage6-model-lock.json \
  --agent-image trace-g-office-v2-agent-qwen:step6-local \
  --mutator-image trace-g-office-v2-mutator-qwen:step6-local \
  --data-root "$CAMPAIGN_ROOT" --generations "$TARGET" --gpu-device "$GPU_DEVICE" \
  > "$RESULT_ROOT/run-to-${TARGET}.json"
TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh -m sandbox.fuzzer.v2_cli report \
  --db "$DB" --campaign-id "$CAMPAIGN_ID" --output "$RESULT_ROOT/campaign-report.json"
docker ps -a --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" \
  --format '{{.ID}} {{.Image}} {{.Status}}' > "$RESULT_ROOT/container-residue.txt"
docker volume ls --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" \
  --format '{{.Name}}' > "$RESULT_ROOT/volume-residue.txt"
test ! -s "$RESULT_ROOT/container-residue.txt"
test ! -s "$RESULT_ROOT/volume-residue.txt"
if (( TARGET >= 2 )); then
  TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh \
    scripts/audit_office_v2_stage6_campaign.py \
    two-generation-gate --db "$DB" --campaign-id "$CAMPAIGN_ID" \
    --output "$RESULT_ROOT/two-generation-gate.json"
fi
archive_campaign success
trap - EXIT
echo "Campaign $CAMPAIGN_ID reached target generation $TARGET"
