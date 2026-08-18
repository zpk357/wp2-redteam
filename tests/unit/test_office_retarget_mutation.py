from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sandbox.coverage.models import CampaignCoverageFeedback, CoverageSaturationSummary
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.candidate_generation import CandidateRejectionCode
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_mutation import (
    OfficeExpressionMutationRunner,
    OfficeMutationArtifactStore,
    OfficeMutationCandidate,
    OfficeMutationDimension,
    OfficeMutationIntegrityError,
    OfficeMutationMode,
    OfficeMutationPlanner,
    OfficeMutationPlanningError,
    OfficeMutationProviderResult,
    OfficeMutationRejectionCode,
    OfficeMutationValidationStatus,
    OfficeMutationValidator,
    RuleBasedOfficeMutationProvider,
    office_mutation_request_digest,
)


def _feedback() -> CampaignCoverageFeedback:
    return CampaignCoverageFeedback(
        campaign_id="office-retarget-test",
        taxonomy_version="risk-taxonomy-v1",
        taxonomy_digest="sha256:" + "1" * 64,
        risk_mapping_version="office-risk-mapping-v1",
        risk_mapping_digest="sha256:" + "2" * 64,
        risk_scope_version="office-risk-scope-v1",
        risk_scope_digest="sha256:" + "3" * 64,
        include_empty=True,
        observed_behavior_paths=0,
        saturation=CoverageSaturationSummary(
            observations=0,
            trailing_without_behavior_gain=0,
            max_without_behavior_gain=0,
            trailing_without_execution_risk_gain=0,
            max_without_execution_risk_gain=0,
            trailing_without_any_gain=0,
            max_without_any_gain=0,
        ),
    )


def _parent():
    return OFFICE_V1_TEST_MATRIX.attack_cases[0]


def _objective_only_target():
    return OFFICE_V1_TEST_MATRIX.attack_cases[4]


def _recomposed_target():
    return OFFICE_V1_TEST_MATRIX.attack_cases[9]


def _retarget_plan(*, recompose: bool = False):
    target = _recomposed_target() if recompose else _objective_only_target()
    assert target.attack is not None
    return OfficeMutationPlanner().plan_retarget(
        parent=_parent(),
        feedback=_feedback(),
        provider_identity=RuleBasedOfficeMutationProvider.identity,
        target_task_id=target.benign_task.task_id,
        target_objective_id=target.attack.objective.objective_id,
        target_carrier_id=target.attack.carrier.carrier_id,
        operator_id="explicit-office-target-redirection",
        expected_path="read_email>read_drive_file",
        random_seed=23,
        requested_count=1,
        max_output_tokens=1_024,
    )


class _SingleCandidateProvider:
    identity = RuleBasedOfficeMutationProvider.identity

    def __init__(self, factory) -> None:
        self.factory = factory

    async def mutate(self, plan, parent):
        candidate = self.factory(plan, parent)
        response = candidate.model_dump_json().encode("utf-8")
        return OfficeMutationProviderResult(
            candidates=(candidate,),
            request_digest=office_mutation_request_digest(plan, parent),
            response_digest=sha256_digest(candidate),
            response_bytes=len(response),
        )


@pytest.mark.asyncio
async def test_explicit_objective_a_to_b_retarget_creates_registered_child(
    tmp_path: Path,
) -> None:
    parent = _parent()
    target = _objective_only_target()
    plan = _retarget_plan()

    assert plan.mode == OfficeMutationMode.EXPLICIT_TARGET_REDIRECTION
    assert plan.changed_dimensions == (
        OfficeMutationDimension.ATTACK_OBJECTIVE,
        OfficeMutationDimension.ATTACK_EXPRESSION,
    )
    assert plan.before_components.objective_id != plan.planned_components.objective_id

    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        result = await OfficeExpressionMutationRunner(
            provider=RuleBasedOfficeMutationProvider(),
            validator=OfficeMutationValidator(),
            store=store,
        ).run(plan=plan, parent=parent)
        record = store.validations()[0]

    assert len(result.accepted_child_case_ids) == 1
    assert record.status == OfficeMutationValidationStatus.ACCEPTED
    assert record.actual_changed_dimensions == plan.changed_dimensions
    assert record.child_case is not None
    assert record.child_case.parent_case_id == parent.case_id
    assert record.child_case.benign_task == target.benign_task
    assert record.child_case.attack is not None
    assert record.child_case.attack.objective == target.attack.objective
    assert record.child_case.attack.carrier == target.attack.carrier


@pytest.mark.asyncio
async def test_retarget_can_explicitly_recompose_task_objective_and_carrier(
    tmp_path: Path,
) -> None:
    parent = _parent()
    target = _recomposed_target()
    plan = _retarget_plan(recompose=True)

    assert plan.changed_dimensions == (
        OfficeMutationDimension.BENIGN_TASK,
        OfficeMutationDimension.ATTACK_OBJECTIVE,
        OfficeMutationDimension.INJECTION_CARRIER,
        OfficeMutationDimension.ATTACK_EXPRESSION,
    )
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        await OfficeExpressionMutationRunner(
            provider=RuleBasedOfficeMutationProvider(),
            validator=OfficeMutationValidator(),
            store=store,
        ).run(plan=plan, parent=parent)
        record = store.validations()[0]

    assert record.status == OfficeMutationValidationStatus.ACCEPTED
    assert record.actual_changed_dimensions == plan.changed_dimensions
    assert record.child_case is not None
    assert record.child_case.benign_task == target.benign_task
    assert record.child_case.attack is not None
    assert record.child_case.attack.objective == target.attack.objective
    assert record.child_case.attack.carrier == target.attack.carrier


