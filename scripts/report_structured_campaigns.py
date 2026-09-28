"""Compare archived checkpoints offline; never start a model or a campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sandbox.structured_v1.campaign import CampaignCheckpoint
from sandbox.structured_v1.campaign_report import checkpoint_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guided", type=Path, required=True)
    parser.add_argument("--evolution", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {args.guided.resolve(), args.evolution.resolve()}:
        parser.error("output must not overwrite an input checkpoint")
    result = {"kind": "development-pair-no-significance-test", "arms": {}}
    states = []
    for arm, path in (("coverage_guided", args.guided), ("random_evolution", args.evolution)):
        raw = path.read_bytes()
        state = CampaignCheckpoint.model_validate_json(raw)
        states.append(state)
        if any(item.arm != arm for item in state.selections):
            raise ValueError(f"wrong arm in {path}")
        result["arms"][arm] = {
            "checkpoint_sha256": hashlib.sha256(raw).hexdigest(),
            **checkpoint_report(state),
        }
    result["same_limits"] = states[0].limits == states[1].limits
    result["identity_sets"] = [sorted({
        (coverage.fixture_id, coverage.execution.manifest_digest,
         coverage.execution.execution_config_digest, coverage.execution.coverage_version)
        for coverage in state.search.parent_coverage.values() if coverage.execution
    }) for state in states]
    result["same_identity"] = (
        len(result["identity_sets"][0]) == 1
        and result["identity_sets"][0] == result["identity_sets"][1]
    )
    result["complete"] = (
        result["same_limits"] and result["same_identity"]
        and all(item["complete"] for item in result["arms"].values())
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    if not result["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
