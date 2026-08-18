#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/server_activate_gpu.sh <GPU_INDEX>" >&2
  exit 2
fi

GPU_INDEX="$1"
if ! [[ "$GPU_INDEX" =~ ^[0-9]+$ ]]; then
  echo "ERROR: GPU_INDEX must be a non-negative physical GPU index" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$ROOT_DIR"
source scripts/server_env.sh

PERSIST_ROOT="${TRACE_G_PERSIST_ROOT:-$(cd "$ROOT_DIR/.." && pwd -P)}"
STATE_DIR="$PERSIST_ROOT/deploy-state"
STAGE_STATE=".trace-g/cpu-stage.json"
ACTIVATE_STATE="$STATE_DIR/gpu-activate.json"
ACTIVATE_LOG="$STATE_DIR/gpu-activate.log"
toolkit_tmp=""
ENV_FILE="${ENV_FILE:-deploy/.env.server}"
ALLOW_SYSTEM_CHANGES="${ALLOW_SYSTEM_CHANGES:-0}"
OLLAMA_CONTAINER="trace-g-ollama"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run GPU activation as root" >&2
  exit 1
fi
if [[ "$ALLOW_SYSTEM_CHANGES" != "0" && "$ALLOW_SYSTEM_CHANGES" != "1" ]]; then
  echo "ERROR: ALLOW_SYSTEM_CHANGES must be 0 or 1" >&2
  exit 1
fi
test -f "$STAGE_STATE" || {
  echo "ERROR: CPU staging state is missing: $STAGE_STATE" >&2
  exit 1
}
test -f .trace-g/model-lock.json || {
  echo "ERROR: packaged model lock is missing" >&2
  exit 1
}
test -f "$ENV_FILE" || {
  echo "ERROR: server environment file is missing: $ENV_FILE" >&2
  exit 1
}

mkdir -p "$STATE_DIR"
touch "$ACTIVATE_LOG"
exec > >(tee -a "$ACTIVATE_LOG") 2>&1

activation_failed() {
  local status=$?
  trap - EXIT
  if [[ -n "$toolkit_tmp" && -d "$toolkit_tmp" ]]; then
    rm -rf -- "$toolkit_tmp"
  fi
  mkdir -p "$STATE_DIR"
  if command -v nvidia-smi >/dev/null; then
    nvidia-smi -i "$GPU_INDEX" > "$STATE_DIR/nvidia-smi-failed.txt" 2>&1 || true
  fi
  if command -v docker >/dev/null &&
     docker inspect "$OLLAMA_CONTAINER" >/dev/null 2>&1; then
    docker inspect "$OLLAMA_CONTAINER" > "$STATE_DIR/ollama-failed-inspect.json" 2>/dev/null || true
    docker logs "$OLLAMA_CONTAINER" > "$STATE_DIR/ollama-failed.log" 2>&1 || true
    docker stop "$OLLAMA_CONTAINER" > "$STATE_DIR/ollama-failed-stop.log" 2>&1 || true
  fi
  if command -v docker >/dev/null && [[ -f "$ENV_FILE" ]]; then
    docker compose --env-file "$ENV_FILE" \
      -f deploy/docker-compose.server.yaml down \
      >> "$STATE_DIR/ollama-failed-stop.log" 2>&1 || true
  fi
  printf '{"schema_version":"1.0","phase":"gpu-activate","status":"failed","exit_code":%d,"timestamp":"%s"}\n' \
    "$status" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$ACTIVATE_STATE"
  echo "ERROR: GPU activation failed; diagnostics preserved at $STATE_DIR" >&2
  exit "$status"
}
trap activation_failed EXIT

for command_name in docker nvidia-smi tar tee; do
  command -v "$command_name" >/dev/null || {
    echo "ERROR: missing required command: $command_name" >&2
    exit 1
  }
done
docker version >/dev/null
docker compose version >/dev/null
nvidia-smi -i "$GPU_INDEX" >/dev/null

IFS=$'\t' read -r KIT_DIR LOCKED_MODEL_NAME LOCKED_OLLAMA_IMAGE < <(
  TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh - \
    "$STAGE_STATE" .trace-g/model-lock.json <<'PY'
import json
import sys

stage_path, lock_path = sys.argv[1:]
stage = json.load(open(stage_path, encoding="utf-8"))
lock = json.load(open(lock_path, encoding="utf-8"))
if stage.get("status") != "complete" or stage.get("gpu_required") is not False:
    raise SystemExit("ERROR: CPU staging is not complete")
print(f"{stage['kit_dir']}\t{lock['model_name']}\t{lock['ollama_image']}")
PY
)
test -d "$KIT_DIR"

has_nvidia_runtime() {
  docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'
}

