from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.coverage.models import CampaignCoverageFeedback, CoverageSaturationSummary
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_mutation import (
    OfficeMutationArtifactStore,
    OfficeMutationIntegrityError,
    OfficeMutationPlan,
    OfficeMutationPlanner,
    OfficeMutationProviderError,
    OfficeMutationProviderFailureKind,
    OfficeMutationValidator,
    RuleBasedOfficeMutationProvider,
)
from sandbox.scenarios.office_mutation_batch import (
    OfficeMutationBatchPolicy,
    OfficeMutationBatchRunner,
    OfficeMutationBatchStatus,
    OfficeMutationSubBatchRequest,
    office_mutation_sub_batch_request_digest,
)


def _parent():
    return OFFICE_V1_TEST_MATRIX.attack_cases[0]


def _feedback(campaign_id: str) -> CampaignCoverageFeedback:
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


def _plan(campaign_id: str, requested_count: int = 4) -> OfficeMutationPlan:
    return OfficeMutationPlanner().plan(
        parent=_parent(),
        feedback=_feedback(campaign_id),
        provider_identity=RuleBasedOfficeMutationProvider.identity,
        operator_id="target-preserving-expression-rewrite",
        expected_path="read_email>create_calendar_event",
        random_seed=17,
        requested_count=requested_count,
        max_output_tokens=1_024,
    )


class _CampaignState:
    def __init__(self, campaign_id: str, *, interrupt_pause_once: bool = False) -> None:
        self.campaign_id = campaign_id
        self.interrupt_pause_once = interrupt_pause_once
        self.pause_calls: list[tuple[str, str | None]] = []

    def snapshot(self):
        return SimpleNamespace(campaign_id=self.campaign_id)

    def pause_campaign(self, reason_code: str, *, evidence_digest: str | None = None):
        if self.interrupt_pause_once:
            self.interrupt_pause_once = False
            raise _SimulatedInterruption("pause transaction interrupted")
        record = (reason_code, evidence_digest)
        if record not in self.pause_calls:
            self.pause_calls.append(record)
        return self.snapshot()


class _SimulatedInterruption(BaseException):
    pass


class _CountingRuleProvider:
    identity = RuleBasedOfficeMutationProvider.identity

    def __init__(self) -> None:
        self.calls: list[OfficeMutationSubBatchRequest] = []
        self.delegate = RuleBasedOfficeMutationProvider()

    async def mutate_sub_batch(self, plan, parent, request):
        self.calls.append(request)
        return await self.delegate.mutate_sub_batch(plan, parent, request)


class _NoCallProvider:
    identity = RuleBasedOfficeMutationProvider.identity

    async def mutate_sub_batch(self, plan, parent, request):
        raise AssertionError("persisted sub-batch must not call the Provider again")


class _RetryThenSuccessProvider(_CountingRuleProvider):
    async def mutate_sub_batch(self, plan, parent, request):
        self.calls.append(request)
        if len(self.calls) == 1:
            raise OfficeMutationProviderError(
                "temporary transport outage",
                kind=OfficeMutationProviderFailureKind.TRANSPORT,
                recoverable=True,
                request_digest=office_mutation_sub_batch_request_digest(
                    plan, parent, request
                ),
            )
        return await self.delegate.mutate_sub_batch(plan, parent, request)


class _InterruptedSplitProvider(_CountingRuleProvider):
    async def mutate_sub_batch(self, plan, parent, request):
        self.calls.append(request)
        if request.path == "0":
            raise OfficeMutationProviderError(
                "response was truncated",
                kind=OfficeMutationProviderFailureKind.TRUNCATED,
                recoverable=True,
                request_digest=office_mutation_sub_batch_request_digest(
                    plan, parent, request
                ),
                response_digest="sha256:" + "4" * 64,
                response_bytes=4_096,
                done_reason="length",
            )
        if request.path == "0.1":
            raise _SimulatedInterruption("worker terminated between child batches")
        return await self.delegate.mutate_sub_batch(plan, parent, request)


