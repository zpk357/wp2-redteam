from __future__ import annotations

import pytest

from sandbox.fuzzer.v2_campaign import (
    CampaignCompletionStatus,
    CampaignLifecycle,
    CampaignPhase,
    evaluate_campaign_lifecycle,
)
from sandbox.fuzzer.v2_campaign_state import (
    build_campaign_budget,
    reserve_mutation_budget,
    settle_mutation_budget,
)
from sandbox.fuzzer.v2_corpus import ExecutionCosts, PayloadExecutionRef
from sandbox.fuzzer.v2_frontier import FrontierSchedulingState
from sandbox.fuzzer.v2_loop_contracts import (
    ExecutionClosure,
    build_candidate_work_from_handoff,
    build_execution_handoff,
    build_mutation_budget_reservation,
    build_preparation_cost_settlement,
)
from sandbox.fuzzer.v2_scheduler import new_baseline_exposure_ledger, update_baseline_item
from sandbox.fuzzer.v2_work import BudgetReservation, seal_work_contract
from sandbox.mutation.v2_brief import build_minimal_fact_brief
from sandbox.mutation.v2_contracts import build_v2_mutation_field_registry
from sandbox.mutation.v2_materializer import (
    SlotMaterializationTarget,
    TextMaterializationOperation,
)
from sandbox.mutation.v2_preparation import prepare_candidate
from sandbox.mutation.v2_provider import RuleBasedV2MutationProvider
from sandbox.replay.digests import sha256_digest
from tests.unit.test_office_v2_controlled_mutation_contracts import plan


async def ready_preparation():
    mutation_plan = plan()
    brief = build_minimal_fact_brief(
        plan=mutation_plan,
        frontier_description="Exercise one frozen policy boundary.",
        operator_instructions=("Change only the expression structure.",),
        scenario_facts=(),
        parent_payload_texts=("Parent test instruction",),
    )
    preparation, _ = await prepare_candidate(
        campaign_id="campaign-1",
        plan=mutation_plan,
        brief=brief,
        registry=build_v2_mutation_field_registry(),
        provider=RuleBasedV2MutationProvider(),
        parent_text_by_slot={"slot-1": "Parent test instruction"},
        scenario_case_id="scenario-case-1",
        targets=(
            SlotMaterializationTarget(
                payload_slot_id="slot-1",
                resource_id="message-1",
                resource_version="v1",
                field_path="body",
                original_content="Original business content",
                operation=TextMaterializationOperation.APPEND,
            ),
        ),
    )
    return mutation_plan, preparation


def complete_baseline():
    ledger = new_baseline_exposure_ledger()
    for index, item in enumerate(ledger.items):
        ledger = update_baseline_item(
            ledger,
            objective_id=item.objective_id,
            execution_record_id=f"execution-{index}",
        )
    return ledger


def test_mutation_budget_is_reserved_before_use_and_exactly_released() -> None:
    initial = build_campaign_budget(mutator_token_limit=1000)
    reserved = reserve_mutation_budget(initial, tokens=512, cost_microunits=100)
    assert reserved.reserved.mutator_tokens == 512
    settled = settle_mutation_budget(
        reserved,
        reserved_tokens=512,
        reserved_cost_microunits=100,
        actual=ExecutionCosts(mutator_tokens=80, monetary_microunits=10),
    )
    assert settled.reserved.mutator_tokens == 0
    assert settled.consumed.mutator_tokens == 80

    with pytest.raises(ValueError, match="token budget exceeded"):
        reserve_mutation_budget(initial, tokens=1001, cost_microunits=0)


@pytest.mark.asyncio
async def test_ready_preparation_has_one_locked_handoff_and_work() -> None:
    mutation_plan, preparation = await ready_preparation()
    allocation = mutation_plan.allocation.base_allocation
    reservation = build_mutation_budget_reservation(
        campaign_id="campaign-1",
        allocation=allocation,
        mutation_plan_digest=mutation_plan.plan_digest,
        reserved_tokens=mutation_plan.budget.plan_total_token_budget,
        reserved_cost_microunits=(
            mutation_plan.budget.reserved_total_cost_microunits
        ),
    )
    settlement = build_preparation_cost_settlement(
        reservation=reservation, preparation=preparation
    )
    assert settlement.actual_costs.mutator_tokens > 0
    assert settlement.released_tokens >= 0

    handoff = build_execution_handoff(
        campaign_id="campaign-1",
        allocation=allocation,
        preparation=preparation,
    )
    work = build_candidate_work_from_handoff(
        handoff=handoff,
        reservation=BudgetReservation(agent_tokens=500, elapsed_ms=10_000),
    )
    assert work.generation_allocation_digest == allocation.allocation_digest
    assert work.baseline_snapshot_digest == allocation.coverage_snapshot_digest


@pytest.mark.asyncio
async def test_handoff_rejects_non_ready_or_lineage_drift() -> None:
    mutation_plan, preparation = await ready_preparation()
    rejected = preparation.model_copy(update={"state": "rejected"})
    with pytest.raises(ValueError, match="only ready"):
        build_execution_handoff(
            campaign_id="campaign-1",
            allocation=mutation_plan.allocation.base_allocation,
            preparation=rejected,
        )
    drifted = preparation.model_copy(
        update={
            "materialized_candidate": preparation.materialized_candidate.model_copy(
                update={"binding_source_digest": sha256_digest("drift")}
            )
        }
    )
    with pytest.raises(ValueError, match="binding lineage differs"):
        build_execution_handoff(
            campaign_id="campaign-1",
            allocation=mutation_plan.allocation.base_allocation,
            preparation=drifted,
        )


def test_observed_and_used_evidence_are_an_ordered_factual_prefix() -> None:
    observed = PayloadExecutionRef(
        payload_spec_id="payload-1", evidence_digest=sha256_digest("read")
    )
    used = PayloadExecutionRef(
        payload_spec_id="payload-1", evidence_digest=sha256_digest("use")
    )
    closure = seal_work_contract(
        ExecutionClosure,
        {
            "closure_id": "closure-1",
            "work_id": "work-1",
            "execution_id": "execution-1",
            "materialized_candidate_id": "candidate-1",
            "trace_digest": sha256_digest("trace"),
            "manifest_digest": sha256_digest("manifest"),
            "oracle_fact_digest": sha256_digest("oracle"),
            "initial_state_digest": sha256_digest("initial"),
            "final_state_digest": sha256_digest("final"),
            "observed_payload_refs": (observed,),
            "used_payload_refs": (used,),
            "submitted": True,
            "termination_reason": "submit",
            "cleanup_confirmed": True,
        },
        "closure_digest",
    )
    assert closure.used_payload_refs == (used,)
    with pytest.raises(ValueError, match="must first be observed"):
        seal_work_contract(
            ExecutionClosure,
            {
                **closure.model_dump(mode="python", exclude={"closure_digest"}),
                "observed_payload_refs": (),
            },
            "closure_digest",
        )


def test_baseline_complete_is_an_event_not_a_terminal_status() -> None:
    lifecycle = evaluate_campaign_lifecycle(
        current=CampaignLifecycle(),
        baseline_ledger=complete_baseline(),
        risk_frontier_states=(FrontierSchedulingState.READY,),
        behavior_frontier_states=(),
    )
    assert lifecycle.phase is CampaignPhase.ADAPTIVE
    assert lifecycle.baseline_complete_event_emitted
    assert lifecycle.completion_status is None
    assert CampaignCompletionStatus.BASELINE_COMPLETE.value == "baseline_complete"
