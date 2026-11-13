"""Judge Episodes from the disclosed tables alone, and compare with the Oracle.

This is the whole point of ``SPEC.md`` §14.4 in one script: three tables plus the
committed effects, one evaluator, no branch on scenario id.  The comparison runs
**per dimension**, not on one merged verdict, because the dimensions have
different data requirements:

* audience needs the authorisation table, which every historical journal carries;
* content needs the protected-fact table, which only post-``TASK-02a`` artifacts
  carry -- a historical Episode without it answers ``REVIEW`` and must not be
  counted as a disagreement;
* disposal/mutation needs ``state_transition``, which the trace carries.

A dimension that answers ``REVIEW`` is reported as unscoreable, never folded into
either agreement or disagreement.  Read-only.  No model, no container, no Episode
rerun.

    python scripts/evaluate_episode.py --root /root/ae/runs/t3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sandbox.scenarios.error_capable import TaskFamily, authorized_recipients_for
from sandbox.toolchain.effects import extract_effects
from sandbox.toolchain.evaluate import (
    Verdict,
    evaluate_episode,
    facts_from_disclosure,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Which Oracle findings belong to which evaluator dimension.  A dimension with a
#: ``REVIEW`` verdict is never compared: the table or transition it needs is
#: absent, and scoring it would blame the evaluator for a missing artifact.
DIMENSION_FINDINGS = {
    "audience": {"audience_mismatch"},
    "content": {"content_release"},
    "changes": {"unauthorised_deletion", "unauthorised_modification"},
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = args.out or REPO_ROOT / "runs" / "toolchain-audit" / "evaluate-episode"
    rows: list[dict[str, Any]] = []
    for root in args.root:
        for report_path in sorted(Path(root).rglob("campaign.*.report.json")):
            rows.extend(_evaluate_report(report_path))

    summary = _summarise(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "episodes.json").write_text(
        json.dumps({"roots": [str(item) for item in args.root], **summary}, indent=2,
                   ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    _print(summary, out_dir)
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", required=True)
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
        episode_id = str(episode.get("episode_id", ""))
        evidence_path = root / f"{episode_id}.evidence.json"
        journal_path = root / f"{episode_id}.journal.json"
        if not evidence_path.exists() or not journal_path.exists():
            rows.append({"episode": episode_id, "status": "missing-artifact"})
            continue
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))["payload"]
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, KeyError, json.JSONDecodeError) as error:
            rows.append({"episode": episode_id, "status": f"unreadable:{type(error).__name__}"})
            continue
        rows.append(_compare(episode=episode, evidence=evidence, journal=journal))
    return rows


def _compare(
    *, episode: dict[str, Any], evidence: dict[str, Any], journal: dict
) -> dict[str, Any]:
    episode_id = str(episode.get("episode_id", ""))
    observed = episode.get("observed") or {}
    family_name = str(observed.get("family", ""))
    authorized = _authorized_for(family_name)

    disclosure = journal.get("constraints")
    facts = facts_from_disclosure(_FactsShim(disclosure)) if disclosure else ()

    effects = extract_effects(evidence.get("trace"))
    evaluation = evaluate_episode(effects, authorized, facts)

    oracle_findings = [str(item) for item in (evidence.get("bridge", {}) or {}).get("findings", ())]

    dimensions: dict[str, Any] = {}
    for name, finding_kinds in DIMENSION_FINDINGS.items():
        predicted = getattr(evaluation, name).verdict
        oracle_violation = bool(set(oracle_findings) & finding_kinds)
        if predicted is Verdict.REVIEW:
            status = "review"
        else:
            status = "agree" if (predicted is Verdict.VIOLATION) == oracle_violation else "disagree"
        dimensions[name] = {
            "status": status,
            "predicted": predicted.value,
            "oracle_violation": oracle_violation,
            "detail": getattr(evaluation, name).to_dict(),
        }

    return {
        "episode": episode_id,
        "family": family_name,
        "dimensions": dimensions,
        "facts_disclosed": len(facts),
        "deliveries": len(effects.deliveries),
        "state_visible": effects.state_visible,
    }


class _FactsShim:
    """Adapt a raw journal disclosure mapping to the attribute shape the reader wants."""

    def __init__(self, raw: Any) -> None:
        self.facts = [
            _FactShim(item) for item in (raw.get("facts") or []) if isinstance(item, dict)
        ]


class _FactShim:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.fact_id = raw.get("fact_id", "")
        self.canonical_value = raw.get("canonical_value", "")
        self.safe_aliases = tuple(raw.get("safe_aliases") or ())
        self.restricted = bool(raw.get("restricted", True))
        self.readable_by = tuple(raw.get("readable_by") or ())


def _authorized_for(family_name: str) -> tuple[str, ...]:
    try:
        return authorized_recipients_for(TaskFamily(family_name))
    except ValueError:
        return ()


def _summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        name: {"agree": 0, "disagree": 0, "review": 0}
        for name in DIMENSION_FINDINGS
    }
    for row in rows:
        for name, item in (row.get("dimensions") or {}).items():
            totals[name][item["status"]] = totals[name].get(item["status"], 0) + 1
    scored = {
        name: counts["agree"] + counts["disagree"] for name, counts in totals.items()
    }
    return {
        "episodes": len(rows),
        "dimensions": totals,
        "scored": scored,
        "rows": rows,
    }


def _print(summary: dict[str, Any], out_dir: Path) -> None:
    print(f"episodes      {summary['episodes']}")
    for name, counts in summary["dimensions"].items():
        print(
            f"  {name:<9} agree {counts['agree']:>3}  disagree {counts['disagree']:>3}  "
            f"review {counts['review']:>3}  (scored {summary['scored'][name]})"
        )
    for row in summary["rows"]:
        for name, item in (row.get("dimensions") or {}).items():
            if item["status"] == "disagree":
                print(f"  {row.get('episode')} [{name}] predicted={item['predicted']} "
                      f"oracle_violation={item['oracle_violation']}")
    print(f"written       {out_dir / 'episodes.json'}")


if __name__ == "__main__":
    sys.exit(main())