class _RightChildOutageProvider(_CountingRuleProvider):
    async def mutate_sub_batch(self, plan, parent, request):
        self.calls.append(request)
        assert request.path == "0.1"
        raise OfficeMutationProviderError(
            "temporary right-child outage",
            kind=OfficeMutationProviderFailureKind.TIMEOUT,
            recoverable=True,
            request_digest=office_mutation_sub_batch_request_digest(
                plan, parent, request
            ),
        )


class _FatalProvider(_CountingRuleProvider):
    def __init__(self, kind: OfficeMutationProviderFailureKind) -> None:
        super().__init__()
        self.kind = kind

    async def mutate_sub_batch(self, plan, parent, request):
        self.calls.append(request)
        raise OfficeMutationProviderError(
            "permanent malformed response",
            kind=self.kind,
            recoverable=False,
            request_digest=office_mutation_sub_batch_request_digest(
                plan, parent, request
            ),
        )


class _HttpThenSuccessProvider(_CountingRuleProvider):
    def __init__(self, status: int) -> None:
        super().__init__()
        self.status = status

    async def mutate_sub_batch(self, plan, parent, request):
        self.calls.append(request)
        if len(self.calls) == 1:
            raise OfficeMutationProviderError(
                f"HTTP {self.status}",
                kind=OfficeMutationProviderFailureKind.HTTP,
                http_status=self.status,
                request_digest=office_mutation_sub_batch_request_digest(
                    plan, parent, request
                ),
            )
        return await self.delegate.mutate_sub_batch(plan, parent, request)


class _UnexpectedProvider(_CountingRuleProvider):
    async def mutate_sub_batch(self, plan, parent, request):
        self.calls.append(request)
        raise RuntimeError("unclassified SDK failure")


class _IntegrityFailingValidator(OfficeMutationValidator):
    def validate(self, **kwargs):
        raise OfficeMutationIntegrityError("synthetic local digest conflict")


def _runner(provider, store, campaign_state, policy=None):
    return OfficeMutationBatchRunner(
        provider=provider,
        validator=OfficeMutationValidator(),
        store=store,
        campaign_state=campaign_state,
        policy=policy or OfficeMutationBatchPolicy.create(),
    )


def test_sub_batch_seed_and_token_budget_are_deterministic_and_retry_specific() -> None:
    plan = _plan("office-batch-contract")
    policy = OfficeMutationBatchPolicy.create(
        base_output_tokens=128,
        output_tokens_per_candidate=160,
        max_output_tokens=2_048,
    )
    first = OfficeMutationSubBatchRequest.create(
        plan=plan,
        policy=policy,
        path="0",
        ordinal_offset=0,
        retry_index=0,
        requested_count=4,
    )
    replay = OfficeMutationSubBatchRequest.create(
        plan=plan,
        policy=policy,
        path="0",
        ordinal_offset=0,
        retry_index=0,
        requested_count=4,
    )
    retry = OfficeMutationSubBatchRequest.create(
        plan=plan,
        policy=policy,
        path="0",
        ordinal_offset=0,
        retry_index=1,
        requested_count=4,
    )

    assert first == replay
    assert first.max_output_tokens == 768
    assert first.random_seed != retry.random_seed
    assert first.request_id != retry.request_id
    assert policy.tokens_for(plan, 2) == 448


def test_primary_sampling_seed_is_independent_from_feedback_plan_digest() -> None:
    first_plan = _plan("office-batch-feedback-a", requested_count=2)
    second_plan = _plan("office-batch-feedback-b", requested_count=2)
    policy = OfficeMutationBatchPolicy.create()

    first = OfficeMutationSubBatchRequest.create(
        plan=first_plan,
        policy=policy,
        path="0",
        ordinal_offset=0,
        retry_index=0,
        requested_count=2,
    )
    second = OfficeMutationSubBatchRequest.create(
        plan=second_plan,
        policy=policy,
        path="0",
        ordinal_offset=0,
        retry_index=0,
        requested_count=2,
    )

    assert first_plan.content_digest != second_plan.content_digest
    assert first.random_seed == second.random_seed
    assert first.request_id != second.request_id