if ! has_nvidia_runtime; then
  if [[ "$ALLOW_SYSTEM_CHANGES" != "1" ]]; then
    echo "ERROR: NVIDIA Docker runtime is missing." >&2
    echo "Re-run with ALLOW_SYSTEM_CHANGES=1 only on a dedicated rental instance." >&2
    exit 1
  fi
  if ! command -v nvidia-ctk >/dev/null; then
    command -v apt-get >/dev/null || {
      echo "ERROR: apt-get is required for the bundled NVIDIA runtime packages" >&2
      exit 1
    }
    toolkit_tmp="$(mktemp -d)"
    tar -xzf "$KIT_DIR/runtime/nvidia-container-toolkit_1.19.1_deb_amd64.tar.gz" \
      -C "$toolkit_tmp"
    package_root="$(find "$toolkit_tmp" -type d -path '*/packages/ubuntu18.04/amd64' -print -quit)"
    test -n "$package_root"
    apt-get install -y \
      "$package_root/libnvidia-container1_1.19.1-1_amd64.deb" \
      "$package_root/libnvidia-container-tools_1.19.1-1_amd64.deb" \
      "$package_root/nvidia-container-toolkit-base_1.19.1-1_amd64.deb" \
      "$package_root/nvidia-container-toolkit_1.19.1-1_amd64.deb"
    rm -rf -- "$toolkit_tmp"
    toolkit_tmp=""
  fi
  nvidia-ctk runtime configure --runtime=docker
  if command -v systemctl >/dev/null; then
    systemctl restart docker
  else
    service docker restart
  fi
  docker version >/dev/null
  has_nvidia_runtime
fi

trace_g_load_server_env "$ENV_FILE"
if [[ "$MODEL_NAME" != "$LOCKED_MODEL_NAME" ]]; then
  echo "ERROR: MODEL_NAME differs from the packaged model lock" >&2
  exit 1
fi
if [[ "$OLLAMA_IMAGE" != "$LOCKED_OLLAMA_IMAGE" ]]; then
  echo "ERROR: OLLAMA_IMAGE differs from the packaged model lock" >&2
  exit 1
fi
if [[ "$TRACE_G_MODEL_DIR" != /* || ! -f "$TRACE_G_MODEL_DIR/.trace-g-model-archive.sha256" ]]; then
  echo "ERROR: persistent model directory is missing its archive marker" >&2
  exit 1
fi

tmp_env="$(mktemp)"
awk -v gpu="$GPU_INDEX" '
  BEGIN { replaced = 0 }
  /^OLLAMA_GPU_DEVICE=/ {
    print "OLLAMA_GPU_DEVICE=" gpu
    replaced = 1
    next
  }
  { print }
  END {
    if (!replaced) {
      print "OLLAMA_GPU_DEVICE=" gpu
    }
  }
' "$ENV_FILE" > "$tmp_env"
install -m 0600 "$tmp_env" "$ENV_FILE"
rm -f -- "$tmp_env"
trace_g_load_server_env "$ENV_FILE"

docker run --rm --gpus "device=$GPU_INDEX" \
  --entrypoint nvidia-smi "$OLLAMA_IMAGE" >/dev/null
docker compose --env-file "$ENV_FILE" \
  -f deploy/docker-compose.server.yaml \
  up -d --wait --pull never ollama

test "$(docker network inspect -f '{{.Internal}}' trace-g-model-internal)" = "true"
test "$(docker inspect -f '{{.State.Health.Status}}' "$OLLAMA_CONTAINER")" = "healthy"

scripts/server_python.sh scripts/verify_server_locks.py model \
  --lock .trace-g/model-lock.json \
  --ollama-image "$OLLAMA_IMAGE" \
  --endpoint http://ollama:11434 \
  --output .trace-g/model-verification.json
install -m 0600 .trace-g/model-verification.json "$STATE_DIR/model-verification.json"

scripts/server_python.sh scripts/warm_ollama_model.py \
  --endpoint http://ollama:11434 \
  --model "$MODEL_NAME" \
  --timeout 180 \
  --keep-alive 15m \
  > .trace-g/model-warmup.json
install -m 0600 .trace-g/model-warmup.json "$STATE_DIR/model-warmup.json"

nvidia-smi -i "$GPU_INDEX" > "$STATE_DIR/nvidia-smi-activated.txt"
TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh - \
  .trace-g/gpu-activate.json "$GPU_INDEX" "$LOCKED_MODEL_NAME" <<'PY'
import datetime
import json
import pathlib
import sys

state_path, gpu_index, model_name = sys.argv[1:]
payload = {
    "schema_version": "1.0",
    "phase": "gpu-activate",
    "status": "complete",
    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "gpu_index": int(gpu_index),
    "model_name": model_name,
    "model_digest_verified": True,
}
pathlib.Path(state_path).write_text(
    json.dumps(payload, indent=2) + "\n",
    encoding="utf-8",
)
PY
install -m 0600 .trace-g/gpu-activate.json "$ACTIVATE_STATE"

trap - EXIT
echo "GPU activation completed with packaged model digest verified."
echo "Next: CAMPAIGN_ID=qwen3-smoke-001 CAMPAIGN_MODE=smoke bash scripts/server_real_model_smoke.sh"
