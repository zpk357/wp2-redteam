"""Deterministic baseline, parent selection, and single-candidate scheduling."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_seed_pools import RiskType
from .v2_selection import SelectionKind, SelectionPolicy, SelectionReceipt


class ComparisonContext(OfficeV2Contract):
    actor_id: Identifier
    task_id: Identifier
    resource_binding_digest: Sha256Digest
    allocation_target_digest: Sha256Digest
    authorization_branch: Identifier
    baseline_snapshot_digest: Sha256Digest
    comparison_context_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"comparison_context_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.comparison_context_digest != sha256_digest(self.digest_payload()):
            raise ValueError("comparison context digest does not match")
        return self


class OperatorAllocation(OfficeV2Contract):
    operator_allocation_id: Identifier
    risk_type: RiskType
    supporting_execution_record_id: Identifier
    selected_operator_families: tuple[Identifier, ...] = Field(min_length=1, max_length=3)
    selected_operator_variants: tuple[Identifier, ...] = Field(min_length=1, max_length=3)
    operator_constraint_digests: tuple[Sha256Digest, ...] = Field(min_length=1, max_length=3)
    operator_count_selection_receipt: SelectionReceipt
    family_selection_receipts: tuple[SelectionReceipt, ...] = Field(min_length=1, max_length=3)
    variant_selection_receipts: tuple[SelectionReceipt, ...] = Field(min_length=1, max_length=3)
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    policy_digest: Sha256Digest | None = None
    operator_allocation_digest: Sha256Digest

    @field_validator("selected_operator_families")
    @classmethod
    def operator_families_are_unique(cls, value: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(set(value)) != len(value):
            raise ValueError("operator allocation cannot repeat an operator family")
        return value

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"operator_allocation_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        count = len(self.selected_operator_families)
        if self.operator_count_selection_receipt.selection_kind is not SelectionKind.OPERATOR:
            raise ValueError("operator count receipt has wrong selection kind")
        if self.operator_count_selection_receipt.selected_option_id != str(count):
            raise ValueError("operator count receipt does not match selected families")
        if not (
            len(self.selected_operator_variants)
            == len(self.operator_constraint_digests)
            == len(self.family_selection_receipts)
            == len(self.variant_selection_receipts)
            == count
        ):
            raise ValueError("operator families, variants, constraints, and receipts must align")
        receipts = (
            self.operator_count_selection_receipt,
            *self.family_selection_receipts,
            *self.variant_selection_receipts,
        )
        if any(receipt.selection_kind is not SelectionKind.OPERATOR for receipt in receipts):
            raise ValueError("all operator receipts must use the operator selection kind")
        if any(
            receipt.selection_policy is not SelectionPolicy.RANDOM_UNIFORM
            for receipt in receipts
        ):
            raise ValueError("operator selection receipts must be uniformly random")
        if {
            option.option_id for option in self.operator_count_selection_receipt.options
        } != {"1", "2", "3"}:
            raise ValueError("operator count must be uniformly selected from one to three")
        if tuple(receipt.selected_option_id for receipt in self.family_selection_receipts) != (
            self.selected_operator_families
        ):
            raise ValueError("operator family receipts do not match selection order")
        if tuple(receipt.selected_option_id for receipt in self.variant_selection_receipts) != (
            self.selected_operator_variants
        ):
            raise ValueError("operator variant receipts do not match selection order")
        for index, receipt in enumerate(self.family_selection_receipts):
            already_selected = set(self.selected_operator_families[:index])
            if already_selected.intersection(option.option_id for option in receipt.options):
                raise ValueError("operator families must be sampled without replacement")
        if any(
            not variant.startswith(f"{family}.")
            for family, variant in zip(
                self.selected_operator_families,
                self.selected_operator_variants,
                strict=True,
            )
        ):
            raise ValueError("selected operator variant does not belong to its family")
        if self.operator_allocation_digest != sha256_digest(self.digest_payload()):
            raise ValueError("operator allocation digest does not match")
        return self


class GenerationAllocation(OfficeV2Contract):
    generation_allocation_id: Identifier
    generation_index: int = Field(ge=0)
    risk_type: RiskType
    allocation_target_digest: Sha256Digest
    parent_seed_id: Identifier
    executable_seed_id: Identifier
    supporting_execution_record_id: Identifier
    binding_source_digest: Sha256Digest
    candidate_count: Literal[1] = 1
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    score_components: tuple[tuple[Identifier, int], ...] = Field(default_factory=tuple)
    direction_selection_receipt: SelectionReceipt
    parent_selection_receipt: SelectionReceipt
    coverage_snapshot_digest: Sha256Digest
    corpus_digest: Sha256Digest
    seed_catalog_digest: Sha256Digest
    risk_progress_digest: Sha256Digest
    risk_type_progress_level: int = Field(ge=0, le=3)
    risk_pool_selection_digest: Sha256Digest | None = None
    policy_digest: Sha256Digest | None = None
    allocation_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"allocation_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def identity_and_digest_match(self) -> Self:
        if self.direction_selection_receipt.selection_kind is not SelectionKind.RISK_DIRECTION:
            raise ValueError("generation allocation requires a risk-direction receipt")
        if self.direction_selection_receipt.selected_option_id != self.risk_type.value:
            raise ValueError("risk direction receipt does not close")
        if self.parent_selection_receipt.selected_option_id != self.parent_seed_id:
            raise ValueError("parent receipt does not close")
        if self.risk_pool_selection_digest is None:
            raise ValueError("generation allocation is missing risk-pool selection digest")
        if self.parent_selection_receipt.selection_kind is not SelectionKind.PARENT:
            raise ValueError("generation allocation parent receipt has wrong kind")
        if any(
            receipt.campaign_id != self.direction_selection_receipt.campaign_id
            or receipt.generation_index != self.generation_index
            for receipt in (self.direction_selection_receipt, self.parent_selection_receipt)
        ):
            raise ValueError("generation allocation selection receipts disagree")
        if self.allocation_digest != sha256_digest(self.digest_payload()):
            raise ValueError("generation allocation digest does not match")
        return self


class MutationGenerationAllocation(OfficeV2Contract):
    mutation_generation_allocation_id: Identifier
    base_allocation: GenerationAllocation
    initial_context: ComparisonContext
    operator_allocation: OperatorAllocation
    final_context: ComparisonContext
    mutation_allocation_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"mutation_allocation_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def ownership_chain_and_digest_match(self) -> Self:
        base = self.base_allocation
        operator = self.operator_allocation
        if operator.risk_type is not base.risk_type:
            raise ValueError("operator allocation targets a different risk pool")
        if operator.supporting_execution_record_id != base.supporting_execution_record_id:
            raise ValueError("operator allocation uses a different supporting execution")
        if self.initial_context != self.final_context:
            raise ValueError("semantic mutation cannot change execution context")
        if self.final_context.allocation_target_digest != base.allocation_target_digest:
            raise ValueError("final comparison context does not match scheduled target")
        if self.mutation_allocation_digest != sha256_digest(self.digest_payload()):
            raise ValueError("mutation generation allocation digest does not match")
        return self


__all__ = [
    "ComparisonContext",
    "GenerationAllocation",
    "MutationGenerationAllocation",
    "OperatorAllocation",
]
