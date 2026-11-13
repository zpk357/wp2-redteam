"""Compare raw committed effects against what the existing Oracle recorded.

This is the reconciliation step ``TASK.md`` TASK-02b asks for before a generic
evaluator is written.  Raw effects come from ``trace.steps`` only; the Oracle's
view comes from ``bridge.observations``.  Where the two disagree, the Oracle is
carrying a rule that is not written down anywhere else -- and that rule is what
the reusable-constraint version has to reproduce or replace.

Read-only.  No model, no container, no Episode rerun.

    python scripts/reconcile_effects.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sandbox.toolchain.effects import extract_effects

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ROOTS = (
    Path(r"D:\hxjh\runs\downloaded\extracted\old-abl2-20261025-013624"),
    Path(r"D:\hxjh\runs\downloaded\extracted\abl3-20261025-070525"),
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    roots = tuple(args.root) if args.root else DEFAULT_ROOTS
    out_dir = args.out or REPO_ROOT / "runs" / "toolchain-audit" / "reconcile"

    rows: list[dict[str, Any]] = []
    for root in roots:
        for report_path in sorted(root.rglob("campaign.*.report.json")):
            rows.extend(_reconcile_report(report_path))

    summary = _summarise(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "reconcile.json").write_text(
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


def _reconcile_report(report_path: Path) -> list[dict[str, Any]]:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))["payload"]["report"]
    except (OSError, KeyError, json.JSONDecodeError):
        return []
    root = report_path.parent
    rows: list[dict[str, Any]] = []
    for episode in report.get("episodes", []):
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
        rows.append(_compare(episode, payload))
    return rows


def _compare(episode: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    raw = extract_effects(payload.get("trace"))
    bridge = payload.get("bridge") or {}
    observations = bridge.get("observations") or []

    # Multiplicity matters: two deliveries to the same person are two hand-offs, and comparing
    # de-duplicated sets reported 26 Episodes as disagreeing when only one actually did.
    raw_recipients = Counter(
        recipient for delivery in raw.deliveries for recipient in delivery.recipients
    )
    oracle_recipients = Counter(
        str(item.get("recipient", "")) for item in observations if item.get("recipient")
    )
    raw_payloads = sorted(delivery.payload for delivery in raw.deliveries)
    oracle_payloads = sorted(str(item.get("payload", "")) for item in observations)

    count_match = len(raw.deliveries) == len(observations)
    recipients_match = raw_recipients == oracle_recipients
    payloads_match = raw_payloads == oracle_payloads

    return {
        "episode": episode.get("episode_id"),
        "status": "match" if (count_match and recipients_match and payloads_match) else "differs",
        "raw_deliveries": len(raw.deliveries),
        "oracle_observations": len(observations),
        "raw_recipients": dict(sorted(raw_recipients.items())),
        "oracle_recipients": dict(sorted(oracle_recipients.items())),
        "count_match": count_match,
        "recipients_match": recipients_match,
        "payloads_match": payloads_match,
        "raw_changes": len(raw.changes),
        "oracle_findings": list(bridge.get("findings") or ()),
    }


def _summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    differs = [row for row in rows if row.get("status") == "differs"]
    unmatched = [row for row in rows if str(row.get("status", "")).startswith("no-evidence")]
    return {
        "episodes": total,
        "match": sum(1 for row in rows if row.get("status") == "match"),
        "differs": len(differs),
        "no_evidence": len(unmatched),
        "count_diffs": sum(1 for row in differs if not row.get("count_match")),
        "recipient_diffs": sum(1 for row in differs if not row.get("recipients_match")),
        "payload_diffs": sum(1 for row in differs if not row.get("payloads_match")),
        "rows": rows,
    }


def _print(summary: dict[str, Any], out_dir: Path) -> None:
    print(f"episodes            {summary['episodes']}")
    print(f"agree with Oracle   {summary['match']}")
    print(f"differ              {summary['differs']}")
    print(f"  delivery count    {summary['count_diffs']}")
    print(f"  recipient sets    {summary['recipient_diffs']}")
    print(f"  payloads          {summary['payload_diffs']}")
    print(f"no evidence file    {summary['no_evidence']}")
    shown = 0
    for row in summary["rows"]:
        if row.get("status") == "differs" and shown < 10:
            print(
                f"  {row['episode']}: raw={row['raw_recipients']} "
                f"oracle={row['oracle_recipients']} "
                f"n={row['raw_deliveries']}/{row['oracle_observations']}"
            )
            shown += 1
    print(f"written             {out_dir / 'reconcile.json'}")


if __name__ == "__main__":
    sys.exit(main())
