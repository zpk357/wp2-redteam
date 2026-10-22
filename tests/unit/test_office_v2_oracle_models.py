from __future__ import annotations

from typing import Any, TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_models import AttackEntryKind
from sandbox.scenarios.office_v2.oracle_models import (
    EVIDENCE_REF_ADAPTER,
    SCENARIO_ORACLE_RESULT_ADAPTER,
    AssertionEvaluation,
    AssertionMatchStatus,
    CompleteScenarioOracleResult,
    ExposureFact,
    ExposureStage,
    InteractionEventEvidenceRef,
    InvalidEvidenceOracleResult,
    MaterializationEvidenceRef,
    MilestoneFact,
    MilestoneOutcome,
    ObjectiveCompletionKind,
    OracleAssertionStage,
    OracleFailure,
    OracleFailureCode,
    OutputEvidenceRef,
    PlannedObjectiveResult,
    PolicyDecisionEvidenceRef,
    SecurityFactSet,
    StateAssertionEvidenceRef,
    StateRole,
    StateTransitionEvidenceRef,
    TaskAssertionFact,
    TaskAssertionStatus,
    TaskGoalFact,
    TaskGoalStatus,
    TaskInputEvidenceRef,
    TerminationEvidenceRef,
    ToolInvocationEvidenceRef,
    ToolResultEvidenceRef,
    UtilityDisposition,
    UtilityResult,
    ViolationFact,
    ViolationKind,
)

ZERO_DIGEST = "sha256:" + "0" * 64
ModelT = TypeVar("ModelT", bound=BaseModel)


def _signed_fact(model_type: type[ModelT], **payload: Any) -> ModelT:
    draft = model_type.model_construct(**payload, fact_digest=ZERO_DIGEST)
    return model_type(**payload, fact_digest=sha256_digest(draft.digest_payload()))


def _signed_result(model_type: type[ModelT], **payload: Any) -> ModelT:
    draft = model_type.model_construct(**payload, result_digest=ZERO_DIGEST)
    return model_type(**payload, result_digest=sha256_digest(draft.digest_payload()))


def _evidence_refs() -> dict[str, Any]:
    return {
        "task": TaskInputEvidenceRef(
            evidence_id="evidence.task",
            evidence_digest=ZERO_DIGEST,
            task_id="task.sample",
            task_digest=ZERO_DIGEST,
        ),
        "invocation": ToolInvocationEvidenceRef(
            evidence_id="evidence.invocation",
            evidence_digest=ZERO_DIGEST,
            sequence=0,
            invocation_id="invocation.0",
            tool_name="read_drive_file",
        ),
        "result": ToolResultEvidenceRef(
            evidence_id="evidence.result",
            evidence_digest=ZERO_DIGEST,
            sequence=0,
            invocation_id="invocation.0",
            tool_name="read_drive_file",
        ),
        "decision": PolicyDecisionEvidenceRef(
            evidence_id="evidence.decision",
            evidence_digest=ZERO_DIGEST,
            sequence=0,
            decision_id="decision.0",
        ),
        "transition": StateTransitionEvidenceRef(
            evidence_id="evidence.transition",
            evidence_digest=ZERO_DIGEST,
            sequence=0,
            transaction_id="transaction.0",
            committed=True,
        ),
        "output": OutputEvidenceRef(
            evidence_id="evidence.output",
            evidence_digest=ZERO_DIGEST,
            sequence=0,
            invocation_id="invocation.0",
            field_path=("data", "items", "0", "content"),
            value_digest=ZERO_DIGEST,
        ),
        "interaction": InteractionEventEvidenceRef(
            evidence_id="evidence.interaction",
            evidence_digest=ZERO_DIGEST,
            sequence=1,
            event_type="user_message",
            logical_time=1001,
        ),
        "initial": StateAssertionEvidenceRef(
            evidence_id="evidence.initial",
            evidence_digest=ZERO_DIGEST,
            assertion_id="state.initial",
            state_role=StateRole.INITIAL,
            state_digest=ZERO_DIGEST,
        ),
        "materialization": MaterializationEvidenceRef(
            evidence_id="evidence.materialization",
            evidence_digest=ZERO_DIGEST,
            materialization_id="materialization.sample",
            transition_digest=ZERO_DIGEST,
        ),
        "termination": TerminationEvidenceRef(
            evidence_id="evidence.termination",
            evidence_digest=ZERO_DIGEST,
            sequence=2,
            termination_reason="submitted",
        ),
    }


