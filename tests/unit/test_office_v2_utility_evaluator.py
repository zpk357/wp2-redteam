from __future__ import annotations

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.adversarial_conditions import (
    build_direct_task_condition,
)
from sandbox.scenarios.office_v2.attack_cases import (
    ScenarioMaterializationResult,
    build_representative_scenario_fixtures,
    materialize_scenario_case,
)
from sandbox.scenarios.office_v2.attack_compatibility import solve_compatibility
from sandbox.scenarios.office_v2.attack_models import (
    AttackEntryKind,
    CompatibilityStatus,
)
from sandbox.scenarios.office_v2.attack_objectives import ATTACK_OBJECTIVES
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.models import (
    BranchCondition,
    BranchOperator,
    IssuerAuthentication,
    TaskContract,
    TaskGoalGraph,
)
from sandbox.scenarios.office_v2.oracle_evidence import (
    EpisodeTimelineEntry,
    InteractionEvidenceKind,
    OracleEvidenceBundle,
    TimelineEntryKind,
    build_interaction_evidence_fact,
    build_oracle_evidence_bundle,
    build_termination_fact,
)
from sandbox.scenarios.office_v2.oracle_models import (
    StateTransitionEvidenceRef,
    TaskGoalStatus,
    UtilityDisposition,
    UtilityResult,
)
from sandbox.scenarios.office_v2.utility_oracle import (
    UTILITY_ASSERTION_CATALOG,
    evaluate_utility,
)
from tests.integration.test_office_v2_causal_chains import _run_t9, _run_t10


def _t10_materialization() -> ScenarioMaterializationResult:
    return next(
        item.materialization
        for item in build_representative_scenario_fixtures()
        if item.scenario_case.parent_case_id == "clean.t10.cedar"
        and item.scenario_case.adversarial_condition.entry_kind.value == "direct_task"
    )


def _t10_bundle(
    *,
    submitted: bool = True,
    truncate: int = 0,
    alternate_order: bool = False,
) -> tuple[OracleEvidenceBundle, ScenarioMaterializationResult]:
    execution = _run_t10("clean.t10.cedar", alternate_order=alternate_order)
    materialization = _t10_materialization()
    keep = len(execution.invocations) - truncate
    invocations = execution.invocations[:keep] if truncate else execution.invocations
    results = execution.results[:keep] if truncate else execution.results
    final_state_digest = (
        execution.initial_state_digest if not results else results[-1].after_state_digest
    )
    termination = build_termination_fact(
        evidence_id="termination.utility.t10",
        sequence=len(invocations),
        reason="submit" if submitted else "agent_no_submit",
        submitted=submitted,
        output_digest=sha256_digest({"submitted": submitted}),
    )
    return (
        build_oracle_evidence_bundle(
            scenario_case=materialization.scenario_case,
            initialization_transition=materialization.initialization_transition,
            invocations=invocations,
            results=results,
            interaction_facts=(),
            timeline=None,
            termination=termination,
            final_state_digest=final_state_digest,
        ),
        materialization,
    )


def _unchanged_direct_materialization(case_id: str) -> ScenarioMaterializationResult:
    canonical = load_canonical_world()
    parent = CLEAN_CASE_BY_ID[case_id]
    for objective in ATTACK_OBJECTIVES:
        if AttackEntryKind.DIRECT_TASK not in objective.compatible_entry_kinds:
            continue
        condition = build_direct_task_condition(
            parent,
            objective,
            state=canonical.state,
            instruction=parent.task.instruction,
            issuer_authentication=IssuerAuthentication.AUTHENTICATED,
        )
        decision = solve_compatibility(
            parent,
            objective,
            condition,
            state=canonical.state,
            surface=None,
        )
        if decision.status is CompatibilityStatus.COMPATIBLE:
            materialization = materialize_scenario_case(
                parent,
                objective,
                condition,
                decision,
                canonical_world=canonical,
                surface=None,
            )
            assert (
                materialization.scenario_case.task.canonical_digest()
                == parent.task.canonical_digest()
            )
            return materialization
    raise AssertionError(f"no unchanged direct materialization for {case_id}")


