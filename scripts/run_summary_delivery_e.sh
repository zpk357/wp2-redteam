#!/usr/bin/env bash
# SPEC-SOC-MAT-E-20260925: one authorized batch, no retries or automatic resume.
set -euo pipefail
: "${DEPLOYMENT_ROOT:?set a new deployment root}"
: "${CAMPAIGN_PYTHON:?set deployed venv Python}"
: "${CAMPAIGN_IMAGE:?set the rebuilt sha256 image ID}"
: "${STAGE_STARTED_AT:?set once before build; do not reset}"
RUN_ID="${1:?provide a unique run ID}"
[[ "$RUN_ID" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 2
[[ "$STAGE_STARTED_AT" =~ ^[0-9]+$ ]] || exit 2
[[ "$CAMPAIGN_IMAGE" =~ ^sha256:[a-f0-9]{64}$ ]] || exit 2
cd "$DEPLOYMENT_ROOT/repo"
export PYTHONPATH="$PWD/src:$PWD/agent_image"
DEADLINE=$((STAGE_STARTED_AT + 50400))
DATA="$DEPLOYMENT_ROOT/e-$RUN_ID"
[[ ! -e "$DATA" ]] || { echo 'Refusing existing run directory' >&2; exit 3; }
mkdir -p "$DATA/evidence"
REPORT_ARGS=()
for pair in 1 2 3 4 5 6 7 8; do
  seed="summary-delivery-e-final-$(printf '%02d' "$pair")"
  arms=(coverage_guided random_evolution)
  # Balance time/order effects without selecting order from observed coverage.
  if (( pair % 2 == 0 )); then arms=(random_evolution coverage_guided); fi
  for arm in "${arms[@]}"; do
    remain=$((DEADLINE - $(date +%s)))
    (( remain > 120 )) || { echo 'Shared deadline reached' >&2; exit 4; }
    root="$DATA/$seed-$arm"
    [[ ! -e "$root" ]] || exit 3
    echo "start $seed $arm" | tee -a "$DATA/evidence/stage.log"
    timeout "$remain" "$CAMPAIGN_PYTHON" scripts/run_structured_v1_episode.py two-arm \
      --image "$CAMPAIGN_IMAGE" --fixture summary-delivery-e --arm "$arm" \
      --seed "$seed" --opportunities 16 --parents 10 --data-root "$root" \
      --model-provider ollama --text-provider ollama \
      --model-name qwen3.5:27b-q4_K_M --endpoint http://127.0.0.1:11434 \
      --network-mode host --max-model-calls 16 --max-tool-calls 16 \
      --wall-clock-seconds 600 --max-input-tokens 200000 --max-output-tokens 16000 \
      --max-expense-units 216 --mutation-requests 2 \
      --mutation-input-tokens 8192 --mutation-output-tokens 4096 \
      > "$DATA/evidence/$seed-$arm.log" 2>&1
    echo "done $seed $arm" | tee -a "$DATA/evidence/stage.log"
  done
  REPORT_ARGS+=(--pair "$DATA/$seed-coverage_guided" "$DATA/$seed-random_evolution")
  remain=$((DEADLINE - $(date +%s)))
  (( remain > 0 )) || exit 4
  timeout "$remain" "$CAMPAIGN_PYTHON" scripts/report_summary_delivery_e.py \
    "${REPORT_ARGS[@]}" --output "$DATA/evidence/report.json"
  # Stop the single batch on invalid evidence or missing research conditions.
  "$CAMPAIGN_PYTHON" -c 'import json,sys; r=json.load(open(sys.argv[1])); sys.exit(0 if all(p["valid"] and p["research_conditions"] for p in r["pairs"]) else 5)' \
    "$DATA/evidence/report.json"
done
echo "stage done: $(( $(date +%s) - STAGE_STARTED_AT ))s elapsed" | tee -a "$DATA/evidence/stage.log"