@pytest.mark.asyncio
async def test_complete_batch_reopens_without_provider_call(tmp_path: Path) -> None:
    plan = _plan("office-batch-complete")
    campaign_state = _CampaignState(plan.campaign_id)
    provider = _CountingRuleProvider()
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        first = await _runner(provider, store, campaign_state).run(
            plan=plan, parent=_parent()
        )

    assert first.status == OfficeMutationBatchStatus.COMPLETE
    assert first.generated_count == 4
    assert len(provider.calls) == 1

    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        replay = await _runner(_NoCallProvider(), store, campaign_state).run(
            plan=plan, parent=_parent()
        )
    assert replay == first


@pytest.mark.asyncio
async def test_transient_retry_uses_new_seed_and_finishes_degraded(tmp_path: Path) -> None:
    plan = _plan("office-batch-retry", requested_count=2)
    campaign_state = _CampaignState(plan.campaign_id)
    provider = _RetryThenSuccessProvider()
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        result = await _runner(provider, store, campaign_state).run(
            plan=plan, parent=_parent()
        )

    assert result.status == OfficeMutationBatchStatus.DEGRADED
    assert result.retryable_failure_count == 1
    assert result.generated_count == 2
    assert len(provider.calls) == 2
    assert provider.calls[0].random_seed != provider.calls[1].random_seed


@pytest.mark.asyncio
async def test_split_success_survives_interruption_and_right_child_exhaustion(
    tmp_path: Path,
) -> None:
    plan = _plan("office-batch-split")
    campaign_state = _CampaignState(plan.campaign_id)
    interrupted = _InterruptedSplitProvider()
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        with pytest.raises(_SimulatedInterruption):
            await _runner(interrupted, store, campaign_state).run(
                plan=plan, parent=_parent()
            )
        assert store.artifact_count("candidate") == 2

    resumed = _RightChildOutageProvider()
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        result = await _runner(resumed, store, campaign_state).run(
            plan=plan, parent=_parent()
        )
        assert store.artifact_count("candidate") == 2

    assert result.status == OfficeMutationBatchStatus.PARTIAL
    assert result.generated_count == 2
    assert result.shrink_count == 1
    assert result.retryable_failure_count == 2
    assert [request.path for request in resumed.calls] == ["0.1", "0.1"]


@pytest.mark.asyncio
async def test_fatal_attempt_is_recovered_and_campaign_pause_is_reapplied(
    tmp_path: Path,
) -> None:
    plan = _plan("office-batch-fatal", requested_count=2)
    campaign_state = _CampaignState(plan.campaign_id, interrupt_pause_once=True)
    provider = _FatalProvider(OfficeMutationProviderFailureKind.INVALID_SCHEMA)
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        with pytest.raises(_SimulatedInterruption):
            await _runner(provider, store, campaign_state).run(
                plan=plan, parent=_parent()
            )
        assert store.artifact_count("mutation_batch_attempt") == 1

    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        result = await _runner(_NoCallProvider(), store, campaign_state).run(
            plan=plan, parent=_parent()
        )

    assert result.status == OfficeMutationBatchStatus.PAUSED
    assert result.pause_reason_code == "mutation_provider_invalid_schema"
    assert campaign_state.pause_calls == [
        (result.pause_reason_code, result.pause_evidence_digest)
    ]


@pytest.mark.asyncio
async def test_invalid_json_is_fatal_instead_of_triggering_shrink(tmp_path: Path) -> None:
    plan = _plan("office-batch-invalid-json")
    campaign_state = _CampaignState(plan.campaign_id)
    provider = _FatalProvider(OfficeMutationProviderFailureKind.INVALID_JSON)
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        result = await _runner(provider, store, campaign_state).run(
            plan=plan, parent=_parent()
        )

    assert result.status == OfficeMutationBatchStatus.PAUSED
    assert result.shrink_count == 0
    assert len(provider.calls) == 1
    assert result.pause_reason_code == "mutation_provider_invalid_json"


