"""Host-owned contracts for Office V2 controlled semantic mutation."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.fuzzer.v2_mutation_identity import build_v2_mutation_identity_lock
from sandbox.fuzzer.v2_scheduler import MutationGenerationAllocation
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest


class MutationFieldClass(StrEnum):
    FROZEN = "frozen"
    MUTABLE = "mutable"
    CONDITIONALLY_MUTABLE = "conditionally_mutable"
    DERIVED = "derived"


class MutationFieldAuthority(StrEnum):
    PROVIDER_TEXT = "provider_text"
    HOST_OPERATOR = "host_operator"
    SCHEDULER_ALLOCATION = "scheduler_allocation"
    HOST_DERIVED = "host_derived"


class MutationFieldRule(OfficeV2Contract):
    field_path: str = Field(min_length=1, max_length=512)
    field_class: MutationFieldClass
    authority: MutationFieldAuthority
    condition_code: Identifier | None = None

    @model_validator(mode="after")
    def class_matches_authority(self) -> Self:
        if self.field_class is MutationFieldClass.DERIVED:
            if self.authority is not MutationFieldAuthority.HOST_DERIVED:
                raise ValueError("derived fields must be host-derived")
        elif self.authority is MutationFieldAuthority.HOST_DERIVED:
            raise ValueError("host-derived authority requires derived field")
        if self.field_class is MutationFieldClass.CONDITIONALLY_MUTABLE:
            if self.condition_code is None:
                raise ValueError("conditionally mutable field requires condition code")
        elif self.condition_code is not None:
            raise ValueError("only conditionally mutable fields accept a condition code")
        if (
            self.authority is MutationFieldAuthority.PROVIDER_TEXT
            and self.field_class is not MutationFieldClass.MUTABLE
        ):
            raise ValueError("provider text fields must be mutable")
        return self


class MutationFieldRegistry(OfficeV2Contract):
    registry_version: Identifier
    object_shape: Identifier
    rules: tuple[MutationFieldRule, ...] = Field(min_length=1)
    registry_digest: Sha256Digest

    @field_validator("rules")
    @classmethod
    def rules_are_complete_once(
        cls, value: tuple[MutationFieldRule, ...]
    ) -> tuple[MutationFieldRule, ...]:
        paths = tuple(item.field_path for item in value)
        if len(paths) != len(set(paths)):
            raise ValueError("mutation field registry classifies a field more than once")
        return tuple(sorted(value, key=lambda item: item.field_path))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"registry_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.registry_digest != sha256_digest(self.digest_payload()):
            raise ValueError("mutation field registry digest does not match")
        return self

    def require_paths(self, actual_paths: tuple[str, ...]) -> None:
        expected = {item.field_path for item in self.rules}
        actual = set(actual_paths)
        if len(actual) != len(actual_paths) or actual != expected:
            raise ValueError("mutation field registry has unknown, duplicate, or missing fields")

    def rule_for(self, field_path: str) -> MutationFieldRule:
        match = next((item for item in self.rules if item.field_path == field_path), None)
        if match is None:
            raise ValueError("mutation field is not classified")
        return match


def seal_field_registry(
    *, registry_version: str, object_shape: str, rules: tuple[MutationFieldRule, ...]
) -> MutationFieldRegistry:
    canonical_rules = tuple(sorted(rules, key=lambda item: item.field_path))
    payload = {
        "registry_version": registry_version,
        "object_shape": object_shape,
        "rules": canonical_rules,
    }
    draft = MutationFieldRegistry.model_construct(
        **payload, registry_digest="sha256:" + "0" * 64
    )
    return MutationFieldRegistry(
        **payload, registry_digest=sha256_digest(draft.digest_payload())
    )


V2_MUTATION_FIELD_RULES = (
    MutationFieldRule(
        field_path="scenario.canonical_world",
        field_class=MutationFieldClass.FROZEN,
        authority=MutationFieldAuthority.HOST_OPERATOR,
    ),
    MutationFieldRule(
        field_path="scenario.parent_case",
        field_class=MutationFieldClass.FROZEN,
        authority=MutationFieldAuthority.HOST_OPERATOR,
    ),
    MutationFieldRule(
        field_path="scenario.objective",
        field_class=MutationFieldClass.CONDITIONALLY_MUTABLE,
        authority=MutationFieldAuthority.SCHEDULER_ALLOCATION,
        condition_code="retarget-allocation-required",
    ),
    MutationFieldRule(
        field_path="scenario.actor",
        field_class=MutationFieldClass.CONDITIONALLY_MUTABLE,
        authority=MutationFieldAuthority.SCHEDULER_ALLOCATION,
        condition_code="rebind-allocation-required",
    ),
    MutationFieldRule(
        field_path="scenario.task",
        field_class=MutationFieldClass.CONDITIONALLY_MUTABLE,
        authority=MutationFieldAuthority.SCHEDULER_ALLOCATION,
        condition_code="rebind-allocation-required",
    ),
    MutationFieldRule(
        field_path="scenario.resource_bindings",
        field_class=MutationFieldClass.CONDITIONALLY_MUTABLE,
        authority=MutationFieldAuthority.SCHEDULER_ALLOCATION,
        condition_code="rebind-allocation-required",
    ),
    MutationFieldRule(
        field_path="scenario.authorization_branch",
        field_class=MutationFieldClass.CONDITIONALLY_MUTABLE,
        authority=MutationFieldAuthority.SCHEDULER_ALLOCATION,
        condition_code="authorization-branch-allocation-required",
    ),
    MutationFieldRule(
        field_path="condition.placements",
        field_class=MutationFieldClass.CONDITIONALLY_MUTABLE,
        authority=MutationFieldAuthority.HOST_OPERATOR,
        condition_code="operator-allocation-required",
    ),
    MutationFieldRule(
        field_path="condition.operator_outputs",
        field_class=MutationFieldClass.CONDITIONALLY_MUTABLE,
        authority=MutationFieldAuthority.HOST_OPERATOR,
        condition_code="operator-allocation-required",
    ),
    MutationFieldRule(
        field_path="condition.payload_slots.generated_content",
        field_class=MutationFieldClass.MUTABLE,
        authority=MutationFieldAuthority.PROVIDER_TEXT,
    ),
    MutationFieldRule(
        field_path="condition.delivered_payloads",
        field_class=MutationFieldClass.DERIVED,
        authority=MutationFieldAuthority.HOST_DERIVED,
    ),
    MutationFieldRule(
        field_path="condition.digests",
        field_class=MutationFieldClass.DERIVED,
        authority=MutationFieldAuthority.HOST_DERIVED,
    ),
)


def build_v2_mutation_field_registry() -> MutationFieldRegistry:
    return seal_field_registry(
        registry_version="office-v2-mutation-field-registry-v1",
        object_shape="office-v2-materialized-scenario-case-v1",
        rules=V2_MUTATION_FIELD_RULES,
    )


class PayloadSlot(OfficeV2Contract):
    payload_slot_id: Identifier
    payload_spec_id: Identifier
    placement_ref: str = Field(min_length=1, max_length=512)
    field_path: str = Field(min_length=1, max_length=512)
    min_length: int = Field(default=1, ge=1)
    max_length: int = Field(default=2048, ge=1, le=8192)
    content_constraints: tuple[Identifier, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def length_range_is_valid(self) -> Self:
        if self.min_length > self.max_length:
            raise ValueError("payload slot length range is invalid")
        return self


class MutationProviderBudget(OfficeV2Contract):
    plan_total_token_budget: int = Field(gt=0)
    per_attempt_token_limit: int = Field(gt=0)
    reserved_total_cost_microunits: int = Field(ge=0)
    max_attempts: int = Field(default=2, ge=1, le=5)
    timeout_ms: int = Field(default=60_000, ge=100, le=300_000)

    @model_validator(mode="after")
    def attempt_limit_fits_total(self) -> Self:
        if self.per_attempt_token_limit > self.plan_total_token_budget:
            raise ValueError("per-attempt token limit exceeds plan total")
        return self


class MutationIntent(OfficeV2Contract):
    mutation_intent_id: Identifier
    mutation_allocation_digest: Sha256Digest
    frontier_id: Identifier
    parent_seed_id: Identifier
    supporting_execution_record_id: Identifier
    binding_source_digest: Sha256Digest
    comparison_context_digest: Sha256Digest
    baseline_snapshot_digest: Sha256Digest
    feedback_digest: Sha256Digest
    operator_allocation_digest: Sha256Digest
    intent_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"intent_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.intent_digest != sha256_digest(self.digest_payload()):
            raise ValueError("mutation intent digest does not match")
        return self


class MutationPlan(OfficeV2Contract):
    mutation_plan_id: Identifier
    intent: MutationIntent
    allocation: MutationGenerationAllocation
    payload_slots: tuple[PayloadSlot, ...] = Field(min_length=1)
    composition_operator: bool = False
    field_registry_digest: Sha256Digest
    changed_field_paths: tuple[str, ...] = Field(min_length=1)
    preserved_field_paths: tuple[str, ...] = Field(min_length=1)
    provider_id: Identifier
    model_identity_digest: Sha256Digest
    prompt_identity_digest: Sha256Digest
    response_schema_digest: Sha256Digest
    budget: MutationProviderBudget
    mutation_identity_digest: Sha256Digest
    plan_digest: Sha256Digest

    @field_validator("payload_slots")
    @classmethod
    def slots_are_canonical(cls, value: tuple[PayloadSlot, ...]) -> tuple[PayloadSlot, ...]:
        ids = tuple(item.payload_slot_id for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("payload slot ids must be unique")
        return tuple(sorted(value, key=lambda item: item.payload_slot_id))

    @field_validator("changed_field_paths", "preserved_field_paths")
    @classmethod
    def field_paths_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("mutation plan field paths must be unique")
        return tuple(sorted(value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"plan_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def ownership_and_digest_match(self) -> Self:
        base = self.allocation.base_allocation
        operator = self.allocation.operator_allocation
        if self.intent.mutation_allocation_digest != self.allocation.mutation_allocation_digest:
            raise ValueError("mutation intent refers to a different allocation")
        if self.intent.frontier_id != base.frontier_id:
            raise ValueError("mutation plan changed scheduled frontier")
        if self.intent.parent_seed_id != base.parent_seed_id:
            raise ValueError("mutation plan changed parent seed")
        if self.intent.supporting_execution_record_id != base.supporting_execution_record_id:
            raise ValueError("mutation plan changed supporting execution")
        if self.intent.binding_source_digest != base.binding_source_digest:
            raise ValueError("mutation plan changed binding source")
        if self.intent.comparison_context_digest != (
            self.allocation.final_context.comparison_context_digest
        ):
            raise ValueError("mutation plan changed comparison context")
        if self.intent.baseline_snapshot_digest != base.coverage_snapshot_digest:
            raise ValueError("mutation plan changed coverage baseline")
        if self.intent.feedback_digest != operator.feedback_digest:
            raise ValueError("mutation plan changed feedback")
        if self.intent.operator_allocation_digest != operator.operator_allocation_digest:
            raise ValueError("mutation plan changed operator allocation")
        if len(self.payload_slots) != 1 and not self.composition_operator:
            raise ValueError("ordinary mutation plan requires exactly one payload slot")
        changed = set(self.changed_field_paths)
        preserved = set(self.preserved_field_paths)
        if changed & preserved:
            raise ValueError("changed and preserved fields overlap")
        expected_identity = build_v2_mutation_identity_lock().identity_digest
        if self.mutation_identity_digest != expected_identity:
            raise ValueError("mutation plan identity drifted")
        if self.plan_digest != sha256_digest(self.digest_payload()):
            raise ValueError("mutation plan digest does not match")
        return self


def seal_contract(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(
        **payload, **{digest_field: "sha256:" + "0" * 64}
    )
    return model_type(
        **payload, **{digest_field: sha256_digest(draft.digest_payload())}
    )


__all__ = [
    "MutationFieldAuthority",
    "MutationFieldClass",
    "MutationFieldRegistry",
    "MutationFieldRule",
    "MutationIntent",
    "MutationPlan",
    "MutationProviderBudget",
    "PayloadSlot",
    "V2_MUTATION_FIELD_RULES",
    "build_v2_mutation_field_registry",
    "seal_contract",
    "seal_field_registry",
]
