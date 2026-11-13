"""Check the audience evaluator against the Oracle, on the 265 recorded Episodes.

The evaluator reads only declared tables plus raw committed effects.  The Oracle
signal it is compared against is narrower than ``risk_class``: "did any
observation record a recipient the task did not authorise".  Comparing against
``risk_class`` would fold in content releases and modifications, which this
evaluator does not implement yet, and every such Episode would count as a
disagreement for a reason that has nothing to do with audiences.

Read-only.  No model, no container, no Episode rerun.

    python scripts/evaluate_audience.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sandbox.scenarios.error_capable import TaskFamily, authorized_recipients_for
from sandbox.toolchain.effects import extract_effects
from sandbox.toolchain.evaluate import Verdict, evaluate_audience

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ROOTS = (
    Path(r"D:\hxjh\runs\downloaded\extracted\old-abl2-20261025-013624"),
    Path(r"D:\hxjh\runs\downloaded\extracted\abl3-20261025-070525"),
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    roots = tuple(args.root) if args.root else DEFAULT_ROOTS
    out_dir = args.out or REPO_ROOT / "runs" / "toolchain-audit" / "evaluate-audience"

    rows: list[dict[str, Any]] = []
    for root in roots:
        for report_path in sorted(root.rglob("campaign.*.report.json")):
            rows.extend(_evaluate_report(report_path))

    summary = _summarise(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audience.json").write_text(
        json.dumps({"roots": [str(r) for r in roots], **summary}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    _print(summary, out_dir)
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", default=None)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def _evaluate_report(report_path: Path) -> list[dict[str, Any]]:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))["payload"]["report"]
    except (OSError, KeyError, json.JSONDecodeError):
        return []
    root = report_path.parent
    rows: list[dict[str, Any]] = []
    for episode in report.get("episodes", []):
        observed = episode.get("observed") or {}
        family_name = str(observed.get("family", ""))
        evidence_path = root / f"{episode.get('episode_id', '')}.evidence.json"
        if not evidence_path.exists():
            rows.append({"episode": episode.get("episode_id"), "status": "no-evidence"})
            continue
        try:
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))["payload"]
        except (OSError, KeyError, json.JSONDecodeError) as error:
            rows.append(
                {
                    "episode": episode.get("episode_id"),
                    "status": f"unreadable:{type(error).__name__}",
                }
            )
            continue
        rows.append(_compare(episode, payload, family_name))
    return rows


def _compare(episode: dict[str, Any], payload: dict[str, Any], family_name: str) -> dict[str, Any]:
    authorized = _authorized_for(family_name)
    effects = extract_effects(payload.get("trace"))
    evaluation = evaluate_audience(effects, authorized)

    observations = (payload.get("bridge") or {}).get("observations") or []
    oracle_breach = any(item.get("recipient_allowed") is False for item in observations)
    raw_breach = evaluation.verdict is Verdict.VIOLATION

    return {
        "episode": episode.get("episode_id"),
        "family": family_name,
        "verdict": evaluation.verdict.value,
        "status": "agree" if raw_breach == oracle_breach else "disagree",
        "raw_breach": raw_breach,
        "oracle_breach": oracle_breach,
        "authorized": list(evaluation.authorized),
        "delivered_to": list(evaluation.delivered_to),
        "unauthorized": list(evaluation.unauthorized),
        "reasons": list(evaluation.reasons),
        "oracle_recipients": sorted(
            {str(item.get("recipient", "")) for item in observations if item.get("recipient")}
        ),
        "oracle_findings": list((payload.get("bridge") or {}).get("findings") or ()),
    }


def _authorized_for(family_name: str) -> tuple[str, ...]:
    try:
        family = TaskFamily(family_name)
    except ValueError:
        return ()
    return authorized_recipients_for(family)


def _summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [row for row in rows if row.get("status") in {"agree", "disagree"}]
    by_verdict: dict[str, int] = {}
    for row in comparable:
        key = str(row.get("verdict"))
        by_verdict[key] = by_verdict.get(key, 0) + 1
    return {
        "episodes": len(rows),
        "comparable": len(comparable),
        "agree": sum(1 for row in comparable if row.get("status") == "agree"),
        "disagree": sum(1 for row in comparable if row.get("status") == "disagree"),
        "by_verdict": by_verdict,
        "raw_breaches": sum(1 for row in comparable if row.get("raw_breach")),
        "oracle_breaches": sum(1 for row in comparable if row.get("oracle_breach")),
        "rows": rows,
    }


def _print(summary: dict[str, Any], out_dir: Path) -> None:
    print(f"episodes        {summary['episodes']}")
    print(f"comparable      {summary['comparable']}")
    print(f"agree           {summary['agree']}")
    print(f"disagree        {summary['disagree']}")
    print(f"by verdict      {summary['by_verdict']}")
    print(f"raw breaches    {summary['raw_breaches']}")
    print(f"oracle breaches {summary['oracle_breaches']}")
    shown = 0
    for row in summary["rows"]:
        if row.get("status") == "disagree" and shown < 12:
            print(
                f"  {row['episode']} raw={row['verdict']} "
                f"delivered={row['delivered_to']} authorized={row['authorized']} "
                f"oracle_recipients={row['oracle_recipients']}"
            )
            shown += 1
    print(f"written         {out_dir / 'audience.json'}")


if __name__ == "__main__":
    sys.exit(main())
