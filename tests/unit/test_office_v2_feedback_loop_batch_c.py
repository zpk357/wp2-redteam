from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.coverage.v2_episode_coverage import empty_v2_coverage_snapshot
from sandbox.fuzzer.v2_campaign import CampaignLifecycle, record_valid_episode
from sandbox.fuzzer.v2_campaign_loop import (
    build_v2_coverage_artifact,
    promote_coverage_artifact,
)
from sandbox.fuzzer.v2_campaign_state import build_campaign_budget, build_campaign_state
from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_corpus import ExecutionCosts, V2Corpus
from sandbox.fuzzer.v2_feedback import build_next_generation_feedback
from sandbox.fuzzer.v2_identity import build_v2_campaign_identity_lock
from sandbox.fuzzer.v2_orchestrator import (
    GenerationClosureKind,
    build_generation_closure_receipt,
    decide_next_generation,
)
from sandbox.fuzzer.v2_scheduler import (
    new_baseline_exposure_ledger,
    update_baseline_item,
)
from sandbox.replay.digests import sha256_digest
from tests.unit.test_office_v2_risk_coverage import _coverage_input
from tests.unit.test_office_v2_step3_integration_closure import (
    _initial_frontiers,
    _seed_and_candidate,
)

CAMPAIGN_ID = "campaign.feedback.three-generation"


def loop_fixture():
    artifact = build_v2_coverage_artifact(_coverage_input())
    seed, candidate = _seed_and_candidate(
        artifact,
        allocation_id="allocation.bootstrap",
        candidate_id="bootstrap",
    )
    promoted = promote_coverage_artifact(
        campaign_id=CAMPAIGN_ID,
        candidate_id="bootstrap",
        artifact=artifact,
        baseline=empty_v2_coverage_snapshot(),
        seed=seed,
        candidate=candidate,
        attempt_receipt_ids=("attempt-bootstrap",),
        costs=ExecutionCosts(agent_tokens=10),
        corpus_snapshot=V2Corpus().snapshot(),
        frontier_snapshot=_initial_frontiers(),
    )
    objective_id = artifact.episode_facts.planned_risk.objectives[0].objective_id
    ledger = update_baseline_item(
        new_baseline_exposure_ledger(),
        objective_id=objective_id,
        execution_record_id=promoted.execution.execution_record_id,
    )
    state0 = build_campaign_state(
        coverage=promoted.next_coverage,
        corpus=promoted.corpus.snapshot(),
        frontiers=promoted.frontiers,
        exposure_ledger=ledger,
        budget=build_campaign_budget(),
        lifecycle=CampaignLifecycle(),
    )
    return promoted, state0


def advance_state(state, *, coverage_gain: bool):
    return build_campaign_state(
        coverage=state.coverage,
        corpus=state.corpus,
        frontiers=state.frontiers,
        exposure_ledger=state.exposure_ledger,
        budget=state.budget,
        lifecycle=record_valid_episode(
            state.lifecycle, coverage_gain=coverage_gain
        ),
    )


def test_three_generations_require_feedback_and_prior_atomic_closure() -> None:
    promoted, state0 = loop_fixture()
    decision0 = decide_next_generation(
        campaign_id=CAMPAIGN_ID,
        state=state0,
        latest_feedback=None,
    )
    state1 = advance_state(state0, coverage_gain=True)
    feedback1 = build_next_generation_feedback(
        campaign_id=CAMPAIGN_ID,
        generation_index=1,
        execution=promoted.execution,
        delta=promoted.delta,
    )
    closure0 = build_generation_closure_receipt(
        campaign_id=CAMPAIGN_ID,
        generation_index=0,
        closure_kind=GenerationClosureKind.CANDIDATE_SETTLEMENT,
        settlement_id="settlement-0",
        settlement_digest=sha256_digest("settlement-0"),
        resulting_state_digest=state1.state_digest,
    )
    decision1 = decide_next_generation(
        campaign_id=CAMPAIGN_ID,
        state=state1,
        latest_feedback=feedback1,
        previous_decision=decision0,
        previous_closure=closure0,
    )
    state2 = advance_state(state1, coverage_gain=False)
    feedback2 = build_next_generation_feedback(
        campaign_id=CAMPAIGN_ID,
        generation_index=2,
        execution=promoted.execution,
        delta=promoted.delta,
        previous_feedback_digest=feedback1.feedback_digest,
        consecutive_no_gain=True,
    )
    closure1 = build_generation_closure_receipt(
        campaign_id=CAMPAIGN_ID,
        generation_index=1,
        closure_kind=GenerationClosureKind.CANDIDATE_SETTLEMENT,
        settlement_id="settlement-1",
        settlement_digest=sha256_digest("settlement-1"),
        resulting_state_digest=state2.state_digest,
    )
    decision2 = decide_next_generation(
        campaign_id=CAMPAIGN_ID,
        state=state2,
        latest_feedback=feedback2,
        previous_decision=decision1,
        previous_closure=closure1,
    )
    assert decision1.input_feedback_digest == feedback1.feedback_digest
    assert decision2.input_feedback_digest == feedback2.feedback_digest
    assert "recomputed-from-latest-feedback" in decision2.reason_codes

    with pytest.raises(ValueError, match="requires prior atomic settlement"):
        decide_next_generation(
            campaign_id=CAMPAIGN_ID,
            state=state2,
            latest_feedback=feedback2,
            previous_decision=decision1,
        )


def test_reopen_recomputes_the_same_third_generation(tmp_path: Path) -> None:
    promoted, state0 = loop_fixture()
    decision0 = decide_next_generation(
        campaign_id=CAMPAIGN_ID, state=state0, latest_feedback=None
    )
    state1 = advance_state(state0, coverage_gain=True)
    feedback1 = build_next_generation_feedback(
        campaign_id=CAMPAIGN_ID,
        generation_index=1,
        execution=promoted.execution,
        delta=promoted.delta,
    )
    closure0 = build_generation_closure_receipt(
        campaign_id=CAMPAIGN_ID,
        generation_index=0,
        closure_kind=GenerationClosureKind.CANDIDATE_SETTLEMENT,
        settlement_id="settlement-0",
        settlement_digest=sha256_digest("settlement-0"),
        resulting_state_digest=state1.state_digest,
    )
    expected = decide_next_generation(
        campaign_id=CAMPAIGN_ID,
        state=state1,
        latest_feedback=feedback1,
        previous_decision=decision0,
        previous_closure=closure0,
    )
    path = tmp_path / "three-generation.db"
    with V2CampaignStore(path) as store:
        store.create_campaign(
            campaign_id=CAMPAIGN_ID,
            identity=build_v2_campaign_identity_lock(),
            initial_state=state1,
        )
        store.put_generation_decision(decision0)
        store.put_generation_checkpoint(closure=closure0, feedback=feedback1)
    with V2CampaignStore(path) as reopened:
        actual = decide_next_generation(
            campaign_id=CAMPAIGN_ID,
            state=reopened.load_state(CAMPAIGN_ID),
            latest_feedback=reopened.load_latest_feedback(CAMPAIGN_ID),
            previous_decision=reopened.load_latest_generation_decision(CAMPAIGN_ID),
            previous_closure=reopened.load_latest_generation_closure(CAMPAIGN_ID),
        )
    assert actual == expected
