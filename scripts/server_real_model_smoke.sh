#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
source scripts/server_env.sh

ENV_FILE="${ENV_FILE:-deploy/.env.server}"
CAMPAIGN_ID="${CAMPAIGN_ID:-qwen3-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
trace_g_validate_campaign_id "$CAMPAIGN_ID"
RUN_CAMPAIGN="${RUN_CAMPAIGN:-1}"
CLEANUP_OLLAMA="${CLEANUP_OLLAMA:-1}"
AGENT_CASE="${AGENT_CASE:-path-absolute-001}"
MODEL_NETWORK="trace-g-model-internal"
OLLAMA_CONTAINER="trace-g-ollama"
PYTHON="${PYTHON:-scripts/server_python.sh}"
CAMPAIGN_MODE="${CAMPAIGN_MODE:-smoke}"
RISK_SCOPE_PATH="config/risk-scope-server-qwen3.yaml"
if [[ "$CAMPAIGN_MODE" == "data" ]]; then
  FUZZER_CONFIG="config/fuzzer-server-data.example.yaml"
elif [[ "$CAMPAIGN_MODE" == "smoke" ]]; then
  FUZZER_CONFIG="config/fuzzer-server.example.yaml"
else
  echo "ERROR: CAMPAIGN_MODE must be smoke or data" >&2
  exit 1
fi
PROFILE_PATH="config/target-profiles.server.yaml"
MODEL_LOCK_PATH=".trace-g/model-lock.json"
RESULT_DIR="reports/server-real-model/$CAMPAIGN_ID"

test -x "$PYTHON" || {
  echo "ERROR: complete scripts/server_stage_offline.sh before this script" >&2
  exit 1
}
test -f "$ENV_FILE" || {
  echo "ERROR: missing $ENV_FILE" >&2
  exit 1
}
test -f "$MODEL_LOCK_PATH" || {
  echo "ERROR: packaged model lock is missing: $MODEL_LOCK_PATH" >&2
  exit 1
}

trace_g_load_server_env "$ENV_FILE"

: "${OLLAMA_IMAGE:?OLLAMA_IMAGE is required}"
: "${OLLAMA_GPU_DEVICE:?OLLAMA_GPU_DEVICE is required}"
: "${MODEL_NAME:?MODEL_NAME is required}"
: "${PROFILE_ID:?PROFILE_ID is required}"
: "${AGENT_IMAGE:?AGENT_IMAGE is required}"

if [[ "$OLLAMA_GPU_DEVICE" == "REPLACE_WITH_GPU_INDEX" ]] ||
   ! [[ "$OLLAMA_GPU_DEVICE" =~ ^[0-9]+$ ]]; then
  echo "ERROR: set OLLAMA_GPU_DEVICE to one reserved physical GPU index" >&2
  exit 1
fi
if [[ "$MODEL_NAME" != "qwen3:8b" ]]; then
  echo "ERROR: this acceptance script is locked to qwen3:8b, observed $MODEL_NAME" >&2
  exit 1
fi
if [[ "$RUN_CAMPAIGN" != "0" && "$RUN_CAMPAIGN" != "1" ]]; then
  echo "ERROR: RUN_CAMPAIGN must be 0 or 1" >&2
  exit 1
fi

for command_name in docker curl nvidia-smi; do
  command -v "$command_name" >/dev/null || {
    echo "ERROR: missing required command: $command_name" >&2
    exit 1
  }
done
docker compose version >/dev/null
nvidia-smi -i "$OLLAMA_GPU_DEVICE" >/dev/null
mkdir -p "$RESULT_DIR"

if [[ "$CLEANUP_OLLAMA" != "0" && "$CLEANUP_OLLAMA" != "1" ]]; then
  echo "ERROR: CLEANUP_OLLAMA must be 0 or 1" >&2
  exit 1
