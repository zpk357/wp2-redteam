import pytest

from sandbox.structured_v1.campaign import (
    CampaignCheckpoint,
    CampaignLimits,
    CampaignUsage,
    load_checkpoint,
    save_checkpoint,
)


def _checkpoint() -> CampaignCheckpoint:
    return CampaignCheckpoint(limits=CampaignLimits(
        opportunities=2, model_calls=4, tool_calls=4, input_tokens=100,
        output_tokens=100, wall_clock_seconds=60, expense_units=20,
    ))


def test_reservation_settlement_is_persistent_idempotent_and_keeps_missing_usage(tmp_path):
    state = _checkpoint().reserve("receipt-1").mark_submitted("receipt-1")
    actual = CampaignUsage(model_calls=1, tool_calls=1, input_tokens=None,
                           output_tokens=3, wall_clock_seconds=2, expense_units=None)
    state = state.settle("receipt-1", actual)
    assert state.settle("receipt-1", actual) == state
    assert state.usage.opportunities == 1
    assert state.usage.complete is False
    path = tmp_path / "campaign.json"
    save_checkpoint(path, state)
    assert load_checkpoint(path) == state


def test_recovery_never_replays_unknown_effect_and_closure_failure_isolates():
    submitted = _checkpoint().reserve("receipt-1").mark_submitted("receipt-1")
    unknown = submitted.recover(closure_proven=True)
    assert unknown.stopped and unknown.isolated
    assert unknown.stop_reason == "unknown-effect"
    closure = _checkpoint().recover(closure_proven=False)
    assert closure.stopped and closure.stop_reason == "closure-unproven"


def test_failed_call_counts_and_budget_cap_stops_without_free_replacement():
    state = _checkpoint().reserve("receipt-1")
    state = state.settle("receipt-1", CampaignUsage(model_calls=5))
    assert state.stopped and state.usage.opportunities == 1
    with pytest.raises(RuntimeError, match="does not permit"):
        state.reserve("receipt-2")


def test_reservation_refuses_work_the_remaining_budget_cannot_cover():
    with pytest.raises(RuntimeError, match="does not cover"):
        _checkpoint().reserve("receipt-1", CampaignUsage(model_calls=5))
    with pytest.raises(RuntimeError, match="does not cover"):
        _checkpoint().reserve("receipt-1", CampaignUsage(input_tokens=101))
    state = _checkpoint().reserve("receipt-1", CampaignUsage(model_calls=3))
    with pytest.raises(RuntimeError, match="does not cover"):
        state.reserve("receipt-2", CampaignUsage(model_calls=2))

