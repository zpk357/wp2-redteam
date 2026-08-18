from __future__ import annotations

import pytest

from sandbox.fuzzer.v2_corpus import (
    AttackSeed,
    CorpusEntry,
    CorpusEntryState,
    CorpusStatistics,
    ExecutionCosts,
    ExecutionRecord,
    seal_contract,
)
from sandbox.fuzzer.v2_frontier import FrontierKind, FrontierSchedulingState
from sandbox.fuzzer.v2_mutation_identity import V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST
from sandbox.fuzzer.v2_scheduler import (
    AllocationLane,
    AuthorizationBranchAllocation,
    ComparisonContext,
    FrontierOption,
    GenerationAllocation,
    MutationGenerationAllocation,
    OperatorAllocation,
    ParentSelectionCandidate,
    RebindAllocation,
    RetargetAllocation,
    SchedulerPolicy,
    choose_frontier,
    new_baseline_exposure_ledger,
    select_parent,
    update_baseline_item,
)
from sandbox.replay.digests import sha256_digest


def digest(label: str) -> str:
    return sha256_digest({"label": label})


def test_baseline_ledger_has_all_12_objectives_and_only_terminal_proof_advances() -> None:
    ledger = new_baseline_exposure_ledger()
    assert len(ledger.items) == 12
    assert ledger.next_pending() is not None
    objective_id = ledger.next_pending().objective_id
    advanced = update_baseline_item(
        ledger, objective_id=objective_id, execution_record_id="execution-1"
    )
    assert advanced.next_pending().objective_id != objective_id
    assert ledger.next_pending().objective_id == objective_id

    with pytest.raises(ValueError, match="exactly one terminal proof"):
        update_baseline_item(advanced, objective_id=advanced.next_pending().objective_id)


def test_baseline_cursor_and_terminal_updates_are_deterministic_and_idempotent() -> None:
    ledger = new_baseline_exposure_ledger()
    objective_id = ledger.next_pending().objective_id
    first = update_baseline_item(
        ledger, objective_id=objective_id, unreachable_reason_codes=("tool-unavailable",)
    )
    second = update_baseline_item(
        first, objective_id=objective_id, unreachable_reason_codes=("tool-unavailable",)
    )
    assert first == second

    with pytest.raises(ValueError, match="immutable"):
        update_baseline_item(
            first, objective_id=objective_id, execution_record_id="execution-2"
        )


def parent_candidate(
    *, seed_id: str, execution_id: str, frontier_id: str, risk_proximity: int
) -> ParentSelectionCandidate:
    seed = AttackSeed.model_construct(
        seed_id=seed_id,
        seed_content_digest=digest(seed_id),
        payload_specs=(),
        carrier_recipe=None,
        binding_requirements=None,
        operator_history=(),
        parent_seed_id=None,
        root_seed_id=seed_id,
        generation_depth=0,
    )
    execution = ExecutionRecord.model_construct(
        execution_record_id=execution_id,
        seed_id=seed_id,
        binding_source_digest=digest(execution_id + "-binding"),
        normal_task_completed=True,
        costs=ExecutionCosts(),
    )
    entry = CorpusEntry.model_construct(
        corpus_entry_id="entry-" + seed_id,
        seed_id=seed_id,
        seed_kind="risk",
        execution_record_ids=(execution_id,),
        state=CorpusEntryState.ACTIVE,
        statistics=CorpusStatistics(),
    )
    return ParentSelectionCandidate.model_construct(
        corpus_entry=entry,
        seed=seed,
        supporting_execution=execution,
        compatible_frontier_ids=(frontier_id,),
        risk_proximity=risk_proximity,
    )


def test_parent_selection_locks_specific_supporting_execution_and_is_deterministic() -> None:
    frontier_id = "risk-frontier-1"
    low = parent_candidate(
        seed_id="seed-low",
        execution_id="execution-low",
        frontier_id=frontier_id,
        risk_proximity=1,
    )
    high = parent_candidate(
        seed_id="seed-high",
        execution_id="execution-high",
        frontier_id=frontier_id,
        risk_proximity=3,
    )
    first = select_parent(frontier_id=frontier_id, candidates=(low, high))
    second = select_parent(frontier_id=frontier_id, candidates=(high, low))
    assert first == second
    assert first.parent_seed_id == "seed-high"
    assert first.supporting_execution_record_id == "execution-high"
    assert first.binding_source_digest == high.supporting_execution.binding_source_digest