fi
cleanup_ollama() {
  local status=$?
  trap - EXIT
  if [[ "$CLEANUP_OLLAMA" == "1" ]] && docker inspect "$OLLAMA_CONTAINER" >/dev/null 2>&1; then
    docker inspect "$OLLAMA_CONTAINER" > "$RESULT_DIR/ollama-final-inspect.json" 2>/dev/null || true
    docker logs "$OLLAMA_CONTAINER" > "$RESULT_DIR/ollama-final.log" 2>&1 || true
    docker stop "$OLLAMA_CONTAINER" >> "$RESULT_DIR/ollama-cleanup.log" 2>&1 || true
    docker compose --env-file "$ENV_FILE" -f deploy/docker-compose.server.yaml down >> "$RESULT_DIR/ollama-cleanup.log" 2>&1 || true
  fi
  exit "$status"
}
trap cleanup_ollama EXIT

docker compose --env-file "$ENV_FILE" \
  -f deploy/docker-compose.server.yaml \
  up -d --wait --pull never ollama

test "$(docker network inspect -f '{{.Internal}}' "$MODEL_NETWORK")" = "true"
test "$(docker network inspect -f '{{index .Labels "trace-g.network-policy"}}' "$MODEL_NETWORK")" = "ollama-only"
test "$(docker inspect -f '{{.State.Health.Status}}' "$OLLAMA_CONTAINER")" = "healthy"

"$PYTHON" scripts/verify_server_locks.py model \
  --lock "$MODEL_LOCK_PATH" \
  --ollama-image "$OLLAMA_IMAGE" \
  --endpoint http://ollama:11434 \
  --output "$RESULT_DIR/packaged-model-verification.json" |
  tee "$RESULT_DIR/packaged-model-verification.stdout.json"

TRACE_G_CONTROLLER_NETWORK="container:$OLLAMA_CONTAINER" "$PYTHON" scripts/lock_target_profile.py \
  --profile-id "$PROFILE_ID" \
  --model-name "$MODEL_NAME" \
  --ollama-image "$OLLAMA_IMAGE" \
  --ollama-container "$OLLAMA_CONTAINER" \
  --ollama-admin-endpoint http://127.0.0.1:11434 \
  --image "$AGENT_IMAGE" \
  --risk-scope-path "$RISK_SCOPE_PATH" \
  --output "$PROFILE_PATH" |
  tee "$RESULT_DIR/profile-lock.json"

TRACE_G_CONTROLLER_NETWORK="container:$OLLAMA_CONTAINER" "$PYTHON" scripts/verify_target_profile.py \
  --profile-path "$PROFILE_PATH" \
  --profile-id "$PROFILE_ID" \
  --ollama-container "$OLLAMA_CONTAINER" \
  --ollama-admin-endpoint http://127.0.0.1:11434 |
  tee "$RESULT_DIR/profile-verify.json"

MODEL_DIGEST="$("$PYTHON" - "$PROFILE_PATH" "$PROFILE_ID" <<'PY'
import sys
import yaml

path, profile_id = sys.argv[1:]
profiles = yaml.safe_load(open(path, encoding="utf-8"))["profiles"]
matches = [item for item in profiles if item["profile_id"] == profile_id]
if len(matches) != 1:
    raise SystemExit("profile lookup did not return exactly one result")
profile = matches[0]
if profile["model_provider"] != "ollama":
    raise SystemExit("locked target profile is not an Ollama profile")
digest = profile.get("model_digest")
if not digest or not digest.startswith("sha256:"):
    raise SystemExit("locked target profile has no valid model digest")
print(digest)
PY
)"
LOCKED_MODEL_DIGEST="$("$PYTHON" - "$MODEL_LOCK_PATH" <<'PY'
import json
import sys

lock = json.load(open(sys.argv[1], encoding="utf-8"))
digest = lock.get("model_digest")
if not isinstance(digest, str) or not digest.startswith("sha256:"):
    raise SystemExit("packaged model lock has no valid digest")
