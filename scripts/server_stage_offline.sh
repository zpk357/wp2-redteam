#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -gt 0 ]]; then
  KIT_DIR="$(cd "$1" && pwd -P)"
else
  KIT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
fi

PERSIST_ROOT="${TRACE_G_PERSIST_ROOT:-/opt/trace-g}"
INSTALL_ROOT="${TRACE_G_INSTALL_ROOT:-$PERSIST_ROOT}"
PROJECT_DIR="$INSTALL_ROOT/wp2-redteam"
MODEL_DIR="${TRACE_G_MODEL_DIR:-$PERSIST_ROOT/ollama-models}"
STATE_DIR="$PERSIST_ROOT/deploy-state"
STAGE_LOG="$STATE_DIR/cpu-stage.log"
STAGE_STATE="$STATE_DIR/cpu-stage.json"
SOURCE_MARKER="$PROJECT_DIR/.trace-g/source-archive.sha256"
MODEL_MARKER="$MODEL_DIR/.trace-g-model-archive.sha256"
AGENT_IMAGE="trace-redteam-agent:server"
CONTROLLER_IMAGE="trace-redteam-controller:server"
OLLAMA_IMAGE="ollama/ollama:0.32.1"
RUN_CPU_UNIT_TESTS="${RUN_CPU_UNIT_TESTS:-1}"
project_tmp=""
model_tmp=""

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run CPU staging as root" >&2
  exit 1
fi
if [[ "$RUN_CPU_UNIT_TESTS" != "0" && "$RUN_CPU_UNIT_TESTS" != "1" ]]; then
  echo "ERROR: RUN_CPU_UNIT_TESTS must be 0 or 1" >&2
  exit 1
