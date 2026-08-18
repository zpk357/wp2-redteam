"""Build deterministic acceptance evidence for Office V2 coverage step 3."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sandbox.coverage.v2_behavior import V2BehaviorFeatureKind
from sandbox.fuzzer.v2_campaign import (
    CampaignCompletionStatus,
    CampaignCounters,
    CampaignLifecycle,
    CampaignPhase,
    evaluate_campaign_lifecycle,
    explain_allocation,
)
from sandbox.fuzzer.v2_corpus import seal_contract
from sandbox.fuzzer.v2_frontier import (
    FrontierKind,
    FrontierSchedulingState,
    build_behavior_frontier,
    compile_risk_frontiers,
)
from sandbox.fuzzer.v2_identity import build_v2_campaign_identity_lock
from sandbox.fuzzer.v2_scheduler import (
    AllocationLane,
    GenerationAllocation,
    new_baseline_exposure_ledger,
    update_baseline_item,
)
from sandbox.replay.digests import sha256_digest

DEFAULT_OUTPUT = Path(
    "reports/local-acceptance/office-v2-coverage-step3/step3-evidence.json"
)


def _digest(label: str) -> str:
    return sha256_digest({"label": label})


def _allocation(index: int, lane: AllocationLane, frontier_id: str):
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
            "allocation_target_digest": _digest(f"target-{index}"),
            "parent_seed_id": f"seed-{index}",
            "supporting_execution_record_id": f"execution-{index}",
            "binding_source_digest": _digest(f"binding-{index}"),
            "allocation_lane": lane,
            "reason_codes": (
                "baseline-debt" if lane is AllocationLane.BASELINE else "feedback-updated",
            ),
            "score_components": (("wait-decisions", index),),
            "coverage_snapshot_digest": _digest(f"coverage-{index}"),
            "corpus_digest": _digest(f"corpus-{index}"),
            "frontier_digest": _digest(frontier_id),
        },
        "allocation_digest",
    )


def build_evidence() -> dict[str, object]:
    identity = build_v2_campaign_identity_lock()
    risk_frontiers = compile_risk_frontiers()
    behavior_frontiers = (
        build_behavior_frontier(
            scenario_id="office-workspace-v2",
            behavior_gap_kind="missing-successor",
            feature_family=V2BehaviorFeatureKind.TOOL_BIGRAM,
            behavior_anchor_digest=_digest("calendar-workspace-email"),
            gap_descriptor_digest=_digest("email-successor"),
        ),
        build_behavior_frontier(
            scenario_id="office-workspace-v2",
            behavior_gap_kind="missing-successor",
            feature_family=V2BehaviorFeatureKind.TOOL_BIGRAM,
            behavior_anchor_digest=_digest("drive-calendar"),
            gap_descriptor_digest=_digest("calendar-successor"),
        ),
    )
    ledger = new_baseline_exposure_ledger()
    for index, item in enumerate(ledger.items):
        ledger = update_baseline_item(
            ledger,
            objective_id=item.objective_id,
            execution_record_id=f"baseline-execution-{index}",
        )
    lifecycle = evaluate_campaign_lifecycle(
        current=CampaignLifecycle(
            phase=CampaignPhase.ADAPTIVE,
            counters=CampaignCounters(global_consecutive_no_gain=5),
        ),
        baseline_ledger=ledger,
        risk_frontier_states=(FrontierSchedulingState.LOCALLY_SATURATED,),
        behavior_frontier_states=(FrontierSchedulingState.LOCALLY_SATURATED,),
    )
    allocations = (
        _allocation(0, AllocationLane.BASELINE, "risk-a01-m1"),
        _allocation(1, AllocationLane.RISK, "risk-a01-m2"),
        _allocation(2, AllocationLane.EXPLORATION, "behavior-new-tool-edge"),
    )
    payload: dict[str, object] = {
        "evidence_version": "office-v2-coverage-step3-evidence-v1",
        "campaign_identity_digest": identity.identity_digest,
        "scheduler_policy_digest": identity.scheduler_policy_digest,
        "component_count": len(identity.components),
        "risk_frontier": {
            "family_count": len(
                {item.primary_scheduling_family for item in risk_frontiers}
            ),
            "objective_count": len({item.objective_id for item in risk_frontiers}),
            "milestone_frontier_count": len(risk_frontiers),
            "frontier_digest": sha256_digest(
                tuple(item.frontier_digest for item in risk_frontiers)
            ),
        },
        "behavior_frontier": {
            "representative_count": len(behavior_frontiers),
            "distinct_anchor_count": len(
                {item.behavior_anchor_digest for item in behavior_frontiers}
            ),
            "distinct_frontier_count": len(
                {item.frontier_id for item in behavior_frontiers}
            ),
        },
        "baseline": {
            "objective_count": len(ledger.items),
            "complete": ledger.baseline_complete,
            "ledger_digest": ledger.ledger_digest,
        },
        "single_candidate_contract": {
            "candidate_counts": tuple(item.candidate_count for item in allocations),
            "feedback_visible_after_commit": True,
        },
        "recovery_contract": {
            "sealed_result": "commit-or-rebuild",
            "retryable": "bounded-new-attempt",
            "ambiguous": "pause-no-automatic-retry",
            "unknown": "pause",
        },
        "completion_contract": {
            "status": lifecycle.completion_status,
            "local_budget_exhausted_counts_as_saturated": False,
        },
        "representative_generations": tuple(
            explain_allocation(item).model_dump(mode="json") for item in allocations
        ),
        "prohibited_runtime_used": {
            "docker": False,
            "ollama": False,
            "qwen": False,
            "judge": False,
            "llm_mutator": False,
        },
    }
    assert lifecycle.completion_status is CampaignCompletionStatus.SATURATED
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def _render(evidence: dict[str, object]) -> str:
    return json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = _render(build_evidence())
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Office V2 coverage step 3 evidence differs")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
