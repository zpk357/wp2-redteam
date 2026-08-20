#!/usr/bin/env bash
set -Eeuo pipefail

PERSIST_ROOT="${TRACE_G_PERSIST_ROOT:-/opt/trace-g-office-v2-step6}"
PROJECT_DIR="$PERSIST_ROOT/wp2-redteam"
RESULT_DIR="$PROJECT_DIR/reports/server-stage6/preflight"
GPU_DEVICE="${1:-0}"
[[ "$GPU_DEVICE" =~ ^[0-9]+$ ]] || { echo "ERROR: invalid GPU device" >&2; exit 2; }
cd "$PROJECT_DIR"
test "$(python3 -c 'import json; print(json.load(open("../stage.json"))["status"])')" = ready
nvidia-smi -i "$GPU_DEVICE" > /dev/null
mkdir -p "$RESULT_DIR"
GPU_STOP_FILE="$RESULT_DIR/.gpu-monitor-stop"
rm -f \
  "$GPU_STOP_FILE" \
  "$RESULT_DIR/failure.json" \
  "$RESULT_DIR/stage6-preflight.json" \
  "$RESULT_DIR/stage6-gpu-residency.json"
python3 - "$RESULT_DIR/stage6-server-host.json" "$GPU_DEVICE" <<'PY'
import json
import platform
import subprocess
import sys
from pathlib import Path

def output(*command):
    return subprocess.check_output(command, text=True, timeout=10).strip()

payload = {
    "schema_version": "office-v2-stage6-server-host-v1",
    "captured": True,
    "hostname": platform.node(),
    "platform": platform.platform(),
    "gpu_device": sys.argv[2],
    "docker_version": output("docker", "version", "--format", "{{.Server.Version}}"),
    "nvidia_smi": output("nvidia-smi", "-i", sys.argv[2], "--query-gpu=index,name,memory.total,driver_version", "--format=csv,noheader,nounits"),
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
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
archive_preflight_failure() {
  local status=$?
  trap - EXIT
  if [[ -n "${GPU_MONITOR_PID:-}" ]]; then
    touch "$GPU_STOP_FILE"
    wait "$GPU_MONITOR_PID" || true
  fi
  printf '{"schema_version":"office-v2-stage6-preflight-failure-v1","exit_code":%d}\n' \
    "$status" > "$RESULT_DIR/failure.json"
  docker ps -a --filter "label=trace-g.component=office-v2-llm-mutator" \
    --format '{{.ID}} {{.Image}} {{.Status}}' > "$RESULT_DIR/container-residue.txt" || true
  docker ps -a --filter "label=trace-g.component=agent-sandbox" \
    --format '{{.ID}} {{.Image}} {{.Status}}' >> "$RESULT_DIR/container-residue.txt" || true
  docker volume ls --filter "label=trace-g.component=workspace-volume" \
    --format '{{.Name}}' > "$RESULT_DIR/volume-residue.txt" || true
  exit "$status"
}
trap archive_preflight_failure EXIT
python3 scripts/monitor_office_v2_stage6_gpu.py \
  --gpu-device "$GPU_DEVICE" \
  --output "$RESULT_DIR/stage6-gpu-residency.json" \
  --stop-file "$GPU_STOP_FILE" &
GPU_MONITOR_PID=$!
TRACE_G_CONTROLLER_IMAGE="$CONTROLLER_IMAGE" TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh \
  scripts/run_office_v2_stage6_preflight.py \
  --model-lock .trace-g/stage6-model-lock.json \
  --bootstrap .trace-g/stage6-bootstrap.json \
  --agent-image "$AGENT_IMAGE" \
  --mutator-image "$MUTATOR_IMAGE" \
  --data-root "$PROJECT_DIR/.trace-g-data/preflight" \
  --gpu-device "$GPU_DEVICE" \
  --output "$RESULT_DIR/stage6-preflight.json" \
  > "$RESULT_DIR/preflight.log" 2>&1
touch "$GPU_STOP_FILE"
wait "$GPU_MONITOR_PID"
GPU_MONITOR_PID=""
rm -f "$GPU_STOP_FILE"
[[ -z "$(docker ps -a --filter 'label=trace-g.component=office-v2-llm-mutator' --format '{{.ID}}')" ]]
[[ -z "$(docker ps -a --filter 'label=trace-g.component=agent-sandbox' --format '{{.ID}}')" ]]
trap - EXIT
echo "Office V2 Stage 6 preflight passed"
