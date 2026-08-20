"""Immutable handoff and settlement contracts for the Office V2 feedback loop."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from sandbox.mutation.v2_preparation import (
    MutationPreparation,
    MutationPreparationState,
)
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.oracle_models import (
    CompleteScenarioOracleResult,
    ExposureStage,
)

from .v2_campaign_state import V2CampaignStateSnapshot
from .v2_corpus import ExecutionCosts, MaterializedCandidate, PayloadExecutionRef
from .v2_scheduler import GenerationAllocation
from .v2_work import BudgetReservation, CandidateWork, seal_work_contract


class NonEpisodeDisposition(StrEnum):
    PREPARATION_REJECTED = "preparation_rejected"
    PREPARATION_PAUSED = "preparation_paused"
    WORK_PERMANENT_FAILURE = "work_permanent_failure"
    WORK_AMBIGUOUS_FAILURE = "work_ambiguous_failure"
    CANCELLED_BEFORE_EXECUTION = "cancelled_before_execution"


class MutationBudgetReservation(OfficeV2Contract):
    reservation_id: Identifier
    campaign_id: Identifier
    generation_allocation_id: Identifier
    mutation_plan_digest: Sha256Digest
    reserved_tokens: int = Field(gt=0)
    reserved_cost_microunits: int = Field(ge=0)
    reservation_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"reservation_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.reservation_digest != sha256_digest(self.digest_payload()):
            raise ValueError("mutation budget reservation digest does not match")
        return self


class PreparationCostSettlement(OfficeV2Contract):
    settlement_id: Identifier
    campaign_id: Identifier
    reservation_id: Identifier
    preparation_id: Identifier
    preparation_digest: Sha256Digest
    actual_costs: ExecutionCosts
    released_tokens: int = Field(ge=0)
    released_cost_microunits: int = Field(ge=0)
    settlement_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"settlement_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.settlement_digest != sha256_digest(self.digest_payload()):
            raise ValueError("preparation cost settlement digest does not match")
        return self


class NonEpisodeSettlement(OfficeV2Contract):
    settlement_id: Identifier
    campaign_id: Identifier
    generation_allocation_id: Identifier
    preparation_id: Identifier | None = None
    preparation_outcome_digest: Sha256Digest | None = None
    work_id: Identifier | None = None
    attempt_receipt_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    disposition: NonEpisodeDisposition
    actual_costs: ExecutionCosts
    released_reservation: BudgetReservation = Field(default_factory=BudgetReservation)
    invalid_candidate_delta: int = Field(default=0, ge=0)
    operator_rejection_delta: int = Field(default=0, ge=0)
    scheduling_decision_delta: int = Field(default=1, ge=1)
    previous_coverage_digest: Sha256Digest
    next_coverage_digest: Sha256Digest
    previous_exposure_digest: Sha256Digest
    next_exposure_digest: Sha256Digest
    previous_corpus_digest: Sha256Digest
    next_corpus_digest: Sha256Digest
    previous_frontier_digest: Sha256Digest
    next_frontier_digest: Sha256Digest
    next_budget_digest: Sha256Digest
    next_lifecycle_digest: Sha256Digest
    next_state_digest: Sha256Digest
    settlement_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"settlement_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def no_episode_facts_and_digest_match(self) -> Self:
        if self.previous_coverage_digest != self.next_coverage_digest:
            raise ValueError("non-Episode settlement cannot change Coverage")
        if self.previous_exposure_digest != self.next_exposure_digest:
            raise ValueError("non-Episode settlement cannot change Exposure")
        if self.previous_corpus_digest != self.next_corpus_digest:
            raise ValueError("non-Episode settlement cannot change Corpus")
        if self.previous_frontier_digest != self.next_frontier_digest:
            raise ValueError("non-Episode settlement cannot change Frontiers")
        if self.disposition in {
            NonEpisodeDisposition.PREPARATION_REJECTED,
            NonEpisodeDisposition.PREPARATION_PAUSED,
        } and self.preparation_id is None:
            raise ValueError("preparation settlement requires preparation identity")
        if self.settlement_digest != sha256_digest(self.digest_payload()):
            raise ValueError("non-Episode settlement digest does not match")
        return self


class ExecutionHandoff(OfficeV2Contract):
    handoff_id: Identifier
    campaign_id: Identifier
    generation_index: int = Field(ge=0)
    generation_allocation_id: Identifier
    generation_allocation_digest: Sha256Digest
    preparation_id: Identifier
    preparation_digest: Sha256Digest
    materialized_candidate_id: Identifier
    materialization_digest: Sha256Digest
    parent_seed_id: Identifier
    supporting_execution_record_id: Identifier
    binding_source_digest: Sha256Digest
    comparison_context_digest: Sha256Digest
    baseline_snapshot_digest: Sha256Digest
    handoff_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"handoff_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.handoff_digest != sha256_digest(self.digest_payload()):
            raise ValueError("execution handoff digest does not match")
        return self


class ExecutionClosure(OfficeV2Contract):
    closure_id: Identifier
    work_id: Identifier
    execution_id: Identifier
    materialized_candidate_id: Identifier
    trace_digest: Sha256Digest
    manifest_digest: Sha256Digest
    oracle_fact_digest: Sha256Digest
    initial_state_digest: Sha256Digest
    final_state_digest: Sha256Digest
    observed_payload_refs: tuple[PayloadExecutionRef, ...] = Field(default_factory=tuple)
    used_payload_refs: tuple[PayloadExecutionRef, ...] = Field(default_factory=tuple)
    submitted: bool
    termination_reason: Identifier
    cleanup_confirmed: bool
    closure_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"closure_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def evidence_and_digest_match(self) -> Self:
        observed = {item.payload_spec_id for item in self.observed_payload_refs}
        used = {item.payload_spec_id for item in self.used_payload_refs}
        if not used.issubset(observed):
            raise ValueError("used payloads must first be observed")
        if not self.submitted and self.termination_reason == "submit":
            raise ValueError("submit termination requires submitted=true")
        if self.closure_digest != sha256_digest(self.digest_payload()):
            raise ValueError("execution closure digest does not match")
        return self


def build_mutation_budget_reservation(
    *, campaign_id: str, allocation: GenerationAllocation, mutation_plan_digest: str,
    reserved_tokens: int, reserved_cost_microunits: int,
) -> MutationBudgetReservation:
    key = {"campaign": campaign_id, "allocation": allocation.allocation_digest,
           "plan": mutation_plan_digest}
    return seal_work_contract(
        MutationBudgetReservation,
        {
            "reservation_id": "mutation-reservation."
            + sha256_digest(key).removeprefix("sha256:")[:24],
            "campaign_id": campaign_id,
            "generation_allocation_id": allocation.generation_allocation_id,
            "mutation_plan_digest": mutation_plan_digest,
            "reserved_tokens": reserved_tokens,
            "reserved_cost_microunits": reserved_cost_microunits,
        },
        "reservation_digest",
    )


def build_preparation_cost_settlement(
    *, reservation: MutationBudgetReservation, preparation: MutationPreparation,
) -> PreparationCostSettlement:
    if preparation.campaign_id != reservation.campaign_id:
        raise ValueError("preparation and reservation use different Campaigns")
    if preparation.plan.plan_digest != reservation.mutation_plan_digest:
        raise ValueError("preparation and reservation use different MutationPlans")
    if preparation.outcome is None:
        raise ValueError("preparation cost can only settle a terminal preparation")
    outcome = preparation.outcome
    actual_tokens = outcome.actual_input_tokens + outcome.actual_output_tokens
    if actual_tokens > reservation.reserved_tokens:
        raise ValueError("preparation token cost exceeds reservation")
    if outcome.actual_cost_microunits > reservation.reserved_cost_microunits:
        raise ValueError("preparation monetary cost exceeds reservation")
    return seal_work_contract(
        PreparationCostSettlement,
        {
            "settlement_id": "preparation-settlement."
            + preparation.preparation_digest.removeprefix("sha256:")[:24],
            "campaign_id": reservation.campaign_id,
            "reservation_id": reservation.reservation_id,
            "preparation_id": preparation.preparation_id,
            "preparation_digest": preparation.preparation_digest,
            "actual_costs": ExecutionCosts(
                mutator_tokens=actual_tokens,
                monetary_microunits=outcome.actual_cost_microunits,
            ),
            "released_tokens": reservation.reserved_tokens - actual_tokens,
            "released_cost_microunits": (
                reservation.reserved_cost_microunits
                - outcome.actual_cost_microunits
            ),
        },
        "settlement_digest",
    )


def build_execution_handoff(
    *, campaign_id: str, allocation: GenerationAllocation,
    preparation: MutationPreparation,
) -> ExecutionHandoff:
    if preparation.state is not MutationPreparationState.READY:
        raise ValueError("only ready preparation can enter execution")
    candidate = preparation.materialized_candidate
    if candidate is None:
        raise ValueError("ready preparation has no materialized candidate")
    base = preparation.plan.allocation.base_allocation
    if base != allocation:
        raise ValueError("preparation allocation differs from execution allocation")
    checks = {
        "binding": (candidate.binding_source_digest, allocation.binding_source_digest),
        "baseline": (candidate.baseline_snapshot_digest, allocation.coverage_snapshot_digest),
        "generation allocation": (
            candidate.generation_allocation_id,
            allocation.generation_allocation_id,
        ),
    }
    for label, (actual, expected) in checks.items():
        if actual != expected:
            raise ValueError(f"candidate {label} lineage differs")
    return seal_work_contract(
        ExecutionHandoff,
        {
            "handoff_id": "handoff."
            + candidate.materialization_digest.removeprefix("sha256:")[:24],
            "campaign_id": campaign_id,
            "generation_index": allocation.generation_index,
            "generation_allocation_id": allocation.generation_allocation_id,
            "generation_allocation_digest": allocation.allocation_digest,
            "preparation_id": preparation.preparation_id,
            "preparation_digest": preparation.preparation_digest,
            "materialized_candidate_id": candidate.materialized_candidate_id,
            "materialization_digest": candidate.materialization_digest,
            "parent_seed_id": allocation.parent_seed_id,
            "supporting_execution_record_id": allocation.supporting_execution_record_id,
            "binding_source_digest": candidate.binding_source_digest,
            "comparison_context_digest": candidate.comparison_context_digest,
            "baseline_snapshot_digest": candidate.baseline_snapshot_digest,
        },
        "handoff_digest",
    )


def build_candidate_work_from_handoff(
    *, handoff: ExecutionHandoff, reservation: BudgetReservation,
    max_attempts: int = 2,
) -> CandidateWork:
    return seal_work_contract(
        CandidateWork,
        {
            "work_id": "work." + handoff.handoff_digest.removeprefix("sha256:")[:24],
            "campaign_id": handoff.campaign_id,
            "generation_allocation_id": handoff.generation_allocation_id,
            "generation_allocation_digest": handoff.generation_allocation_digest,
            "comparison_context_digest": handoff.comparison_context_digest,
            "baseline_snapshot_digest": handoff.baseline_snapshot_digest,
            "max_attempts": max_attempts,
            "budget_reservation": reservation,
        },
        "work_digest",
    )


def build_execution_closure_from_oracle(
    *,
    work: CandidateWork,
    candidate: MaterializedCandidate,
    execution_id: str,
    trace_digest: str,
    manifest_digest: str,
    oracle_result: CompleteScenarioOracleResult,
    submitted: bool,
    termination_reason: str,
    cleanup_confirmed: bool,
) -> ExecutionClosure:
    """Close one existing Office V2 run without reinterpreting tool events."""
    stages = {
        stage
        for objective in oracle_result.security.planned_objectives
        for stage in objective.exposure_fact.stages
    }
    exposure_refs = tuple(
        ref
        for objective in oracle_result.security.planned_objectives
        for ref in objective.exposure_fact.evidence_refs
    )
    evidence_digest = sha256_digest(
        tuple(
            ref.model_dump(mode="json", exclude_none=False)
            for ref in sorted(exposure_refs, key=lambda item: item.sort_key())
        )
    )
    observed = (
        tuple(
            PayloadExecutionRef(
                payload_spec_id=item.payload_spec_id,
                evidence_digest=evidence_digest,
            )
            for item in candidate.delivered_payloads
        )
        if ExposureStage.OBSERVED in stages and len(candidate.delivered_payloads) == 1
        else ()
    )
    used = observed if ExposureStage.USED in stages else ()
    return seal_work_contract(
        ExecutionClosure,
        {
            "closure_id": "closure."
            + sha256_digest(
                {
                    "work": work.work_digest,
                    "trace": trace_digest,
                    "oracle": oracle_result.result_digest,
                }
            ).removeprefix("sha256:")[:24],
            "work_id": work.work_id,
            "execution_id": execution_id,
            "materialized_candidate_id": candidate.materialized_candidate_id,
            "trace_digest": trace_digest,
            "manifest_digest": manifest_digest,
            "oracle_fact_digest": oracle_result.result_digest,
            "initial_state_digest": oracle_result.initial_state_digest,
            "final_state_digest": oracle_result.final_state_digest,
            "observed_payload_refs": observed,
            "used_payload_refs": used,
            "submitted": submitted,
            "termination_reason": termination_reason,
            "cleanup_confirmed": cleanup_confirmed,
        },
        "closure_digest",
    )


def build_non_episode_settlement(
    *,
    campaign_id: str,
    generation_allocation_id: str,
    disposition: NonEpisodeDisposition,
    previous_state: V2CampaignStateSnapshot,
    next_state: V2CampaignStateSnapshot,
    actual_costs: ExecutionCosts,
    preparation: MutationPreparation | None = None,
    work_id: str | None = None,
    attempt_receipt_ids: tuple[str, ...] = (),
    released_reservation: BudgetReservation | None = None,
) -> NonEpisodeSettlement:
    if next_state.lifecycle.counters.generation_index != (
        previous_state.lifecycle.counters.generation_index + 1
    ):
        raise ValueError("non-Episode settlement must close exactly one generation")
    if (
        next_state.lifecycle.counters.valid_committed_episodes
        != previous_state.lifecycle.counters.valid_committed_episodes
    ):
        raise ValueError("non-Episode settlement cannot add a valid Episode")
    outcome_digest = (
        sha256_digest(preparation.outcome.model_dump(mode="json", exclude_none=False))
        if preparation is not None and preparation.outcome is not None
        else None
    )
    return seal_work_contract(
        NonEpisodeSettlement,
        {
            "settlement_id": "non-episode-settlement."
            + sha256_digest(
                {
                    "campaign": campaign_id,
                    "allocation": generation_allocation_id,
                    "disposition": disposition.value,
                }
            ).removeprefix("sha256:")[:24],
            "campaign_id": campaign_id,
            "generation_allocation_id": generation_allocation_id,
            "preparation_id": preparation.preparation_id if preparation else None,
            "preparation_outcome_digest": outcome_digest,
            "work_id": work_id,
            "attempt_receipt_ids": attempt_receipt_ids,
            "disposition": disposition,
            "actual_costs": actual_costs,
            "released_reservation": released_reservation or BudgetReservation(),
            "invalid_candidate_delta": 1,
            "operator_rejection_delta": (
                preparation.outcome.operator_rejection_count
                if preparation is not None and preparation.outcome is not None
                else 0
            ),
            "scheduling_decision_delta": 1,
            "previous_coverage_digest": previous_state.coverage.snapshot_digest,
            "next_coverage_digest": next_state.coverage.snapshot_digest,
            "previous_exposure_digest": previous_state.exposure_ledger.ledger_digest,
            "next_exposure_digest": next_state.exposure_ledger.ledger_digest,
            "previous_corpus_digest": previous_state.corpus.snapshot_digest,
            "next_corpus_digest": next_state.corpus.snapshot_digest,
            "previous_frontier_digest": previous_state.frontiers.snapshot_digest,
            "next_frontier_digest": next_state.frontiers.snapshot_digest,
            "next_budget_digest": next_state.budget.budget_digest,
            "next_lifecycle_digest": next_state.lifecycle_digest,
            "next_state_digest": next_state.state_digest,
        },
        "settlement_digest",
    )
__all__ = [
    "ExecutionClosure",
    "ExecutionHandoff",
    "MutationBudgetReservation",
    "NonEpisodeDisposition",
    "NonEpisodeSettlement",
    "PreparationCostSettlement",
    "build_candidate_work_from_handoff",
    "build_execution_handoff",
    "build_execution_closure_from_oracle",
    "build_mutation_budget_reservation",
    "build_non_episode_settlement",
    "build_preparation_cost_settlement",
]
