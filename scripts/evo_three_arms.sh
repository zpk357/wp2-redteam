#!/bin/bash
# TASK-06 real-model validation: 3 arms x 3 seeds x 8 episodes = 72 episodes, serial.
# Arms: no_coverage (control) / risk_only (published two-arm treatment) /
# diagnosis_tps (risk schedule + §16 diagnosis exploitation + TPS variant rotation).
# The control and risk arms are gated to byte-identical behaviour with the
# published 48-episode two-arm run (same seeds, baseline v0 payload wording).

set -u
cd /root/ae/t3
PY="${CAMPAIGN_PYTHON:-/root/ae/venv/bin/python}"
ROOT="${RUN_ROOT:-/root/ae/runs/evo-three}"
MODEL="${CAMPAIGN_MODEL:-qwen3.5:27b-q4_K_M}"

mkdir -p "$ROOT"
for rep in 1 2 3; do
  for arm in no_coverage risk_only diagnosis_tps; do
    echo "=== $(date -Is) rep-0${rep} ${arm} ==="
    "$PY" scripts/run_evolution_campaign.py \
      --stage main --repeat "$rep" --arm "$arm" \
      --episodes 8 --adapter ollama --root "$ROOT" --model "$MODEL" \
      || { echo "ARM FAILED: rep-0${rep} ${arm}; stopping"; exit 1; }
  done
done
echo "=== $(date -Is) all 72 episodes done ==="
