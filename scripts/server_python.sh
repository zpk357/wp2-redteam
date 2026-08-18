#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd -P)"
CONTROLLER_IMAGE="trace-redteam-controller:server"
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
if [[ "$RUN_DOCKER_E2E" == "1" ]]; then
  extra_env_args+=(--env TRACE_G_RUN_DOCKER_E2E=1)
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
  --mount "type=bind,source=$PROJECT_DIR,target=$PROJECT_DIR" \
  --workdir "$WORK_DIR" \
  --env HOME=/tmp/controller-home \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --env PYTHONUNBUFFERED=1 \
  --env "PYTHONPATH=$PROJECT_DIR/src:$PROJECT_DIR/agent_image" \
  "${extra_env_args[@]}" \
  --label trace-g.role=controller \
  "$CONTROLLER_IMAGE" "$@"