def test_incompatible_high_score_parent_cannot_cross_hard_filter() -> None:
    compatible = parent_candidate(
        seed_id="seed-compatible",
        execution_id="execution-compatible",
        frontier_id="frontier-target",
        risk_proximity=1,
    )
    incompatible = parent_candidate(
        seed_id="seed-incompatible",
        execution_id="execution-incompatible",
        frontier_id="other-frontier",
        risk_proximity=100,
    )
    selected = select_parent(
        frontier_id="frontier-target", candidates=(incompatible, compatible)
    )
    assert selected.parent_seed_id == "seed-compatible"


def context(
    actor_id: str,
    resource_label: str,
    *,
    target_label: str = "target",
    authorization_branch: str = "delegation-missing",
) -> ComparisonContext:
    return seal_contract(
        ComparisonContext,
        {
            "actor_id": actor_id,
            "task_id": "task-1",
            "resource_binding_digest": digest(resource_label),
            "allocation_target_digest": digest(target_label),
            "authorization_branch": authorization_branch,
            "baseline_snapshot_digest": digest("baseline"),
        },
        "comparison_context_digest",
    )


def test_rebinding_creates_new_comparison_context_instead_of_language_comparison() -> None:
    before = context("user-maya", "apollo")
    after = context("user-jordan", "borealis")
    rebind = seal_contract(
        RebindAllocation,
        {
            "rebind_allocation_id": "rebind-1",
            "previous_comparison_context_digest": before.comparison_context_digest,
            "next_context": after,
            "changed_dimensions": ("actor", "resource"),
        },
        "rebind_digest",
    )
    assert rebind.next_context.comparison_context_digest != before.comparison_context_digest


def generation_allocation(*, target_label: str = "target") -> GenerationAllocation:
    return seal_contract(
        GenerationAllocation,
        {
            "generation_allocation_id": "allocation-mutation-1",
            "generation_index": 1,
            "frontier_kind": FrontierKind.RISK,
            "frontier_id": "frontier-a03",
            "allocation_target_digest": digest(target_label),
            "parent_seed_id": "seed-1",
            "supporting_execution_record_id": "execution-1",
            "binding_source_digest": digest("binding"),
            "allocation_lane": AllocationLane.RISK,
            "reason_codes": ("soft-ranking",),
            "coverage_snapshot_digest": digest("coverage"),
            "corpus_digest": digest("corpus"),
            "frontier_digest": digest("frontier"),
        },
        "allocation_digest",
    )


def operator_allocation(*, frontier_id: str = "frontier-a03") -> OperatorAllocation:
    return seal_contract(
        OperatorAllocation,
        {
            "operator_allocation_id": "operator-allocation-1",
            "frontier_id": frontier_id,
            "supporting_execution_record_id": "execution-1",
            "feedback_digest": digest("feedback"),
            "selected_operator_families": ("entry-placement",),
            "reason_codes": ("not-observed-entry-relocation",),
            "policy_digest": V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST,
        },
        "operator_allocation_digest",
    )


def test_mutation_allocation_chains_scheduler_owned_retarget_and_authorization() -> None:
    initial = context("user-maya", "apollo", target_label="target-a01")
    retargeted = context("user-maya", "apollo", target_label="target-a03")
    authorized = context(
        "user-maya",
        "apollo",
        target_label="target-a03",
        authorization_branch="trusted-grant",
    )
    retarget = seal_contract(
        RetargetAllocation,
        {
            "retarget_allocation_id": "retarget-1",
            "source_objective_id": "objective.a01",
            "destination_objective_id": "objective.a03",
            "destination_milestone_id": "milestone.a03.delivery",
            "destination_target_digest": digest("target-a03"),
            "reason_codes": ("scheduler-frontier-switch",),
            "previous_comparison_context_digest": initial.comparison_context_digest,
            "next_context": retargeted,
        },
        "retarget_digest",
    )
    authorization = seal_contract(
        AuthorizationBranchAllocation,
        {
            "authorization_allocation_id": "authorization-allocation-1",
            "source_authorization_branch": "delegation-missing",
            "destination_authorization_branch": "trusted-grant",
            "reason_codes": ("scheduler-authorization-branch",),
            "previous_comparison_context_digest": retargeted.comparison_context_digest,
            "next_context": authorized,
        },
        "authorization_allocation_digest",
    )
    allocation = seal_contract(
        MutationGenerationAllocation,
        {
            "mutation_generation_allocation_id": "mutation-allocation-1",
            "base_allocation": generation_allocation(target_label="target-a03"),
            "initial_context": initial,
            "operator_allocation": operator_allocation(),
            "retarget_allocation": retarget,
            "authorization_branch_allocation": authorization,
            "final_context": authorized,
        },
        "mutation_allocation_digest",
    )

    assert allocation.final_context.authorization_branch == "trusted-grant"
    assert allocation.final_context.allocation_target_digest == digest("target-a03")


