#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
echo "server_bootstrap_offline.sh is now the CPU-only staging entry point."
exec bash "$SCRIPT_DIR/server_stage_offline.sh" "$@"