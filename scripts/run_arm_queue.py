"""Run arms one after another from a queue file.

Why a file
----------
The set of arms is an experiment design, not a property of the runner.  Hard-coding five of them
into a script means the next arm needs a code change, and a design that lives in code cannot be
diffed against a design that lives in a paper.  Each line here names one arm, what steers it, and
nothing about how many arms there are.

Format (JSON, one object per arm)::

    [
      {"name": "random",          "arm": "random"},
      {"name": "guided-full",     "arm": "guided", "guidance": "full"},
      {"name": "guided-risk",     "arm": "guided", "guidance": "risk"}
    ]

`guidance` is any name registered in `GUIDANCE_PROFILES`; a new arm is a registry entry plus a
line here.  Every arm gets its own run root under `--base`, so one arm failing cannot touch the
artifacts of another, and the queue carries on.

Usage::

    python scripts/run_arm_queue.py --base /root/ae/runs/five-20261023 \
        --arms config/arms/five-arms.json --stage main --repeat 1 --episodes 100 ...
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "agent_image"))


def load_arms(path: Path) -> list[dict[str, str]]:
    import tomllib

    raw = path.read_bytes()
    if path.suffix == ".toml":
        document = tomllib.loads(raw.decode("utf-8"))
        arms = document.get("arms") or []
    else:
        arms = json.loads(raw.decode("utf-8"))
    if not isinstance(arms, list) or not arms:
        raise SystemExit(f"{path} names no arms")
    for item in arms:
        if "name" not in item or "arm" not in item:
            raise SystemExit(f"{path}: every arm needs `name` and `arm`: {item!r}")
    return arms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--arms", type=Path, required=True)
    parser.add_argument("--stage", choices=("pilot", "main"), required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--episodes", type=int, default=100)
    parser.add_argument("--fixture", default="substantive-office-01")
    parser.add_argument("--adapter", default="ollama")
    parser.add_argument("--model", default="qwen3.5:27b-q4_K_M")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--num-ctx", type=int, default=20480)
    parser.add_argument("--num-predict", type=int, default=2048)
    parser.add_argument("--max-tool-requests", type=int, default=24)
    parser.add_argument("--max-continuations", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--skip-existing", action="store_true")
    arguments = parser.parse_args()

    arms = load_arms(arguments.arms)
    print(f"{len(arms)} arms from {arguments.arms}", flush=True)
    failures: list[str] = []
    for item in arms:
        name = item["name"]
        root = arguments.base / name
        root.mkdir(parents=True, exist_ok=True)
        if arguments.skip_existing and any(root.rglob("*.evidence.json")):
            print(f"=== {name}: already has evidence, skipped", flush=True)
            continue
        command = [
            sys.executable,
            str(ROOT / "scripts" / "run_neighborhood_campaign.py"),
            "--stage",
            arguments.stage,
            "--repeat",
            str(arguments.repeat),
            "--episodes",
            str(arguments.episodes),
            "--arm",
            item["arm"],
            "--fixture",
            arguments.fixture,
            "--adapter",
            arguments.adapter,
            "--model",
            arguments.model,
            "--endpoint",
            arguments.endpoint,
            "--num-ctx",
            str(arguments.num_ctx),
            "--num-predict",
            str(arguments.num_predict),
            "--max-tool-requests",
            str(arguments.max_tool_requests),
            "--max-continuations",
            str(arguments.max_continuations),
            "--timeout",
            str(arguments.timeout),
            "--root",
            str(root),
        ]
        if item.get("guidance") and item["arm"] == "guided":
            command += ["--guidance", item["guidance"]]
        print(f"=== {name} start {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}", flush=True)
        started = time.time()
        code = subprocess.call(command, cwd=ROOT)
        elapsed = time.time() - started
        print(f"=== {name} exit={code} elapsed={elapsed / 60:.1f}min", flush=True)
        if code != 0:
            failures.append(name)

    print("ALL_ARMS_DONE" if not failures else f"ARMS_FAILED={','.join(failures)}", flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
