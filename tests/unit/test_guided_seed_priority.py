"""Guided newly-promoted seed priority: one-time queue, consumption, isolation.

Local acceptance for TASK-GUIDED-SEED-PRIORITY-20260920.  The state-level rules
run against the real contracts, and the end-to-end rules (promotion enqueues,
the next generation is prioritised, the settlement consumes) run through the real
Store, scheduler and driver with the synthetic Episode runner used by the L03-1
sample.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign_state import (
    V2CampaignStateSnapshot,
    add_promoted_seed_to_catalog,
    build_campaign_state,
    consume_new_seed_priority,
    enqueue_new_seed_priority,
    settle_risk_progress,
)
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_orchestrator import decide_next_generation
from sandbox.fuzzer.v2_seed_pools import RiskProgressLevel, derive_promoted_seed
from sandbox.fuzzer.v2_selection import (
    SelectionKind,
    SelectionPolicy,
    SelectionReceipt,
    independent_uniform_selection,
    priority_selection,
)
from sandbox.fuzzer.v2_strategy import CampaignStrategy
from tests.unit.test_office_v2_comparison_sample import (
    GENERATIONS,
    GUIDED,
    _run_campaign,
)

INDEPENDENT = "priority.independent"


def _promoted_child(base_state, *, suffix: str = "priority"):
    parent = base_state.seed_catalog.pools[0].root_seeds[0]
    child = derive_promoted_seed(
        parent=parent,
        rewritten_text=f"Please proceed: {parent.base_text} [{suffix}]",
        operator_receipt_ids=(f"operator-receipt.{suffix}",),
    )
    return parent, child


def _queued_state(base_state, *, suffix: str = "priority"):
    parent, child = _promoted_child(base_state, suffix=suffix)
    state = add_promoted_seed_to_catalog(base_state, seed=child)
    state = enqueue_new_seed_priority(state, (child.seed_id,))
    return parent, child, state


def test_priority_selection_is_a_single_option_receipt() -> None:
    receipt = priority_selection(
        selection_kind=SelectionKind.PARENT,
        campaign_id="priority",
        generation_index=3,
        option_id="seed.example",
        reason_codes=("new-seed-priority-queue-head",),
    )

    assert receipt.selection_policy is SelectionPolicy.NEW_SEED_PRIORITY
    assert receipt.selected_option_id == "seed.example"
    assert len(receipt.options) == 1
    assert receipt.total_weight == 1 and receipt.draw == 0
    assert "new-seed-priority-queue-head" in receipt.options[0].reason_codes


def test_a_priority_receipt_cannot_carry_two_options() -> None:
    uniform = independent_uniform_selection(
        selection_kind=SelectionKind.PARENT,
        campaign_id="priority",
        generation_index=0,
        option_ids=("seed.a", "seed.b"),
    )
    forged = {
        **uniform.model_dump(mode="json"),
        "selection_policy": SelectionPolicy.NEW_SEED_PRIORITY.value,
    }
    with pytest.raises(ValidationError, match="exactly one option"):
        SelectionReceipt.model_validate(forged)


def test_an_empty_queue_keeps_historical_state_digests_loadable() -> None:
    state = build_exploratory_bootstrap(episode_limit=2).initial_state
    payload = json.loads(state.model_dump_json(exclude_none=False))
    payload.pop("new_seed_priority_queue")
    restored = V2CampaignStateSnapshot.model_validate(payload)

    # A Campaign written before the mechanism existed must still validate with
    # the digest it was stored with.
    assert restored.new_seed_priority_queue == ()
    assert restored.state_digest == state.state_digest


def test_a_non_empty_queue_participates_in_the_state_digest() -> None:
    base = build_exploratory_bootstrap(episode_limit=4).initial_state
    _, child, queued = _queued_state(base)

    assert queued.new_seed_priority_queue == (child.seed_id,)
    assert queued.state_digest != base.state_digest
    assert queued.new_seed_priority_queue[0] != base.state_digest


def test_the_queue_is_fifo_unique_and_closes_over_the_catalog() -> None:
    base = build_exploratory_bootstrap(episode_limit=4).initial_state
    _, first = _promoted_child(base, suffix="one")
    _, second = _promoted_child(base, suffix="two")
    state = add_promoted_seed_to_catalog(base, seed=first)
    state = add_promoted_seed_to_catalog(state, seed=second)
    state = enqueue_new_seed_priority(state, (first.seed_id, second.seed_id))
    state = enqueue_new_seed_priority(state, (first.seed_id,))

    assert state.new_seed_priority_queue == (first.seed_id, second.seed_id)
    with pytest.raises(ValidationError, match="cannot repeat a seed"):
        V2CampaignStateSnapshot.model_validate(
            {
                **json.loads(state.model_dump_json(exclude_none=False)),
                "new_seed_priority_queue": [first.seed_id, first.seed_id],
            }
        )
    with pytest.raises(ValidationError, match="outside the catalog"):
        V2CampaignStateSnapshot.model_validate(
            {
                **json.loads(state.model_dump_json(exclude_none=False)),
                "new_seed_priority_queue": ["seed.not-in-catalog"],
            }
        )


def test_consumption_is_driven_by_the_selection_receipt() -> None:
    base = build_exploratory_bootstrap(episode_limit=4).initial_state
    _, child, state = _queued_state(base)
    priority_allocation = SimpleNamespace(
        parent_selection_receipt=priority_selection(
            selection_kind=SelectionKind.PARENT,
            campaign_id="priority",
            generation_index=0,
            option_id=child.seed_id,
            reason_codes=("new-seed-priority-queue-head",),
        )
    )
    uniform_allocation = SimpleNamespace(
        parent_selection_receipt=independent_uniform_selection(
            selection_kind=SelectionKind.PARENT,
            campaign_id="priority",
            generation_index=0,
            option_ids=(child.seed_id,),
        )
    )

    # An ordinary draw never spends an opportunity, and a replay is a no-op.
    assert consume_new_seed_priority(state, allocation=uniform_allocation) == state
    consumed = consume_new_seed_priority(state, allocation=priority_allocation)
    assert consumed.new_seed_priority_queue == ()
    assert consume_new_seed_priority(consumed, allocation=priority_allocation) == consumed
    # Empty queue and identical catalog -> the same state as before the promotion
    # was queued, i.e. the opportunity left no residue in the digest.
    empty_queue = add_promoted_seed_to_catalog(base, seed=child)
    assert consumed.state_digest == empty_queue.state_digest


def test_the_queue_survives_pause_resume_and_budget_edits(tmp_path) -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=6)
    _, child, queued = _queued_state(bootstrap.initial_state)
    queued = settle_risk_progress(
        queued,
        risk_type=child.risk_type,
        attempted=True,
        realized=False,
    )
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="prio", initial_state=queued)
        paused = store.pause_campaign("prio", reason="temporary")
        resumed = store.resume_paused_campaign("prio", reason="exploratory-resume")
        extended = store.extend_episode_limit("prio", episode_limit=8)
        loaded = store.load_state("prio")

    for state in (paused, resumed, extended, loaded):
        assert state.new_seed_priority_queue == (child.seed_id,)
    assert extended.budget.episode_limit == 8


def test_an_independent_allocation_ignores_the_queue() -> None:
    base = build_exploratory_bootstrap(episode_limit=4).initial_state
    _, child, queued = _queued_state(base)
    decision = decide_next_generation(
        campaign_id="prio.independent",
        state=queued,
        latest_feedback=None,
        strategy=CampaignStrategy.RANDOM_INDEPENDENT,
    )

    assert decision.allocation.parent_seed_id != child.seed_id
    assert (
        decision.allocation.parent_selection_receipt.selection_policy
        is SelectionPolicy.RANDOM_UNIFORM
    )
    assert (
        decision.allocation.direction_selection_receipt.selection_policy
        is SelectionPolicy.RANDOM_UNIFORM
    )
    # Reading it for a baseline generation does not spend or change the queue.
    assert queued.new_seed_priority_queue == (child.seed_id,)


def _root_seed_ids(base_state) -> tuple[str, str, str]:
    pools = base_state.seed_catalog.pools
    return (
        pools[0].root_seeds[0].seed_id,
        pools[1].root_seeds[0].seed_id,
        pools[2].root_seeds[0].seed_id,
    )


def test_a_promoted_corpus_entry_earns_one_priority_opportunity() -> None:
    """The enqueue rule: only a promoted guided corpus entry enters the queue."""

    from sandbox.fuzzer.v2_real_runtime import _enqueue_promoted_seed

    base = build_exploratory_bootstrap(episode_limit=4).initial_state
    _, child = _promoted_child(base)
    # The driver adds the promoted seed to the catalog before queueing it.
    with_seed = add_promoted_seed_to_catalog(base, seed=child)
    promoted = SimpleNamespace(corpus_entry=object(), promoted_seed=child)
    without_entry = SimpleNamespace(corpus_entry=None, promoted_seed=child)

    guided = _enqueue_promoted_seed(
        with_seed, promoted=promoted, strategy=CampaignStrategy.COVERAGE_GUIDED
    )
    independent = _enqueue_promoted_seed(
        with_seed, promoted=promoted, strategy=CampaignStrategy.RANDOM_INDEPENDENT
    )
    not_promoted = _enqueue_promoted_seed(
        with_seed, promoted=without_entry, strategy=CampaignStrategy.COVERAGE_GUIDED
    )

    assert guided.new_seed_priority_queue == (child.seed_id,)
    assert independent == with_seed
    assert independent.new_seed_priority_queue == ()
    assert not_promoted == with_seed


def test_the_queued_head_wins_the_next_guided_selection() -> None:
    """The selection rule: a non-empty queue beats the ordinary pool draw."""

    from sandbox.fuzzer.v2_campaign_loop import choose_next_allocation

    base = build_exploratory_bootstrap(episode_limit=4).initial_state
    first, second, _third = _root_seed_ids(base)
    queued = enqueue_new_seed_priority(base, (first, second))

    allocation = choose_next_allocation(campaign_id="priority", state=queued)
    uniform_baseline = choose_next_allocation(campaign_id="priority", state=base)

    assert allocation.parent_seed_id == first
    assert allocation.direction_selection_receipt.selection_policy is (
        SelectionPolicy.NEW_SEED_PRIORITY
    )
    assert allocation.parent_selection_receipt.selection_policy is (
        SelectionPolicy.NEW_SEED_PRIORITY
    )
    assert "new-seed-priority-queue-head" in allocation.reason_codes
    # The queue forced both the direction and the parent to the seed's own pool.
    allocation_direction = allocation.risk_type
    pool = base.seed_catalog.pool_for(allocation_direction)
    assert first in {seed.seed_id for seed in pool.seeds}
    # An empty queue still uses the ordinary uniform draw for the same state.
    assert (
        uniform_baseline.parent_selection_receipt.selection_policy
        is SelectionPolicy.RANDOM_UNIFORM
    )
    # The queue is FIFO: consuming the head exposes the second seed next.
    consumed = consume_new_seed_priority(queued, allocation=allocation)
    assert consumed.new_seed_priority_queue == (second,)
    follow_up = choose_next_allocation(campaign_id="priority", state=consumed)
    assert follow_up.parent_seed_id == second


def test_the_random_arm_never_fills_the_queue(tmp_path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    bootstrap = build_exploratory_bootstrap(episode_limit=GENERATIONS)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(
            campaign_id=INDEPENDENT,
            initial_state=bootstrap.initial_state,
            strategy="random_independent",
        )
        _run_campaign(
            store=store,
            data_root=data_root,
            campaign_id=INDEPENDENT,
            strategy=CampaignStrategy.RANDOM_INDEPENDENT,
            campaign_seed_value=7,
            bootstrap=bootstrap,
        )
        state = store.load_state(INDEPENDENT)
        decisions = store.list_generation_decisions(INDEPENDENT)

    assert state.new_seed_priority_queue == ()
    assert state.corpus.entries  # the arm still records its own Episodes
    assert all(
        decision.allocation.parent_selection_receipt.selection_policy
        is SelectionPolicy.RANDOM_UNIFORM
        for decision in decisions
    )


def test_the_report_exposes_the_queue_and_the_selection_source(tmp_path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    bootstrap = build_exploratory_bootstrap(episode_limit=GENERATIONS)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id=GUIDED, initial_state=bootstrap.initial_state)
        _run_campaign(
            store=store,
            data_root=data_root,
            campaign_id=GUIDED,
            strategy=CampaignStrategy.COVERAGE_GUIDED,
            campaign_seed_value=7,
            bootstrap=bootstrap,
        )
        from sandbox.fuzzer.v2_report import build_v2_campaign_report

        report = build_v2_campaign_report(store=store, campaign_id=GUIDED)

    block = report["guided_seed_priority"]
    assert block["mechanism"] == "guided-only"
    assert block["last_selection_source"] in {"new_seed_priority", "uniform_pool"}
    assert isinstance(block["queue"], list)


def test_a_non_episode_close_still_spends_the_opportunity(tmp_path) -> None:
    """Preparations that never settle an Episode must not re-grant the chance."""

    bootstrap = build_exploratory_bootstrap(episode_limit=2)
    first, _second, _third = _root_seed_ids(bootstrap.initial_state)
    queued = enqueue_new_seed_priority(bootstrap.initial_state, (first,))
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="prio.non-episode", initial_state=queued)
        decision = decide_next_generation(
            campaign_id="prio.non-episode",
            state=queued,
            latest_feedback=None,
            strategy=CampaignStrategy.COVERAGE_GUIDED,
        )
        store.put_generation_decision(decision)
        state = store.load_state("prio.non-episode")
        # The closing path the driver uses for a generation without an Episode.
        next_state = build_campaign_state(
            coverage=state.coverage,
            corpus=state.corpus,
            seed_catalog=state.seed_catalog,
            risk_progress=state.risk_progress,
            new_seed_priority_queue=state.new_seed_priority_queue,
            budget=state.budget,
            lifecycle=state.lifecycle,
        )
        next_state = consume_new_seed_priority(
            next_state, allocation=decision.allocation
        )

    assert decision.allocation.parent_seed_id == first
    assert next_state.new_seed_priority_queue == ()


def test_the_queue_does_not_change_seed_progress_or_the_pool() -> None:
    base = build_exploratory_bootstrap(episode_limit=4).initial_state
    _, child, queued = _queued_state(base)
    settled = settle_risk_progress(
        queued,
        risk_type=child.risk_type,
        attempted=False,
        realized=False,
    )

    # Promotion adds exactly one seed to its own pool and the per-risk-type
    # progress still moves 0 -> 1 on selection; the queue is the only addition.
    pool_before = base.seed_catalog.pool_for(child.risk_type)
    pool_after = settled.seed_catalog.pool_for(child.risk_type)
    assert len(pool_after.seeds) == len(pool_before.seeds) + 1
    assert len(settled.seed_catalog.pools) == len(base.seed_catalog.pools)
    levels = {item.risk_type: item.level for item in settled.risk_progress}
    assert levels[child.risk_type] is RiskProgressLevel.SELECTED
    assert settled.new_seed_priority_queue == (child.seed_id,)
