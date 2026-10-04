"""Why the e2 precheck episodes never reached a committed delivery (follow-up reading).

Read-only.  It re-runs the formal acceptance gate over the frozen evidence and prints, per episode,
the failing criteria with their own numbers plus the tool sequence that produced them.  It never
writes into a campaign directory.

Usage:  python docs/reports/20260926-summary-delivery-e2-why.py [--verbose]
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
from sandbox.structured_v1.acceptance import judge_normal_control  # noqa: E402
from sandbox.structured_v1.bundle import load_finalized_bundle  # noqa: E402

ARMS = ("coverage_guided", "random_evolution")
RUN_PREFIX = "paired-mat-e2-01-20260926"
DEFAULT_DATA = "D:/hxjh/runs/mat-e2-01-20260926/repo/data/structured-v1"
DELIVERY_TOOLS = frozenset({"send_email", "share_drive_file", "create_drive_file"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(DEFAULT_DATA))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    fixture = load_fixture("summary-delivery-e2")

    for arm in ARMS:
        root = args.data / f"{RUN_PREFIX}-{arm}"
        finalized = [load_finalized_bundle(path)
                     for path in sorted((root / "finalized").glob("*.json"))]
        print()
        print("===", arm, "| episodes", len(finalized), "===")
        tools: collections.Counter = collections.Counter()
        failed_ids: collections.Counter = collections.Counter()
        delivery_episodes = []
        for final in finalized:
            bundle = final.container_bundle
            records = {record.action_request_id: record for record in bundle.records}
            sequence = []
            for decision in bundle.model_decisions:
                record = records.get(decision.action_request_id)
                if record is None:
                    outcome = "-"
                elif record.committed:
                    outcome = "committed"
                elif any(effect.blocked for effect in record.effects):
                    outcome = "blocked"
                else:
                    outcome = "-"
                sequence.append((decision.phase_id, decision.tool_name, outcome))
                tools[decision.tool_name] += 1
            verdict = judge_normal_control(final, manifest=fixture.manifest,
                                           overlay=fixture.overlay)
            failed = verdict.failed()
            for item in failed:
                failed_ids[item.criterion_id] += 1
            attempted = [step for step in sequence if step[1] in DELIVERY_TOOLS]
            if attempted:
                delivery_episodes.append((bundle.episode_id,
                                          [(step[1], step[2]) for step in attempted]))
            detail = "; ".join(f"{item.criterion_id}[{item.status}]={item.detail}"
                               for item in failed)
            print(f"  {bundle.episode_id} stop={bundle.stop_reason.value} "
                  f"decisions={len(bundle.model_decisions)} accepted={verdict.accepted}")
            if detail:
                print(f"      failed: {detail}")
            if args.verbose:
                for phase, tool, outcome in sequence:
                    print(f"      {phase:<13} {tool:<20} {outcome}")
        print("  tool histogram:", dict(tools.most_common()))
        print("  failed criteria:", dict(failed_ids))
        print("  episodes that called a delivery tool:", delivery_episodes)


if __name__ == "__main__":
    main()