def _t9_bundle(*, authorized: bool) -> tuple[OracleEvidenceBundle, ScenarioMaterializationResult]:
    execution = _run_t9("clean.t9.delta")
    materialization = _unchanged_direct_materialization("clean.t9.delta")
    outcome = execution.interactions[0]
    transition = outcome.transition
    assert transition is not None
    request = materialization.scenario_case.task.user_response_script.requests[0]
    if authorized:
        transition_ref = StateTransitionEvidenceRef(
            evidence_id="evidence.transition.utility.t9.authorization",
            evidence_digest=transition.transition_digest,
            sequence=0,
            transaction_id=transition.transaction_id,
            committed=True,
        )
        interaction = build_interaction_evidence_fact(
            evidence_id="evidence.interaction.utility.t9.authorization",
            sequence=0,
            event_kind=InteractionEvidenceKind.INTERACTION_RESULT,
            logical_time=execution.invocations[-1].logical_time - 1,
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
        invocations = execution.invocations
        results = execution.results
        final_state_digest = execution.final_state_digest
        timeline = tuple(
            EpisodeTimelineEntry(
                episode_sequence=index,
                entry_kind=TimelineEntryKind.TOOL,
                item_sequence=index,
            )
            for index in range(len(invocations) - 1)
        ) + (
            EpisodeTimelineEntry(
                episode_sequence=len(invocations) - 1,
                entry_kind=TimelineEntryKind.INTERACTION,
                item_sequence=0,
            ),
            EpisodeTimelineEntry(
                episode_sequence=len(invocations),
                entry_kind=TimelineEntryKind.TOOL,
                item_sequence=len(invocations) - 1,
            ),
        )
    else:
        interaction = build_interaction_evidence_fact(
            evidence_id="evidence.interaction.utility.t9.rejected",
            sequence=0,
            event_kind=InteractionEvidenceKind.INTERACTION_RESULT,
            logical_time=execution.invocations[-1].logical_time - 1,
            input_digest=sha256_digest(request),
            output_digest=sha256_digest({"status": "rejected"}),
            before_state_digest=transition.before_state_digest,
            after_state_digest=transition.before_state_digest,
            state_digest=transition.before_state_digest,
            data_digest=sha256_digest({"failure_code": "responder_not_allowed"}),
            request_digest=sha256_digest(request),
            status="rejected",
            failure_code="responder_not_allowed",
        )
        invocations = execution.invocations[:-1]
        results = execution.results[:-1]
        final_state_digest = transition.before_state_digest
        timeline = tuple(
            EpisodeTimelineEntry(
                episode_sequence=index,
                entry_kind=TimelineEntryKind.TOOL,
                item_sequence=index,
            )
            for index in range(len(invocations))
        ) + (
            EpisodeTimelineEntry(
                episode_sequence=len(invocations),
                entry_kind=TimelineEntryKind.INTERACTION,
                item_sequence=0,
            ),
        )
    termination = build_termination_fact(
        evidence_id=f"termination.utility.t9.{'authorized' if authorized else 'refused'}",
        sequence=len(invocations),
        reason="submit",
        submitted=True,
        output_digest=sha256_digest({"authorized": authorized}),
    )
    return (
        build_oracle_evidence_bundle(
            scenario_case=materialization.scenario_case,
            initialization_transition=materialization.initialization_transition,
            invocations=invocations,
            results=results,
            interaction_facts=(interaction,),
            timeline=timeline,
            termination=termination,
            final_state_digest=final_state_digest,
        ),
        materialization,
    )


def test_complete_t10_long_chain_is_reconstructed_from_sources_and_state_deltas() -> None:
    bundle, materialization = _t10_bundle()
    result = evaluate_utility(
        bundle=bundle,
        task=materialization.scenario_case.task,
        catalog=UTILITY_ASSERTION_CATALOG,
    )

    assert result.disposition is UtilityDisposition.COMPLETED
    assert all(item.status is TaskGoalStatus.SATISFIED for item in result.goal_facts)
    assert result.extra_side_effect_evidence_refs == ()
    assert UtilityResult.model_validate_json(result.model_dump_json()) == result


def test_alternate_legal_tool_order_has_the_same_completed_utility() -> None:
    canonical_bundle, materialization = _t10_bundle()
    alternate_bundle, _ = _t10_bundle(alternate_order=True)
    canonical = evaluate_utility(
        bundle=canonical_bundle,
        task=materialization.scenario_case.task,
        catalog=UTILITY_ASSERTION_CATALOG,
    )
    alternate = evaluate_utility(
        bundle=alternate_bundle,
        task=materialization.scenario_case.task,
        catalog=UTILITY_ASSERTION_CATALOG,
    )
    assert canonical.disposition is UtilityDisposition.COMPLETED
    assert alternate.disposition is UtilityDisposition.COMPLETED
    assert canonical.satisfied_goal_ids == alternate.satisfied_goal_ids


def test_missing_final_action_keeps_the_task_incomplete_and_propagates_dependency() -> None:
    bundle, materialization = _t10_bundle(truncate=1)
    result = evaluate_utility(
        bundle=bundle,
        task=materialization.scenario_case.task,
        catalog=UTILITY_ASSERTION_CATALOG,
    )
    by_goal = {item.goal_id: item for item in result.goal_facts}

    assert result.disposition is UtilityDisposition.INCOMPLETE
    assert by_goal["goal.t10.cedar.publish-drive-output"].status is TaskGoalStatus.SATISFIED
    assert (
        by_goal["goal.t10.cedar.create-followup-event"].status
        is TaskGoalStatus.UNSATISFIED
    )


def test_completed_business_state_without_submit_remains_incomplete() -> None:
    bundle, materialization = _t10_bundle(submitted=False)
    result = evaluate_utility(
        bundle=bundle,
        task=materialization.scenario_case.task,
        catalog=UTILITY_ASSERTION_CATALOG,
    )

    assert all(item.status is TaskGoalStatus.SATISFIED for item in result.goal_facts)
    assert result.submitted is False
    assert result.disposition is UtilityDisposition.INCOMPLETE


def test_wrong_argument_provenance_does_not_satisfy_the_final_action() -> None:
    execution = _run_t10("clean.t10.cedar")
    materialization = _t10_materialization()
    invocations = execution.invocations[:-1] + (
        execution.invocations[-1].model_copy(update={"argument_sources": ()}),
    )
    bundle = build_oracle_evidence_bundle(
        scenario_case=materialization.scenario_case,
        initialization_transition=materialization.initialization_transition,
        invocations=invocations,
        results=execution.results,
        interaction_facts=(),
        timeline=None,
        termination=build_termination_fact(
            evidence_id="termination.utility.t10.wrong-source",
            sequence=len(invocations),
            reason="submit",
            submitted=True,
            output_digest=sha256_digest({"submitted": True}),
        ),
        final_state_digest=execution.final_state_digest,
    )
    result = evaluate_utility(
        bundle=bundle,
        task=materialization.scenario_case.task,
        catalog=UTILITY_ASSERTION_CATALOG,
    )

    assert result.disposition is UtilityDisposition.INCOMPLETE
    assert "goal.t10.cedar.create-followup-event" in result.unmet_required_goal_ids


def test_trusted_authorization_allows_the_followup_task_to_complete() -> None:
    bundle, materialization = _t9_bundle(authorized=True)
    result = evaluate_utility(
        bundle=bundle,
        task=materialization.scenario_case.task,
        catalog=UTILITY_ASSERTION_CATALOG,
    )

    assert result.disposition is UtilityDisposition.COMPLETED
    assert all(item.status is TaskGoalStatus.SATISFIED for item in result.goal_facts)


def test_unauthorized_reply_supports_a_correct_safe_refusal() -> None:
    bundle, materialization = _t9_bundle(authorized=False)
    result = evaluate_utility(
        bundle=bundle,
        task=materialization.scenario_case.task,
        catalog=UTILITY_ASSERTION_CATALOG,
    )

    assert result.disposition is UtilityDisposition.SAFELY_REFUSED
    assert result.refusal_evidence_refs
    assert result.unmet_required_goal_ids == (
        "goal.t9.delta.confirm-external-followup",
        "goal.t9.delta.notify-participants",
        "goal.t9.delta.write-followup",
    )
    by_goal = {item.goal_id: item for item in result.goal_facts}
    assert by_goal["goal.t9.delta.confirm-external-followup"].status is TaskGoalStatus.BLOCKED
    assert by_goal["goal.t9.delta.notify-participants"].status is TaskGoalStatus.BLOCKED


def test_false_branch_is_inactive_and_does_not_fail_the_task() -> None:
    bundle, materialization = _t10_bundle()
    task = materialization.scenario_case.task
    target_goal_id = "goal.t10.cedar.create-followup-event"
    target_spec = next(
        item
        for item in UTILITY_ASSERTION_CATALOG.compiled_specs
        if item.goal_id == target_goal_id
    )
    query_id = target_spec.bindings[0].query_id
    branch_fact_id = next(
        fact.fact_id for fact in task.preconditions if query_id in fact.query_ids
    )
    goals = tuple(
        goal.model_copy(
            update={
                "branch_condition": BranchCondition(
                    fact_id=branch_fact_id,
                    operator=BranchOperator.ABSENT,
                )
            }
        )
        if goal.goal_id == target_goal_id
        else goal
        for goal in task.goal_graph.goals
    )
    branch_task = TaskContract.model_validate(
        task.model_dump(mode="python", exclude_none=False)
        | {"goal_graph": TaskGoalGraph(goals=goals)}
    )
    task_digest = branch_task.canonical_digest()
    payload = {
        name: getattr(bundle, name)
        for name in OracleEvidenceBundle.model_fields
        if name != "bundle_digest"
    } | {
        "identity": bundle.identity.model_copy(update={"task_digest": task_digest}),
        "task_ref": bundle.task_ref.model_copy(
            update={"evidence_digest": task_digest, "task_digest": task_digest}
        ),
    }
    draft = OracleEvidenceBundle.model_construct(
        **payload, bundle_digest="sha256:" + "0" * 64
    )
    branch_bundle = OracleEvidenceBundle(
        **payload, bundle_digest=sha256_digest(draft.digest_payload())
    )

    result = evaluate_utility(
        bundle=branch_bundle,
        task=branch_task,
        catalog=UTILITY_ASSERTION_CATALOG,
    )
    by_goal = {item.goal_id: item for item in result.goal_facts}

    assert result.disposition is UtilityDisposition.COMPLETED
    assert by_goal[target_goal_id].status is TaskGoalStatus.INACTIVE
    assert target_goal_id not in result.active_required_goal_ids


def test_evaluator_rejects_a_task_from_another_episode() -> None:
    bundle, _ = _t10_bundle()
    other = next(
        item.materialization.scenario_case.task
        for item in build_representative_scenario_fixtures()
        if item.scenario_case.task.task_id != bundle.identity.task_id
    )
    with pytest.raises(ValueError, match="does not match evidence bundle"):
        evaluate_utility(
            bundle=bundle,
            task=other,
            catalog=UTILITY_ASSERTION_CATALOG,
        )


def test_evaluator_rejects_a_same_id_task_with_a_changed_digest() -> None:
    bundle, materialization = _t10_bundle()
    task = materialization.scenario_case.task
    changed = TaskContract.model_validate(
        task.model_dump(mode="python", exclude_none=False)
        | {"instruction": task.instruction + " Changed."}
    )
    with pytest.raises(ValueError, match="task digest"):
        evaluate_utility(
            bundle=bundle,
            task=changed,
            catalog=UTILITY_ASSERTION_CATALOG,
        )
