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
cd "$PROJECT_DIR"
test -f "$PROJECT_DIR/reports/server-stage6/preflight/stage6-preflight.json" || { echo "ERROR: preflight missing" >&2; exit 1; }
mkdir -p "$CAMPAIGN_ROOT" "$RESULT_ROOT"

archive_failure() {
  local status=$?
  trap - EXIT
  printf '{"schema_version":"office-v2-stage6-failure-v1","exit_code":%d}\n' "$status" > "$RESULT_ROOT/failure.json"
  docker ps -a --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" \
    --format '{{.ID}} {{.Image}} {{.Status}}' > "$RESULT_ROOT/container-residue.txt" || true
  docker volume ls --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" \
    --format '{{.Name}}' > "$RESULT_ROOT/volume-residue.txt" || true
  tar -czf "$PROJECT_DIR/reports/server-stage6/${CAMPAIGN_ID}-failed.tar.gz" \
    -C "$PROJECT_DIR/reports/server-stage6" "$CAMPAIGN_ID" || true
  sha256sum "$PROJECT_DIR/reports/server-stage6/${CAMPAIGN_ID}-failed.tar.gz" > \
    "$PROJECT_DIR/reports/server-stage6/${CAMPAIGN_ID}-failed.tar.gz.sha256" || true
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
trap - EXIT
echo "Campaign $CAMPAIGN_ID reached target generation $TARGET"
