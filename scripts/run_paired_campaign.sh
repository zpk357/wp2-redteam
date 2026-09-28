#!/bin/bash
# Development precheck for SPEC-SOC-PC-20260923 on fixture summary-delivery-c: 1 pair x 2 arms
# x 16 opportunities.  Scope, identity, budget, stop rules and evidence layout: run package 23.
#
#   * ONE shared stage deadline covers the whole run (run package 20.5): the deadline is computed
#     once and every arm is started with the time that is left, so the arms cannot drift apart;
#   * no --resume: every (pair, arm) gets a brand-new data root that must not already exist;
#   * any non-zero arm exit stops the stage and leaves the site as it is (SPEC 7): no retry, no
#     seed swap, no silent continuation.  Ordinary generation failures are NOT non-zero exits -
#     they are settled inside the arm as a consumed opportunity.
#
#   placeholder values below must be filled from the rebuilt identity before running.
#
# Usage:  bash scripts/run_paired_campaign.sh <run-id>
set -u

ROOT="${DEPLOYMENT_ROOT:?set deployment root after explicit run authorization}"
REPO="$ROOT/repo"
PY="${CAMPAIGN_PYTHON:?set deployed venv python}"
IMAGE="${CAMPAIGN_IMAGE:?set rebuilt immutable image digest}"
# Defaults are this run's frozen scope (run package 23): summary-delivery-c, one pair, 16
# opportunities per arm, one shared 3h stage deadline. Every one is overridable, but an
# override changes the run scope and must be registered before the stage starts.
FIXTURE="${CAMPAIGN_FIXTURE:-summary-delivery-c}"
OPPORTUNITIES="${CAMPAIGN_OPPORTUNITIES:-16}"
PAIRS="${CAMPAIGN_PAIRS:-1}"
STAGE_SECONDS="${CAMPAIGN_STAGE_SECONDS:-10800}"
SEED_PREFIX="${CAMPAIGN_SEED_PREFIX:-dev-precheck-c}"

RUN_ID="${1:?run id is required, e.g. 20260924-0900}"
EVID="$ROOT/evidence"
mkdir -p "$EVID"

cd "$REPO" || exit 2
export PYTHONPATH="$REPO/src:$REPO/agent_image"

T0="${STAGE_STARTED_AT:?set epoch before image rebuild; do not reset before campaigns}"
case "$T0" in *[!0-9]*|'') echo 'invalid stage start' >&2; exit 2;; esac
DEADLINE=$((T0 + STAGE_SECONDS))
{
  echo "run_id=$RUN_ID"
  echo "fixture=$FIXTURE opportunities=$OPPORTUNITIES pairs=$PAIRS"
  echo "image=$IMAGE"
  echo "t0=$T0 deadline=$DEADLINE stage_seconds=$STAGE_SECONDS"
  echo "source_commit=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "image_digest=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || echo unknown)"
} | tee "$EVID/paired-$RUN_ID.stage.log"

for index in $(seq -w 1 "$PAIRS"); do
  pair=$(printf '%s-%02d' "$SEED_PREFIX" "$index")
  for arm in coverage_guided random_evolution; do
    remain=$((DEADLINE - $(date +%s)))
    if [ "$remain" -le 120 ]; then
      echo "stage deadline reached before $pair/$arm; stopping (site preserved)" | tee -a "$EVID/paired-$RUN_ID.stage.log"
      exit 1
    fi
    data_root="data/structured-v1/paired-$RUN_ID-$pair-$arm"
    if [ -e "$data_root" ]; then
      echo "REFUSING: $data_root already exists (no resume, no reuse)" | tee -a "$EVID/paired-$RUN_ID.stage.log"
      exit 3
    fi
    log="$EVID/paired-$RUN_ID-$pair-$arm.log"
    echo "=== $pair $arm (timeout ${remain}s) ===" | tee -a "$EVID/paired-$RUN_ID.stage.log"
    timeout "$remain" "$PY" scripts/run_structured_v1_episode.py two-arm \
      --image "$IMAGE" --fixture "$FIXTURE" --arm "$arm" \
      --seed "$pair" --opportunities "$OPPORTUNITIES" --parents 10 \
      --data-root "$data_root" \
      --model-provider ollama --text-provider ollama \
      --model-name qwen3.5:27b-q4_K_M --endpoint http://127.0.0.1:11434 \
      --network-mode host \
      --max-model-calls 12 --max-tool-calls 12 --wall-clock-seconds 600 \
      --max-input-tokens 200000 --max-output-tokens 16000 --max-expense-units 216 \
      --mutation-requests 2 --mutation-input-tokens 8192 --mutation-output-tokens 4096 \
      > "$log" 2>&1
    code=$?
    echo "$pair $arm exit=$code log=$log" | tee -a "$EVID/paired-$RUN_ID.stage.log"
    if [ "$code" -ne 0 ]; then
      echo "stopping stage after $pair/$arm (SPEC 7: no retry, site preserved)" | tee -a "$EVID/paired-$RUN_ID.stage.log"
      exit "$code"
    fi
  done
  remain=$((DEADLINE - $(date +%s)))
  if [ "$remain" -le 0 ]; then exit 1; fi
  timeout "$remain" "$PY" scripts/report_structured_campaigns.py \
    --guided "data/structured-v1/paired-$RUN_ID-$pair-coverage_guided/checkpoint.json" \
    --evolution "data/structured-v1/paired-$RUN_ID-$pair-random_evolution/checkpoint.json" \
    --output "$EVID/paired-$RUN_ID-$pair.report.json" || exit 1
done

echo "stage done: $(( $(date +%s) - T0 ))s elapsed" | tee -a "$EVID/paired-$RUN_ID.stage.log"
