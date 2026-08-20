#!/usr/bin/env bash
set -Eeuo pipefail

KIT_DIR="$(cd "${1:-$(dirname "$0")}" && pwd -P)"
PERSIST_ROOT="${TRACE_G_PERSIST_ROOT:-/opt/trace-g-office-v2-step6}"
PROJECT_DIR="$PERSIST_ROOT/wp2-redteam"
SOURCE_ARCHIVE="$KIT_DIR/source/wp2-redteam-source.tar"
REPAIR_LOCK="$KIT_DIR/locks/stage6-repair-plan.json"
BASE_MODEL_LOCK="$KIT_DIR/locks/stage6-base-model-lock.json"
CURRENT_MODEL_LOCK="$PROJECT_DIR/.trace-g/stage6-model-lock.json"

[[ "$(id -u)" -eq 0 ]] || { echo "ERROR: run repair as root" >&2; exit 1; }
for command_name in docker python3 sha256sum tar; do
  command -v "$command_name" >/dev/null || { echo "ERROR: missing $command_name" >&2; exit 1; }
done
[[ -d "$PROJECT_DIR" ]] || { echo "ERROR: existing Stage 6 project is missing" >&2; exit 1; }
[[ -f "$BASE_MODEL_LOCK" ]] || { echo "ERROR: repair base model lock is missing" >&2; exit 1; }
[[ -f "$CURRENT_MODEL_LOCK" ]] || { echo "ERROR: active model lock is missing" >&2; exit 1; }
cd "$KIT_DIR"
sha256sum -c SHA256SUMS

SOURCE_REVISION="$(python3 - "$REPAIR_LOCK" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["source_revision"])
PY
)"
STAGE_DIR="$PERSIST_ROOT/.repair-stage-$SOURCE_REVISION"
BACKUP_DIR="$PERSIST_ROOT/wp2-redteam-before-$SOURCE_REVISION"
[[ ! -e "$STAGE_DIR" ]] || { echo "ERROR: repair staging path already exists" >&2; exit 1; }
[[ ! -e "$BACKUP_DIR" ]] || { echo "ERROR: repair backup path already exists" >&2; exit 1; }
mkdir -p "$STAGE_DIR"
tar -xf "$SOURCE_ARCHIVE" -C "$STAGE_DIR"

PYTHONPATH="$STAGE_DIR/src:$STAGE_DIR" python3 - \
  "$REPAIR_LOCK" "$BASE_MODEL_LOCK" "$CURRENT_MODEL_LOCK" "$SOURCE_ARCHIVE" "$STAGE_DIR" <<'PY'
import hashlib
import subprocess
import sys
from pathlib import Path

from sandbox.fuzzer.v2_stage6_identity import Stage6ModelLock, Stage6RepairPlanLock


def digest(path: Path) -> str:
    value = hashlib.sha256(path.read_bytes()).hexdigest()
    return "sha256:" + value


repair = Stage6RepairPlanLock.model_validate_json(Path(sys.argv[1]).read_bytes())
base = Stage6ModelLock.model_validate_json(Path(sys.argv[2]).read_bytes())
current = Stage6ModelLock.model_validate_json(Path(sys.argv[3]).read_bytes())
archive = Path(sys.argv[4])
source = Path(sys.argv[5])
if repair.base_model_lock_digest != base.lock_digest:
    raise SystemExit("ERROR: installed base lock differs from repair plan")
if (
    current.manifest_digest != base.manifest_digest
    or current.controller_image_id != base.controller_image_id
):
    raise SystemExit("ERROR: current runtime does not descend from the repair base")
if digest(archive) != repair.source_archive_sha256 or archive.stat().st_size != repair.source_archive_bytes:
    raise SystemExit("ERROR: repair source archive identity differs")
for role in repair.roles:
    if digest(source / role.dockerfile) != role.dockerfile_sha256:
        raise SystemExit(f"ERROR: Dockerfile identity differs for {role.role.value}")
    for item in role.copied_files:
        if digest(source / item.path) != item.sha256:
            raise SystemExit(f"ERROR: copied file identity differs: {item.path}")
    actual = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", role.base_image_reference],
        text=True,
    ).strip().lower()
    if actual != role.base_image_id.lower():
        raise SystemExit(f"ERROR: base image identity differs for {role.role.value}")
controller = subprocess.check_output(
    ["docker", "image", "inspect", "--format", "{{.Id}}", repair.controller_image_reference],
    text=True,
).strip().lower()
if controller != repair.controller_image_id.lower():
    raise SystemExit("ERROR: controller image identity differs")
PY

mapfile -t ROLE_BUILD < <(PYTHONPATH="$STAGE_DIR/src:$STAGE_DIR" python3 - "$REPAIR_LOCK" <<'PY'
import sys
from pathlib import Path
from sandbox.fuzzer.v2_stage6_identity import Stage6RepairPlanLock

lock = Stage6RepairPlanLock.model_validate_json(Path(sys.argv[1]).read_bytes())
for role in lock.roles:
    print("\t".join((role.role.value, role.base_image_reference, role.final_image_reference, role.dockerfile)))
PY
)
for line in "${ROLE_BUILD[@]}"; do
  IFS=$'\t' read -r role base_ref final_ref dockerfile <<<"$line"
  build_arg="AGENT_BASE_IMAGE"
  [[ "$role" == mutator ]] && build_arg="MUTATOR_BASE_IMAGE"
  docker build --network none --build-arg "$build_arg=$base_ref" \
    --tag "$final_ref" --file "$STAGE_DIR/$dockerfile" "$STAGE_DIR"