print(digest)
PY
)"
if [[ "$MODEL_DIGEST" != "$LOCKED_MODEL_DIGEST" ]]; then
  echo "ERROR: target profile digest differs from the packaged model lock" >&2
  exit 1
fi


"$PYTHON" - "$MODEL_NAME" "$MODEL_DIGEST" "$RESULT_DIR/ollama-warmup.json" <<'PY'
import json
import pathlib
import sys
import urllib.request

model_name, model_digest, output_path = sys.argv[1:]
tags = json.load(urllib.request.urlopen("http://ollama:11434/api/tags", timeout=15))
matches = [item for item in tags["models"] if item.get("name") == model_name]
observed_digest = matches[0].get("digest") if len(matches) == 1 else None
if isinstance(observed_digest, str) and not observed_digest.startswith("sha256:"):
    observed_digest = f"sha256:{observed_digest.lower()}"
if observed_digest != model_digest:
    raise SystemExit("Ollama tag does not match the locked model digest")
payload = {
    "model": model_name,
    "prompt": "Reply with only TRACE_G_READY.",
    "stream": False,
    "think": False,
    "options": {"temperature": 0, "num_predict": 32},
}
request = urllib.request.Request(
    "http://ollama:11434/api/generate",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
response = json.load(urllib.request.urlopen(request, timeout=300))
if not response.get("done"):
    raise SystemExit("Ollama warm-up did not complete")
pathlib.Path(output_path).write_text(
    json.dumps(response, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(f"Real model warm-up completed: {model_name}@{model_digest}")
PY

docker exec "$OLLAMA_CONTAINER" ollama ps | tee "$RESULT_DIR/ollama-ps.txt"
grep -qi 'GPU' "$RESULT_DIR/ollama-ps.txt" || {
  echo "ERROR: ollama ps does not report GPU residency" >&2
  exit 1
}
nvidia-smi -i "$OLLAMA_GPU_DEVICE" > "$RESULT_DIR/nvidia-smi.txt"
GPU_MEMORY_USED="$(nvidia-smi -i "$OLLAMA_GPU_DEVICE" \
  --query-gpu=memory.used --format=csv,noheader,nounits | head -n 1 | tr -d ' ')"
if ! [[ "$GPU_MEMORY_USED" =~ ^[0-9]+$ ]] || (( GPU_MEMORY_USED < 1024 )); then
  echo "ERROR: expected at least 1024 MiB GPU memory after Qwen warm-up, observed $GPU_MEMORY_USED" >&2
  exit 1
fi

"$PYTHON" -m sandbox.cli run \
  --case "$AGENT_CASE" \
  --image "$AGENT_IMAGE" \
  --model-provider ollama \
  --model-name "$MODEL_NAME" \
  --model-digest "$MODEL_DIGEST" \
  --ollama-endpoint http://ollama:11434 \
  --model-network "$MODEL_NETWORK" |
  tee "$RESULT_DIR/agent-run.json"

"$PYTHON" - "$RESULT_DIR/agent-run.json" "$MODEL_NAME" "$MODEL_DIGEST" <<'PY'
import json
import pathlib
import sys

run_path, model_name, model_digest = sys.argv[1:]
run = json.load(open(run_path, encoding="utf-8"))
if run["execution"]["status"] != "succeeded" or not run["container_removed"]:
    raise SystemExit("real-model Agent smoke did not succeed and clean up")
trajectory_path = pathlib.Path(run["trajectory_path"])
events = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]
expected = f"ollama:{model_name}@{model_digest}"
model_sources = [event["source"] for event in events if event["event_type"] == "model_start"]
if not model_sources or any(source != expected for source in model_sources):
    raise SystemExit(f"trajectory model source mismatch: expected {expected}, got {model_sources}")
if any("fake" in str(event.get("source", "")).casefold() for event in events):
    raise SystemExit("trajectory contains a Fake model source")
print(f"Trajectory proves real Agent model usage: {expected}")
PY

bash scripts/server_validate_trace_workspace.sh "$CAMPAIGN_ID" "$MODEL_DIGEST" |
  tee "$RESULT_DIR/trace-workspace-validation.log"

if [[ "$RUN_CAMPAIGN" == "1" ]]; then
  COMMON=(
    --config "$FUZZER_CONFIG"
    --campaign-id "$CAMPAIGN_ID"
    --target-profiles-path "$PROFILE_PATH"
    --risk-scope-path "$RISK_SCOPE_PATH"
    --image "$AGENT_IMAGE"
    --model-provider ollama
    --model-name "$MODEL_NAME"
    --model-digest "$MODEL_DIGEST"
    --ollama-endpoint http://ollama:11434
    --model-network "$MODEL_NETWORK"
    --mutation-provider ollama
    --mutation-model-name "$MODEL_NAME"
    --mutation-model-digest "$MODEL_DIGEST"
    --mutation-endpoint http://127.0.0.1:11434
  )

  if [[ "$CAMPAIGN_MODE" == "data" ]]; then
    mapfile -t INITIAL_CASES < <("$PYTHON" -m sandbox.cli list-cases)
  else
    INITIAL_CASES=(
      path-absolute-001
      command-destructive-001
      network-egress-001
    )
  fi
  SEED_ARGS=()
  for case_id in "${INITIAL_CASES[@]}"; do
    SEED_ARGS+=(--initial-case "$case_id")
  done

  TRACE_G_CONTROLLER_NETWORK="container:$OLLAMA_CONTAINER" "$PYTHON" -m sandbox.cli campaign create "${COMMON[@]}" "${SEED_ARGS[@]}" |
    tee "$RESULT_DIR/campaign-create.json"

  TRACE_G_CONTROLLER_NETWORK="container:$OLLAMA_CONTAINER" "$PYTHON" -m sandbox.cli campaign run "${COMMON[@]}" |
    tee "$RESULT_DIR/campaign-run.json"

  "$PYTHON" -m sandbox.cli campaign export \
    --campaign-id "$CAMPAIGN_ID" \
    --store-root data/fuzzing \
    --include-prompts \
    --output "$RESULT_DIR/campaign-export.json"

  "$PYTHON" -m sandbox.cli coverage export     --campaign-id "$CAMPAIGN_ID"     --coverage-root data/coverage     --taxonomy-path config/risk-taxonomy.yaml     --risk-scope-path "$RISK_SCOPE_PATH"     --output "$RESULT_DIR/coverage-export.json"

  "$PYTHON" -m sandbox.cli mutate export     --full     --campaign-id "$CAMPAIGN_ID"     --mutation-root data/mutations     --coverage-root data/coverage     --taxonomy-path config/risk-taxonomy.yaml     --risk-scope-path "$RISK_SCOPE_PATH"     --operator-registry-path config/mutation-operators.yaml     --output "$RESULT_DIR/mutation-export.json"

  "$PYTHON" scripts/build_server_learning_dataset.py     --campaign-id "$CAMPAIGN_ID"     --campaign-export "$RESULT_DIR/campaign-export.json"     --coverage-export "$RESULT_DIR/coverage-export.json"     --mutation-export "$RESULT_DIR/mutation-export.json"     --trajectory-dir data/trajectories     --output-dir "$RESULT_DIR/learning"     --taxonomy-path config/risk-taxonomy.yaml     --risk-scope-path "$RISK_SCOPE_PATH"     --operator-registry-path config/mutation-operators.yaml     --fuzzer-config "$FUZZER_CONFIG"     --target-profile-path "$PROFILE_PATH" |
    tee "$RESULT_DIR/learning-export.json"
  "$PYTHON" - "$RESULT_DIR/campaign-export.json" "$MODEL_NAME" "$MODEL_DIGEST" \
    "$RESULT_DIR/campaign-validation.json" "$CAMPAIGN_MODE" <<'PY'
import json
import pathlib
import sys

export_path, model_name, model_digest, validation_path, campaign_mode = sys.argv[1:]
payload = json.load(open(export_path, encoding="utf-8"))
manifest = payload["manifest"]
expected_source = f"ollama:{model_name}@{model_digest}"
checks = {
    "agent_provider_is_ollama": manifest["agent_model_name"] == model_name,
    "agent_digest_locked": manifest["agent_model_digest"] == model_digest,
    "mutation_provider_is_ollama": manifest["mutation_provider"] == "ollama",
    "mutation_model_locked": manifest["mutation_model_digest"] == model_digest,
    "corpus_nonempty": len(payload["corpus"]) > 0,
    "mutation_work_committed": any(
        item["source"]["kind"] == "mutation" and item["status"] == "committed"
        for item in payload["work_items"]
    ),
    "mutated_seed_promoted": any(seed["origin"] == "mutation" for seed in payload["seeds"]),
    "second_generation_requirement_satisfied": (
        campaign_mode != "data"
        or any(
            seed["origin"] == "mutation" and seed["mutation_depth"] >= 2
            for seed in payload["seeds"]
        )
    ),
}
trajectory_count = 0
for item in payload["work_items"]:
    raw_path = item.get("trajectory_path")
    if not raw_path:
        continue
    path = pathlib.Path(raw_path)
    if not path.is_file():
        raise SystemExit(f"missing campaign trajectory: {path}")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    sources = [event["source"] for event in events if event["event_type"] == "model_start"]
    if not sources or any(source != expected_source for source in sources):
        raise SystemExit(f"campaign trajectory {path} used unexpected model sources: {sources}")
    if any("fake" in str(event.get("source", "")).casefold() for event in events):
        raise SystemExit(f"campaign trajectory {path} contains a Fake model source")
    trajectory_count += 1
checks["all_campaign_trajectories_use_locked_model"] = trajectory_count > 0

failed = sorted(name for name, passed in checks.items() if not passed)
summary = {
    "schema_version": "1.0",
    "model_name": model_name,
    "model_digest": model_digest,
    "expected_trajectory_source": expected_source,
    "campaign_id": manifest["campaign_id"],
    "campaign_mode": campaign_mode,
    "trajectory_count": trajectory_count,
    "max_mutation_depth": max(seed["mutation_depth"] for seed in payload["seeds"]),
    "checks": checks,
    "passed": not failed,
    "failed_checks": failed,
}
pathlib.Path(validation_path).write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
if failed:
    raise SystemExit(f"campaign acceptance failed: {failed}")
print("Real-model automated mutation loop passed.")
PY

  ACCEPTANCE_ARGS=(
    --campaign-id "$CAMPAIGN_ID"
    --result-dir "$RESULT_DIR"
    --output "$RESULT_DIR/weeks-1-5-validation.json"
  )
  if [[ "$CAMPAIGN_MODE" == "data" ]]; then
    ACCEPTANCE_ARGS+=(--require-golden-pool)
  fi
  "$PYTHON" scripts/build_weeks_1_5_validation.py "${ACCEPTANCE_ARGS[@]}" |
    tee "$RESULT_DIR/weeks-1-5-validation.log"

  if docker ps -aq --filter "label=trace-g.campaign-id=$CAMPAIGN_ID" | grep -q .; then
    echo "ERROR: campaign containers remain after completion" >&2
    exit 1
  fi
fi

docker compose --env-file "$ENV_FILE" \
  -f deploy/docker-compose.server.yaml logs --no-color ollama \
  > "$RESULT_DIR/ollama.log"

echo "Real-model validation artifacts: $RESULT_DIR"
echo "Run scripts/server_export_results.sh $CAMPAIGN_ID before releasing the server."
