"""Regenerate the zero-information readout for one settled arm (`SPEC-SOC-ZIE-20260930`).

Read-only: it loads a checkpoint and its finalized bundles and prints the tables the report quotes.
Every number in the companion markdown comes from this script.

Usage:
    project_python docs/reports/20260930-zero-information-episodes-readout.py <run-root> <tag>:<arm>
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO / "agent_image"), str(REPO / "scripts")]

from sandbox.scenarios.structured_v1.fixtures import load_fixture  # noqa: E402
from sandbox.structured_v1.campaign import load_checkpoint  # noqa: E402
from sandbox.structured_v1.campaign_report import checkpoint_report  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "zie_report", REPO / "scripts" / "report_summary_delivery_e2.py"
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

FIXTURE = "summary-delivery-enc-01"
#: The four episodes whose wrap-up was produced under the conflicting action-phase text.  Their
#: non-delivery cannot be attributed to resisting the attack (`ZIE` §1.4).
CONTAMINATED = {2, 4, 7, 15}


def main() -> None:
    run_root = Path(sys.argv[1])
    tag, _, arm = sys.argv[2].partition(":")
    root = run_root / "repo/data/structured-v1" / f"{tag}-{arm}"
    fixture = load_fixture(FIXTURE)

    state = load_checkpoint(root / "checkpoint.json")
    # This also raises when the checkpoint does not reconcile with its receipts.
    checkpoint = checkpoint_report(state)
    curves = {item["opportunity"]: item for item in checkpoint["curves"]}

    data = report.read_arm(root, arm, fixture)
    episodes = {item["opportunity"]: item for item in data["episodes"]}
    completion = {
        item["opportunity"]: item["completion"]
        for item in data["episodes"]
    }

    print(f"# arm: {tag}-{arm}")
    print(f"settled opportunities: {data['opportunities']}")
    print(f"counts: {data['counts']}")
    print(f"termination kinds: {data['completion']['termination_kinds']}")
    print(f"material sources: {checkpoint['material_sources']}")
    print()
    print("## old vs new denominator")
    print(f"  old: decidable={data['zero_information']['decidable_episodes_old']} "
          f"W/(W+F)={data['zero_information']['rates_old']['W/(W+F)']}")
    print(f"  new: decidable={data['zero_information']['decidable_episodes_new']} "
          f"W/(W+F)={data['zero_information']['rates_new']['W/(W+F)']}")
    print(f"  protocol failures: {data['zero_information']['protocol_failure_opportunities']}")
    print()
    print("## per episode")
    print(f"  {'opp':<4} {'layer':<13} {'dir':<15} {'kind':<19} {'sent':<6} {'verdict':<8} "
          f"{'source':<17} note")
    for index in sorted(episodes):
        episode = episodes[index]
        finished = completion[index]
        curve = curves[index]
        note = []
        if index in CONTAMINATED:
            note.append("defence-contaminated")
        if curve["material_source"] == "control":
            note.append("never-attacked")
        if curve["material_source"] == "inherited":
            note.append(f"inherited-from-{curve['planned_operation']}")
        if finished["termination_kind"] == "budget-truncated":
            note.append("protocol-failure")
        print(f"  {index:<4} {str(curve['selection_layer']):<13} {episode['direction']:<15} "
              f"{finished['termination_kind']:<19} {str(finished['delivery_attempted']):<6} "
              f"{episode['verdict']:<8} {str(curve['material_source']):<17} {', '.join(note)}")
    print()
    print("## defence composition")
    clean = [
        index for index in episodes
        if completion[index]["termination_kind"] == "delivery-attempted"
        and episodes[index]["verdict"] != "W"
    ]
    wrapped = [
        index for index in episodes
        if completion[index]["termination_kind"] == "wrapped-up"
    ]
    print(f"  delivered and clean: {clean}")
    print(f"  wrapped up without delivering: {wrapped}")
    print(f"  of the defence, contaminated: "
          f"{sorted((set(clean) | set(wrapped)) & CONTAMINATED)}")
    print(f"  of the defence, never attacked: "
          f"{sorted(i for i in episodes if curves[i]['material_source'] == 'control')}")


main()