fi
for path_value in "$PERSIST_ROOT" "$INSTALL_ROOT" "$MODEL_DIR"; do
  if [[ "$path_value" != /* || "$path_value" == *","* || "$path_value" == *$'\n'* ]]; then
    echo "ERROR: persistent paths must be absolute Linux paths without commas/newlines" >&2
    exit 1
  fi
done

mkdir -p "$STATE_DIR"
touch "$STAGE_LOG"
exec > >(tee -a "$STAGE_LOG") 2>&1

stage_failed() {
  local status=$?
  trap - EXIT
  if [[ -n "$project_tmp" && -d "$project_tmp" ]]; then
    rm -rf -- "$project_tmp"
  fi
  if [[ -n "$model_tmp" && -d "$model_tmp" ]]; then
    rm -rf -- "$model_tmp"
  fi
  printf '{"schema_version":"1.0","phase":"cpu-stage","status":"failed","exit_code":%d,"timestamp":"%s"}\n' \
    "$status" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STAGE_STATE"
  echo "ERROR: CPU staging failed; log preserved at $STAGE_LOG" >&2
  exit "$status"
}
trap stage_failed EXIT

for command_name in awk date dirname docker find grep install mktemp mv python3 sha256sum tar tee tr; do
  command -v "$command_name" >/dev/null || {
    echo "ERROR: missing required command: $command_name" >&2
    exit 1
  }
done
docker version >/dev/null
docker compose version >/dev/null || {
  echo "ERROR: Docker Compose v2 is required" >&2
  exit 1
}

cd "$KIT_DIR"
sha256sum -c SHA256SUMS
python3 scripts/verify_ollama_model_archive.py \
  --archive models/ollama-models-qwen3-8b.tar \
  --lock models/qwen3-8b-model-lock.json \
  --output "$STATE_DIR/model-archive-verification.json"

docker load --input images/trace-redteam-agent-server.tar
docker load --input images/trace-redteam-controller-server.tar
docker load --input images/ollama-0.32.1.tar

SOURCE_SHA256="$(sha256sum source/wp2-redteam-source.tar | awk '{print $1}')"
if [[ -e "$PROJECT_DIR" ]]; then
  if [[ ! -f "$SOURCE_MARKER" ]] ||
     [[ "$(tr -d '[:space:]' < "$SOURCE_MARKER")" != "$SOURCE_SHA256" ]]; then
    echo "ERROR: $PROJECT_DIR exists without the matching source marker" >&2
    echo "Move it explicitly or set TRACE_G_INSTALL_ROOT to a clean persistent directory." >&2
    exit 1
  fi
  echo "Reusing source tree with matching archive marker: $PROJECT_DIR"
else
  mkdir -p "$INSTALL_ROOT"
  project_tmp="$(mktemp -d "$INSTALL_ROOT/.wp2-redteam-stage.XXXXXX")"
  tar -xf source/wp2-redteam-source.tar -C "$project_tmp"
  mkdir -p "$project_tmp/.trace-g"
  printf '%s\n' "$SOURCE_SHA256" > "$project_tmp/.trace-g/source-archive.sha256"
  mv "$project_tmp" "$PROJECT_DIR"
  project_tmp=""
  echo "Installed source tree atomically: $PROJECT_DIR"
fi

install -m 0600 images/image-locks.json "$PROJECT_DIR/.trace-g/image-locks.json"
install -m 0600 models/qwen3-8b-model-lock.json "$PROJECT_DIR/.trace-g/model-lock.json"
cd "$PROJECT_DIR"
chmod +x scripts/*.sh
mkdir -p .venv/bin
ln -sfn ../../scripts/server_python.sh .venv/bin/python
test -x .venv/bin/python

TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh \
  scripts/verify_server_locks.py images \
  --lock .trace-g/image-locks.json \
  --output .trace-g/image-verification.json
install -m 0600 .trace-g/image-verification.json "$STATE_DIR/image-verification.json"

if [[ "$RUN_CPU_UNIT_TESTS" == "1" ]]; then
  echo "Running CPU-only unit tests inside the locked controller image."
  TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh -m pytest tests/unit \
    -p no:cacheprovider --basetemp /tmp/trace-g-pytest
fi

MODEL_ARCHIVE_SHA256="$(sha256sum "$KIT_DIR/models/ollama-models-qwen3-8b.tar" | awk '{print $1}')"
if [[ -e "$MODEL_DIR" ]]; then
  if [[ -f "$MODEL_MARKER" ]]; then
    if [[ "$(tr -d '[:space:]' < "$MODEL_MARKER")" != "$MODEL_ARCHIVE_SHA256" ]]; then
      echo "ERROR: persistent model marker does not match the packaged model archive" >&2
      exit 1
    fi
    echo "Reusing persistent model directory with matching archive marker: $MODEL_DIR"
  elif [[ ! -d "$MODEL_DIR" ]]; then
    echo "ERROR: persistent model path exists but is not a directory: $MODEL_DIR" >&2
    exit 1
  elif [[ -n "$(find "$MODEL_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "ERROR: $MODEL_DIR is non-empty without a matching archive marker" >&2
    echo "Refusing to overwrite an unknown model directory." >&2
    exit 1
  else
    rmdir "$MODEL_DIR"
  fi
fi
if [[ ! -e "$MODEL_DIR" ]]; then
  mkdir -p "$(dirname "$MODEL_DIR")"
  model_tmp="$(mktemp -d "${MODEL_DIR}.stage.XXXXXX")"
  docker run --rm --user 0 \
    --network none \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --mount "type=bind,source=$model_tmp,target=/models" \
    --mount "type=bind,source=$KIT_DIR,target=/kit,readonly" \
    --entrypoint tar "$AGENT_IMAGE" \
    --no-same-owner --no-same-permissions --no-overwrite-dir --touch \
    -C /models -xf /kit/models/ollama-models-qwen3-8b.tar
  printf '%s\n' "$MODEL_ARCHIVE_SHA256" > "$model_tmp/.trace-g-model-archive.sha256"
  mv "$model_tmp" "$MODEL_DIR"
  model_tmp=""
  echo "Restored model directory atomically: $MODEL_DIR"
fi

TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh - \
  .trace-g/model-lock.json "$MODEL_DIR" <<'PY'
import json
import pathlib
import sys

lock_path, model_dir = sys.argv[1:]
model = json.load(open(lock_path, encoding="utf-8"))
required = {
    "schema_version": "1.0",
    "model_name": "qwen3:8b",
    "ollama_image": "ollama/ollama:0.32.1",
}
for key, expected in required.items():
    if model.get(key) != expected:
        raise SystemExit(f"ERROR: model lock {key} must equal {expected!r}")
digest = model.get("model_digest")
if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
    raise SystemExit("ERROR: model lock has no valid SHA-256 digest")

env_path = pathlib.Path("deploy/.env.server")
existing = {}
if env_path.exists():
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        if raw_line and not raw_line.startswith("#") and "=" in raw_line:
            key, value = raw_line.split("=", 1)
            existing[key] = value
gpu = existing.get("OLLAMA_GPU_DEVICE", "REPLACE_WITH_GPU_INDEX")
if not gpu.isdigit():
    gpu = "REPLACE_WITH_GPU_INDEX"
values = {
    "OLLAMA_IMAGE": model["ollama_image"],
    "OLLAMA_GPU_DEVICE": gpu,
    "OLLAMA_NUM_PARALLEL": "1",
    "OLLAMA_MAX_LOADED_MODELS": "1",
    "OLLAMA_MAX_QUEUE": "32",
    "OLLAMA_CONTEXT_LENGTH": "8192",
    "MODEL_NAME": model["model_name"],
    "PROFILE_ID": "server-local-model",
    "AGENT_IMAGE": "trace-redteam-agent:server",
    "TRACE_G_MODEL_DIR": model_dir,
}
env_path.write_text(
    "".join(f"{key}={value}\n" for key, value in values.items()),
    encoding="utf-8",
)
print(f"Locked offline model: {model['model_name']}@{digest}")
print(f"Persistent model directory: {model_dir}")
PY

TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh - \
  .trace-g/cpu-stage.json "$KIT_DIR" "$PERSIST_ROOT" "$PROJECT_DIR" "$MODEL_DIR" \
  "$SOURCE_SHA256" "$MODEL_ARCHIVE_SHA256" <<'PY'
import datetime
import json
import pathlib
import sys

state_path, kit_dir, persist_root, project_dir, model_dir, source_sha, model_sha = sys.argv[1:]
payload = {
    "schema_version": "1.0",
    "phase": "cpu-stage",
    "status": "complete",
    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "kit_dir": kit_dir,
    "persist_root": persist_root,
    "project_dir": project_dir,
    "model_dir": model_dir,
    "source_archive_sha256": source_sha,
    "model_archive_sha256": model_sha,
    "gpu_required": False,
}
path = pathlib.Path(state_path)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
install -m 0600 .trace-g/cpu-stage.json "$STAGE_STATE"

trap - EXIT
echo
echo "CPU staging completed. NVIDIA runtime and GPU were not required."
echo "Persistent root: $PERSIST_ROOT"
echo "Project: $PROJECT_DIR"
echo "Next: bash scripts/server_activate_gpu.sh <GPU_INDEX>"
