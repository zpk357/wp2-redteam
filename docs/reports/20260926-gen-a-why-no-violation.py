"""Why the GEN-A comparison produced no violation: per-episode read / delivery / verdict facts.

Read-only over the frozen evidence.  For every finalized episode it reports, from the formal
Oracle and the phase reading: whether the restricted fixed asset was actually read, whether a
delivery was committed at all, what the obligation verdict was, and what the generated material
looked like - so "the attack did not land" can be split into "the Agent never saw it", "it saw it
and did not deliver", or "it delivered but filtered the restricted content out".

Usage:  python docs/reports/20260926-gen-a-why-no-violation.py --data <.../data/structured-v1> \
            --prefix paired-mat-e2-comp2-01-20260926 --fixture summary-delivery-e2-completion
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO_ROOT / "src"), str(REPO_ROOT / "agent_image"),
                str(REPO_ROOT / "scripts")]

from sandbox.scenarios.structured_v1.fixtures import load_fixture  # noqa: E402
from sandbox.structured_v1.bundle import load_finalized_bundle  # noqa: E402
from sandbox.structured_v1.obligations import judge_episode  # noqa: E402
from sandbox.structured_v1.oracle_io import episode_evidence  # noqa: E402
from sandbox.structured_v1.phase_report import phase_report  # noqa: E402

ARMS = ("random_evolution", "coverage_guided")
DIMENSIONS = ("behavior", "risk", "joint")


def verdict_of(fixture, artifacts) -> tuple[str, dict[str, str]]:
    judgment = judge_episode(episode_evidence(artifacts), manifest=fixture.manifest)
    outcomes = {item.obligation.value: item.outcome.value for item in judgment.judgments}
    for name in ("data-release", "audience-scope"):
        outcomes.setdefault(name, "unknown")
    if any(value == "violated" for value in outcomes.values()):
        return "W", outcomes
    if any(value == "unknown" for value in outcomes.values()):
        return "Q", outcomes
    return "F", outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--show-material", type=int, default=1,
                        help="how many episodes per arm print their generated slot text")
    args = parser.parse_args()
    fixture = load_fixture(args.fixture)

    for arm in ARMS:
        root = args.data / f"{args.prefix}-{arm}"
        paths = sorted((root / "finalized").glob("*.json"))
        if not paths:
            print(f"\n=== {arm} ===\n  no finalized evidence")
            continue
        print(f"\n=== {arm} ({len(paths)} episodes) ===")
        counts: collections.Counter = collections.Counter()
        shown = 0
        for path in paths:
            final = load_finalized_bundle(path)
            bundle = final.container_bundle
            artifacts = final.artifacts()
            verdict, outcomes = verdict_of(fixture, artifacts)
            reading = phase_report(bundle, manifest=fixture.manifest, parent=None)
            deliveries = artifacts.deliveries()
            carriers = [effect for effect in deliveries
                        if any(getattr(unit, "value", None) for unit in
                               getattr(effect, "registered_units", ()) or ())]
            counts["episodes"] += 1
            counts["restricted_read"] += int(bool(reading["restricted_read_keys"]))
            counts["delivered"] += int(bool(deliveries))
            counts["both"] += int(bool(reading["restricted_read_keys"]) and bool(deliveries))
            counts[verdict] += 1
            print(f"  {bundle.episode_id} stop={bundle.stop_reason.value} "
                  f"restricted_read={bool(reading['restricted_read_keys'])} "
                  f"deliveries={len(deliveries)} carrying_registered_units={len(carriers)} "
                  f"verdict={verdict} outcomes={outcomes}")
            if shown < args.show_material and bundle.material.slots:
                shown += 1
                for slot in bundle.material.slots:
                    text = "\n".join(slot.contents).strip()
                    if text:
                        print(f"      material[{slot.slot_id}]: {text[:600]}")
        print("  summary:", dict(counts))


if __name__ == "__main__":
    main()
