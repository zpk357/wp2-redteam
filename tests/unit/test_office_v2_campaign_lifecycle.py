from __future__ import annotations

from sandbox.fuzzer.v2_campaign import (
    CampaignCompletionStatus,
    CampaignCounters,
    CampaignLifecycle,
    CampaignPhase,
    evaluate_campaign_lifecycle,
    record_failed_attempt,
    record_valid_episode,
)
from sandbox.fuzzer.v2_frontier import FrontierSchedulingState
from sandbox.fuzzer.v2_scheduler import new_baseline_exposure_ledger, update_baseline_item


def complete_baseline():
    ledger = new_baseline_exposure_ledger()
    for index, item in enumerate(ledger.items):
        ledger = update_baseline_item(
            ledger,
            objective_id=item.objective_id,
            execution_record_id=f"execution-{index}",
        )
    return ledger


def test_baseline_complete_is_phase_transition_not_final_saturation() -> None:
    lifecycle = evaluate_campaign_lifecycle(
        current=CampaignLifecycle(),
        baseline_ledger=complete_baseline(),
        risk_frontier_states=(FrontierSchedulingState.READY,),
        behavior_frontier_states=(),
    )
    assert lifecycle.phase is CampaignPhase.ADAPTIVE
    assert lifecycle.baseline_complete_event_emitted
    assert lifecycle.completion_status is None


def test_budget_exhaustion_with_open_gap_is_incomplete_not_saturated() -> None:
    lifecycle = evaluate_campaign_lifecycle(
        current=CampaignLifecycle(phase=CampaignPhase.ADAPTIVE),
        baseline_ledger=complete_baseline(),
        risk_frontier_states=(FrontierSchedulingState.LOCAL_BUDGET_EXHAUSTED,),
        behavior_frontier_states=(),
        budget_exhausted=True,
    )
    assert (
        lifecycle.completion_status
        is CampaignCompletionStatus.BUDGET_EXHAUSTED_INCOMPLETE
    )


def test_only_local_saturation_plus_global_valid_no_gain_can_saturate() -> None:
    current = CampaignLifecycle(
        phase=CampaignPhase.ADAPTIVE,
        counters=CampaignCounters(global_consecutive_no_gain=5),
    )
    lifecycle = evaluate_campaign_lifecycle(
        current=current,
        baseline_ledger=complete_baseline(),
        risk_frontier_states=(FrontierSchedulingState.LOCALLY_SATURATED,),
        behavior_frontier_states=(FrontierSchedulingState.UNREACHABLE,),
    )
    assert lifecycle.completion_status is CampaignCompletionStatus.SATURATED


def test_failed_attempt_does_not_advance_generation_or_no_gain_window() -> None:
    current = CampaignLifecycle()
    failed = record_failed_attempt(current)
    assert failed.counters.invalid_or_failed_attempts == 1
    assert failed.counters.generation_index == 0
    assert failed.counters.global_consecutive_no_gain == 0

    committed = record_valid_episode(failed, coverage_gain=False)
    assert committed.counters.generation_index == 1
    assert committed.counters.global_consecutive_no_gain == 1


def test_pause_and_cancel_are_explicit_terminal_decisions() -> None:
    ledger = new_baseline_exposure_ledger()
    paused = evaluate_campaign_lifecycle(
        current=CampaignLifecycle(),
        baseline_ledger=ledger,
        risk_frontier_states=(),
        behavior_frontier_states=(),
        pause_reason="identity-drift",
    )
    cancelled = evaluate_campaign_lifecycle(
        current=CampaignLifecycle(),
        baseline_ledger=ledger,
        risk_frontier_states=(),
        behavior_frontier_states=(),
        cancelled=True,
    )
    assert paused.completion_status is CampaignCompletionStatus.PAUSED
    assert cancelled.completion_status is CampaignCompletionStatus.CANCELLED
