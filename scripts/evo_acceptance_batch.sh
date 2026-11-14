#!/bin/bash
# Acceptance batch: EV-03 replays (6 frozen candidates x (5 reruns + 5 neutral)) then
# competitive-baseline arms (random_uniform / llm_select / pure_random x 3 seeds x 8 eps).
# Serial (one GPU).  Interrupted arms resume in place: frozen selections replay without
# model calls and settled episodes return their sealed traces, so re-running this script
# continues where it stopped.  A failed piece is recorded and skipped, not fatal.

set -u
cd /root/ae/t3
PY=/root/ae/venv/bin/python
EV03_ROOT=/root/ae/runs/ev03
BASE_ROOT=/root/ae/runs/evo-baseline
MODEL=qwen3.5:27b-q4_K_M

for combo in _tmp_srv/ev03/*.json; do
  label=$(basename "$combo" .json)
  echo "=== $(date -Is) EV03 $label ==="
  "$PY" scripts/run_ev03_replay.py --combination "$combo" --label "$label" \
    --reruns 5 --neutral 5 --root "$EV03_ROOT" --adapter ollama --model "$MODEL" \
    || echo "EV03 $label FAILED (recorded, continuing)"
done

for rep in 1 2 3; do
  for arm in random_uniform llm_select pure_random; do
    extra=""
    if [ "$arm" = "pure_random" ]; then extra="--mode random"; fi
    echo "=== $(date -Is) baseline rep-0${rep} ${arm} ==="
    "$PY" scripts/run_evolution_campaign.py --stage main --repeat "$rep" --arm "$arm" \
      $extra --episodes 8 --adapter ollama --root "$BASE_ROOT" --model "$MODEL" \
      || echo "BASELINE rep-0${rep} ${arm} FAILED (recorded, continuing)"
  done
done
echo "=== $(date -Is) acceptance batch done ==="
