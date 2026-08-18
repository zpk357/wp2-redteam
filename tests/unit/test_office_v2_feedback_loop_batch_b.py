from __future__ import annotations

from sandbox.coverage.v2_episode_coverage import empty_v2_coverage_snapshot
from sandbox.fuzzer.v2_campaign_loop import (
    build_v2_coverage_artifact,
    promote_coverage_artifact,
)
from sandbox.fuzzer.v2_corpus import ExecutionCosts, V2Corpus
from sandbox.fuzzer.v2_feedback import (
    FindingReplayStatus,
    SeedPromotionDisposition,
    build_finding,
    build_next_generation_feedback,
    decide_seed_promotion,
    update_finding_replay,
)
from sandbox.replay.digests import sha256_digest
from tests.unit.test_office_v2_risk_coverage import _coverage_input
from tests.unit.test_office_v2_step3_integration_closure import (
    _initial_frontiers,
    _seed_and_candidate,
)


def promoted_result():
    artifact = build_v2_coverage_artifact(_coverage_input())
    seed, candidate = _seed_and_candidate(
        artifact,
        allocation_id="allocation.feedback",
        candidate_id="feedback",
    )
    result = promote_coverage_artifact(
        campaign_id="campaign-feedback",
        candidate_id="feedback",
        artifact=artifact,
        baseline=empty_v2_coverage_snapshot(),
        seed=seed,
        candidate=candidate,
        attempt_receipt_ids=("attempt-feedback",),
        costs=ExecutionCosts(agent_tokens=10),
        corpus_snapshot=V2Corpus().snapshot(),
        frontier_snapshot=_initial_frontiers(),
    )
    return artifact, result


def test_finding_is_stable_and_replay_updates_only_verification_state() -> None:
    artifact, result = promoted_result()
    first = build_finding(
        campaign_id="campaign-feedback",
        facts=artifact.episode_facts,
        delta=result.delta,
        execution=result.execution,
    )
    second = build_finding(
        campaign_id="campaign-feedback",
        facts=artifact.episode_facts,
        delta=result.delta,
        execution=result.execution,
    )
    assert first == second
    assert first.replay_status is FindingReplayStatus.REPLAY_REQUIRED

    confirmed = update_finding_replay(
        first,
        confirmed=True,
        replay_manifest_digest=sha256_digest("strict-replay"),
    )
    assert confirmed.finding_key == first.finding_key
    assert confirmed.replay_status is FindingReplayStatus.REPLAY_CONFIRMED
    assert confirmed.canonical_fact_digest == first.canonical_fact_digest


def test_risk_finding_and_parent_seed_eligibility_are_separate() -> None:
    artifact, result = promoted_result()
    finding = build_finding(
        campaign_id="campaign-feedback",
        facts=artifact.episode_facts,
        delta=result.delta,
        execution=result.execution,
    )
    utility_failed = artifact.episode_facts.model_copy(
        update={
            "eligibility": artifact.episode_facts.eligibility.model_copy(
                update={"normal_task_completed": False}
            )
        }
    )
    decision = decide_seed_promotion(
        facts=utility_failed,
        delta=result.delta,
        finding=finding,
    )
    assert decision.disposition is SeedPromotionDisposition.FINDING_ONLY


def test_next_generation_feedback_is_recomputed_and_chained() -> None:
    _, result = promoted_result()
    first = build_next_generation_feedback(
        campaign_id="campaign-feedback",
        generation_index=1,
        execution=result.execution,
        delta=result.delta,
    )
    second = build_next_generation_feedback(
        campaign_id="campaign-feedback",
        generation_index=2,
        execution=result.execution,
        delta=result.delta,
        previous_feedback_digest=first.feedback_digest,
        consecutive_no_gain=True,
    )
    assert second.previous_feedback_digest == first.feedback_digest
    assert second.feedback_digest != first.feedback_digest
    assert second.gap_kind.value == "consecutive_no_gain"
