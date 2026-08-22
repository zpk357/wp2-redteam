#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd -P)"
CONTROLLER_IMAGE="${TRACE_G_CONTROLLER_IMAGE:-trace-redteam-controller:server}"
CONTROLLER_NETWORK="${TRACE_G_CONTROLLER_NETWORK:-trace-g-model-internal}"
DOCKER_SOCKET="/var/run/docker.sock"
RUN_DOCKER_E2E="${TRACE_G_RUN_DOCKER_E2E:-0}"

case "$CONTROLLER_NETWORK" in
  trace-g-model-internal | none | container:trace-g-ollama) ;;
  *)
    echo "ERROR: TRACE_G_CONTROLLER_NETWORK must be trace-g-model-internal, container:trace-g-ollama, or none" >&2
    exit 1
    ;;
esac
if [[ "$RUN_DOCKER_E2E" != "0" && "$RUN_DOCKER_E2E" != "1" ]]; then
  echo "ERROR: TRACE_G_RUN_DOCKER_E2E must be 0 or 1" >&2
  exit 1
fi

extra_env_args=()
label_args=(--label trace-g.role=controller)
stage_mount_args=()
ROOT_STAGE_RECORD="$PROJECT_DIR/../stage.json"
if [[ -f "$ROOT_STAGE_RECORD" ]]; then
  stage_mount_args+=(
    --mount "type=bind,source=$ROOT_STAGE_RECORD,target=$ROOT_STAGE_RECORD,readonly"
  )
fi
if [[ "$RUN_DOCKER_E2E" == "1" ]]; then
  extra_env_args+=(--env TRACE_G_RUN_DOCKER_E2E=1)
fi
if [[ -n "${TRACE_G_CAMPAIGN_ID:-}" ]]; then
  [[ "$TRACE_G_CAMPAIGN_ID" =~ ^[a-z0-9][a-z0-9.-]{0,63}$ ]] || {
    echo "ERROR: invalid TRACE_G_CAMPAIGN_ID" >&2
    exit 1
  }
  [[ -n "${TRACE_G_WORK_ITEM_ID:-}" && "${TRACE_G_ATTEMPT:-}" =~ ^[1-9][0-9]*$ ]] || {
    echo "ERROR: Campaign-labelled Controller requires work item and attempt" >&2
    exit 1
  }
  label_args+=(
    --label "trace-g.campaign-id=$TRACE_G_CAMPAIGN_ID"
    --label "trace-g.work-item-id=$TRACE_G_WORK_ITEM_ID"
    --label "trace-g.attempt=$TRACE_G_ATTEMPT"
  )
fi

command -v docker >/dev/null || {
  echo "ERROR: docker is required to start the locked controller" >&2
  exit 1
}
[[ -S "$DOCKER_SOCKET" ]] || {
  echo "ERROR: Docker socket is unavailable: $DOCKER_SOCKET" >&2
  exit 1
}
[[ -f "$PROJECT_DIR/pyproject.toml" ]] || {
  echo "ERROR: unable to resolve the wp2-redteam project root" >&2
  exit 1
}
mkdir -p "$PROJECT_DIR/.trace-g-data" "$PROJECT_DIR/reports"

WORK_DIR="$PWD"
case "$WORK_DIR" in
  "$PROJECT_DIR" | "$PROJECT_DIR"/*) ;;
  *) WORK_DIR="$PROJECT_DIR" ;;
esac

SOCKET_GID="$(stat -c '%g' "$DOCKER_SOCKET")"

exec docker run --rm --init -i \
  --name "trace-g-controller-$PPID-$$" \
  --user "$(id -u):$(id -g)" \
  --group-add "$SOCKET_GID" \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=256m \
  --tmpfs /run:rw,nosuid,nodev,noexec,size=16m \
  --pids-limit 512 \
  --network "$CONTROLLER_NETWORK" \
  --mount "type=bind,source=$DOCKER_SOCKET,target=$DOCKER_SOCKET" \
  --mount "type=bind,source=$PROJECT_DIR,target=$PROJECT_DIR,readonly" \
  --mount "type=bind,source=$PROJECT_DIR/.trace-g-data,target=$PROJECT_DIR/.trace-g-data" \
  --mount "type=bind,source=$PROJECT_DIR/reports,target=$PROJECT_DIR/reports" \
  "${stage_mount_args[@]}" \
  --workdir "$WORK_DIR" \
  --env HOME=/tmp/controller-home \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --env PYTHONUNBUFFERED=1 \
  --env "PYTHONPATH=$PROJECT_DIR/src:$PROJECT_DIR/agent_image" \
  "${extra_env_args[@]}" \
  "${label_args[@]}" \
  "$CONTROLLER_IMAGE" "$@"