def _complete_result() -> CompleteScenarioOracleResult:
    refs = _evidence_refs()
    task_assertion = _signed_fact(
        TaskAssertionFact,
        assertion_id="assertion.task.complete",
        status=TaskAssertionStatus.SATISFIED,
        evidence_refs=(refs["output"],),
    )
    task_goal = _signed_fact(
        TaskGoalFact,
        goal_id="goal.task.complete",
        required=True,
        depends_on=(),
        status=TaskGoalStatus.SATISFIED,
        assertion_facts=(task_assertion,),
        blocking_evidence_refs=(),
    )
    utility = _signed_fact(
        UtilityResult,
        task_id="task.sample",
        disposition=UtilityDisposition.COMPLETED,
        goal_facts=(task_goal,),
        active_required_goal_ids=("goal.task.complete",),
        satisfied_goal_ids=("goal.task.complete",),
        unmet_required_goal_ids=(),
        submitted=True,
        termination_reason="submitted",
        termination_evidence_refs=(refs["termination"],),
        refusal_evidence_refs=(),
    )
    exposure = _signed_fact(
        ExposureFact,
        condition_id="condition.sample",
        entry_kind=AttackEntryKind.INDIRECT_CONTENT,
        stages=(ExposureStage.PLANNED, ExposureStage.DELIVERED, ExposureStage.OBSERVED),
        evidence_refs=tuple(
            sorted((refs["task"], refs["output"]), key=lambda item: item.sort_key())
        ),
    )
    attempted = _signed_fact(
        AssertionEvaluation,
        assertion_id="assertion.risk.attempted",
        stage=OracleAssertionStage.ATTEMPTED,
        match_status=AssertionMatchStatus.MATCHED,
        evidence_refs=(refs["invocation"],),
    )
    realized = _signed_fact(
        AssertionEvaluation,
        assertion_id="assertion.risk.realized",
        stage=OracleAssertionStage.REALIZED,
        match_status=AssertionMatchStatus.MATCHED,
        evidence_refs=(refs["transition"],),
    )
    milestone = _signed_fact(
        MilestoneFact,
        milestone_id="milestone.sample",
        required=True,
        depends_on=(),
        dependency_satisfied=True,
        outcome=MilestoneOutcome.REALIZED,
        attempted_evaluations=(attempted,),
        blocked_evaluations=(),
        realized_evaluations=(realized,),
    )
    objective = _signed_fact(
        PlannedObjectiveResult,
        objective_id="objective.sample",
        condition_id="condition.sample",
        entry_kind=AttackEntryKind.INDIRECT_CONTENT,
        exposure_fact=exposure,
        completion_kind=ObjectiveCompletionKind.FULL,
        milestone_facts=(milestone,),
        required_milestone_ids=("milestone.sample",),
        realized_milestone_ids=("milestone.sample",),
    )
    violation = _signed_fact(
        ViolationFact,
        violation_id="violation.sample",
        violation_kind=ViolationKind.DELEGATION_VIOLATION,
        planned=True,
        objective_id="objective.sample",
        side_effect_committed=True,
        evidence_refs=(refs["decision"], refs["transition"]),
    )
    security = _signed_fact(
        SecurityFactSet,
        exposure_facts=(exposure,),
        planned_objectives=(objective,),
        violations=(violation,),
    )
    evidence_closure = tuple(sorted(refs.values(), key=lambda item: item.sort_key()))
    return _signed_result(
        CompleteScenarioOracleResult,
        scenario_case_id="scenario.sample",
        scenario_case_digest=ZERO_DIGEST,
        input_bundle_digest=ZERO_DIGEST,
        initial_state_digest=ZERO_DIGEST,
        final_state_digest=ZERO_DIGEST,
        utility=utility,
        security=security,
        evidence_closure=evidence_closure,
    )


def test_closed_evidence_ref_union_round_trips_every_variant() -> None:
    for ref in _evidence_refs().values():
        restored = EVIDENCE_REF_ADAPTER.validate_json(ref.model_dump_json())
        assert restored == ref


def test_complete_result_round_trips_with_separate_utility_and_security() -> None:
    result = _complete_result()

    restored = SCENARIO_ORACLE_RESULT_ADAPTER.validate_json(result.model_dump_json())

    assert restored == result
    assert restored.utility.disposition is UtilityDisposition.COMPLETED
    assert restored.security.planned_objectives[0].completion_kind is ObjectiveCompletionKind.FULL


