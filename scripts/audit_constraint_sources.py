"""Measure how often the reusable constraint tables can be recovered from a run root.

Reads journals only.  No model, no container, no Episode rerun.  The number this
prints is the answer to ``TASK.md`` TASK-01: if both tables cannot be recovered
for every Episode, the "one evaluator over declared data" plan has a product gap
to close before anything else.

    python scripts/audit_constraint_sources.py
    python scripts/audit_constraint_sources.py --root <run root> --out runs/audit
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from sandbox.scenarios.error_capable import ConstraintDisclosure
from sandbox.toolchain.constraints import load_world_constraints

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ROOTS = (
    Path(r"D:\hxjh\runs\downloaded\extracted\old-abl2-20261025-013624"),
    Path(r"D:\hxjh\runs\downloaded\extracted\abl3-20261025-070525"),
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        action="append",
        default=None,
        help="run root to scan; repeatable (default: the two 2026-10-25 roots)",
    )
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    roots = tuple(args.root) if args.root else DEFAULT_ROOTS
    out_dir = args.out or _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    episodes: list[dict[str, Any]] = []
    for root in roots:
        for journal in sorted(root.rglob("*.journal.json")):
            episodes.append(_inspect(journal, root))

    report = _summarise(episodes)
    (out_dir / "audit.json").write_text(
        json.dumps({"roots": [str(r) for r in roots], **report}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    _print(report, out_dir)
    return 0 if report["totals"]["complete"] == report["totals"]["episodes"] else 1


def _inspect(journal_path: Path, root: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "journal": str(journal_path),
        "arm": _arm_label(journal_path, root),
    }
    try:
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        record["error"] = f"{type(error).__name__}: {error}"
        return record

    constraints = load_world_constraints(payload.get("world_state"))
    record["constraints"] = constraints.summary()
    record["third_table"] = _third_table(payload)
    return record


def _third_table(payload: Any) -> dict[str, Any]:
    """Whether the disclosure landed, and whether its digest still holds."""

    if not isinstance(payload, dict):
        return {"present": False, "valid": False}
    raw = payload.get("constraints")
    if not isinstance(raw, dict):
        return {"present": False, "valid": False}
    try:
        disclosure = ConstraintDisclosure.model_validate(raw)
    except ValueError as error:
        return {"present": True, "valid": False, "error": type(error).__name__}
    return {
        "present": True,
        "valid": disclosure.digest_is_valid(),
        "facts": len(disclosure.facts),
        "restricted_facts": sum(1 for fact in disclosure.facts if fact.restricted),
        "authorized_recipients": len(disclosure.authorized_recipients),
    }


def _arm_label(journal_path: Path, root: Path) -> str:
    """Root-relative arm path, so 334 Episodes do not collapse into one bucket."""

    try:
        return str(journal_path.parent.parent.relative_to(root)).replace("\\", "/")
    except ValueError:
        return journal_path.parent.parent.name


def _summarise(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(episodes)
    complete = sum(1 for item in episodes if item.get("constraints", {}).get("complete"))
    audience = sum(1 for item in episodes if item.get("constraints", {}).get("audience_table"))
    policy = sum(1 for item in episodes if item.get("constraints", {}).get("policy_table"))
    third_present = sum(1 for item in episodes if item.get("third_table", {}).get("present"))
    third_valid = sum(1 for item in episodes if item.get("third_table", {}).get("valid"))
    unreadable = sum(1 for item in episodes if "error" in item)
    malformed_rows = sum(len(item.get("constraints", {}).get("malformed", ())) for item in episodes)
    grants = sorted({item.get("constraints", {}).get("grants", 0) for item in episodes})
    policies = sorted({item.get("constraints", {}).get("policies", 0) for item in episodes})
    facts_per_episode = sorted(
        {
            item.get("third_table", {}).get("facts", 0)
            for item in episodes
            if item.get("third_table", {}).get("present")
        }
    )

    totals = {
        "episodes": total,
        "audience_table": audience,
        "policy_table": policy,
        "complete": complete,
        "complete_ratio": round(complete / total, 4) if total else 0.0,
        "third_table_present": third_present,
        "third_table_valid": third_valid,
        "facts_per_disclosed_episode": _spread(facts_per_episode),
        "unreadable_journals": unreadable,
        "malformed_rows": malformed_rows,
        "grant_rows_per_episode": _spread(grants),
        "policy_rows_per_episode": _spread(policies),
    }
    by_arm: dict[str, dict[str, int]] = {}
    for item in episodes:
        arm = item.get("arm") or "?"
        bucket = by_arm.setdefault(arm, {"episodes": 0, "complete": 0})
        bucket["episodes"] += 1
        bucket["complete"] += 1 if item.get("constraints", {}).get("complete") else 0
    return {"totals": totals, "by_arm": by_arm, "episodes": episodes}


def _spread(values: list[int]) -> dict[str, int]:
    if not values:
        return {}
    return {"min": values[0], "max": values[-1], "distinct": len(values)}


def _default_out_dir() -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "runs" / "toolchain-audit" / stamp


def _print(report: dict[str, Any], out_dir: Path) -> None:
    totals = report["totals"]
    print(f"episodes                    {totals['episodes']}")
    print(f"audience table recoverable  {totals['audience_table']}")
    print(f"policy table recoverable    {totals['policy_table']}")
    print(f"both tables recoverable     {totals['complete']} ({totals['complete_ratio']:.1%})")
    print(f"third table disclosed       {totals['third_table_present']}")
    print(f"third table digest valid    {totals['third_table_valid']}")
    print(f"facts per disclosed episode {totals['facts_per_disclosed_episode']}")
    print(f"unreadable journals         {totals['unreadable_journals']}")
    print(f"malformed constraint rows   {totals['malformed_rows']}")
    print(f"grant rows per episode      {totals['grant_rows_per_episode']}")
    print(f"policy rows per episode     {totals['policy_rows_per_episode']}")
    for arm, bucket in sorted(report["by_arm"].items()):
        print(f"  {arm:12s} episodes={bucket['episodes']:3d} complete={bucket['complete']:3d}")
    print(f"written                     {out_dir / 'audit.json'}")


if __name__ == "__main__":
    sys.exit(main())
