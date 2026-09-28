"""FDM-T05: checkpoint identity and local crash boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.structured_v1.campaign import (
    CURRENT_CAMPAIGN_PROTOCOL_IDENTITY,
    CampaignCheckpoint,
    CampaignLimits,
    CampaignUsage,
    load_checkpoint,
    run_opportunity,
)
from sandbox.structured_v1.experiment_inputs import prepare_inputs
from sandbox.structured_v1.search import ArmKind, TwoArmSearch


def _limits() -> CampaignLimits:
    return CampaignLimits(
        opportunities=3,
        model_calls=3,
        tool_calls=3,
        input_tokens=100,
        output_tokens=100,
        wall_clock_seconds=60,
        expense_units=30,
    )


def _search(manifest, seed: str = "t05") -> TwoArmSearch:
    return TwoArmSearch(
        {item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates},
        manifest,
        seed=seed,
    )


def test_new_campaign_binds_protocol_identity_and_legacy_work_cannot_resume(manifest):
    checkpoint = CampaignCheckpoint(limits=_limits())
    bound = checkpoint.bind_protocol()
    assert bound.protocol_identity == CURRENT_CAMPAIGN_PROTOCOL_IDENTITY
    assert bound.search.protocol_identity is None  # search is bound when a TwoArmSearch is used

    legacy = checkpoint.reserve("opportunity-0")
    with pytest.raises(ValueError, match="legacy checkpoint"):
        legacy.bind_protocol()


def test_selection_and_generation_receipts_are_persisted_before_execution(manifest, tmp_path):
    path = Path(tmp_path) / "t05-boundaries.json"
    checkpoint = CampaignCheckpoint(limits=_limits())
    search = _search(manifest)

    with pytest.raises(RuntimeError, match="fault after selection"):
        run_opportunity(
            path,
            checkpoint,
            search,
            arm=ArmKind.COVERAGE_GUIDED,
            manifest=manifest,
            execute=lambda case: pytest.fail("execution must not start"),
            planned=CampaignUsage(model_calls=1),
            fault_after="selection",
        )
    selected = load_checkpoint(path)
    assert selected.protocol_identity == CURRENT_CAMPAIGN_PROTOCOL_IDENTITY
    assert selected.pending_selection is not None
    assert selected.pending_case is None
    assert not selected.reservations["opportunity-0"].submitted
    assert selected.search.plans == ()

    # The same checkpoint can be resumed in a real process with the same search inputs.  The
    # persisted selection is consumed; no second selection draw is allowed.
    resumed = _search(manifest)
    with pytest.raises(RuntimeError, match="fault after generation"):
        run_opportunity(
            path,
            selected,
            resumed,
            arm=ArmKind.COVERAGE_GUIDED,
            manifest=manifest,
            execute=lambda case: pytest.fail("execution must not start"),
            planned=CampaignUsage(model_calls=1),
            fault_after="generation",
        )
    generated = load_checkpoint(path)
    assert generated.pending_selection is not None
    assert generated.pending_case is not None
    assert generated.pending_generation is not None
    assert generated.pending_case.mutation_lineage.generation_identity in {
        "root-restart-0",
        "opportunity-0",
    }
    assert generated.search.opportunity == 1
    assert generated.reservations["opportunity-0"].submitted is False

    resumed_again = _search(manifest)
    with pytest.raises(RuntimeError, match="fault after submission"):
        run_opportunity(
            path,
            generated,
            resumed_again,
            arm=ArmKind.COVERAGE_GUIDED,
            manifest=manifest,
            execute=lambda case: pytest.fail("execution must not start"),
            planned=CampaignUsage(model_calls=1),
            fault_after="submission",
        )
    submitted = load_checkpoint(path)
    assert submitted.pending_case == generated.pending_case
    assert submitted.pending_generation == generated.pending_generation
    assert submitted.reservations["opportunity-0"].submitted


def test_unfinalized_receipt_recovery_isolated_after_financial_settlement():
    checkpoint = CampaignCheckpoint(limits=_limits()).reserve("opportunity-0").mark_submitted(
        "opportunity-0"
    )
    checkpoint = checkpoint.record_receipt(
        "opportunity-0", CampaignUsage(model_calls=1, tool_calls=1)
    )
    recovered = checkpoint.recover(closure_proven=True)
    assert recovered.reservations["opportunity-0"].settled
    assert recovered.stopped and recovered.isolated
    assert recovered.stop_reason == "settlement-unfinalized"
