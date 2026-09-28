"""Persistent sub-batch orchestration for office semantic mutation plans."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import Field, ValidationError, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.models import FrozenContract, Identifier, TestCase
from sandbox.scenarios.office_mutation import (
    OfficeMutationArtifactStore,
    OfficeMutationCandidate,
    OfficeMutationIntegrityError,
    OfficeMutationPlan,
    OfficeMutationProviderCall,
    OfficeMutationProviderCallStatus,
    OfficeMutationProviderError,
    OfficeMutationProviderFailureKind,
    OfficeMutationProviderIdentity,
    OfficeMutationProviderResult,
    OfficeMutationValidationRecord,
    OfficeMutationValidator,
    build_office_mutation_provider_call,
)

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_REQUEST_KIND = "mutation_batch_request"
_ATTEMPT_KIND = "mutation_batch_attempt"
_RUN_KIND = "mutation_batch_run"
_RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class OfficeMutationBatchStatus(StrEnum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    PARTIAL = "partial"
    NO_PROGRESS = "no_progress"
    PAUSED = "paused"


class OfficeMutationSubBatchOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    SHRINK_REQUIRED = "shrink_required"
    FATAL_FAILURE = "fatal_failure"


class OfficeMutationBatchPolicy(FrozenContract):
    policy_version: str = Field(default="office-mutation-batch-policy-v1", min_length=1)
    preferred_batch_size: int = Field(default=4, ge=2, le=4)
    provider_max_attempts: int = Field(default=2, ge=1, le=5)
    base_output_tokens: int = Field(default=128, ge=1, le=32_768)
    output_tokens_per_candidate: int = Field(default=160, ge=1, le=32_768)
    max_output_tokens: int = Field(default=2_048, ge=128, le=32_768)
    content_digest: str = Field(pattern=_DIGEST_PATTERN)

    @classmethod
    def create(
        cls,
        *,
        policy_version: str = "office-mutation-batch-policy-v1",
        preferred_batch_size: int = 4,
        provider_max_attempts: int = 2,
        base_output_tokens: int = 128,
        output_tokens_per_candidate: int = 160,
        max_output_tokens: int = 2_048,
    ) -> OfficeMutationBatchPolicy:
        payload = {
            "schema_version": "1.0",
            "policy_version": policy_version,
            "preferred_batch_size": preferred_batch_size,
            "provider_max_attempts": provider_max_attempts,
            "base_output_tokens": base_output_tokens,
            "output_tokens_per_candidate": output_tokens_per_candidate,
            "max_output_tokens": max_output_tokens,
        }
        return cls(content_digest=sha256_digest(payload), **payload)

    @model_validator(mode="after")
    def validate_policy(self) -> OfficeMutationBatchPolicy:
        if self.base_output_tokens + self.output_tokens_per_candidate < 128:
            raise ValueError("single-candidate output token formula is below protocol minimum")
        if self.base_output_tokens + self.output_tokens_per_candidate > 32_768:
            raise ValueError("single-candidate output token formula exceeds hard limit")
        expected = sha256_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest != expected:
            raise ValueError("office mutation batch policy digest mismatch")
        return self

    def tokens_for(self, plan: OfficeMutationPlan, requested_count: int) -> int:
        if not 1 <= requested_count <= 4:
            raise OfficeMutationIntegrityError(
                "office mutation sub-batch count must be between one and four"
            )
        return min(
            plan.max_output_tokens,
            self.max_output_tokens,
            self.base_output_tokens
            + self.output_tokens_per_candidate * requested_count,
        )


DEFAULT_OFFICE_MUTATION_BATCH_POLICY = OfficeMutationBatchPolicy.create()


class OfficeMutationSubBatchRequest(FrozenContract):
    request_id: Identifier
    plan_id: Identifier
    path: str = Field(min_length=1, max_length=128)
    ordinal_offset: int = Field(ge=0, le=3)
    retry_index: int = Field(ge=0, le=4)
    requested_count: int = Field(ge=1, le=4)
    random_seed: int = Field(ge=0)
    max_output_tokens: int = Field(ge=128, le=32_768)
    policy_version: str = Field(min_length=1, max_length=128)
    policy_digest: str = Field(pattern=_DIGEST_PATTERN)
    content_digest: str = Field(pattern=_DIGEST_PATTERN)

    @classmethod
    def create(
        cls,
        *,
        plan: OfficeMutationPlan,
        policy: OfficeMutationBatchPolicy,
        path: str,
        ordinal_offset: int,
        retry_index: int,
        requested_count: int,
    ) -> OfficeMutationSubBatchRequest:
        if ordinal_offset + requested_count > plan.requested_count:
            raise OfficeMutationIntegrityError(
                "office mutation sub-batch escapes the frozen candidate range"
            )
        seed_digest = sha256_digest(
            {
                "plan_seed": plan.random_seed,
                "path": path,
                "ordinal_offset": ordinal_offset,
                "retry_index": retry_index,
                "requested_count": requested_count,
            }
        )
        payload = {
            "schema_version": "1.0",
            "plan_id": plan.plan_id,
            "path": path,
            "ordinal_offset": ordinal_offset,
            "retry_index": retry_index,
            "requested_count": requested_count,
            "random_seed": int(seed_digest.removeprefix("sha256:")[:16], 16),
            "max_output_tokens": policy.tokens_for(plan, requested_count),
            "policy_version": policy.policy_version,
            "policy_digest": policy.content_digest,
        }
        identity = sha256_digest(payload)
        request_id = "office-subbatch-request-" + identity.removeprefix("sha256:")[:24]
        return cls(
            request_id=request_id,
            content_digest=sha256_digest({"request_id": request_id, **payload}),
            **payload,
        )

    @model_validator(mode="after")
    def validate_identity(self) -> OfficeMutationSubBatchRequest:
        projection = self.model_dump(
            mode="json", exclude={"request_id", "content_digest"}
        )
        expected_id = "office-subbatch-request-" + sha256_digest(
            projection
        ).removeprefix("sha256:")[:24]
        if self.request_id != expected_id:
            raise ValueError("office mutation sub-batch request identity mismatch")
        expected_digest = sha256_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest != expected_digest:
            raise ValueError("office mutation sub-batch request digest mismatch")
        return self


def office_mutation_sub_batch_request_digest(
    plan: OfficeMutationPlan,
    parent: TestCase,
    request: OfficeMutationSubBatchRequest,
) -> str:
    plan.assert_integrity()
    parent.assert_integrity()
    if (
        request.plan_id != plan.plan_id
        or plan.parent_case_id != parent.case_id
        or plan.parent_case_digest != parent.content_digest
    ):
        raise OfficeMutationIntegrityError(
            "office mutation sub-batch request lineage mismatch"
        )
    return sha256_digest(
        {
            "plan_digest": plan.content_digest,
            "parent_case_digest": parent.content_digest,
            "sub_batch_request_digest": request.content_digest,
            "provider_identity": plan.provider_identity,
        }
    )


class OfficeMutationSubBatchAttempt(FrozenContract):
    attempt_id: Identifier
    plan_id: Identifier
    request_id: Identifier
    provider_call_id: Identifier
    outcome: OfficeMutationSubBatchOutcome
    candidate_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    validation_record_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    accepted_child_case_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    error_kind: OfficeMutationProviderFailureKind | None = None
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)
    content_digest: str = Field(pattern=_DIGEST_PATTERN)

    @classmethod
    def create(
        cls,
        *,
        plan_id: str,
        request_id: str,
        provider_call_id: str,
        outcome: OfficeMutationSubBatchOutcome,
        candidates: Sequence[OfficeMutationCandidate] = (),
        validations: Sequence[OfficeMutationValidationRecord] = (),
        accepted_child_case_ids: Sequence[str] = (),
        error_kind: OfficeMutationProviderFailureKind | None = None,
        reason_code: str | None = None,
    ) -> OfficeMutationSubBatchAttempt:
        payload = {
            "schema_version": "1.0",
            "plan_id": plan_id,
            "request_id": request_id,
            "provider_call_id": provider_call_id,
            "outcome": outcome,
            "candidate_ids": tuple(item.candidate_id for item in candidates),
            "validation_record_ids": tuple(item.record_id for item in validations),
            "accepted_child_case_ids": tuple(accepted_child_case_ids),
            "error_kind": error_kind,
            "reason_code": reason_code,
        }
        identity = sha256_digest(payload)
        attempt_id = "office-subbatch-attempt-" + identity.removeprefix("sha256:")[:24]
        return cls(
            attempt_id=attempt_id,
            content_digest=sha256_digest({"attempt_id": attempt_id, **payload}),
            **payload,
        )

    @model_validator(mode="after")
    def validate_attempt(self) -> OfficeMutationSubBatchAttempt:
        succeeded = self.outcome == OfficeMutationSubBatchOutcome.SUCCEEDED
        if succeeded:
            if self.error_kind is not None or self.reason_code is not None:
                raise ValueError("successful sub-batch attempt cannot contain an error")
            if len(self.candidate_ids) != len(self.validation_record_ids):
                raise ValueError("successful sub-batch artifacts must remain aligned")
            if not self.candidate_ids:
                raise ValueError("successful sub-batch attempt requires generated artifacts")
        elif self.candidate_ids or self.validation_record_ids or self.accepted_child_case_ids:
            raise ValueError("failed sub-batch attempt cannot claim generated artifacts")
        elif self.error_kind is None or self.reason_code is None:
            raise ValueError("failed sub-batch attempt requires classified failure evidence")
        projection = self.model_dump(
            mode="json", exclude={"attempt_id", "content_digest"}
        )
        expected_id = "office-subbatch-attempt-" + sha256_digest(
            projection
        ).removeprefix("sha256:")[:24]
        if self.attempt_id != expected_id:
            raise ValueError("office mutation sub-batch attempt identity mismatch")
        expected_digest = sha256_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest != expected_digest:
            raise ValueError("office mutation sub-batch attempt digest mismatch")
        return self


class OfficeMutationBatchRunResult(FrozenContract):
    run_id: Identifier
    campaign_id: str = Field(min_length=1, max_length=256)
    plan_id: Identifier
    plan_digest: str = Field(pattern=_DIGEST_PATTERN)
    policy_version: str = Field(min_length=1, max_length=128)
    policy_digest: str = Field(pattern=_DIGEST_PATTERN)
    status: OfficeMutationBatchStatus
    requested_count: int = Field(ge=1, le=4)
    generated_count: int = Field(ge=0, le=4)
    request_ids: tuple[Identifier, ...]
    attempt_ids: tuple[Identifier, ...]
    candidate_ids: tuple[Identifier, ...]
    validation_record_ids: tuple[Identifier, ...]
    accepted_child_case_ids: tuple[Identifier, ...]
    retryable_failure_count: int = Field(ge=0)
    shrink_count: int = Field(ge=0)
    pause_reason_code: str | None = Field(default=None, min_length=1, max_length=128)
    pause_evidence_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    content_digest: str = Field(pattern=_DIGEST_PATTERN)

    @classmethod
    def create(
        cls,
        *,
        plan: OfficeMutationPlan,
        policy: OfficeMutationBatchPolicy,
        status: OfficeMutationBatchStatus,
        requests: Sequence[OfficeMutationSubBatchRequest],
        attempts: Sequence[OfficeMutationSubBatchAttempt],
        candidates: Sequence[OfficeMutationCandidate],
        validations: Sequence[OfficeMutationValidationRecord],
        accepted_child_case_ids: Sequence[str],
        retryable_failure_count: int,
        shrink_count: int,
        pause_reason_code: str | None,
        pause_evidence_digest: str | None,
    ) -> OfficeMutationBatchRunResult:
        identity = sha256_digest(
            {
                "plan_digest": plan.content_digest,
                "policy_digest": policy.content_digest,
            }
        )
        run_id = "office-mutation-batch-" + identity.removeprefix("sha256:")[:24]
        payload = {
            "schema_version": "1.0",
            "campaign_id": plan.campaign_id,
            "plan_id": plan.plan_id,
            "plan_digest": plan.content_digest,
            "policy_version": policy.policy_version,
            "policy_digest": policy.content_digest,
            "status": status,
            "requested_count": plan.requested_count,
            "generated_count": len(candidates),
            "request_ids": tuple(item.request_id for item in requests),
            "attempt_ids": tuple(item.attempt_id for item in attempts),
            "candidate_ids": tuple(item.candidate_id for item in candidates),
            "validation_record_ids": tuple(item.record_id for item in validations),
            "accepted_child_case_ids": tuple(accepted_child_case_ids),
            "retryable_failure_count": retryable_failure_count,
            "shrink_count": shrink_count,
            "pause_reason_code": pause_reason_code,
            "pause_evidence_digest": pause_evidence_digest,
        }
        return cls(
            run_id=run_id,
            content_digest=sha256_digest({"run_id": run_id, **payload}),
            **payload,
        )

    @model_validator(mode="after")
    def validate_run(self) -> OfficeMutationBatchRunResult:
        expected_id = "office-mutation-batch-" + sha256_digest(
            {
                "plan_digest": self.plan_digest,
                "policy_digest": self.policy_digest,
            }
        ).removeprefix("sha256:")[:24]
        if self.run_id != expected_id:
            raise ValueError("office mutation batch run identity mismatch")
        if len(self.candidate_ids) != self.generated_count:
            raise ValueError("batch generated_count does not match candidate artifacts")
        if len(self.validation_record_ids) != self.generated_count:
            raise ValueError("batch candidates and validations must remain aligned")
        if self.generated_count > self.requested_count:
            raise ValueError("batch result exceeds the frozen candidate count")
        paused = self.status == OfficeMutationBatchStatus.PAUSED
        if paused != bool(self.pause_reason_code and self.pause_evidence_digest):
            raise ValueError("paused batch requires exactly one pause reason and evidence")
        if self.status == OfficeMutationBatchStatus.COMPLETE and (
            self.generated_count != self.requested_count
            or self.retryable_failure_count
            or self.shrink_count
        ):
            raise ValueError("complete batch must finish without recovery degradation")
        if self.status == OfficeMutationBatchStatus.DEGRADED and (
            self.generated_count != self.requested_count
            or not (self.retryable_failure_count or self.shrink_count)
        ):
            raise ValueError("degraded batch must finish through retry or shrink recovery")
        if self.status == OfficeMutationBatchStatus.PARTIAL and not (
            0 < self.generated_count < self.requested_count
        ):
            raise ValueError("partial batch requires bounded partial progress")
        if (
            self.status == OfficeMutationBatchStatus.NO_PROGRESS
            and self.generated_count != 0
        ):
            raise ValueError("no-progress batch cannot contain generated candidates")
        expected_digest = sha256_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest != expected_digest:
            raise ValueError("office mutation batch result digest mismatch")
        return self


class OfficeMutationSubBatchProvider(Protocol):
    identity: OfficeMutationProviderIdentity

    async def mutate_sub_batch(
        self,
        plan: OfficeMutationPlan,
        parent: TestCase,
        request: OfficeMutationSubBatchRequest,
    ) -> OfficeMutationProviderResult: ...


class OfficeCampaignPauseController(Protocol):
    def snapshot(self) -> object: ...

    def pause_campaign(
        self, reason_code: str, *, evidence_digest: str | None = None
    ) -> object: ...


@dataclass
class _BatchAccumulator:
    requests: list[OfficeMutationSubBatchRequest] = field(default_factory=list)
    attempts: list[OfficeMutationSubBatchAttempt] = field(default_factory=list)
    candidates: list[OfficeMutationCandidate] = field(default_factory=list)
    validations: list[OfficeMutationValidationRecord] = field(default_factory=list)
    accepted_child_case_ids: list[str] = field(default_factory=list)
    retryable_failure_count: int = 0
    shrink_count: int = 0
    pause_reason_code: str | None = None
    pause_evidence_digest: str | None = None


class OfficeMutationBatchRunner:
    def __init__(
        self,
        *,
        provider: OfficeMutationSubBatchProvider,
        validator: OfficeMutationValidator,
        store: OfficeMutationArtifactStore,
        campaign_state: OfficeCampaignPauseController,
        policy: OfficeMutationBatchPolicy = DEFAULT_OFFICE_MUTATION_BATCH_POLICY,
    ) -> None:
        self.provider = provider
        self.validator = validator
        self.store = store
        self.campaign_state = campaign_state
        self.policy = policy

    async def run(
        self, *, plan: OfficeMutationPlan, parent: TestCase
    ) -> OfficeMutationBatchRunResult:
        plan.assert_integrity()
        self.validator.assert_plan_scope(plan=plan, parent=parent)
        snapshot = self.campaign_state.snapshot()
        if getattr(snapshot, "campaign_id", None) != plan.campaign_id:
            raise OfficeMutationIntegrityError(
                "office mutation Campaign state does not match the frozen plan"
            )
        self.store.save_plan(plan)
        run_id = self._run_id(plan)
        existing_run = self.store.artifact_json(_RUN_KIND, run_id)
        if existing_run is not None:
            return OfficeMutationBatchRunResult.model_validate_json(existing_run)

        accumulator = _BatchAccumulator()
        await self._execute_node(
            plan=plan,
            parent=parent,
            path="0",
            ordinal_offset=0,
            requested_count=plan.requested_count,
            accumulator=accumulator,
        )
        status = self._status(plan, accumulator)
        result = OfficeMutationBatchRunResult.create(
            plan=plan,
            policy=self.policy,
            status=status,
            requests=accumulator.requests,
            attempts=accumulator.attempts,
            candidates=accumulator.candidates,
            validations=accumulator.validations,
            accepted_child_case_ids=accumulator.accepted_child_case_ids,
            retryable_failure_count=accumulator.retryable_failure_count,
            shrink_count=accumulator.shrink_count,
            pause_reason_code=accumulator.pause_reason_code,
            pause_evidence_digest=accumulator.pause_evidence_digest,
        )
        self.store.save_artifact_bundle(
            ((_RUN_KIND, result.run_id, result.model_dump_json()),)
        )
        return result

    def _run_id(self, plan: OfficeMutationPlan) -> str:
        identity = sha256_digest(
            {
                "plan_digest": plan.content_digest,
                "policy_digest": self.policy.content_digest,
            }
        )
        return "office-mutation-batch-" + identity.removeprefix("sha256:")[:24]

    async def _execute_node(
        self,
        *,
        plan: OfficeMutationPlan,
        parent: TestCase,
        path: str,
        ordinal_offset: int,
        requested_count: int,
        accumulator: _BatchAccumulator,
    ) -> None:
        for retry_index in range(self.policy.provider_max_attempts):
            if accumulator.pause_reason_code is not None:
                return
            request = OfficeMutationSubBatchRequest.create(
                plan=plan,
                policy=self.policy,
                path=path,
                ordinal_offset=ordinal_offset,
                retry_index=retry_index,
                requested_count=requested_count,
            )
            self._append_once(accumulator.requests, request, "request_id")
            self.store.save_artifact_bundle(
                ((_REQUEST_KIND, request.request_id, request.model_dump_json()),)
            )
            existing_attempt = self._load_attempt(request)
            if existing_attempt is not None:
                self._append_once(accumulator.attempts, existing_attempt, "attempt_id")
                if await self._recover_attempt(
                    plan=plan,
                    parent=parent,
                    request=request,
                    attempt=existing_attempt,
                    accumulator=accumulator,
                ):
                    return
                continue

            attempt = await self._call_provider(
                plan=plan,
                parent=parent,
                request=request,
                accumulator=accumulator,
            )
            self._append_once(accumulator.attempts, attempt, "attempt_id")
            if attempt.outcome == OfficeMutationSubBatchOutcome.SUCCEEDED:
                return
            if attempt.outcome == OfficeMutationSubBatchOutcome.FATAL_FAILURE:
                self._pause(attempt, accumulator)
                return
            if attempt.outcome == OfficeMutationSubBatchOutcome.SHRINK_REQUIRED:
                accumulator.shrink_count += 1
                await self._split_node(
                    plan=plan,
                    parent=parent,
                    path=path,
                    ordinal_offset=ordinal_offset,
                    requested_count=requested_count,
                    accumulator=accumulator,
                )
                return
            accumulator.retryable_failure_count += 1

    async def _recover_attempt(
        self,
        *,
        plan: OfficeMutationPlan,
        parent: TestCase,
        request: OfficeMutationSubBatchRequest,
        attempt: OfficeMutationSubBatchAttempt,
        accumulator: _BatchAccumulator,
    ) -> bool:
        if attempt.outcome == OfficeMutationSubBatchOutcome.SUCCEEDED:
            self._load_success_artifacts(plan, parent, request, attempt, accumulator)
            return True
        if attempt.outcome == OfficeMutationSubBatchOutcome.FATAL_FAILURE:
            self._pause(attempt, accumulator)
            return True
        if attempt.outcome == OfficeMutationSubBatchOutcome.SHRINK_REQUIRED:
            accumulator.shrink_count += 1
            await self._split_node(
                plan=plan,
                parent=parent,
                path=request.path,
                ordinal_offset=request.ordinal_offset,
                requested_count=request.requested_count,
                accumulator=accumulator,
            )
            return True
        accumulator.retryable_failure_count += 1
        return False

    async def _split_node(
        self,
        *,
        plan: OfficeMutationPlan,
        parent: TestCase,
        path: str,
        ordinal_offset: int,
        requested_count: int,
        accumulator: _BatchAccumulator,
    ) -> None:
        if requested_count <= 1:
            return
        left_count = requested_count // 2
        right_count = requested_count - left_count
        await self._execute_node(
            plan=plan,
            parent=parent,
            path=f"{path}.0",
            ordinal_offset=ordinal_offset,
            requested_count=left_count,
            accumulator=accumulator,
        )
        if accumulator.pause_reason_code is None:
            await self._execute_node(
                plan=plan,
                parent=parent,
                path=f"{path}.1",
                ordinal_offset=ordinal_offset + left_count,
                requested_count=right_count,
                accumulator=accumulator,
            )

    async def _call_provider(
        self,
        *,
        plan: OfficeMutationPlan,
        parent: TestCase,
        request: OfficeMutationSubBatchRequest,
        accumulator: _BatchAccumulator,
    ) -> OfficeMutationSubBatchAttempt:
        expected_digest = office_mutation_sub_batch_request_digest(plan, parent, request)
        if self.provider.identity != plan.provider_identity:
            return self._save_failure(
                plan=plan,
                request=request,
                request_digest=expected_digest,
                kind=OfficeMutationProviderFailureKind.MODEL_MISMATCH,
                reason_code="mutation_provider_identity_mismatch",
                detail="runtime provider identity differs from frozen MutationPlan",
                outcome=OfficeMutationSubBatchOutcome.FATAL_FAILURE,
            )
        try:
            result = await self.provider.mutate_sub_batch(plan, parent, request)
        except OfficeMutationProviderError as exc:
            kind = (
                exc.kind
                if isinstance(exc.kind, OfficeMutationProviderFailureKind)
                else OfficeMutationProviderFailureKind.PROVIDER
            )
            outcome, reason = self._classify_provider_error(exc, request.requested_count)
            if kind != exc.kind:
                outcome = OfficeMutationSubBatchOutcome.FATAL_FAILURE
                reason = "mutation_provider_unclassified_failure"
            supplied_digest = exc.request_digest or expected_digest
            detail = str(exc)
            if supplied_digest != expected_digest:
                outcome = OfficeMutationSubBatchOutcome.FATAL_FAILURE
                reason = "mutation_provider_request_digest_mismatch"
                supplied_digest = expected_digest
                detail = f"{detail}; Provider supplied a mismatched request digest"
            return self._save_failure(
                plan=plan,
                request=request,
                request_digest=supplied_digest,
                kind=kind,
                reason_code=reason,
                detail=detail,
                outcome=outcome,
                response_digest=self._safe_digest(exc.response_digest),
                response_bytes=self._safe_non_negative_int(exc.response_bytes),
                response_summary=str(exc.response_summary),
                http_status=self._safe_http_status(exc.http_status),
                done_reason=self._safe_optional_text(exc.done_reason, 128),
            )
        except Exception as exc:
            return self._save_failure(
                plan=plan,
                request=request,
                request_digest=expected_digest,
                kind=OfficeMutationProviderFailureKind.PROVIDER,
                reason_code="mutation_provider_unclassified_failure",
                detail=f"unexpected provider failure: {type(exc).__name__}: {exc}",
                outcome=OfficeMutationSubBatchOutcome.FATAL_FAILURE,
            )
        if result.request_digest != expected_digest:
            return self._save_failure(
                plan=plan,
                request=request,
                request_digest=result.request_digest,
                kind=OfficeMutationProviderFailureKind.MODEL_MISMATCH,
                reason_code="mutation_provider_request_digest_mismatch",
                detail="provider response does not match the frozen sub-batch request",
                outcome=OfficeMutationSubBatchOutcome.FATAL_FAILURE,
                response_digest=result.response_digest,
                response_bytes=result.response_bytes,
                done_reason=result.done_reason,
            )
        if len(result.candidates) != request.requested_count:
            return self._save_failure(
                plan=plan,
                request=request,
                request_digest=result.request_digest,
                kind=OfficeMutationProviderFailureKind.INVALID_SCHEMA,
                reason_code="mutation_provider_candidate_count_mismatch",
                detail="provider candidate count differs from the frozen sub-batch count",
                outcome=OfficeMutationSubBatchOutcome.FATAL_FAILURE,
                response_digest=result.response_digest,
                response_bytes=result.response_bytes,
                done_reason=result.done_reason,
            )
        try:
            return self._save_success(
                plan=plan,
                parent=parent,
                request=request,
                result=result,
                accumulator=accumulator,
            )
        except OfficeMutationIntegrityError as exc:
            return self._save_failure(
                plan=plan,
                request=request,
                request_digest=result.request_digest,
                kind=OfficeMutationProviderFailureKind.PROVIDER,
                reason_code="mutation_artifact_integrity_failure",
                detail=f"local mutation integrity failure: {exc}",
                outcome=OfficeMutationSubBatchOutcome.FATAL_FAILURE,
                response_digest=result.response_digest,
                response_bytes=result.response_bytes,
                done_reason=result.done_reason,
            )
        except (ValidationError, ValueError) as exc:
            return self._save_failure(
                plan=plan,
                request=request,
                request_digest=result.request_digest,
                kind=OfficeMutationProviderFailureKind.INVALID_SCHEMA,
                reason_code="mutation_provider_invalid_schema",
                detail=f"provider candidate validation failed: {type(exc).__name__}: {exc}",
                outcome=OfficeMutationSubBatchOutcome.FATAL_FAILURE,
                response_digest=result.response_digest,
                response_bytes=result.response_bytes,
                done_reason=result.done_reason,
            )
        except Exception as exc:
            return self._save_failure(
                plan=plan,
                request=request,
                request_digest=result.request_digest,
                kind=OfficeMutationProviderFailureKind.PROVIDER,
                reason_code="mutation_validation_unclassified_failure",
                detail=f"unexpected local validation failure: {type(exc).__name__}: {exc}",
                outcome=OfficeMutationSubBatchOutcome.FATAL_FAILURE,
                response_digest=result.response_digest,
                response_bytes=result.response_bytes,
                done_reason=result.done_reason,
            )

    def _save_success(
        self,
        *,
        plan: OfficeMutationPlan,
        parent: TestCase,
        request: OfficeMutationSubBatchRequest,
        result: OfficeMutationProviderResult,
        accumulator: _BatchAccumulator,
    ) -> OfficeMutationSubBatchAttempt:
        call = build_office_mutation_provider_call(
            plan=plan,
            request_digest=result.request_digest,
            status=OfficeMutationProviderCallStatus.SUCCEEDED,
            response_digest=result.response_digest,
            response_bytes=result.response_bytes,
            generated_count=len(result.candidates),
            done_reason=result.done_reason,
            prompt_eval_count=result.prompt_eval_count,
            eval_count=result.eval_count,
        )
        candidates = tuple(
            OfficeMutationCandidate.create(
                plan_id=plan.plan_id,
                ordinal=request.ordinal_offset + candidate.ordinal,
                scenario_template_id=candidate.scenario_template_id,
                task_id=candidate.task_id,
                objective_id=candidate.objective_id,
                carrier_id=candidate.carrier_id,
                expression=candidate.expression,
                claimed_operator_id=candidate.claimed_operator_id,
                claimed_expected_path=candidate.claimed_expected_path,
            )
            for candidate in result.candidates
        )
        known_expressions = self._known_expression_digests(plan, accumulator)
        validations: list[OfficeMutationValidationRecord] = []
        accepted_ids: list[str] = []
        for candidate in candidates:
            record = self.validator.validate(
                plan=plan,
                candidate=candidate,
                provider_call=call,
                parent=parent,
                known_expression_digests=known_expressions,
            )
            validations.append(record)
            if record.child_case is not None:
                known_expressions.add(record.actual_components.expression_digest)
                accepted_ids.append(record.child_case.case_id)
        attempt = OfficeMutationSubBatchAttempt.create(
            plan_id=plan.plan_id,
            request_id=request.request_id,
            provider_call_id=call.call_id,
            outcome=OfficeMutationSubBatchOutcome.SUCCEEDED,
            candidates=candidates,
            validations=validations,
            accepted_child_case_ids=accepted_ids,
        )
        artifacts = [("provider_call", call.call_id, call.model_dump_json())]
        artifacts.extend(
            ("candidate", item.candidate_id, item.model_dump_json())
            for item in candidates
        )
        artifacts.extend(
            ("validation", item.record_id, item.model_dump_json())
            for item in validations
        )
        artifacts.append((_ATTEMPT_KIND, attempt.attempt_id, attempt.model_dump_json()))
        self.store.save_artifact_bundle(tuple(artifacts))
        self._merge_success(candidates, tuple(validations), accepted_ids, accumulator)
        return attempt

    def _save_failure(
        self,
        *,
        plan: OfficeMutationPlan,
        request: OfficeMutationSubBatchRequest,
        request_digest: str,
        kind: OfficeMutationProviderFailureKind,
        reason_code: str,
        detail: str,
        outcome: OfficeMutationSubBatchOutcome,
        response_digest: str | None = None,
        response_bytes: int | None = None,
        response_summary: str = "",
        http_status: int | None = None,
        done_reason: str | None = None,
    ) -> OfficeMutationSubBatchAttempt:
        call = build_office_mutation_provider_call(
            plan=plan,
            request_digest=request_digest,
            status=OfficeMutationProviderCallStatus.FAILED,
            response_digest=response_digest,
            response_bytes=response_bytes,
            error_kind=kind,
            retryable=outcome
            in {
                OfficeMutationSubBatchOutcome.RETRYABLE_FAILURE,
                OfficeMutationSubBatchOutcome.SHRINK_REQUIRED,
            },
            http_status=http_status,
            done_reason=done_reason,
            response_summary=response_summary[:1_000],
            error_detail=detail,
        )
        attempt = OfficeMutationSubBatchAttempt.create(
            plan_id=plan.plan_id,
            request_id=request.request_id,
            provider_call_id=call.call_id,
            outcome=outcome,
            error_kind=kind,
            reason_code=reason_code,
        )
        self.store.save_artifact_bundle(
            (
                ("provider_call", call.call_id, call.model_dump_json()),
                (_ATTEMPT_KIND, attempt.attempt_id, attempt.model_dump_json()),
            )
        )
        return attempt

    def _load_attempt(
        self, request: OfficeMutationSubBatchRequest
    ) -> OfficeMutationSubBatchAttempt | None:
        matches = [
            OfficeMutationSubBatchAttempt.model_validate_json(raw)
            for raw in self.store.artifact_jsons(_ATTEMPT_KIND)
            if OfficeMutationSubBatchAttempt.model_validate_json(raw).request_id
            == request.request_id
        ]
        if len(matches) > 1:
            raise OfficeMutationIntegrityError(
                "office mutation request has conflicting persisted attempts"
            )
        return matches[0] if matches else None

    def _load_success_artifacts(
        self,
        plan: OfficeMutationPlan,
        parent: TestCase,
        request: OfficeMutationSubBatchRequest,
        attempt: OfficeMutationSubBatchAttempt,
        accumulator: _BatchAccumulator,
    ) -> None:
        call_raw = self.store.artifact_json("provider_call", attempt.provider_call_id)
        if call_raw is None:
            raise OfficeMutationIntegrityError("persisted sub-batch call artifact is missing")
        call = OfficeMutationProviderCall.model_validate_json(call_raw)
        expected_request_digest = office_mutation_sub_batch_request_digest(
            plan, parent, request
        )
        if (
            call.status != OfficeMutationProviderCallStatus.SUCCEEDED
            or call.request_digest != expected_request_digest
        ):
            raise OfficeMutationIntegrityError(
                "persisted sub-batch call does not match its frozen request"
            )
        candidates = []
        validations = []
        for candidate_id, record_id in zip(
            attempt.candidate_ids, attempt.validation_record_ids, strict=True
        ):
            candidate_raw = self.store.artifact_json("candidate", candidate_id)
            validation_raw = self.store.artifact_json("validation", record_id)
            if candidate_raw is None or validation_raw is None:
                raise OfficeMutationIntegrityError(
                    "persisted successful sub-batch is missing generated artifacts"
                )
            candidate = OfficeMutationCandidate.model_validate_json(candidate_raw)
            validation = OfficeMutationValidationRecord.model_validate_json(validation_raw)
            if (
                validation.candidate_id != candidate.candidate_id
                or validation.provider_call_id != call.call_id
                or candidate.plan_id != plan.plan_id
            ):
                raise OfficeMutationIntegrityError(
                    "persisted successful sub-batch lineage is inconsistent"
                )
            candidates.append(candidate)
            validations.append(validation)
        expected_ordinals = list(
            range(request.ordinal_offset, request.ordinal_offset + request.requested_count)
        )
        if [item.ordinal for item in candidates] != expected_ordinals:
            raise OfficeMutationIntegrityError(
                "persisted sub-batch candidates do not cover the frozen ordinal range"
            )
        self._merge_success(
            tuple(candidates),
            tuple(validations),
            attempt.accepted_child_case_ids,
            accumulator,
        )

    @staticmethod
    def _merge_success(
        candidates: Sequence[OfficeMutationCandidate],
        validations: Sequence[OfficeMutationValidationRecord],
        accepted_ids: Sequence[str],
        accumulator: _BatchAccumulator,
    ) -> None:
        for candidate in candidates:
            OfficeMutationBatchRunner._append_once(
                accumulator.candidates, candidate, "candidate_id"
            )
        for validation in validations:
            OfficeMutationBatchRunner._append_once(
                accumulator.validations, validation, "record_id"
            )
        for case_id in accepted_ids:
            if case_id not in accumulator.accepted_child_case_ids:
                accumulator.accepted_child_case_ids.append(case_id)

    def _known_expression_digests(
        self, plan: OfficeMutationPlan, accumulator: _BatchAccumulator
    ) -> set[str]:
        known = {plan.before_components.expression_digest} - {None}
        known.update(
            item.actual_components.expression_digest
            for item in accumulator.validations
            if item.plan_id == plan.plan_id
        )
        return known

    @staticmethod
    def _classify_provider_error(
        error: OfficeMutationProviderError, requested_count: int
    ) -> tuple[OfficeMutationSubBatchOutcome, str]:
        if error.kind in {
            OfficeMutationProviderFailureKind.TRUNCATED,
            OfficeMutationProviderFailureKind.RESPONSE_TOO_LARGE,
        }:
            if requested_count > 1:
                return (
                    OfficeMutationSubBatchOutcome.SHRINK_REQUIRED,
                    "mutation_provider_response_requires_shrink",
                )
            return (
                OfficeMutationSubBatchOutcome.RETRYABLE_FAILURE,
                "mutation_provider_singleton_truncation",
            )
        if error.kind in {
            OfficeMutationProviderFailureKind.TRANSPORT,
            OfficeMutationProviderFailureKind.TIMEOUT,
        } and error.recoverable:
            return (
                OfficeMutationSubBatchOutcome.RETRYABLE_FAILURE,
                "mutation_provider_transient_failure",
            )
        if (
            error.kind == OfficeMutationProviderFailureKind.HTTP
            and error.http_status in _RETRYABLE_HTTP_STATUSES
        ):
            return (
                OfficeMutationSubBatchOutcome.RETRYABLE_FAILURE,
                "mutation_provider_transient_http",
            )
        reason_by_kind = {
            OfficeMutationProviderFailureKind.INVALID_JSON: "mutation_provider_invalid_json",
            OfficeMutationProviderFailureKind.INVALID_SCHEMA: "mutation_provider_invalid_schema",
            OfficeMutationProviderFailureKind.MODEL_MISMATCH: "mutation_provider_model_mismatch",
            OfficeMutationProviderFailureKind.HTTP: "mutation_provider_permanent_http",
            OfficeMutationProviderFailureKind.TRANSPORT: "mutation_provider_permanent_transport",
            OfficeMutationProviderFailureKind.TIMEOUT: "mutation_provider_permanent_timeout",
            OfficeMutationProviderFailureKind.PROVIDER: "mutation_provider_permanent_failure",
        }
        return (
            OfficeMutationSubBatchOutcome.FATAL_FAILURE,
            reason_by_kind.get(error.kind, "mutation_provider_unclassified_failure"),
        )

    @staticmethod
    def _safe_digest(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        if len(value) != 71 or not value.startswith("sha256:"):
            return None
        try:
            int(value.removeprefix("sha256:"), 16)
        except ValueError:
            return None
        return value.lower()

    @staticmethod
    def _safe_non_negative_int(value: object) -> int | None:
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else None
        )

    @staticmethod
    def _safe_http_status(value: object) -> int | None:
        return (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and 100 <= value <= 599
            else None
        )

    @staticmethod
    def _safe_optional_text(value: object, limit: int) -> str | None:
        if value is None:
            return None
        return str(value)[:limit]

    def _pause(
        self,
        attempt: OfficeMutationSubBatchAttempt,
        accumulator: _BatchAccumulator,
    ) -> None:
        if attempt.reason_code is None:
            raise OfficeMutationIntegrityError("fatal mutation attempt lacks pause reason")
        self.campaign_state.pause_campaign(
            attempt.reason_code, evidence_digest=attempt.content_digest
        )
        accumulator.pause_reason_code = attempt.reason_code
        accumulator.pause_evidence_digest = attempt.content_digest

    @staticmethod
    def _status(
        plan: OfficeMutationPlan, accumulator: _BatchAccumulator
    ) -> OfficeMutationBatchStatus:
        if accumulator.pause_reason_code is not None:
            return OfficeMutationBatchStatus.PAUSED
        if len(accumulator.candidates) == plan.requested_count:
            if accumulator.retryable_failure_count or accumulator.shrink_count:
                return OfficeMutationBatchStatus.DEGRADED
            return OfficeMutationBatchStatus.COMPLETE
        if accumulator.candidates:
            return OfficeMutationBatchStatus.PARTIAL
        return OfficeMutationBatchStatus.NO_PROGRESS

    @staticmethod
    def _append_once(items: list, item: object, identity_field: str) -> None:
        identity = getattr(item, identity_field)
        if all(getattr(existing, identity_field) != identity for existing in items):
            items.append(item)