def test_evidence_order_is_canonical_and_duplicate_ids_are_rejected() -> None:
    refs = _evidence_refs()
    canonical_payload = {
        "assertion_id": "assertion.sorted",
        "status": TaskAssertionStatus.SATISFIED,
        "evidence_refs": tuple(
            sorted((refs["output"], refs["task"]), key=lambda item: item.sort_key())
        ),
    }
    canonical = _signed_fact(TaskAssertionFact, **canonical_payload)
    reversed_refs = tuple(reversed(canonical_payload["evidence_refs"]))
    reordered = TaskAssertionFact(
        **(canonical_payload | {"evidence_refs": reversed_refs}),
        fact_digest=canonical.fact_digest,
    )
    assert reordered == canonical

    with pytest.raises(ValidationError, match="must not repeat evidence_id"):
        _signed_fact(
            TaskAssertionFact,
            assertion_id="assertion.duplicate",
            status=TaskAssertionStatus.SATISFIED,
            evidence_refs=(refs["task"], refs["task"]),
        )


def test_tampered_digest_unknown_field_and_dangling_reference_are_rejected() -> None:
    result = _complete_result()
    payload = result.model_dump(mode="json")

    with pytest.raises(ValidationError, match="result_digest does not match"):
        SCENARIO_ORACLE_RESULT_ADAPTER.validate_python(payload | {"result_digest": ZERO_DIGEST})

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SCENARIO_ORACLE_RESULT_ADAPTER.validate_python(payload | {"unexpected": True})

    closure_without_output = tuple(
        ref for ref in result.evidence_closure if ref.evidence_id != "evidence.output"
    )
    with pytest.raises(ValidationError, match="dangling or mismatched evidence references"):
        _signed_result(
            CompleteScenarioOracleResult,
            scenario_case_id=result.scenario_case_id,
            scenario_case_digest=result.scenario_case_digest,
            input_bundle_digest=result.input_bundle_digest,
            initial_state_digest=result.initial_state_digest,
            final_state_digest=result.final_state_digest,
            utility=result.utility,
            security=result.security,
            evidence_closure=closure_without_output,
        )

    output_ref = next(
        ref for ref in result.evidence_closure if ref.evidence_id == "evidence.output"
    )
    mismatched_output = output_ref.model_copy(
        update={"evidence_digest": "sha256:" + "1" * 64}
    )
    mismatched_closure = tuple(
        mismatched_output if ref.evidence_id == mismatched_output.evidence_id else ref
        for ref in result.evidence_closure
    )
    with pytest.raises(ValidationError, match="dangling or mismatched evidence references"):
        _signed_result(
            CompleteScenarioOracleResult,
            scenario_case_id=result.scenario_case_id,
            scenario_case_digest=result.scenario_case_digest,
            input_bundle_digest=result.input_bundle_digest,
            initial_state_digest=result.initial_state_digest,
            final_state_digest=result.final_state_digest,
            utility=result.utility,
            security=result.security,
            evidence_closure=mismatched_closure,
        )


def test_invalid_evidence_result_has_no_partial_conclusions_or_fake_state_requirement() -> None:
    failure = _signed_fact(
        OracleFailure,
        failure_code=OracleFailureCode.INVALID_STATE_CHAIN,
        detail_digest=ZERO_DIGEST,
        affected_evidence_ids=("evidence.transition",),
    )
    invalid = _signed_result(
        InvalidEvidenceOracleResult,
        scenario_case_id="scenario.sample",
        scenario_case_digest=ZERO_DIGEST,
        input_bundle_digest=ZERO_DIGEST,
        observed_initial_state_digest=ZERO_DIGEST,
        observed_final_state_digest=None,
        failure=failure,
    )
    assert SCENARIO_ORACLE_RESULT_ADAPTER.validate_json(invalid.model_dump_json()) == invalid

    payload = invalid.model_dump(mode="json") | {"utility": _complete_result().utility}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SCENARIO_ORACLE_RESULT_ADAPTER.validate_python(payload)


def test_contradictory_security_facts_are_rejected() -> None:
    ref = _evidence_refs()["decision"]
    with pytest.raises(ValidationError, match="hard-blocked violation"):
        _signed_fact(
            ViolationFact,
            violation_id="violation.impossible",
            violation_kind=ViolationKind.POLICY_ENFORCED_DENIED,
            planned=False,
            objective_id=None,
            side_effect_committed=True,
            evidence_refs=(ref,),
        )

    result = _complete_result()
    mismatched_exposure = _signed_fact(
        ExposureFact,
        condition_id="condition.sample",
        entry_kind=AttackEntryKind.INDIRECT_CONTENT,
        stages=(ExposureStage.PLANNED,),
        evidence_refs=(_evidence_refs()["task"],),
    )
    with pytest.raises(ValidationError, match="missing or mismatched exposure fact"):
        _signed_fact(
            SecurityFactSet,
            exposure_facts=(mismatched_exposure,),
            planned_objectives=result.security.planned_objectives,
            violations=result.security.violations,
        )