def test_mutation_allocation_rejects_silent_retarget_or_operator_drift() -> None:
    initial = context("user-maya", "apollo", target_label="target-a01")
    silent_target_change = context("user-maya", "apollo", target_label="target-a03")
    payload = {
        "mutation_generation_allocation_id": "mutation-allocation-invalid",
        "base_allocation": generation_allocation(target_label="target-a03"),
        "initial_context": initial,
        "operator_allocation": operator_allocation(),
        "final_context": silent_target_change,
    }
    with pytest.raises(ValueError, match="final comparison context"):
        seal_contract(
            MutationGenerationAllocation, payload, "mutation_allocation_digest"
        )

    wrong_operator = operator_allocation(frontier_id="other")
    payload["final_context"] = initial
    payload["operator_allocation"] = wrong_operator
    with pytest.raises(ValueError, match="operator allocation targets"):
        seal_contract(
            MutationGenerationAllocation, payload, "mutation_allocation_digest"
        )


def option(
    frontier_id: str,
    kind: FrontierKind,
    *,
    baseline: bool = False,
    wait: int = 0,
    risk_gap: int = 0,
    rarity: int = 0,
) -> FrontierOption:
    return FrontierOption(
        frontier_kind=kind,
        frontier_id=frontier_id,
        scheduling_state=FrontierSchedulingState.READY,
        baseline_pending=baseline,
        wait_decisions=wait,
        local_budget_remaining=10,
        risk_gap_score=risk_gap,
        behavior_rarity_score=rarity,
    )


def test_scheduler_hard_order_is_baseline_then_starvation_then_behavior_reserve() -> None:
    policy = SchedulerPolicy()
    baseline = option("frontier-baseline", FrontierKind.RISK, baseline=True)
    starved = option("frontier-starved", FrontierKind.RISK, wait=9)
    behavior = option("frontier-behavior", FrontierKind.BEHAVIOR, rarity=10)
    chosen, lane, reasons = choose_frontier(
        options=(behavior, starved, baseline), generation_index=4, policy=policy
    )
    assert chosen is baseline
    assert lane is AllocationLane.BASELINE
    assert reasons == ("baseline-debt",)

    chosen, lane, _ = choose_frontier(
        options=(behavior, starved), generation_index=4, policy=policy
    )
    assert chosen is starved
    assert lane is AllocationLane.STARVATION

    chosen, lane, _ = choose_frontier(
        options=(behavior, option("risk", FrontierKind.RISK, risk_gap=20)),
        generation_index=4,
        policy=policy,
    )
    assert chosen is behavior
    assert lane is AllocationLane.EXPLORATION


def test_generation_allocation_is_always_single_candidate() -> None:
    payload = {
        "generation_allocation_id": "allocation-1",
        "generation_index": 1,
        "frontier_kind": FrontierKind.RISK,
        "frontier_id": "frontier-1",
        "allocation_target_digest": digest("target"),
        "parent_seed_id": "seed-1",
        "supporting_execution_record_id": "execution-1",
        "binding_source_digest": digest("binding"),
        "allocation_lane": AllocationLane.RISK,
        "reason_codes": ("soft-ranking",),
        "coverage_snapshot_digest": digest("coverage"),
        "corpus_digest": digest("corpus"),
        "frontier_digest": digest("frontier"),
    }
    allocation = seal_contract(
        GenerationAllocation, payload, "allocation_digest"
    )
    assert allocation.candidate_count == 1

    payload["candidate_count"] = 2
    with pytest.raises(ValueError, match="Input should be 1"):
        seal_contract(GenerationAllocation, payload, "allocation_digest")
