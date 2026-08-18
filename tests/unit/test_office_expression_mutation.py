from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sandbox.coverage.models import CampaignCoverageFeedback, CoverageSaturationSummary
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_mutation import (
    OfficeExpressionMutationRunner,
    OfficeMutationArtifactStore,
    OfficeMutationCandidate,
    OfficeMutationDimension,
    OfficeMutationIntegrityError,
    OfficeMutationPlan,
    OfficeMutationPlanner,
    OfficeMutationProviderError,
    OfficeMutationProviderFailureKind,
    OfficeMutationProviderIdentity,
    OfficeMutationProviderKind,
    OfficeMutationProviderResult,
    OfficeMutationRejectionCode,
    OfficeMutationValidationStatus,
    OfficeMutationValidator,
    RuleBasedOfficeMutationProvider,
    office_mutation_request_digest,
)


def _parent():
    return OFFICE_V1_TEST_MATRIX.attack_cases[0]


def _feedback(campaign_id: str = "office-mutation-test") -> CampaignCoverageFeedback:
    return CampaignCoverageFeedback(
        campaign_id=campaign_id,
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


def _plan(
    *,
    provider_identity: OfficeMutationProviderIdentity | None = None,
    campaign_id: str = "office-mutation-test",
    requested_count: int = 2,
) -> OfficeMutationPlan:
    provider_identity = provider_identity or RuleBasedOfficeMutationProvider.identity
    return OfficeMutationPlanner().plan(
        parent=_parent(),
        feedback=_feedback(campaign_id),
        provider_identity=provider_identity,
        operator_id="target-preserving-expression-rewrite",
        expected_path="read_email>create_calendar_event",
        random_seed=17,
        requested_count=requested_count,
        max_output_tokens=1_024,
    )


class _PlanMustExistProvider:
    identity = RuleBasedOfficeMutationProvider.identity

    def __init__(self, store: OfficeMutationArtifactStore) -> None:
        self.store = store
        self.delegate = RuleBasedOfficeMutationProvider()

    async def mutate(self, plan, parent):
        assert self.store.has_artifact("plan", plan.plan_id)
        return await self.delegate.mutate(plan, parent)


class _SingleCandidateProvider:
    identity = RuleBasedOfficeMutationProvider.identity

    def __init__(self, candidate_factory) -> None:
        self.candidate_factory = candidate_factory

    async def mutate(self, plan, parent):
        candidate = self.candidate_factory(plan, parent)
        response = candidate.model_dump_json().encode("utf-8")
        return OfficeMutationProviderResult(
            candidates=(candidate,),
            request_digest=office_mutation_request_digest(plan, parent),
            response_digest=sha256_digest(candidate),
            response_bytes=len(response),
        )


class _FailingProvider:
    identity = RuleBasedOfficeMutationProvider.identity

    def __init__(self, store: OfficeMutationArtifactStore) -> None:
        self.store = store

    async def mutate(self, plan, parent):
        assert self.store.has_artifact("plan", plan.plan_id)
        raise OfficeMutationProviderError(
            "synthetic provider outage",
            kind=OfficeMutationProviderFailureKind.TRANSPORT,
            recoverable=True,
            request_digest=office_mutation_request_digest(plan, parent),
        )


@pytest.mark.asyncio
async def test_plan_is_frozen_and_persisted_before_provider_call(tmp_path: Path) -> None:
    plan = _plan()
    parent = _parent()

    assert plan.changed_dimensions == (OfficeMutationDimension.ATTACK_EXPRESSION,)
    assert OfficeMutationDimension.ATTACK_OBJECTIVE in plan.preserved_dimensions
    assert plan.before_components.objective_id == plan.planned_components.objective_id
    assert plan.before_components.expression_digest is not None
    assert plan.planned_components.expression_digest is None

    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        runner = OfficeExpressionMutationRunner(
            provider=_PlanMustExistProvider(store),
            validator=OfficeMutationValidator(),
            store=store,
        )
        result = await runner.run(plan=plan, parent=parent)

        assert store.artifact_count("plan") == 1
        assert store.artifact_count("provider_call") == 1
        assert store.artifact_count("candidate") == 2
        assert store.artifact_count("validation") == 2
        assert len(result.accepted_child_case_ids) == 2


@pytest.mark.asyncio
async def test_accepted_child_changes_only_expression_and_keeps_lineage(tmp_path: Path) -> None:
    plan = _plan(requested_count=1)
    parent = _parent()
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        runner = OfficeExpressionMutationRunner(
            provider=RuleBasedOfficeMutationProvider(),
            validator=OfficeMutationValidator(),
            store=store,
        )
        await runner.run(plan=plan, parent=parent)
        record = store.validations()[0]

    assert record.status == OfficeMutationValidationStatus.ACCEPTED
    assert record.actual_changed_dimensions == (
        OfficeMutationDimension.ATTACK_EXPRESSION,
    )
    assert record.child_case is not None
    assert record.child_case.parent_case_id == parent.case_id
    assert record.child_case.scenario == parent.scenario
    assert record.child_case.benign_task == parent.benign_task
    assert record.child_case.attack is not None
    assert record.child_case.attack.objective == parent.attack.objective
    assert record.child_case.attack.carrier == parent.attack.carrier
    assert record.child_case.agent == parent.agent
    assert record.child_case.budget == parent.budget


@pytest.mark.asyncio
async def test_silent_task_drift_is_rejected_before_test_case_creation(tmp_path: Path) -> None:
    def drift(plan, parent):
        assert parent.attack is not None
        return OfficeMutationCandidate.create(
            plan_id=plan.plan_id,
            ordinal=0,
            scenario_template_id=parent.scenario.template_id,
            task_id="silently-retargeted-task",
            objective_id=parent.attack.objective.objective_id,
            carrier_id=parent.attack.carrier.carrier_id,
            expression="Rephrased attack requirement: " + parent.attack.payload,
            claimed_operator_id=plan.operator_id,
            claimed_expected_path=plan.expected_path,
        )

    plan = _plan(requested_count=1)
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        runner = OfficeExpressionMutationRunner(
            provider=_SingleCandidateProvider(drift),
            validator=OfficeMutationValidator(),
            store=store,
        )
        result = await runner.run(plan=plan, parent=_parent())
        record = store.validations()[0]

    assert result.accepted_child_case_ids == ()
    assert OfficeMutationRejectionCode.SILENT_COMPONENT_DRIFT in record.rejection_codes
    assert record.child_case is None


@pytest.mark.asyncio
async def test_normalization_only_change_is_rejected(tmp_path: Path) -> None:
    def unchanged(plan, parent):
        assert parent.attack is not None
        return OfficeMutationCandidate.create(
            plan_id=plan.plan_id,
            ordinal=0,
            scenario_template_id=parent.scenario.template_id,
            task_id=parent.benign_task.task_id,
            objective_id=parent.attack.objective.objective_id,
            carrier_id=parent.attack.carrier.carrier_id,
            expression="  " + parent.attack.payload.replace(" ", "  ") + "  ",
            claimed_operator_id=plan.operator_id,
            claimed_expected_path=plan.expected_path,
        )

    plan = _plan(requested_count=1)
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        result = await OfficeExpressionMutationRunner(
            provider=_SingleCandidateProvider(unchanged),
            validator=OfficeMutationValidator(),
            store=store,
        ).run(plan=plan, parent=_parent())
        record = store.validations()[0]

    assert result.accepted_child_case_ids == ()
    assert OfficeMutationRejectionCode.EXPRESSION_UNCHANGED in record.rejection_codes


@pytest.mark.asyncio
async def test_repeated_run_is_idempotent(tmp_path: Path) -> None:
    plan = _plan()
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        runner = OfficeExpressionMutationRunner(
            provider=RuleBasedOfficeMutationProvider(),
            validator=OfficeMutationValidator(),
            store=store,
        )
        first = await runner.run(plan=plan, parent=_parent())
        second = await runner.run(plan=plan, parent=_parent())

        assert first == second
        assert store.artifact_count("plan") == 1
        assert store.artifact_count("provider_call") == 1
        assert store.artifact_count("candidate") == 2
        assert store.artifact_count("validation") == 2
        assert store.artifact_count("run") == 1

    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as restored:
        assert restored.get_plan(plan.plan_id) == plan
        assert len(restored.candidates()) == 2
        assert len(restored.validations()) == 2
        assert restored.runs() == [first]


@pytest.mark.asyncio
async def test_provider_failure_keeps_plan_and_failure_audit(tmp_path: Path) -> None:
    plan = _plan()
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        runner = OfficeExpressionMutationRunner(
            provider=_FailingProvider(store),
            validator=OfficeMutationValidator(),
            store=store,
        )
        with pytest.raises(OfficeMutationProviderError, match="synthetic provider outage"):
            await runner.run(plan=plan, parent=_parent())

        assert store.artifact_count("plan") == 1
        assert store.artifact_count("provider_call") == 1
        assert store.artifact_count("candidate") == 0
        call = store.provider_calls()[0]
        assert call.status.value == "failed"
        assert call.retryable is True


def test_plan_digest_and_target_preserving_dimensions_cannot_be_forged() -> None:
    plan = _plan()
    changed = plan.model_dump(mode="python")
    changed["changed_dimensions"] = (
        OfficeMutationDimension.ATTACK_EXPRESSION,
        OfficeMutationDimension.ATTACK_OBJECTIVE,
    )

    with pytest.raises(ValidationError, match="do not match planned component differences"):
        OfficeMutationPlan.model_validate(changed)

    changed = plan.model_dump(mode="python")
    changed["random_seed"] = 99
    with pytest.raises(ValidationError, match="plan_id does not match"):
        OfficeMutationPlan.model_validate(changed)


def test_real_provider_identity_requires_model_digest_and_endpoint() -> None:
    with pytest.raises(ValidationError, match="locked model, digest, and endpoint"):
        OfficeMutationProviderIdentity(
            kind=OfficeMutationProviderKind.OLLAMA,
            provider_version="ollama-v1",
            model_name="qwen3",
            prompt_version="office-expression-v1",
            response_schema_version="office-candidate-v1",
        )


def test_plan_cannot_reference_a_gap_absent_from_feedback() -> None:
    with pytest.raises(OfficeMutationIntegrityError, match="not present in report"):
        OfficeMutationPlanner().plan(
            parent=_parent(),
            feedback=_feedback(),
            provider_identity=RuleBasedOfficeMutationProvider.identity,
            operator_id="target-preserving-expression-rewrite",
            random_seed=1,
            requested_count=1,
            max_output_tokens=512,
            expected_risk_gap_ids=("unreported-risk-gap",),
        )


@pytest.mark.asyncio
async def test_runner_rejects_a_resigned_plan_with_a_different_catalog_lock(
    tmp_path: Path,
) -> None:
    plan = _plan(requested_count=1)
    payload = plan.model_dump(mode="python", exclude={"plan_id", "content_digest"})
    payload["catalog_manifest_digest"] = "sha256:" + "9" * 64
    identity_digest = sha256_digest(payload)
    plan_id = "office-plan-" + identity_digest.removeprefix("sha256:")[:24]
    forged = OfficeMutationPlan(
        plan_id=plan_id,
        content_digest=sha256_digest({"plan_id": plan_id, **payload}),
        **payload,
    )

    with OfficeMutationArtifactStore(tmp_path, forged.campaign_id) as store:
        runner = OfficeExpressionMutationRunner(
            provider=RuleBasedOfficeMutationProvider(),
            validator=OfficeMutationValidator(),
            store=store,
        )
        with pytest.raises(OfficeMutationIntegrityError, match="validator lock"):
            await runner.run(plan=forged, parent=_parent())

        assert store.artifact_count("plan") == 0
        assert store.artifact_count("provider_call") == 0


def test_provider_candidate_ordinals_must_be_contiguous() -> None:
    plan = _plan(requested_count=2)
    parent = _parent()
    assert parent.attack is not None
    candidate = OfficeMutationCandidate.create(
        plan_id=plan.plan_id,
        ordinal=1,
        scenario_template_id=parent.scenario.template_id,
        task_id=parent.benign_task.task_id,
        objective_id=parent.attack.objective.objective_id,
        carrier_id=parent.attack.carrier.carrier_id,
        expression="Changed expression: " + parent.attack.payload,
        claimed_operator_id=plan.operator_id,
        claimed_expected_path=plan.expected_path,
    )

    with pytest.raises(ValidationError, match="contiguous from zero"):
        OfficeMutationProviderResult(
            candidates=(candidate,),
            request_digest="sha256:" + "4" * 64,
            response_digest="sha256:" + "5" * 64,
            response_bytes=1,
        )
