#!/usr/bin/env bash
set -uo pipefail

CAMPAIGN_ID="${1:-}"
if [[ -n "$CAMPAIGN_ID" ]] &&
   ! [[ "$CAMPAIGN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "ERROR: invalid campaign ID" >&2
  exit 2
fi

PERSIST_ROOT="${TRACE_G_PERSIST_ROOT:-/opt/trace-g}"
PROJECT_DIR="${TRACE_G_INSTALL_ROOT:-$PERSIST_ROOT}/wp2-redteam"
STATE_DIR="$PERSIST_ROOT/deploy-state"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESCUE_DIR="$STATE_DIR/abort-$TIMESTAMP"
ARCHIVE="$STATE_DIR/trace-g-deploy-abort-$TIMESTAMP.tar.gz"
OLLAMA_CONTAINER="trace-g-ollama"

mkdir -p "$RESCUE_DIR"
printf '%s\n' \
  "TRACE-G deployment/experiment abort diagnostics." \
  "This archive is not a passing acceptance result." \
  "No model, trajectory, database, or source directory was deleted." \
  > "$RESCUE_DIR/README.txt"

if command -v docker >/dev/null; then
  docker version > "$RESCUE_DIR/docker-version.txt" 2>&1 || true
  docker info > "$RESCUE_DIR/docker-info.txt" 2>&1 || true
  docker image ls --digests --no-trunc > "$RESCUE_DIR/docker-images.txt" 2>&1 || true
  docker ps -a --no-trunc > "$RESCUE_DIR/docker-containers.txt" 2>&1 || true
  if docker inspect "$OLLAMA_CONTAINER" >/dev/null 2>&1; then
    docker inspect "$OLLAMA_CONTAINER" > "$RESCUE_DIR/ollama-inspect.json" 2>&1 || true
    docker logs "$OLLAMA_CONTAINER" > "$RESCUE_DIR/ollama.log" 2>&1 || true
    docker stop "$OLLAMA_CONTAINER" > "$RESCUE_DIR/ollama-stop.txt" 2>&1 || true
  fi
fi

if command -v nvidia-smi >/dev/null; then
  nvidia-smi > "$RESCUE_DIR/nvidia-smi.txt" 2>&1 || true
fi

if [[ -d "$PROJECT_DIR" ]]; then
  for source_path in \
    "$PROJECT_DIR/.trace-g" \
    "$PROJECT_DIR/reports/server-real-model"; do
    if [[ -e "$source_path" ]]; then
      cp -a "$source_path" "$RESCUE_DIR/" 2>/dev/null || true
    fi
  done

  if [[ -f "$PROJECT_DIR/deploy/.env.server" ]] &&
     [[ -f "$PROJECT_DIR/deploy/docker-compose.server.yaml" ]] &&
     command -v docker >/dev/null; then
    (
      cd "$PROJECT_DIR" &&
      docker compose --env-file deploy/.env.server \
        -f deploy/docker-compose.server.yaml down
    ) > "$RESCUE_DIR/compose-down.txt" 2>&1 || true
  fi

  if [[ -n "$CAMPAIGN_ID" ]] &&
     [[ -x "$PROJECT_DIR/scripts/server_export_incomplete.sh" ]] &&
     [[ -d "$PROJECT_DIR/reports/server-real-model/$CAMPAIGN_ID" ]]; then
    (
      cd "$PROJECT_DIR" &&
      bash scripts/server_export_incomplete.sh "$CAMPAIGN_ID"
    ) > "$RESCUE_DIR/incomplete-export.txt" 2>&1 || true
  fi
fi

tar -czf "$ARCHIVE" -C "$STATE_DIR" "$(basename "$RESCUE_DIR")"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

echo "Abort diagnostics: $ARCHIVE"
echo "SHA256 file: $ARCHIVE.sha256"
echo "Ollama was stopped if it existed. Verify nvidia-smi before releasing the instance."

