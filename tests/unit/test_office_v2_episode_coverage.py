from __future__ import annotations

import pytest

from sandbox.coverage.v2_contracts import build_v2_candidate_batch_baseline
from sandbox.coverage.v2_episode_coverage import (
    V2CandidateEpisode,
    V2EpisodeCoverageError,
    build_v2_episode_coverage_facts,
    empty_v2_coverage_snapshot,
    evaluate_v2_candidate_batch,
)
from sandbox.replay.digests import sha256_digest
from tests.unit.test_office_v2_risk_coverage import _coverage_input
from tests.unit.test_office_v2_unexpected_risk import _unexpected_input


def _batch(candidate_ids, snapshot):
    return build_v2_candidate_batch_baseline(
        campaign_id="campaign.coverage-v2.001",
        candidate_set_id="candidate-set.001",
        candidate_set_digest=sha256_digest(candidate_ids),
        candidate_ids=tuple(candidate_ids),
        baseline_snapshot_digest=snapshot.snapshot_digest,
    )


def test_episode_composes_behavior_risk_links_and_eligibility() -> None:
    facts = build_v2_episode_coverage_facts(_coverage_input())

    assert facts.behavior.primary_features
    assert facts.planned_risk.objectives
    assert facts.eligibility.submitted
    assert facts.behavior_risk_links
    assert all(item.evidence_refs for item in facts.behavior_risk_links)


def test_unexpected_risk_gets_context_and_behavior_links_without_inventing_intent() -> None:
    facts = build_v2_episode_coverage_facts(_unexpected_input())

    unexpected = facts.unexpected_risk.violations[0]
    contexts = [
        item
        for item in facts.risk_context_cells
        if item.planned_or_unexpected == "unexpected"
    ]

    assert contexts
    assert all(item.violation_id == unexpected.violation_id for item in contexts)
    assert all(item.objective_id is None and item.milestone_id is None for item in contexts)
    assert any(
        item.risk_fact_key_digest == unexpected.unexpected_risk_digest
        for item in facts.behavior_risk_links
    )


def test_same_batch_candidates_compare_against_one_shared_snapshot() -> None:
    facts = build_v2_episode_coverage_facts(_coverage_input())
    snapshot = empty_v2_coverage_snapshot()
    candidates = (
        V2CandidateEpisode(candidate_id="candidate.a", episode_facts=facts),
        V2CandidateEpisode(candidate_id="candidate.b", episode_facts=facts),
    )

    result = evaluate_v2_candidate_batch(
        batch_baseline=_batch(("candidate.a", "candidate.b"), snapshot),
        baseline_snapshot=snapshot,
        candidates=candidates,
    )

    first, second = result.deltas
    assert first.new_primary_behavior_features == second.new_primary_behavior_features
    assert first.new_milestone_outcome_bits == second.new_milestone_outcome_bits
    assert first.baseline_snapshot_digest == second.baseline_snapshot_digest
    assert len(result.next_snapshot.canonical_fact_digests) == 1


def test_batch_result_is_independent_of_candidate_execution_order() -> None:
    facts = build_v2_episode_coverage_facts(_coverage_input())
    snapshot = empty_v2_coverage_snapshot()
    baseline = _batch(("candidate.a", "candidate.b"), snapshot)
    candidates = (
        V2CandidateEpisode(candidate_id="candidate.a", episode_facts=facts),
        V2CandidateEpisode(candidate_id="candidate.b", episode_facts=facts),
    )

    forward = evaluate_v2_candidate_batch(
        batch_baseline=baseline,
        baseline_snapshot=snapshot,
        candidates=candidates,
    )
    reverse = evaluate_v2_candidate_batch(
        batch_baseline=baseline,
        baseline_snapshot=snapshot,
        candidates=tuple(reversed(candidates)),
    )

    assert forward == reverse


def test_second_submission_of_same_facts_has_zero_coverage_delta() -> None:
    facts = build_v2_episode_coverage_facts(_coverage_input())
    empty = empty_v2_coverage_snapshot()
    first = evaluate_v2_candidate_batch(
        batch_baseline=_batch(("candidate.a",), empty),
        baseline_snapshot=empty,
        candidates=(V2CandidateEpisode(candidate_id="candidate.a", episode_facts=facts),),
    )
    second = evaluate_v2_candidate_batch(
        batch_baseline=_batch(("candidate.b",), first.next_snapshot),
        baseline_snapshot=first.next_snapshot,
        candidates=(V2CandidateEpisode(candidate_id="candidate.b", episode_facts=facts),),
    )

    delta = second.deltas[0]
    assert delta.new_primary_behavior_features == ()
    assert delta.new_behavior_profile is None
    assert delta.new_milestone_outcome_bits == ()
    assert delta.new_behavior_risk_links == ()


def test_batch_rejects_wrong_snapshot_or_membership() -> None:
    facts = build_v2_episode_coverage_facts(_coverage_input())
    snapshot = empty_v2_coverage_snapshot()
    candidate = V2CandidateEpisode(candidate_id="candidate.a", episode_facts=facts)
    wrong = _batch(("candidate.a",), snapshot).model_copy(
        update={"baseline_snapshot_digest": "sha256:" + "1" * 64}
    )

    with pytest.raises(V2EpisodeCoverageError, match="snapshot digest"):
        evaluate_v2_candidate_batch(
            batch_baseline=wrong,
            baseline_snapshot=snapshot,
            candidates=(candidate,),
        )
    with pytest.raises(V2EpisodeCoverageError, match="membership"):
        evaluate_v2_candidate_batch(
            batch_baseline=_batch(("candidate.a", "candidate.b"), snapshot),
            baseline_snapshot=snapshot,
            candidates=(candidate,),
        )
