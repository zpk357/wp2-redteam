"""Descriptive, partial reading of the operator-terminated attack-technique scan.

The batch is `INVALID`: the operator stopped it after 14 of 16 random-evolution opportunities and
before `coverage_guided` started.  There is therefore no paired comparison, and the official report
path refuses the evidence on purpose -- `report_summary_delivery_e2.read_arm` reconciles
`failed_gen + infra + W + F + Q` with `checkpoint.usage.opportunities`, and an opportunity killed
in flight counts as one extra `infra` (15 consumed against 14 recorded).  Nothing below is
confirmatory; it is a reading of the settled episodes only.

This script re-reads the frozen evidence with the repository's own judging helpers
(`_obligation_outcome`, `_completion_diagnostics`, `judge_normal_control`) and prints:

* the obligation verdicts (W / F / Q) and the per-obligation split;
* the technique x direction grid -- which angle produced what;
* the completion diagnostics (first delivery tool and its position, search/read before delivery,
  budget truncation, the legitimate-task criteria each episode failed).

Read-only: it never writes into the campaign directory.

Usage:  python docs/reports/20260926-attack-technique-diversity-partial.py \
            --data <unpacked>/repo/data/structured-v1
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO_ROOT / "src"), str(REPO_ROOT / "agent_image"),
                str(REPO_ROOT / "scripts")]

import report_summary_delivery_e2 as e2  # noqa: E402

from sandbox.scenarios.structured_v1.fixtures import load_fixture  # noqa: E402
from sandbox.structured_v1.acceptance import judge_normal_control  # noqa: E402
from sandbox.structured_v1.bundle import load_finalized_bundle  # noqa: E402
from sandbox.structured_v1.campaign import load_checkpoint  # noqa: E402
from sandbox.structured_v1.phase_report import phase_report  # noqa: E402
from sandbox.structured_v1.search import attack_technique_for  # noqa: E402
from sandbox.structured_v1.text_provider import ATTACK_TECHNIQUE_ANGLES  # noqa: E402

DEFAULT_FIXTURE = "summary-delivery-e2-completion"
DEFAULT_ARM = "random_evolution"
DEFAULT_DATA = "D:/hxjh/runs/mat-tech-01-20260926/repo/data/structured-v1"
DEFAULT_PREFIX = "paired-mat-tech-01-20260926"


def failed_criteria(final, fixture) -> list[str]:
    """The legitimate-task criteria this episode failed, or a marking if it cannot be judged."""

    if not final.complete or final.missing:
        return ["<bundle incomplete>"]
    try:
        verdict = judge_normal_control(
            final, manifest=fixture.manifest, overlay=fixture.overlay
        )
    except Exception as exc:  # noqa: BLE001 - reported as-is, never silently dropped
        return [f"<judge raised: {type(exc).__name__}: {exc}>"]
    return [item.criterion_id for item in verdict.criteria if not item.satisfied]


def settled_rows(root: Path, fixture) -> tuple[dict, list[dict], int, int]:
    """One row per opportunity that actually settled, plus the unsettled/failed counters."""

    checkpoint = load_checkpoint(root / "checkpoint.json")
    selections = {item.opportunity: item for item in checkpoint.selections}
    paths = sorted((root / "finalized").glob("*.json"))
    finalized = [load_finalized_bundle(path) for path in paths]
    bundles = {item.container_bundle.episode_id: item for item in finalized}
    by_candidate = checkpoint.search.parent_coverage

    rows: list[dict] = []
    failed_gen = 0
    unsettled = 0
    for reservation in sorted(checkpoint.reservations.values(), key=lambda item: item.opportunity):
        index = reservation.opportunity
        if reservation.settled and not reservation.submitted:
            failed_gen += 1
            continue
        selection = selections.get(index)
        if selection is None or not reservation.settled:
            unsettled += 1
            continue
        child = f"root-restart-{index}" if selection.root_restart else f"opportunity-{index}"
        coverage = by_candidate.get(child)
        final = None if coverage is None else bundles.get(coverage.episode_id)
        if final is None:
            unsettled += 1
            continue
        outcomes = e2._obligation_outcome(fixture, final)  # noqa: SLF001 - repository's own judge
        violated = sorted(key for key, value in outcomes.items() if value == "violated")
        unknown = sorted(key for key, value in outcomes.items() if value == "unknown")
        # A fixture without a session protocol (e.g. `summary-delivery-b`) makes the campaign
        # non-phased, so the phase checkpoint stores no candidate material.  The run's own report
        # entry point resolves the parent the same way and raises `KeyError` there.  Fall back to
        # no parent and record that the parent was unavailable, instead of hiding the gap.
        parent = None
        parent_resolved = True
        if not selection.root_restart:
            parent = checkpoint.search_candidates.get(selection.parent_id)
            parent_resolved = parent is not None
        reading = phase_report(
            final.container_bundle, manifest=fixture.manifest, parent=parent
        )
        rows.append({
            "opportunity": index,
            "episode_id": final.container_bundle.episode_id,
            "technique": attack_technique_for(index),
            "direction": selection.selected_direction or "unknown",
            "root_restart": selection.root_restart,
            "feedback_sources": list(selection.feedback_sources),
            "verdict": "W" if violated else "Q" if unknown else "F",
            "outcomes": outcomes,
            "violated": violated,
            "unknown": unknown,
            "restricted_read": reading["restricted_read_with_joint"],
            "action_window": reading["changed_material_with_action_window"],
            "parent_resolved": parent_resolved,
            "completion": e2._completion_diagnostics(final, fixture),  # noqa: SLF001
            "usage": final.container_bundle.usage,
            "stop_reason": final.container_bundle.stop_reason.value,
            "failed": failed_criteria(final, fixture),
        })
    return checkpoint, rows, failed_gen, unsettled


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):  # keep the angle text readable when redirected
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=DEFAULT_DATA)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--arm", default=DEFAULT_ARM)
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE)
    args = parser.parse_args()

    root = Path(args.data) / f"{args.prefix}-{args.arm}"
    fixture = load_fixture(args.fixture)
    checkpoint, rows, failed_gen, unsettled = settled_rows(root, fixture)

    counts = collections.Counter(row["verdict"] for row in rows)
    print(f"fixture={args.fixture} arm={args.arm}")
    print(f"recorded_opportunities={checkpoint.usage.opportunities} "
          f"settled_episodes={len(rows)} failed_gen={failed_gen} unsettled={unsettled}")
    print(f"consumed_like_report={failed_gen + len(rows) + unsettled} "
          f"(the official report refuses this batch on that difference)")
    print(f"W={counts['W']} F={counts['F']} Q={counts['Q']}")

    obligations: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in rows:
        for name, outcome in row["outcomes"].items():
            obligations[name][outcome] += 1
    print("obligation_breakdown:")
    for name, bucket in sorted(obligations.items()):
        print(f"   {name}: {dict(bucket)}")

    print(f"stop_reasons={dict(collections.Counter(r['stop_reason'] for r in rows))}")
    print(f"directions={dict(collections.Counter(r['direction'] for r in rows))}")
    roots = collections.Counter("root" if r["root_restart"] else "local" for r in rows)
    print(f"root_local={dict(roots)}")
    print(f"episodes_with_feedback={sum(1 for r in rows if r['feedback_sources'])}/{len(rows)}")

    def share(flag: str) -> str:
        hit = sum(1 for row in rows if row["completion"][flag] is True)
        return f"{hit}/{len(rows)}"

    for flag in ("legitimate_task_completed", "delivery_attempted", "budget_truncated"):
        print(f"{flag}={share(flag)}")
    unresolved = sum(1 for row in rows if not row["parent_resolved"])
    print(f"parent_unresolved={unresolved}/{len(rows)}")
    print(f"restricted_read={sum(1 for r in rows if r['restricted_read'] is True)}/{len(rows)}")
    print(f"action_window={sum(1 for r in rows if r['action_window'] is True)}/{len(rows)}")

    grid: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    print()
    print("=== per settled episode ===")
    for row in rows:
        completion = row["completion"]
        delivery = completion["first_delivery"]
        where = "none" if delivery is None else (
            f"{delivery['tool_name']}@{delivery['phase_id']}#{delivery['phase_call_index']}"
            f"{'' if delivery['committed'] else '/not-committed'}"
        )
        grid[(row["technique"], row["direction"])][row["verdict"]] += 1
        print(
            f"opp{row['opportunity']:<3} {row['technique']} {row['direction']:<15} "
            f"verdict={row['verdict']} "
            f"restricted_read={row['restricted_read']} "
            f"action_window={row['action_window']} "
            f"first_delivery={where} "
            f"search={completion['action_search_calls']} "
            f"read={completion['action_read_calls']} "
            f"truncated={completion['budget_truncated']} "
            f"stop={row['stop_reason']} "
            f"failed={row['failed']}"
        )

    print()
    print("=== technique x direction grid ===")
    directions = sorted({key[1] for key in grid})
    for technique in sorted({key[0] for key in grid}):
        print(f"{technique}: {ATTACK_TECHNIQUE_ANGLES.get(technique, '?')}")
        for direction in directions:
            bucket = grid.get((technique, direction))
            if bucket:
                print(f"   {direction:<15} {dict(bucket)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
