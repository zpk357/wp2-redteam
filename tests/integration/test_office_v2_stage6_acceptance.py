from __future__ import annotations

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASES
from sandbox.scenarios.office_v2.oracle import evaluate_clean_scenario_oracle
from sandbox.scenarios.office_v2.oracle_evidence import (
    EpisodeTimelineEntry,
    InteractionEvidenceKind,
    TimelineEntryKind,
    build_clean_oracle_evidence_bundle,
    build_interaction_evidence_fact,
    build_termination_fact,
)
from sandbox.scenarios.office_v2.oracle_models import (
    StateTransitionEvidenceRef,
    TaskGoalStatus,
)
from tests.integration.test_office_v2_causal_chains import _run_case


def _interaction_evidence(clean_case, execution):
    facts = []
    insertion_points: dict[int, int] = {}
    for sequence, outcome in enumerate(execution.interactions):
        transition = outcome.transition
        assert transition is not None
        request = next(
            item
            for item in clean_case.task.user_response_script.requests
            if item.request_id == outcome.request_id
        )
        insertion = next(
            index
            for index, invocation in enumerate(execution.invocations)
            if invocation.before_state_digest == transition.after_state_digest
        )
        insertion_points[insertion] = sequence
        transition_ref = StateTransitionEvidenceRef(
            evidence_id=f"evidence.transition.clean.{clean_case.case_id}.{sequence}",
            evidence_digest=transition.transition_digest,
            sequence=sequence,
            transaction_id=transition.transaction_id,
            committed=True,
        )
        facts.append(
            build_interaction_evidence_fact(
                evidence_id=f"evidence.interaction.clean.{clean_case.case_id}.{sequence}",
                sequence=sequence,
                event_kind=InteractionEvidenceKind.INTERACTION_RESULT,
                logical_time=outcome.grant.valid_from,
                input_digest=sha256_digest(request),
                output_digest=outcome.outcome_digest,
                before_state_digest=transition.before_state_digest,
                after_state_digest=transition.after_state_digest,
                state_digest=transition.after_state_digest,
                data_digest=outcome.outcome_digest,
                request_digest=sha256_digest(request),
                status=outcome.status.value,
                transition_ref=transition_ref,
                advances_state=True,
            )
        )

    timeline = []
    for tool_sequence in range(len(execution.invocations)):
        interaction_sequence = insertion_points.get(tool_sequence)
        if interaction_sequence is not None:
            timeline.append(
                EpisodeTimelineEntry(
                    episode_sequence=len(timeline),
                    entry_kind=TimelineEntryKind.INTERACTION,
                    item_sequence=interaction_sequence,
                )
            )
        timeline.append(
            EpisodeTimelineEntry(
                episode_sequence=len(timeline),
                entry_kind=TimelineEntryKind.TOOL,
                item_sequence=tool_sequence,
            )
        )
    return tuple(facts), tuple(timeline)


def _evaluate_clean_reference_case(clean_case):
    execution = _run_case(clean_case)
    interactions, timeline = _interaction_evidence(clean_case, execution)
    termination = build_termination_fact(
        evidence_id=f"termination.clean.{clean_case.case_id}",
        sequence=len(execution.invocations),
        reason="submit",
        submitted=True,
        output_digest=sha256_digest({"case_id": clean_case.case_id, "submitted": True}),
    )
    bundle = build_clean_oracle_evidence_bundle(
        clean_case=clean_case,
        invocations=execution.invocations,
        results=execution.results,
        interaction_facts=interactions,
        timeline=timeline,
        termination=termination,
        initial_state_digest=execution.initial_state_digest,
        final_state_digest=execution.final_state_digest,
    )
    result = evaluate_clean_scenario_oracle(
        bundle=bundle,
        clean_case=clean_case,
    )
    return bundle, result


@pytest.mark.parametrize("clean_case", CLEAN_CASES, ids=lambda item: item.case_id)
def test_all_24_clean_reference_cases_have_utility_without_planned_attack_intent(
    clean_case,
) -> None:
    assert len(CLEAN_CASES) == 24
    _, result = _evaluate_clean_reference_case(clean_case)

    active_required = tuple(
        goal
        for goal in result.utility.goal_facts
        if goal.required and goal.status is not TaskGoalStatus.INACTIVE
    )
    assert active_required
    assert all(goal.assertion_facts for goal in active_required)
    assert result.security.exposure_facts == ()
    assert result.security.planned_objectives == ()
    assert all(not item.planned for item in result.security.violations)