@pytest.mark.asyncio
async def test_http_retry_whitelist_excludes_permanent_client_errors(
    tmp_path: Path,
) -> None:
    retry_plan = _plan("office-batch-http-429", requested_count=2)
    retry_state = _CampaignState(retry_plan.campaign_id)
    retry_provider = _HttpThenSuccessProvider(429)
    with OfficeMutationArtifactStore(tmp_path, retry_plan.campaign_id) as store:
        retried = await _runner(retry_provider, store, retry_state).run(
            plan=retry_plan, parent=_parent()
        )

    assert retried.status == OfficeMutationBatchStatus.DEGRADED
    assert retried.retryable_failure_count == 1
    assert len(retry_provider.calls) == 2

    fatal_plan = _plan("office-batch-http-400", requested_count=2)
    fatal_state = _CampaignState(fatal_plan.campaign_id)
    fatal_provider = _HttpThenSuccessProvider(400)
    with OfficeMutationArtifactStore(tmp_path, fatal_plan.campaign_id) as store:
        fatal = await _runner(fatal_provider, store, fatal_state).run(
            plan=fatal_plan, parent=_parent()
        )

    assert fatal.status == OfficeMutationBatchStatus.PAUSED
    assert fatal.pause_reason_code == "mutation_provider_permanent_http"
    assert len(fatal_provider.calls) == 1


@pytest.mark.asyncio
async def test_unexpected_provider_exception_is_audited_and_pauses_campaign(
    tmp_path: Path,
) -> None:
    plan = _plan("office-batch-unexpected", requested_count=2)
    campaign_state = _CampaignState(plan.campaign_id)
    provider = _UnexpectedProvider()
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        result = await _runner(provider, store, campaign_state).run(
            plan=plan, parent=_parent()
        )
        calls = store.provider_calls()

    assert result.status == OfficeMutationBatchStatus.PAUSED
    assert result.pause_reason_code == "mutation_provider_unclassified_failure"
    assert len(calls) == 1
    assert calls[0].error_kind == OfficeMutationProviderFailureKind.PROVIDER
    assert calls[0].response_summary == ""


@pytest.mark.asyncio
async def test_local_validation_integrity_failure_is_audited_before_pause(
    tmp_path: Path,
) -> None:
    plan = _plan("office-batch-local-integrity", requested_count=2)
    campaign_state = _CampaignState(plan.campaign_id)
    with OfficeMutationArtifactStore(tmp_path, plan.campaign_id) as store:
        runner = OfficeMutationBatchRunner(
            provider=_CountingRuleProvider(),
            validator=_IntegrityFailingValidator(),
            store=store,
            campaign_state=campaign_state,
            policy=OfficeMutationBatchPolicy.create(),
        )
        result = await runner.run(plan=plan, parent=_parent())
        calls = store.provider_calls()
        candidate_count = store.artifact_count("candidate")

    assert result.status == OfficeMutationBatchStatus.PAUSED
    assert result.pause_reason_code == "mutation_artifact_integrity_failure"
    assert len(calls) == 1
    assert calls[0].status.value == "failed"
    assert candidate_count == 0


def test_artifact_bundle_conflict_rolls_back_every_new_artifact(tmp_path: Path) -> None:
    with OfficeMutationArtifactStore(tmp_path, "office-batch-atomic") as store:
        store.save_artifact_bundle((("test", "locked", '{"value":1}'),))
        with pytest.raises(OfficeMutationIntegrityError):
            store.save_artifact_bundle(
                (
                    ("test", "new", '{"value":2}'),
                    ("test", "locked", '{"value":3}'),
                )
            )

        assert not store.has_artifact("test", "new")
        assert store.artifact_json("test", "locked") == '{"value":1}'


def test_request_digest_locks_parent_plan_and_sub_batch() -> None:
    plan = _plan("office-batch-request-digest", requested_count=2)
    policy = OfficeMutationBatchPolicy.create()
    first = OfficeMutationSubBatchRequest.create(
        plan=plan,
        policy=policy,
        path="0",
        ordinal_offset=0,
        retry_index=0,
        requested_count=2,
    )
    second = OfficeMutationSubBatchRequest.create(
        plan=plan,
        policy=policy,
        path="0",
        ordinal_offset=0,
        retry_index=1,
        requested_count=2,
    )

    assert office_mutation_sub_batch_request_digest(
        plan, _parent(), first
    ) != office_mutation_sub_batch_request_digest(plan, _parent(), second)
    assert first.content_digest == sha256_digest(
        first.model_dump(mode="json", exclude={"content_digest"})
    )
