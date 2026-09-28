"""Two-phase candidate work and immutable attempt receipts."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_agent_behavior import AgentBehaviorAssessment
from .v2_corpus import ExecutionCosts, ExecutionRecord
from .v2_promotion import PromotionDecision
from .v2_target_preservation import TargetPreservationAssessment


class CandidateWorkState(StrEnum):
    ALLOCATED = "allocated"
    EXECUTING = "executing"
    SEALED = "sealed"
    AMBIGUOUS = "ambiguous"
    COMMITTED = "committed"
    FAILED = "failed"


class AttemptDisposition(StrEnum):
    SUCCEEDED = "succeeded"
    RETRYABLE = "retryable"
    PERMANENT_FAILURE = "permanent-failure"
    UNKNOWN_FAILURE = "unknown-failure"
    AMBIGUOUS = "ambiguous"


RETRYABLE_ERROR_CODES = frozenset(
    {
        "connect-timeout",
        "connection-error",
        "provider-429",
        "provider-500",
        "provider-502",
        "provider-503",
        "provider-504",
        "response-truncated",
        "episode-timeout",
        "episode-runtime-transport",
        "episode-runtime-infrastructure",
        "episode-cleanup",
        "episode-execution-failed",
    }
)


class BudgetReservation(OfficeV2Contract):
    mutator_tokens: int = Field(default=0, ge=0)
    agent_tokens: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)
    monetary_microunits: int = Field(default=0, ge=0)


class CandidateWork(OfficeV2Contract):
    work_id: Identifier
    campaign_id: Identifier
    generation_allocation_id: Identifier
    generation_allocation_digest: Sha256Digest
    comparison_context_digest: Sha256Digest
    baseline_snapshot_digest: Sha256Digest
    max_attempts: int = Field(default=2, ge=1)
    budget_reservation: BudgetReservation
    state: CandidateWorkState = CandidateWorkState.ALLOCATED
    attempt_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    sealed_execution_record_id: Identifier | None = None
    work_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"work_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def state_and_digest_match(self) -> Self:
        if len(self.attempt_ids) > self.max_attempts:
            raise ValueError("candidate work exceeds bounded attempts")
        if (
            self.state in {CandidateWorkState.SEALED, CandidateWorkState.COMMITTED}
            and self.sealed_execution_record_id is None
        ):
            raise ValueError("sealed work requires execution record")
        if self.work_digest != sha256_digest(self.digest_payload()):
            raise ValueError("candidate work digest does not match")
        return self


class AttemptReceipt(OfficeV2Contract):
    attempt_id: Identifier
    work_id: Identifier
    attempt_number: int = Field(ge=1)
    disposition: AttemptDisposition
    error_code: Identifier | None = None
    response_digest: Sha256Digest | None = None
    response_byte_count: int = Field(default=0, ge=0)
    response_truncated: bool = False
    http_status: int | None = Field(default=None, ge=100, le=599)
    bounded_summary: str = Field(default="", max_length=500)
    costs: ExecutionCosts
    receipt_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def disposition_and_digest_match(self) -> Self:
        if self.disposition is AttemptDisposition.SUCCEEDED and self.error_code is not None:
            raise ValueError("successful attempt cannot carry error code")
        if self.disposition is not AttemptDisposition.SUCCEEDED and self.error_code is None:
            raise ValueError("failed attempt requires error code")
        if (
            self.disposition is AttemptDisposition.RETRYABLE
            and self.error_code not in RETRYABLE_ERROR_CODES
        ):
            raise ValueError("retryable attempt uses non-whitelisted error")
        if self.receipt_digest != sha256_digest(self.digest_payload()):
            raise ValueError("attempt receipt digest does not match")
        return self


class CandidateSettlement(OfficeV2Contract):
    settlement_id: Identifier
    work_id: Identifier
    attempt_receipt_ids: tuple[Identifier, ...] = Field(min_length=1)
    execution_record_id: Identifier
    execution_record: ExecutionRecord
    coverage_delta_digest: Sha256Digest
    next_coverage_snapshot_digest: Sha256Digest
    promotion_decision: PromotionDecision
    promotion_decision_digest: Sha256Digest
    behavior_assessment: AgentBehaviorAssessment
    behavior_assessment_digest: Sha256Digest
    target_preservation: TargetPreservationAssessment
    target_preservation_digest: Sha256Digest
    corpus_entry_id: Identifier | None = None
    corpus_snapshot_digest: Sha256Digest
    budget_digest: Sha256Digest
    lifecycle_digest: Sha256Digest
    next_campaign_state_digest: Sha256Digest
    settlement_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"settlement_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.execution_record_id != self.execution_record.execution_record_id:
            raise ValueError("candidate settlement execution record identity differs")
        if tuple(sorted(self.attempt_receipt_ids)) != self.execution_record.attempt_receipt_ids:
            raise ValueError("candidate settlement execution attempt lineage differs")
        if self.behavior_assessment_digest != self.behavior_assessment.assessment_digest:
            raise ValueError("candidate settlement behavior assessment does not match digest")
        if (
            self.behavior_assessment.scenario_case_id != self.execution_record.scenario_case_id
            or self.behavior_assessment.actor_id != self.execution_record.actor_id
            or self.behavior_assessment.task_id != self.execution_record.task_id
            or self.behavior_assessment.initial_state_digest
            != self.execution_record.initial_state_digest
            or self.behavior_assessment.final_state_digest
            != self.execution_record.final_state_digest
            or self.behavior_assessment.evidence_bundle_digest
            != self.execution_record.evidence_bundle_digest
            or self.behavior_assessment.behavior_source_digest
            != self.execution_record.behavior_source_digest
            or self.behavior_assessment.canonical_fact_digest
            != self.execution_record.episode_digest
            or self.behavior_assessment.acquisition_source_digest
            != self.execution_record.manifest_digest
            or self.behavior_assessment.oracle_fact_digest
            != self.execution_record.oracle_fact_digest
            or self.behavior_assessment.oracle_result_digest
            != self.execution_record.oracle_result_digest
        ):
            raise ValueError("candidate settlement behavior assessment belongs to another Episode")
        if self.promotion_decision_digest != sha256_digest(
            self.promotion_decision.model_dump(mode="json", exclude_none=False)
        ):
            raise ValueError("candidate settlement promotion decision does not match digest")
        if self.target_preservation_digest != self.target_preservation.assessment_digest:
            raise ValueError("candidate settlement target preservation does not match digest")
        if self.settlement_digest != sha256_digest(self.digest_payload()):
            raise ValueError("candidate settlement digest does not match")
        return self


def seal_work_contract(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(**payload, **{digest_field: "sha256:" + "0" * 64})
    return model_type(**payload, **{digest_field: sha256_digest(draft.digest_payload())})


def retry_allowed(*, work: CandidateWork, receipts: tuple[AttemptReceipt, ...]) -> bool:
    if work.state in {
        CandidateWorkState.SEALED,
        CandidateWorkState.COMMITTED,
        CandidateWorkState.AMBIGUOUS,
    }:
        return False
    if not receipts or len(receipts) >= work.max_attempts:
        return False
    last = receipts[-1]
    return last.disposition is AttemptDisposition.RETRYABLE


def total_attempt_costs(receipts: tuple[AttemptReceipt, ...]) -> ExecutionCosts:
    return ExecutionCosts(
        mutator_tokens=sum(item.costs.mutator_tokens for item in receipts),
        agent_tokens=sum(item.costs.agent_tokens for item in receipts),
        elapsed_ms=sum(item.costs.elapsed_ms for item in receipts),
        monetary_microunits=sum(item.costs.monetary_microunits for item in receipts),
    )


__all__ = [
    "AttemptDisposition",
    "AttemptReceipt",
    "BudgetReservation",
    "CandidateSettlement",
    "CandidateWork",
    "CandidateWorkState",
    "RETRYABLE_ERROR_CODES",
    "retry_allowed",
    "seal_work_contract",
    "total_attempt_costs",
]
