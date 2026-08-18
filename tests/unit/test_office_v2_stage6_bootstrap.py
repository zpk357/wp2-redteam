from __future__ import annotations

from sandbox.fuzzer.v2_campaign_loop import choose_next_allocation
from sandbox.fuzzer.v2_scheduler import BaselineStatus
from scripts.build_office_v2_stage6_bootstrap import build_stage6_bootstrap


def test_stage6_bootstrap_has_one_parent_per_objective_and_empty_campaign_state() -> None:
    bootstrap = build_stage6_bootstrap(model_identity_digest="sha256:" + "a" * 64)
    state = bootstrap.initial_state
    objective_ids = {item.objective_id for item in state.frontiers.risk_frontiers}

    assert len(state.corpus.entries) == len(objective_ids) == 12
    assert {
        item.origin_intent.objective_id for item in state.corpus.seeds if item.origin_intent
    } == objective_ids
    assert all(item.status is BaselineStatus.PENDING for item in state.exposure_ledger.items)
    assert not state.coverage.canonical_fact_digests
    assert state.budget.used_episodes == state.budget.reserved_episodes == 0
    assert state.budget.consumed.mutator_tokens == state.budget.consumed.agent_tokens == 0
    assert state.lifecycle.counters.generation_index == 0
    assert all(
        item.termination_reason == "frozen-materialization-parent"
        and not item.submitted
        and not item.normal_task_completed
        and item.costs == item.costs.__class__()
        for item in state.corpus.execution_records
    )

    allocation = choose_next_allocation(campaign_id="campaign.stage6", state=state)
    assert allocation.parent_seed_id in {item.seed_id for item in state.corpus.seeds}
    assert allocation.allocation_lane.value == "baseline"
