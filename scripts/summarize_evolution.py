"""Collect the TASK-05 read-outs from every arm root into one table.

One ``evolution-campaign.json`` per arm is the input; this script never runs an
Episode, so it can be pointed at a half-finished experiment and will report only
the arms that exist.  What it prints is exactly the comparison TASK-05 asks for:
same budget, distinct confirmed violations, the discovery curve, tokens per
violation, and repeat exploration -- per arm, never merged.

    python scripts/summarize_evolution.py --root /root/ae/runs/evo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rows: list[dict[str, Any]] = []
    for root in args.root:
        for path in sorted(Path(root).rglob("evolution-campaign.json")):
            rows.append(_row(path))
    if not rows:
        print("no evolution-campaign.json found", file=sys.stderr)
        return 1
    _print(rows)
    out = args.out
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"arms": rows}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"written {out}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def _row(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scheduler = payload.get("scheduler") or {}
    report = payload.get("report") or {}
    episodes = report.get("episodes") or []
    # A violation is per Episode in the report's own record; the scheduler's
    # counter is a second view of the same fact and disagreement means a bug.
    violations = [
        episode for episode in episodes
        if (episode.get("observed") or {}).get("risk_class") == "violation"
    ]
    return {
        "root": str(path.parent),
        "arm": payload.get("arm"),
        "stage": payload.get("stage"),
        "repeat": payload.get("repeat"),
        "seed": payload.get("seed"),
        "episodes": len(episodes),
        "violations": len(violations),
        "scheduler_violations": scheduler.get("violations"),
        "behaviour_keys": scheduler.get("behaviour_keys"),
        "risk_keys": scheduler.get("risk_keys"),
        "risk_levels": scheduler.get("risk_levels"),
        "repeat_exploration": scheduler.get("repeat_exploration"),
        "tokens": scheduler.get("tokens"),
        "tokens_per_violation": scheduler.get("tokens_per_violation"),
        "discovery_curve": _curve(episodes),
        "target_reach": payload.get("readouts", {}).get("target_reach"),
    }


def _curve(episodes: list[dict[str, Any]]) -> list[int]:
    """Cumulative violations after each Episode -- the discovery curve."""

    total = 0
    curve: list[int] = []
    for episode in episodes:
        if (episode.get("observed") or {}).get("risk_class") == "violation":
            total += 1
        curve.append(total)
    return curve


def _print(rows: list[dict[str, Any]]) -> None:
    print(
        f"{'arm':<16}{'rep':<5}{'eps':<5}{'viol':<6}{'behaviour':<11}"
        f"{'risk':<6}{'repeat':<8}{'tokens':<9}{'tok/viol':<10}curve"
    )
    for row in rows:
        print(
            f"{str(row['arm']):<16}{str(row['repeat']):<5}{row['episodes']:<5}"
            f"{row['violations']:<6}{str(row['behaviour_keys']):<11}"
            f"{str(row['risk_keys']):<6}{str(row['repeat_exploration']):<8}"
            f"{str(row['tokens']):<9}{str(row['tokens_per_violation']):<10}"
            f"{row['discovery_curve']}"
        )
    # The one consistency check worth having: the scheduler's counter and the
    # report's own record must agree, or one of them is lying about a violation.
    for row in rows:
        if row["scheduler_violations"] not in (None, row["violations"]):
            print(
                f"MISMATCH {row['root']}: report={row['violations']} "
                f"scheduler={row['scheduler_violations']}"
            )


if __name__ == "__main__":
    sys.exit(main())