done

mkdir -p "$STAGE_DIR/.trace-g"
cp -a "$PROJECT_DIR/.trace-g/." "$STAGE_DIR/.trace-g/"
cp "$BASE_MODEL_LOCK" "$STAGE_DIR/.trace-g/stage6-base-model-lock.json"
PYTHONPATH="$STAGE_DIR/src:$STAGE_DIR" python3 \
  "$STAGE_DIR/scripts/build_office_v2_stage6_repair_lock.py" seal \
  --repair-lock "$REPAIR_LOCK" \
  --base-model-lock "$STAGE_DIR/.trace-g/stage6-base-model-lock.json" \
  --model-lock-output "$STAGE_DIR/.trace-g/stage6-model-lock.json" \
  --receipt-output "$STAGE_DIR/.trace-g/stage6-repair-application.json"

MUTATOR_IMAGE="$(PYTHONPATH="$STAGE_DIR/src:$STAGE_DIR" python3 - "$STAGE_DIR/.trace-g/stage6-model-lock.json" <<'PY'
import sys
from pathlib import Path
from sandbox.fuzzer.v2_stage6_identity import Stage6ModelLock, Stage6Role

lock = Stage6ModelLock.model_validate_json(Path(sys.argv[1]).read_bytes())
print(next(item.image_reference for item in lock.roles if item.role is Stage6Role.MUTATOR))
PY
)"
docker run --rm --network none --entrypoint python "$MUTATOR_IMAGE" -c \
  'import importlib.util; from sandbox.mutation.v2_brief import MutationCandidateResponse; from sandbox.ollama_schema import ollama_compatible_schema; assert importlib.util.find_spec("docker") is None; schema=ollama_compatible_schema(MutationCandidateResponse.model_json_schema()); assert schema.get("type") == "object"; assert schema.get("properties"); print("mutator-import-schema-smoke=passed")'

SOURCE_SWAPPED=0
INSTALL_COMMITTED=0
FAILED_DIR="$PERSIST_ROOT/.repair-failed-$SOURCE_REVISION"
STAGE_JSON="$PERSIST_ROOT/stage.json"
STAGE_JSON_BACKUP="$PERSIST_ROOT/.stage-before-$SOURCE_REVISION.json"
[[ ! -e "$FAILED_DIR" ]] || { echo "ERROR: failed repair preservation path already exists" >&2; exit 1; }
if [[ -f "$STAGE_JSON" ]]; then
  cp "$STAGE_JSON" "$STAGE_JSON_BACKUP"
fi
python3 - "$STAGE_JSON" <<'PY'
import json
import os
import sys
from pathlib import Path

target = Path(sys.argv[1])
temporary = target.with_suffix(".repairing.tmp")
temporary.write_text(json.dumps({"schema_version": "office-v2-stage6-stage-v2", "status": "repairing"}, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, target)
PY

rollback_source_swap() {
  local status=$?
  trap - EXIT
  if [[ "$INSTALL_COMMITTED" -eq 1 ]]; then
    exit "$status"
  fi
  if [[ "$SOURCE_SWAPPED" -eq 1 && -d "$BACKUP_DIR" ]]; then
    for persistent in .trace-g-data reports; do
      if [[ -e "$PROJECT_DIR/$persistent" && ! -e "$BACKUP_DIR/$persistent" ]]; then
        mv "$PROJECT_DIR/$persistent" "$BACKUP_DIR/$persistent"
      fi
    done
    if [[ -d "$PROJECT_DIR" ]]; then
      mv "$PROJECT_DIR" "$FAILED_DIR"
    fi
    mv "$BACKUP_DIR" "$PROJECT_DIR"
  fi
  if [[ -f "$STAGE_JSON_BACKUP" ]]; then
    mv "$STAGE_JSON_BACKUP" "$STAGE_JSON"
  else
    rm -f "$STAGE_JSON"
  fi
  exit "$status"
}
trap rollback_source_swap EXIT
mv "$PROJECT_DIR" "$BACKUP_DIR"
mv "$STAGE_DIR" "$PROJECT_DIR"
SOURCE_SWAPPED=1
for persistent in .trace-g-data reports; do
  if [[ -e "$BACKUP_DIR/$persistent" ]]; then
    mv "$BACKUP_DIR/$persistent" "$PROJECT_DIR/$persistent"
  fi
done
chmod +x "$PROJECT_DIR"/scripts/*.sh
python3 - "$STAGE_JSON" "$SOURCE_REVISION" "$REPAIR_LOCK" <<'PY'
import json
import os
import sys
from pathlib import Path

target = Path(sys.argv[1])
repair = json.load(open(sys.argv[3], encoding="utf-8"))
payload = {
    "schema_version": "office-v2-stage6-stage-v2",
    "status": "ready",
    "source_revision": sys.argv[2],
    "repair_lock_digest": repair["lock_digest"],
}
temporary = target.with_suffix(".tmp")
temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, target)
PY
INSTALL_COMMITTED=1
rm -f "$STAGE_JSON_BACKUP"
trap - EXIT
echo "Office V2 Stage 6 repair applied; previous source preserved at $BACKUP_DIR"
