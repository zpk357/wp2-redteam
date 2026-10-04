"""Re-judge frozen episodes with the finalized artifacts and compare with the report script.

`report_summary_delivery_e2._obligation_outcome` judges `final.container_bundle.artifacts()`, whose
closure is the container's own view.  `FinalizedEpisodeBundle.artifacts()` additionally applies the
host closure receipt, which is what `judge_episode` needs to call a channel closed.  This script
runs both and prints where they disagree, so a report reading of `unknown` can be told apart from a
genuinely undecidable episode.

Usage:  python docs/reports/20260926-obligation-outcome-recheck.py \
            --data <.../data/structured-v1> --prefix paired-<run> --fixture <fixture-id>
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

ARMS = ("coverage_guided", "random_evolution")


def judge(fixture, artifacts) -> dict[str, str]:
    from sandbox.structured_v1.oracle_io import judge_artifacts

    judgment = judge_artifacts(artifacts, manifest=fixture.manifest)
    return {item.obligation.value: item.outcome.value for item in judgment.judgments}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--fixture", required=True)
    args = parser.parse_args()
    fixture = load_fixture(args.fixture)

    for arm in ARMS:
        root = args.data / f"{args.prefix}-{arm}"
        if not root.is_dir():
            print(f"--- arm {arm}: missing {root}")
            continue
        container_counts: collections.Counter = collections.Counter()
        finalized_counts: collections.Counter = collections.Counter()
        disagreements = []
        for path in sorted((root / "finalized").glob("*.json")):
            final = load_finalized_bundle(path)
            from_container = judge(fixture, final.container_bundle.artifacts())
            from_finalized = judge(fixture, final.artifacts())
            for value in from_container.values():
                container_counts[value] += 1
            for value in from_finalized.values():
                finalized_counts[value] += 1
            if from_container != from_finalized:
                disagreements.append((final.container_bundle.episode_id,
                                      from_container, from_finalized))
        print()
        print(f"=== {arm} ===")
        print("  report path (container artifacts):", dict(container_counts))
        print("  correct path (finalized artifacts):", dict(finalized_counts))
        print("  episodes where they disagree:", len(disagreements))


if __name__ == "__main__":
    main()
