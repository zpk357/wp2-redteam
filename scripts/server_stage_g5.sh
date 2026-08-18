#!/usr/bin/env bash
set -Eeuo pipefail

KIT_DIR="$(cd "${1:-$(dirname "$0")/..}" && pwd -P)"
PERSIST_ROOT="${TRACE_G_PERSIST_ROOT:-/opt/trace-g-g5}"
PROJECT_DIR="$PERSIST_ROOT/wp2-redteam"
STATE_DIR="$PERSIST_ROOT/deploy-state"
SOURCE_ARCHIVE="$KIT_DIR/source/wp2-redteam-source.tar"
LOCK="$KIT_DIR/g5-server-kit-lock.json"
SOURCE_MARKER="$PROJECT_DIR/.trace-g/g5-source.sha256"
project_tmp=""

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run G5 staging as root" >&2
  exit 1
fi
case "$PERSIST_ROOT" in
  /*) ;;
  *) echo "ERROR: TRACE_G_PERSIST_ROOT must be an absolute Linux path" >&2; exit 1 ;;
esac
for command_name in awk chmod cmp cp docker ln mktemp mv python3 rm sha256sum tar tr; do
  command -v "$command_name" >/dev/null || {
    echo "ERROR: missing required command: $command_name" >&2
    exit 1
  }
done

mkdir -p "$STATE_DIR"
stage_failed() {
  local status=$?
  trap - EXIT
  if [[ -n "$project_tmp" && -d "$project_tmp" ]]; then
    rm -rf -- "$project_tmp"
  fi
  printf '{"schema_version":"1.0","gate":"5.G5","status":"failed","exit_code":%d}\n' \
    "$status" > "$STATE_DIR/g5-stage.json"
  exit "$status"
}
trap stage_failed EXIT

cd "$KIT_DIR"
sha256sum -c SHA256SUMS
python3 scripts/verify_g5_server_kit.py --lock "$LOCK" --kit-root "$KIT_DIR"
docker load --input images/trace-redteam-agent-qwen-g5.tar
docker load --input images/trace-redteam-controller-g5.tar

SOURCE_SHA256="$(sha256sum "$SOURCE_ARCHIVE" | awk '{print $1}')"
if [[ -e "$PROJECT_DIR" ]]; then
  if [[ ! -f "$SOURCE_MARKER" ]] || [[ "$(tr -d '[:space:]' < "$SOURCE_MARKER")" != "$SOURCE_SHA256" ]]; then
    echo "ERROR: existing project tree does not match the locked G5 source" >&2
    exit 1
  fi
  if [[ ! -f "$PROJECT_DIR/.trace-g/g5-server-kit-lock.json" ]] || \
     ! cmp -s "$LOCK" "$PROJECT_DIR/.trace-g/g5-server-kit-lock.json"; then
    echo "ERROR: existing project tree has a different G5 kit lock" >&2
    exit 1
  fi
else
  mkdir -p "$PERSIST_ROOT"
  project_tmp="$(mktemp -d "$PERSIST_ROOT/.wp2-redteam-g5.XXXXXX")"
  tar -xf "$SOURCE_ARCHIVE" -C "$project_tmp"
  mkdir -p "$project_tmp/.trace-g"
  printf '%s\n' "$SOURCE_SHA256" > "$project_tmp/.trace-g/g5-source.sha256"
  cp "$LOCK" "$project_tmp/.trace-g/g5-server-kit-lock.json"
  mv "$project_tmp" "$PROJECT_DIR"
  project_tmp=""
fi

cd "$PROJECT_DIR"
chmod +x scripts/*.sh
mkdir -p .venv/bin
ln -sfn ../../scripts/server_python.sh .venv/bin/python
TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh \
  scripts/verify_g5_server_kit.py \
  --lock .trace-g/g5-server-kit-lock.json \
  --loaded-images \
  --output "$STATE_DIR/g5-loaded-images.json"

if [[ "${RUN_G5_CPU_TESTS:-1}" == "1" ]]; then
  TRACE_G_CONTROLLER_NETWORK=none scripts/server_python.sh -m pytest \
    tests/unit/test_g5_server_preparation.py \
    tests/unit/test_langgraph_react_runtime.py \
    tests/unit/test_recording_components.py \
    -p no:cacheprovider --basetemp /tmp/trace-g-g5-pytest
fi

printf '{"schema_version":"1.0","gate":"5.G5","status":"ready","source_sha256":"%s"}\n' \
  "$SOURCE_SHA256" > "$STATE_DIR/g5-stage.json"
trap - EXIT
echo "G5 server staging ready: $PROJECT_DIR"
echo "Next: TRACE_G_PERSIST_ROOT=$PERSIST_ROOT bash scripts/server_run_g5_gate.sh <run-id> <gpu-device>"
