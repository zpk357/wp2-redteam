"""Three-question reading for the summary-delivery-e2 precheck (run package section 3).

Read-only.  It re-reads the frozen campaign evidence (checkpoint + finalized bundles) and prints
Q1 (did the local child see the change), Q2 (what followed the read) and Q3 (descendant gains).
It never writes into a campaign directory and never re-judges an episode beyond `phase_report`.

Source paths are resolved relative to this file (the repository), and the campaign data is given
as an argument, so the reading can be reproduced locally from the downloaded evidence:

Usage:  python docs/reports/20260926-summary-delivery-e2-precheck.py \
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
from sandbox.structured_v1.bundle import load_finalized_bundle  # noqa: E402
from sandbox.structured_v1.campaign import load_checkpoint  # noqa: E402
from sandbox.structured_v1.rendering import render_material  # noqa: E402

DIMENSIONS = ("behavior", "risk", "joint")
ARMS = ("coverage_guided", "random_evolution")
RUN_PREFIX = "paired-mat-e2-01-20260926"
DEFAULT_DATA = "D:/hxjh/runs/mat-e2-01-20260926/repo/data/structured-v1"


def keys_of(coverage) -> dict[str, set]:
    return {dimension: {item.key for item in getattr(coverage, dimension)}
            for dimension in DIMENSIONS}


def arm_rows(root: Path, fixture) -> list[dict]:
    checkpoint = load_checkpoint(root / "checkpoint.json")
    finalized = [load_finalized_bundle(path)
                 for path in sorted((root / "finalized").glob("*.json"))]
    bundles = {item.container_bundle.episode_id: item for item in finalized}
    by_candidate = checkpoint.search.parent_coverage
    selections = {item.opportunity: item for item in checkpoint.selections}
    registered = {slot.slot_id for slot in fixture.manifest.slots}
    rows: list[dict] = []
    for reservation in sorted(checkpoint.reservations.values(), key=lambda item: item.opportunity):
        index = reservation.opportunity
        selection = selections.get(index)
        if reservation.settled and not reservation.submitted:
            rows.append({"opportunity": index, "kind": "failed_gen"})
            continue
        if selection is None:
            rows.append({"opportunity": index, "kind": "infra"})
            continue
        child = f"root-restart-{index}" if selection.root_restart else f"opportunity-{index}"
        coverage = by_candidate.get(child)
        if coverage is None or coverage.episode_id not in bundles:
            rows.append({"opportunity": index, "kind": "infra"})
            continue
        bundle = bundles[coverage.episode_id].container_bundle
        parent = (None if selection.root_restart
                  else checkpoint.search_candidates[selection.parent_id])
        reading = e2.phase_report(bundle, manifest=fixture.manifest, parent=parent)
        parent_slots = ({} if parent is None else
                        {slot.slot_id: slot
                         for slot in render_material(parent, fixture.manifest).slots})
        changed = sorted(slot.slot_id for slot in bundle.material.slots
                         if slot.slot_id in registered and parent_slots.get(slot.slot_id) != slot)
        read_slots = sorted({read["slot_id"] for read in reading["reads"] if read["slot_id"]})
        parent_coverage = None if parent is None else by_candidate.get(selection.parent_id)
        rows.append({
            "opportunity": index,
            "kind": "root" if selection.root_restart else "local",
            "parent": None if selection.root_restart else selection.parent_id,
            "changed_slots": changed,
            "read_slots": read_slots,
            "read_changed": sorted(set(changed) & set(read_slots)),
            "unread_changed": sorted(set(changed) - set(read_slots)),
            "window": reading["changed_material_with_action_window"],
            "restricted_with_joint": reading["restricted_read_with_joint"],
            "feedback_sources": sorted(selection.feedback_sources),
            "keys": keys_of(coverage),
            "parent_keys": None if parent_coverage is None else keys_of(parent_coverage),
            "channels": e2._channel_facts(bundle),
        })
    return rows


def show(arm: str, rows: list[dict]) -> None:
    episodes = [row for row in rows if "keys" in row]
    print()
    print("===", arm, "===")
    print("episodes", len(episodes),
          "| failed_gen", sum(1 for row in rows if row["kind"] == "failed_gen"),
          "| infra", sum(1 for row in rows if row["kind"] == "infra"))
    print("root/local", dict(collections.Counter(row["kind"] for row in episodes)))

    local = [row for row in episodes if row["kind"] == "local"]
    print("-- Q1: did the local child see the change --")
    for row in local:
        print(f"   opp{row['opportunity']:<2} parent={str(row['parent']):<16} "
              f"changed={','.join(row['changed_slots']) or '-'} "
              f"read={','.join(row['read_slots']) or '-'} "
              f"read_changed={','.join(row['read_changed']) or '-'} "
              f"unread_changed={','.join(row['unread_changed']) or '-'}")
    saw = [row for row in local if row["read_changed"]]
    only_same = [row for row in local if not row["read_changed"] and row["read_slots"]]
    blind = [row for row in local if not row["read_changed"] and not row["read_slots"]]
    print("   locals", len(local), "| read the changed content", len(saw),
          "| read only unchanged slots", len(only_same), "| read no slot at all", len(blind))

    print("-- Q2: after the read --")
    windows = [row for row in episodes if row["window"]]
    print("   episodes with changed-material-in-action-window", len(windows))
    for row in windows:
        print(f"   opp{row['opportunity']:<2} {row['kind']} "
              f"changed={','.join(row['changed_slots']) or '-'} "
              f"read_changed={','.join(row['read_changed']) or '-'} parent={row['parent']}")
    delivered = [row for row in episodes
                 if any(fact["attempts"] for fact in row["channels"].values())]
    print("   episodes with a delivery attempt", len(delivered),
          [(row["opportunity"],
            {key: value["attempts"] for key, value in row["channels"].items() if value["attempts"]})
           for row in delivered])

    print("-- Q3: descendant gains --")
    running = {dimension: set() for dimension in DIMENSIONS}
    for row in episodes:
        gain_parent = (None if row["parent_keys"] is None else
                       {dimension: len(row["keys"][dimension] - row["parent_keys"][dimension])
                        for dimension in DIMENSIONS})
        gain_history = {dimension: len(row["keys"][dimension] - running[dimension])
                        for dimension in DIMENSIONS}
        for dimension in DIMENSIONS:
            running[dimension] |= row["keys"][dimension]
        print(f"   {'R' if row['kind'] == 'root' else 'L'} opp{row['opportunity']:<2} "
              f"parent={str(row['parent']):<16} vs_parent={gain_parent} "
              f"vs_arm_history={gain_history}")
    print("   feedback_sources non-empty",
          [row["opportunity"] for row in episodes if row["feedback_sources"]])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(DEFAULT_DATA),
                        help="directory that holds the paired-<run>-<arm> evidence roots")
    args = parser.parse_args()
    fixture = load_fixture("summary-delivery-e2")
    for arm in ARMS:
        show(arm, arm_rows(args.data / f"{RUN_PREFIX}-{arm}", fixture))


if __name__ == "__main__":
    main()
