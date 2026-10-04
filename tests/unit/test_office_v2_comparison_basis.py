"""Comparison basis: generation/feedback pairing and paired-seed propagation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from sandbox.fuzzer import v2_real_runtime
from sandbox.fuzzer.v2_bootstrap import build_exploratory_bootstrap
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_orchestrator import decide_next_generation
from sandbox.fuzzer.v2_real_runtime import _RealGenerationDriver
from sandbox.fuzzer.v2_report import build_v2_generation_series
from sandbox.fuzzer.v2_risk_pool_scheduler import build_formal_risk_allocation
from sandbox.fuzzer.v2_runtime import run_or_resume_campaign
from sandbox.fuzzer.v2_seed_pools import (
    RiskProgressLevel,
    RiskType,
    RiskTypeProgressState,
    build_initial_seed_catalog,
)
from sandbox.fuzzer.v2_selection import campaign_seed
from sandbox.fuzzer.v2_strategy import CampaignStrategy
from sandbox.mutation.v2_policy import select_formal_operator
from sandbox.mutation.v2_provider import RuleBasedV2MutationProvider
from sandbox.replay.exceptions import ReplayPreparationError

BEHAVIOR_LIMIT = (
    "recorded execution failed (tool_call_budget_exceeded): tool call budget exceeded: 24"
)


class _StopAfterPlanResolution(Exception):
    """Sentinel: stop the driver right after it resolves the Mutation Plan."""


def behaviour_limit_driver(
    store,
    bootstrap,
    *,
    campaign_seed_value: int,
    strategy: CampaignStrategy = CampaignStrategy.COVERAGE_GUIDED,
):
    provider = RuleBasedV2MutationProvider()
    runner = SimpleNamespace(
        execute=AsyncMock(side_effect=ReplayPreparationError(-32108, BEHAVIOR_LIMIT)),
        cleanup_interrupted=Mock(),
    )
    return (
        _RealGenerationDriver(
            store=store,
            bootstrap=bootstrap,
            mutation_provider=provider,
            episode_runner=runner,
            strategy=strategy,
            campaign_seed_value=campaign_seed_value,
        ),
        provider,
        runner,
    )


def test_operator_selection_ignores_the_campaign_name() -> None:
    first = select_formal_operator(
        campaign_id="campaign.alpha",
        generation_index=3,
        risk_type=RiskType.SENSITIVE_INFORMATION_DISCLOSURE,
        supporting_record_id="record.shared",
        campaign_seed_value=17,
    )
    second = select_formal_operator(
        campaign_id="campaign.beta",
        generation_index=3,
        risk_type=RiskType.SENSITIVE_INFORMATION_DISCLOSURE,
        supporting_record_id="record.shared",
        campaign_seed_value=17,
    )

    assert first.allocation is not None and second.allocation is not None
    assert (
        first.allocation.selected_operator_families
        == second.allocation.selected_operator_families
    )
    assert (
        first.allocation.selected_operator_variants
        == second.allocation.selected_operator_variants
    )
    assert first.allocation.operator_count_selection_receipt.campaign_seed == 17
    assert first.allocation.operator_count_selection_receipt.campaign_id == "campaign.alpha"


def test_operator_selection_varies_with_the_explicit_campaign_seed() -> None:
    draws = {
        (
            select_formal_operator(
                campaign_id="campaign.one",
                generation_index=1,
                risk_type=RiskType.DESTRUCTIVE_OPERATION,
                supporting_record_id="record.seed-sweep",
                campaign_seed_value=seed,
            ).allocation.selected_operator_families
        )
        for seed in range(32)
    }

    assert len(draws) > 1


def test_operator_selection_without_an_explicit_seed_keeps_using_the_campaign_id() -> None:
    decision = select_formal_operator(
        campaign_id="campaign.legacy",
        generation_index=1,
        risk_type=RiskType.DESTRUCTIVE_OPERATION,
        supporting_record_id="record.legacy",
    )

    assert decision.allocation is not None
    assert (
        decision.allocation.operator_count_selection_receipt.campaign_seed
        == campaign_seed("campaign.legacy")
    )


def test_risk_direction_and_parent_ignore_the_campaign_name() -> None:
    catalog = build_initial_seed_catalog()
    selectable = frozenset(seed.seed_id for pool in catalog.pools for seed in pool.seeds)
    progress = tuple(
        RiskTypeProgressState(risk_type=risk_type, level=RiskProgressLevel.NOT_SELECTED)
        for risk_type in RiskType
    )
    arguments = {
        "catalog": catalog,
        "progress_states": progress,
        "generation_index": 2,
        "campaign_seed_value": 5,
        "selectable_seed_ids": selectable,
        "budget_remaining": 5,
    }

    first = build_formal_risk_allocation(campaign_id="campaign.alpha", **arguments)
    second = build_formal_risk_allocation(campaign_id="campaign.beta", **arguments)

    assert first.parent_seed_id == second.parent_seed_id
    assert first.risk_type is second.risk_type


def test_a_fresh_generation_draws_operators_with_the_explicit_campaign_seed(
    tmp_path, monkeypatch
) -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=4)
    select = Mock(side_effect=lambda **kwargs: select_formal_operator(**kwargs))
    monkeypatch.setattr("sandbox.fuzzer.v2_real_runtime.select_formal_operator", select)
    monkeypatch.setattr(
        "sandbox.fuzzer.v2_real_runtime.build_mutation_budget_reservation",
        Mock(side_effect=_StopAfterPlanResolution),
    )

    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="fresh", initial_state=bootstrap.initial_state)
        decision = decide_next_generation(
            campaign_id="fresh", state=bootstrap.initial_state, latest_feedback=None
        )
        store.put_generation_decision(decision)
        driver, _, _ = behaviour_limit_driver(store, bootstrap, campaign_seed_value=41)

        with pytest.raises(_StopAfterPlanResolution):
            driver.advance(
                campaign_id="fresh",
                decision=decision,
                state=bootstrap.initial_state,
                previous_feedback=None,
            )

    assert select.called
    assert select.call_args.kwargs["campaign_seed_value"] == 41


def test_recovering_a_generation_reuses_its_saved_plan(tmp_path, monkeypatch) -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=4)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        driver, _, _ = behaviour_limit_driver(store, bootstrap, campaign_seed_value=3)
        run_or_resume_campaign(
            store=store,
            campaign_id="reuse",
            initial_state=bootstrap.initial_state,
            generation_count=1,
            driver=driver,
            exploratory=True,
            max_generation_attempts=1,
        )
        decision = store.load_latest_generation_decision("reuse")
        assert decision is not None
        saved = store.load_preparation_for_allocation(
            "reuse", decision.allocation.generation_allocation_id
        )
        assert saved is not None

        select = Mock(side_effect=AssertionError("operators must not be re-drawn"))
        monkeypatch.setattr("sandbox.fuzzer.v2_real_runtime.select_formal_operator", select)
        monkeypatch.setattr(
            "sandbox.fuzzer.v2_real_runtime.build_mutation_budget_reservation",
            Mock(side_effect=_StopAfterPlanResolution),
        )
        recovering, _, _ = behaviour_limit_driver(store, bootstrap, campaign_seed_value=99)

        with pytest.raises(_StopAfterPlanResolution):
            recovering.advance(
                campaign_id="reuse",
                decision=decision,
                state=store.load_state("reuse"),
                previous_feedback=None,
            )

    assert not select.called


def test_a_legacy_reservation_is_rebuilt_from_the_per_campaign_name_seed(
    tmp_path, monkeypatch
) -> None:
    """A generation interrupted before operator sampling used the explicit seed
    persisted a plan built from the legacy per-Campaign-name draw. Resuming must
    rebuild that plan deterministically and verify it against the stored digest."""

    bootstrap = build_exploratory_bootstrap(episode_limit=4)
    real_select = v2_real_runtime.select_formal_operator
    real_prepare = v2_real_runtime.prepare_candidate
    attempts = {"count": 0}

    def legacy_select(**kwargs):
        kwargs.pop("campaign_seed_value", None)
        return real_select(**kwargs)

    def flaky_prepare(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("mutation interrupted before the preparation was saved")
        return real_prepare(**kwargs)

    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="legacy", initial_state=bootstrap.initial_state)
        decision = decide_next_generation(
            campaign_id="legacy", state=bootstrap.initial_state, latest_feedback=None
        )
        store.put_generation_decision(decision)

        monkeypatch.setattr(
            "sandbox.fuzzer.v2_real_runtime.select_formal_operator", legacy_select
        )
        monkeypatch.setattr("sandbox.fuzzer.v2_real_runtime.prepare_candidate", flaky_prepare)
        legacy_driver, _, _ = behaviour_limit_driver(store, bootstrap, campaign_seed_value=99)
        with pytest.raises(RuntimeError, match="mutation interrupted"):
            legacy_driver.advance(
                campaign_id="legacy",
                decision=decision,
                state=bootstrap.initial_state,
                previous_feedback=None,
            )
        reservation = store.load_mutation_reservation_for_allocation(
            "legacy", decision.allocation.generation_allocation_id
        )
        assert reservation is not None

        calls: list[int | None] = []

        def spying_select(**kwargs):
            calls.append(kwargs.get("campaign_seed_value"))
            return real_select(**kwargs)

        monkeypatch.setattr(
            "sandbox.fuzzer.v2_real_runtime.select_formal_operator", spying_select
        )
        driver, _, _ = behaviour_limit_driver(store, bootstrap, campaign_seed_value=99)
        state = store.resume_paused_campaign("legacy", reason="upgraded")
        advance = driver.resume_incomplete(
            campaign_id="legacy",
            decision=decision,
            state=state,
            previous_feedback=None,
        )

        assert advance is not None
        # Explicit seed first, then the deterministic legacy retry, and the retry
        # is accepted only because it reproduces the persisted plan digest.
        assert calls == [99, campaign_seed("legacy")]
        prepared = store.load_preparation_for_allocation(
            "legacy", decision.allocation.generation_allocation_id
        )
        assert prepared is not None
        assert prepared.plan.plan_digest == reservation[0].mutation_plan_digest


def test_generation_series_pairs_each_generation_with_the_feedback_it_produced(
    tmp_path,
) -> None:
    bootstrap = build_exploratory_bootstrap(episode_limit=3)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        driver, _, _ = behaviour_limit_driver(store, bootstrap, campaign_seed_value=0)
        run_or_resume_campaign(
            store=store,
            campaign_id="series",
            initial_state=bootstrap.initial_state,
            generation_count=3,
            driver=driver,
            exploratory=True,
            max_generation_attempts=3,
        )
        decisions = store.list_generation_decisions("series")
        feedback = {
            item.generation_index: item
            for item in store.list_generation_feedback("series")
        }
        series = build_v2_generation_series(store=store, campaign_id="series")

    assert [item.generation_index for item in decisions] == [0, 1, 2]
    assert set(feedback) == {1, 2, 3}
    assert [row["generation_index"] for row in series] == [0, 1, 2]

    for row, decision in zip(series, decisions, strict=True):
        index = row["generation_index"]
        # What the generation consumed is exactly what its decision recorded.
        assert row["input_feedback_digest"] == decision.input_feedback_digest
        # What it produced is the next indexed record.
        assert row["produced_feedback_digest"] == feedback[index + 1].feedback_digest
        assert row["produced_gap_kind"] == feedback[index + 1].gap_kind.value

    # Generation 0 has no persisted consumed feedback; the report must say so
    # instead of borrowing generation 1's record.
    assert series[0]["input_feedback_digest"] is None
    assert series[0]["gap_kind"] is None
    assert series[1]["input_feedback_digest"] == feedback[1].feedback_digest
    assert series[1]["gap_kind"] == feedback[1].gap_kind.value

    # The last generation's increments must be counted, not dropped.
    assert series[-1]["produced_feedback_digest"] == feedback[3].feedback_digest
    assert series[-1]["no_coverage_gain"] is feedback[3].brief.consecutive_no_gain


def test_generation_series_does_not_report_consumed_feedback_for_the_baseline(
    tmp_path,
) -> None:
    """The independent baseline consumes no cross-Episode feedback, and must not
    be reported as if it did, even though its own feedback records exist."""

    bootstrap = build_exploratory_bootstrap(episode_limit=3)
    with V2CampaignStore(tmp_path / "campaign.db") as store:
        driver, _, _ = behaviour_limit_driver(
            store,
            bootstrap,
            campaign_seed_value=0,
            strategy=CampaignStrategy.RANDOM_INDEPENDENT,
        )
        run_or_resume_campaign(
            store=store,
            campaign_id="independent-series",
            initial_state=bootstrap.initial_state,
            generation_count=3,
            driver=driver,
            strategy=CampaignStrategy.RANDOM_INDEPENDENT,
            exploratory=True,
            max_generation_attempts=3,
        )
        decisions = store.list_generation_decisions("independent-series")
        feedback = {
            item.generation_index
            for item in store.list_generation_feedback("independent-series")
        }
        series = build_v2_generation_series(
            store=store, campaign_id="independent-series"
        )

    assert feedback == {1, 2, 3}
    assert [item.input_feedback_digest for item in decisions] == [None, None, None]
    for row in series:
        assert row["input_feedback_digest"] is None
        assert row["gap_kind"] is None
        assert row["produced_feedback_digest"] is not None


def test_resuming_in_the_reservation_window_rederives_the_same_plan(
    tmp_path, monkeypatch
) -> None:
    """A generation interrupted after the budget reservation but before the
    preparation was saved must resume with the same derived plan, not a re-draw."""

    bootstrap = build_exploratory_bootstrap(episode_limit=4)
    real_prepare = v2_real_runtime.prepare_candidate
    attempts = {"count": 0}

    def flaky_prepare(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("mutation interrupted before the preparation was saved")
        return real_prepare(**kwargs)

    monkeypatch.setattr("sandbox.fuzzer.v2_real_runtime.prepare_candidate", flaky_prepare)

    with V2CampaignStore(tmp_path / "campaign.db") as store:
        store.create_campaign(campaign_id="window", initial_state=bootstrap.initial_state)
        decision = decide_next_generation(
            campaign_id="window", state=bootstrap.initial_state, latest_feedback=None
        )
        store.put_generation_decision(decision)
        driver, _, _ = behaviour_limit_driver(store, bootstrap, campaign_seed_value=7)

        with pytest.raises(RuntimeError, match="mutation interrupted"):
            driver.advance(
                campaign_id="window",
                decision=decision,
                state=bootstrap.initial_state,
                previous_feedback=None,
            )
        reservation = store.load_mutation_reservation_for_allocation(
            "window", decision.allocation.generation_allocation_id
        )
        assert reservation is not None
        assert (
            store.load_preparation_for_allocation(
                "window", decision.allocation.generation_allocation_id
            )
            is None
        )

        state = store.resume_paused_campaign("window", reason="fixed-code")
        advance = driver.resume_incomplete(
            campaign_id="window",
            decision=decision,
            state=state,
            previous_feedback=None,
        )

        assert advance is not None
        assert attempts["count"] == 2
        # The reservation itself must be unchanged; its settled flag may flip once
        # the preparation cost is settled.
        assert (
            store.load_mutation_reservation_for_allocation(
                "window", decision.allocation.generation_allocation_id
            )[0]
            == reservation[0]
        )
        assert (
            store.load_preparation_for_allocation(
                "window", decision.allocation.generation_allocation_id
            )
            is not None
        )
