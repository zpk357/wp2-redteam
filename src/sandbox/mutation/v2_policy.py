"""Deterministic Office V2 feedback-to-operator policy."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.fuzzer.v2_mutation_identity import V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST
from sandbox.fuzzer.v2_scheduler import OperatorAllocation
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest


class FeedbackGapKind(StrEnum):
    DELIVERED_NOT_OBSERVED = "delivered_not_observed"
    OBSERVED_NOT_USED = "observed_not_used"
    ATTEMPTED_BLOCKED = "attempted_blocked"
    REALIZED_NO_NEW_BEHAVIOR = "realized_no_new_behavior"
    CONSECUTIVE_NO_GAIN = "consecutive_no_gain"


class OperatorFamily(StrEnum):
    ENTRY_RELOCATION = "entry_relocation"
    PATH_SHORTENING = "path_shortening"
    REACHABLE_PLACEMENT = "reachable_placement"
    PARAMETER_SOURCE = "parameter_source"
    SOURCE_CLAIM = "source_claim"
    EXPRESSION_STRUCTURE = "expression_structure"
    AUTHORIZATION_BRANCH = "authorization_branch"
    LEGAL_REBIND = "legal_rebind"
    CARRIER_PATH_CONTEXT = "carrier_path_context"


class MutationCapability(OfficeV2Contract):
    operator_family: OperatorFamily
    supported_gap_kinds: tuple[FeedbackGapKind, ...] = Field(min_length=1)
    required_seed_properties: tuple[Identifier, ...] = Field(default_factory=tuple)
    changed_dimensions: tuple[Identifier, ...] = Field(min_length=1)
    preserved_dimensions: tuple[Identifier, ...] = Field(min_length=1)
    scheduler_allocation_required: bool = False

    @field_validator(
        "supported_gap_kinds",
        "required_seed_properties",
        "changed_dimensions",
        "preserved_dimensions",
    )
    @classmethod
    def values_are_unique(cls, value: tuple) -> tuple:
        if len(value) != len(set(value)):
            raise ValueError("mutation capability values must be unique")
        return value

    @model_validator(mode="after")
    def dimensions_do_not_overlap(self) -> Self:
        if set(self.changed_dimensions) & set(self.preserved_dimensions):
            raise ValueError("mutation capability changed and preserved dimensions overlap")
        return self


class FeedbackSignal(OfficeV2Contract):
    gap_kind: FeedbackGapKind
    feedback_digest: Sha256Digest
    available_seed_properties: tuple[Identifier, ...] = Field(default_factory=tuple)
    authorized_scheduler_allocations: tuple[Identifier, ...] = Field(default_factory=tuple)
    cooled_operator_families: tuple[OperatorFamily, ...] = Field(default_factory=tuple)


class OperatorSelectionStatus(StrEnum):
    SELECTED = "selected"
    NO_COMPATIBLE_OPERATOR = "no_compatible_operator"


class OperatorSelectionDecision(OfficeV2Contract):
    status: OperatorSelectionStatus
    allocation: OperatorAllocation | None = None
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def status_matches_allocation(self) -> Self:
        if (self.status is OperatorSelectionStatus.SELECTED) != (self.allocation is not None):
            raise ValueError("operator selection status and allocation disagree")
        return self


_POLICY_ORDER: dict[FeedbackGapKind, tuple[OperatorFamily, ...]] = {
    FeedbackGapKind.DELIVERED_NOT_OBSERVED: (
        OperatorFamily.ENTRY_RELOCATION,
        OperatorFamily.PATH_SHORTENING,
        OperatorFamily.REACHABLE_PLACEMENT,
    ),
    FeedbackGapKind.OBSERVED_NOT_USED: (
        OperatorFamily.PARAMETER_SOURCE,
        OperatorFamily.SOURCE_CLAIM,
        OperatorFamily.EXPRESSION_STRUCTURE,
    ),
    FeedbackGapKind.ATTEMPTED_BLOCKED: (
        OperatorFamily.AUTHORIZATION_BRANCH,
        OperatorFamily.LEGAL_REBIND,
    ),
    FeedbackGapKind.REALIZED_NO_NEW_BEHAVIOR: (
        OperatorFamily.CARRIER_PATH_CONTEXT,
    ),
    FeedbackGapKind.CONSECUTIVE_NO_GAIN: (
        OperatorFamily.CARRIER_PATH_CONTEXT,
        OperatorFamily.ENTRY_RELOCATION,
    ),
}


def select_operator(
    *,
    frontier_id: str,
    supporting_execution_record_id: str,
    feedback: FeedbackSignal,
    capabilities: tuple[MutationCapability, ...],
) -> OperatorSelectionDecision:
    by_family = {item.operator_family: item for item in capabilities}
    if len(by_family) != len(capabilities):
        raise ValueError("operator capability manifest contains duplicate families")
    available = set(feedback.available_seed_properties)
    allocations = set(feedback.authorized_scheduler_allocations)
    cooled = set(feedback.cooled_operator_families)
    for family in _POLICY_ORDER[feedback.gap_kind]:
        capability = by_family.get(family)
        if capability is None or family in cooled:
            continue
        if feedback.gap_kind not in capability.supported_gap_kinds:
            continue
        if not set(capability.required_seed_properties).issubset(available):
            continue
        if capability.scheduler_allocation_required and family.value not in allocations:
            continue
        payload = {
            "operator_allocation_id": (
                "operator."
                + sha256_digest(
                    {
                        "frontier": frontier_id,
                        "execution": supporting_execution_record_id,
                        "feedback": feedback.feedback_digest,
                        "family": family.value,
                    }
                ).removeprefix("sha256:")[:24]
            ),
            "frontier_id": frontier_id,
            "supporting_execution_record_id": supporting_execution_record_id,
            "feedback_digest": feedback.feedback_digest,
            "selected_operator_families": (family.value,),
            "reason_codes": (f"feedback-{feedback.gap_kind.value}", "first-compatible"),
            "policy_digest": V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST,
        }
        draft = OperatorAllocation.model_construct(
            **payload, operator_allocation_digest="sha256:" + "0" * 64
        )
        allocation = OperatorAllocation(
            **payload,
            operator_allocation_digest=sha256_digest(draft.digest_payload()),
        )
        return OperatorSelectionDecision(
            status=OperatorSelectionStatus.SELECTED,
            allocation=allocation,
            reason_codes=("deterministic-policy-match",),
        )
    return OperatorSelectionDecision(
        status=OperatorSelectionStatus.NO_COMPATIBLE_OPERATOR,
        reason_codes=("no-compatible-operator",),
    )


__all__ = [
    "FeedbackGapKind",
    "FeedbackSignal",
    "MutationCapability",
    "OperatorFamily",
    "OperatorSelectionDecision",
    "OperatorSelectionStatus",
    "select_operator",
]
