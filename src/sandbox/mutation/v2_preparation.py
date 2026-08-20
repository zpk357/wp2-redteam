"""Recoverable Office V2 mutation preparation lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from sandbox.fuzzer.v2_corpus import MaterializedCandidate
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_brief import MinimalFactBrief
from .v2_candidate import ParsedMutationCandidate, parse_candidate
from .v2_contracts import MutationFieldRegistry, MutationPlan
from .v2_materializer import (
    DeterministicMaterialization,
    SlotMaterializationTarget,
    materialize_candidate,
)
from .v2_provider import MutationProviderAttempt, V2MutationProvider, V2ProviderFailure
from .v2_validation import (
    CandidateValidationDisposition,
    CandidateValidationResult,
    validate_candidate,
)


class MutationPreparationState(StrEnum):
    PLANNED = "planned"
    PROVIDER_RUNNING = "provider_running"
    ACCEPTED = "accepted"
    MATERIALIZED = "materialized"
    READY = "ready"
    REJECTED = "rejected"
    PAUSED = "paused"


class PreparationOutcome(OfficeV2Contract):
    disposition: MutationPreparationState
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    actual_input_tokens: int = Field(default=0, ge=0)
    actual_output_tokens: int = Field(default=0, ge=0)
    actual_cost_microunits: int = Field(default=0, ge=0)
    invalid_candidate_count: int = Field(default=0, ge=0)
    operator_rejection_count: int = Field(default=0, ge=0)


class MutationPreparation(OfficeV2Contract):
    preparation_id: Identifier
    campaign_id: Identifier
    plan: MutationPlan
    brief: MinimalFactBrief
    state: MutationPreparationState
    provider_attempts: tuple[MutationProviderAttempt, ...] = Field(default_factory=tuple)
    parsed_candidate: ParsedMutationCandidate | None = None
    validation: CandidateValidationResult | None = None
    materialized_candidate: MaterializedCandidate | None = None
    outcome: PreparationOutcome | None = None
    preparation_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"preparation_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def lifecycle_and_digest_match(self) -> Self:
        terminal = {
            MutationPreparationState.READY,
            MutationPreparationState.REJECTED,
            MutationPreparationState.PAUSED,
        }
        if self.state in terminal and self.outcome is None:
            raise ValueError("terminal mutation preparation requires outcome")
        if self.state is MutationPreparationState.READY:
            if self.materialized_candidate is None:
                raise ValueError("ready mutation preparation requires materialized candidate")
            if (
                self.validation is None
                or self.validation.disposition
                is not CandidateValidationDisposition.ACCEPTED
            ):
                raise ValueError("ready mutation preparation requires accepted validation")
        if self.materialized_candidate is not None and self.parsed_candidate is None:
            raise ValueError("materialization requires parsed candidate")
        if self.preparation_digest != sha256_digest(self.digest_payload()):
            raise ValueError("mutation preparation digest does not match")
        return self


def seal_preparation(payload: dict[str, object]) -> MutationPreparation:
    draft = MutationPreparation.model_construct(
        **payload, preparation_digest="sha256:" + "0" * 64
    )
    return MutationPreparation(
        **payload, preparation_digest=sha256_digest(draft.digest_payload())
    )


async def prepare_candidate(
    *,
    campaign_id: str,
    plan: MutationPlan,
    brief: MinimalFactBrief,
    registry: MutationFieldRegistry,
    provider: V2MutationProvider,
    parent_text_by_slot: dict[str, str],
    scenario_case_id: str,
    scenario_case_id_resolver: Callable[[ParsedMutationCandidate], str] | None = None,
    seed_id_resolver: Callable[[ParsedMutationCandidate], str] | None = None,
    targets: tuple[SlotMaterializationTarget, ...],
    known_candidate_digests: frozenset[str] = frozenset(),
) -> tuple[MutationPreparation, DeterministicMaterialization | None]:
    attempts: list[MutationProviderAttempt] = []
    result = None
    for attempt_index in range(1, plan.budget.max_attempts + 1):
        consumed_tokens = sum(
            item.input_tokens + item.output_tokens for item in attempts
        )
        if consumed_tokens >= plan.budget.plan_total_token_budget:
            break
        try:
            result = await provider.generate(
                plan=plan, brief=brief, attempt_index=attempt_index
            )
            attempts.append(result.attempt)
            break
        except V2ProviderFailure as exc:
            attempts.append(exc.attempt)
            if not exc.attempt.failure_class.retryable:
                break
    if result is None:
        input_tokens = sum(item.input_tokens for item in attempts)
        output_tokens = sum(item.output_tokens for item in attempts)
        costs = sum(item.actual_cost_microunits for item in attempts)
        outcome = PreparationOutcome(
            disposition=MutationPreparationState.PAUSED,
            reason_codes=(attempts[-1].failure_class.value,),
            actual_input_tokens=input_tokens,
            actual_output_tokens=output_tokens,
            actual_cost_microunits=costs,
        )
        preparation = seal_preparation(
            {
                "preparation_id": (
                    f"preparation.{plan.plan_digest.removeprefix('sha256:')[:24]}"
                ),
                "campaign_id": campaign_id,
                "plan": plan,
                "brief": brief,
                "state": MutationPreparationState.PAUSED,
                "provider_attempts": tuple(attempts),
                "outcome": outcome,
            }
        )
        return preparation, None
    raw = result.candidate.model_dump_json()
    parsed = parse_candidate(
        plan=plan, raw_json=raw, parent_text_by_slot=parent_text_by_slot
    )
    validation = validate_candidate(
        plan=plan,
        registry=registry,
        candidate=parsed,
        known_candidate_digests=known_candidate_digests,
        cumulative_output_tokens=sum(
            item.input_tokens + item.output_tokens for item in attempts
        ),
    )
    costs = {
        "actual_input_tokens": sum(item.input_tokens for item in attempts),
        "actual_output_tokens": sum(item.output_tokens for item in attempts),
        "actual_cost_microunits": sum(
            item.actual_cost_microunits for item in attempts
        ),
    }
    common = {
        "preparation_id": f"preparation.{plan.plan_digest.removeprefix('sha256:')[:24]}",
        "campaign_id": campaign_id,
        "plan": plan,
        "brief": brief,
        "provider_attempts": tuple(attempts),
        "parsed_candidate": parsed,
        "validation": validation,
    }
    if validation.disposition is not CandidateValidationDisposition.ACCEPTED:
        state = (
            MutationPreparationState.PAUSED
            if validation.disposition is CandidateValidationDisposition.PAUSED
            else MutationPreparationState.REJECTED
        )
        outcome = PreparationOutcome(
            disposition=state,
            reason_codes=tuple(
                item.reason_code for item in validation.checks if not item.passed
            ),
            invalid_candidate_count=1,
            operator_rejection_count=1,
            **costs,
        )
        return seal_preparation({**common, "state": state, "outcome": outcome}), None
    resolved_scenario_case_id = (
        scenario_case_id_resolver(parsed)
        if scenario_case_id_resolver is not None
        else scenario_case_id
    )
    materialized = materialize_candidate(
        plan=plan,
        parsed=parsed,
        validation=validation,
        scenario_case_id=resolved_scenario_case_id,
        targets=targets,
        seed_id=(seed_id_resolver(parsed) if seed_id_resolver is not None else None),
    )
    outcome = PreparationOutcome(
        disposition=MutationPreparationState.READY,
        reason_codes=("candidate-ready-for-step-5",),
        **costs,
    )
    preparation = seal_preparation(
        {
            **common,
            "state": MutationPreparationState.READY,
            "materialized_candidate": materialized.candidate,
            "outcome": outcome,
        }
    )
    return preparation, materialized


__all__ = [
    "MutationPreparation",
    "MutationPreparationState",
    "PreparationOutcome",
    "prepare_candidate",
    "seal_preparation",
]
