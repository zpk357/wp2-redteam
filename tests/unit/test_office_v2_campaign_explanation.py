from __future__ import annotations

import json

from sandbox.fuzzer.v2_campaign import explain_allocation
from sandbox.fuzzer.v2_corpus import seal_contract
from sandbox.fuzzer.v2_frontier import FrontierKind
from sandbox.fuzzer.v2_scheduler import AllocationLane, GenerationAllocation
from sandbox.replay.digests import sha256_digest


def digest(label: str) -> str:
    return sha256_digest({"label": label})


def allocation(index: int, lane: AllocationLane, frontier_id: str) -> GenerationAllocation:
    return seal_contract(
        GenerationAllocation,
        {
            "generation_allocation_id": f"allocation-{index}",
            "generation_index": index,
            "frontier_kind": (
                FrontierKind.BEHAVIOR
                if lane is AllocationLane.EXPLORATION
                else FrontierKind.RISK
            ),
            "frontier_id": frontier_id,
            "allocation_target_digest": digest(f"target-{index}"),
            "parent_seed_id": f"seed-{index}",
            "supporting_execution_record_id": f"execution-{index}",
            "binding_source_digest": digest(f"binding-{index}"),
            "allocation_lane": lane,
            "reason_codes": (
                "baseline-debt" if lane is AllocationLane.BASELINE else "behavior-reserve",
            ),
            "score_components": (("wait-decisions", index),),
            "coverage_snapshot_digest": digest(f"coverage-{index}"),
            "corpus_digest": digest(f"corpus-{index}"),
            "frontier_digest": digest(frontier_id),
        },
        "allocation_digest",
    )


def test_three_generation_explanations_are_complete_deterministic_json() -> None:
    allocations = (
        allocation(0, AllocationLane.BASELINE, "risk-a01-m1"),
        allocation(1, AllocationLane.RISK, "risk-a01-m2"),
        allocation(2, AllocationLane.EXPLORATION, "behavior-new-tool-edge"),
    )
    explanations = tuple(explain_allocation(item) for item in allocations)
    rendered = json.dumps(
        [item.model_dump(mode="json") for item in explanations], sort_keys=True
    )
    restored = json.loads(rendered)
    assert [item["frontier_id"] for item in restored] == [
        "risk-a01-m1",
        "risk-a01-m2",
        "behavior-new-tool-edge",
    ]
    assert all(item["supporting_execution_record_id"] for item in restored)
    assert all(len(item["input_snapshot_digests"]) == 4 for item in restored)
    assert "selected" in restored[1]["statement"]