def test_retarget_planner_rejects_unregistered_objective_with_stable_reason() -> None:
    with pytest.raises(OfficeMutationPlanningError) as captured:
        OfficeMutationPlanner().plan_retarget(
            parent=_parent(),
            feedback=_feedback(),
            provider_identity=RuleBasedOfficeMutationProvider.identity,
            target_objective_id="unregistered-objective",
            operator_id="explicit-office-target-redirection",
            random_seed=1,
            requested_count=1,
            max_output_tokens=512,
        )

    assert captured.value.rejection.code == CandidateRejectionCode.UNKNOWN_OBJECTIVE


def test_retarget_planner_rejects_incompatible_registered_composition() -> None:
    target = _objective_only_target()
    incompatible_carrier = _recomposed_target().attack
    assert target.attack is not None
    assert incompatible_carrier is not None
    with pytest.raises(OfficeMutationPlanningError) as captured:
        OfficeMutationPlanner().plan_retarget(
            parent=_parent(),
            feedback=_feedback(),
            provider_identity=RuleBasedOfficeMutationProvider.identity,
            target_objective_id=target.attack.objective.objective_id,
            target_carrier_id=incompatible_carrier.carrier.carrier_id,
            operator_id="explicit-office-target-redirection",
            random_seed=1,
            requested_count=1,
            max_output_tokens=512,
        )

    assert (
        captured.value.rejection.code
        == CandidateRejectionCode.INCOMPATIBLE_COMPOSITION
    )


def test_retarget_requires_a_different_objective() -> None:
    parent = _parent()
    assert parent.attack is not None
    with pytest.raises(OfficeMutationIntegrityError, match="different attack objective"):
        OfficeMutationPlanner().plan_retarget(
            parent=parent,
            feedback=_feedback(),
            provider_identity=RuleBasedOfficeMutationProvider.identity,
            target_objective_id=parent.attack.objective.objective_id,
            operator_id="explicit-office-target-redirection",
            random_seed=1,
            requested_count=1,
            max_output_tokens=512,
        )


def test_retarget_does_not_silently_treat_empty_component_id_as_preserved() -> None:
    target = _objective_only_target()
    assert target.attack is not None
    with pytest.raises(ValidationError, match="task_id"):
        OfficeMutationPlanner().plan_retarget(
            parent=_parent(),
            feedback=_feedback(),
            provider_identity=RuleBasedOfficeMutationProvider.identity,
            target_task_id="",
            target_objective_id=target.attack.objective.objective_id,
            operator_id="explicit-office-target-redirection",
            random_seed=1,
            requested_count=1,
            max_output_tokens=512,
        )


def test_retarget_plan_cannot_claim_a_preknown_generated_expression() -> None:
    plan = _retarget_plan()
    payload = plan.model_dump(mode="python")
    payload["planned_components"]["expression_digest"] = "sha256:" + "7" * 64

    with pytest.raises(ValidationError, match="must remain unknown"):
        type(plan).model_validate(payload)


@pytest.mark.asyncio
async def test_candidate_that_only_partly_applies_recomposition_is_rejected(
    tmp_path: Path,
) -> None:
    def partial(candidate_plan, parent):
        assert parent.attack is not None
        return OfficeMutationCandidate.create(
            plan_id=candidate_plan.plan_id,
            ordinal=0,
            scenario_template_id=parent.scenario.template_id,
            task_id=parent.benign_task.task_id,
            objective_id=candidate_plan.planned_components.objective_id,
            carrier_id=parent.attack.carrier.carrier_id,
            expression="Explicitly redirected expression",
            claimed_operator_id=candidate_plan.operator_id,
            claimed_expected_path=candidate_plan.expected_path,
        )

    plan = _retarget_plan(recompose=True)
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        result = await OfficeExpressionMutationRunner(
            provider=_SingleCandidateProvider(partial),
            validator=OfficeMutationValidator(),
            store=store,
        ).run(plan=plan, parent=_parent())
        record = store.validations()[0]

    assert result.accepted_child_case_ids == ()
    assert OfficeMutationRejectionCode.SILENT_COMPONENT_DRIFT in record.rejection_codes
    assert record.child_case is None


@pytest.mark.asyncio
async def test_candidate_drift_to_unregistered_component_is_rejected(
    tmp_path: Path,
) -> None:
    def unregistered(candidate_plan, parent):
        assert parent.attack is not None
        return OfficeMutationCandidate.create(
            plan_id=candidate_plan.plan_id,
            ordinal=0,
            scenario_template_id=parent.scenario.template_id,
            task_id=candidate_plan.planned_components.task_id,
            objective_id="unregistered-objective",
            carrier_id=candidate_plan.planned_components.carrier_id,
            expression="Unregistered redirected expression",
            claimed_operator_id=candidate_plan.operator_id,
            claimed_expected_path=candidate_plan.expected_path,
        )

    plan = _retarget_plan()
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        result = await OfficeExpressionMutationRunner(
            provider=_SingleCandidateProvider(unregistered),
            validator=OfficeMutationValidator(),
            store=store,
        ).run(plan=plan, parent=_parent())
        record = store.validations()[0]

    assert result.accepted_child_case_ids == ()
    assert OfficeMutationRejectionCode.UNREGISTERED_COMPONENT in record.rejection_codes
    assert OfficeMutationRejectionCode.SILENT_COMPONENT_DRIFT in record.rejection_codes
