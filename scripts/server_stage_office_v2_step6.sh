#!/usr/bin/env bash
set -Eeuo pipefail

KIT_DIR="$(cd "${1:-$(dirname "$0")/..}" && pwd -P)"
PERSIST_ROOT="${TRACE_G_PERSIST_ROOT:-/opt/trace-g-office-v2-step6}"
PROJECT_DIR="$PERSIST_ROOT/wp2-redteam"
SOURCE_ARCHIVE="$KIT_DIR/source/wp2-redteam-source.tar"

[[ "$(id -u)" -eq 0 ]] || { echo "ERROR: run staging as root" >&2; exit 1; }
for command_name in docker python3 sha256sum tar; do
  command -v "$command_name" >/dev/null || { echo "ERROR: missing $command_name" >&2; exit 1; }
done
[[ ! -e "$PROJECT_DIR" ]] || { echo "ERROR: preserve existing $PROJECT_DIR" >&2; exit 1; }
cd "$KIT_DIR"
sha256sum -c SHA256SUMS
docker load --input images/office-v2-stage6-qwen-role-images.tar
docker load --input images/trace-redteam-controller-server.tar
mkdir -p "$PROJECT_DIR"
tar -xf "$SOURCE_ARCHIVE" -C "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR/.trace-g" "$PROJECT_DIR/.trace-g-data" \
  "$PROJECT_DIR/reports/server-stage6"
cp locks/stage6-model-lock.json "$PROJECT_DIR/.trace-g/stage6-model-lock.json"
cp bootstrap/stage6-bootstrap.json "$PROJECT_DIR/.trace-g/stage6-bootstrap.json"
chmod +x "$PROJECT_DIR"/scripts/*.sh
printf '{"schema_version":"office-v2-stage6-stage-v1","status":"ready"}\n' > "$PERSIST_ROOT/stage.json"
echo "Office V2 Stage 6 staging ready: $PROJECT_DIR"
