"""Strict, scenario-owned contracts for Office V2 attack construction."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, ValidationInfo, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_ATTACK_CASE_CATALOG_VERSION,
    OFFICE_V2_ATTACK_CONTRACT_VERSION,
    OFFICE_V2_ATTACK_FIELD_REGISTRY_VERSION,
    OFFICE_V2_ATTACK_MATERIALIZER_VERSION,
    OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION,
    OFFICE_V2_REACHABILITY_VERSION,
)
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ActionKind,
    ActionScope,
    ActorContext,
    AwareDateTime,
    FieldPathSegment,
    Identifier,
    InteractionContract,
    IssuerAuthentication,
    OfficeV2Contract,
    PrincipalId,
    PrincipalKind,
    ResolvedBinding,
    ResourceKind,
    ResourceRef,
    ResourceRelation,
    Sha256Digest,
    TaskContract,
    WorldVersion,
    canonicalize_identifiers,
    canonicalize_resource_refs,
)


class AttackEntryKind(StrEnum):
    DIRECT_TASK = "direct_task"
    INDIRECT_CONTENT = "indirect_content"
    FORGED_AUTHORIZATION = "forged_authorization"
    PARAMETER_SOURCE_MANIPULATION = "parameter_source_manipulation"


class ObjectiveBindingKind(StrEnum):
    RESOURCE = "resource"
    PRINCIPAL = "principal"


class ObjectiveFactKind(StrEnum):
    TOOL_ATTEMPT = "tool_attempt"
    POLICY_DECISION = "policy_decision"
    OBJECT_CREATED = "object_created"
    FIELD_CHANGED = "field_changed"
    RELATION_CHANGED = "relation_changed"
    BUSINESS_OBJECT_PRESENT = "business_object_present"


class AttackFieldValueKind(StrEnum):
    TEXT = "text"
    PRINCIPAL = "principal"
    PRINCIPAL_SET = "principal_set"
    LOGICAL_TIME = "logical_time"
    RESOURCE_REF = "resource_ref"
    RESOURCE_REF_SET = "resource_ref_set"


class AttackFieldOperation(StrEnum):
    REPLACE = "replace"
    APPEND = "append"
    PREPEND = "prepend"
    REPLACE_ITEM = "replace_item"


class SemanticParameterKind(StrEnum):
    RECIPIENT = "recipient"
    PARTICIPANT_SET = "participant_set"
    START_TIME = "start_time"
    END_TIME = "end_time"
    RESOURCE_REFERENCE = "resource_reference"


class CompatibilityStatus(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNREACHABLE = "unreachable"


class CompatibilityPurpose(StrEnum):
    REALIZED_WITNESS = "realized_witness"
    BLOCKED_CALIBRATION = "blocked_calibration"


class CompatibilityReasonCode(StrEnum):
    COMPATIBLE = "compatible"
    OBJECTIVE_PRECONDITION_UNSATISFIED = "objective_precondition_unsatisfied"
    OBJECTIVE_BINDING_UNRESOLVED = "objective_binding_unresolved"
    ENTRY_KIND_NOT_SUPPORTED = "entry_kind_not_supported"
    REACHABLE_FIELD_MISSING = "reachable_field_missing"
    FIELD_OPERATION_INCOMPATIBLE = "field_operation_incompatible"
    VERIFICATION_SOURCE_MISSING = "verification_source_missing"
    AUTHORITATIVE_GRANT_PRESENT = "authoritative_grant_present"
    ACTOR_CAPABILITY_MISSING = "actor_capability_missing"
    PLATFORM_EFFECT_UNREACHABLE = "platform_effect_unreachable"
    POLICY_ENFORCE_BLOCKS_REQUIRED_MILESTONE = "policy_enforce_blocks_required_milestone"
    TASK_ISSUER_PROFILE_INVALID = "task_issuer_profile_invalid"
    WORLD_OR_CATALOG_IDENTITY_MISMATCH = "world_or_catalog_identity_mismatch"


def _canonical_strings(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    return canonicalize_identifiers(value, field_name=field_name)


class ObjectiveBindingSlot(OfficeV2Contract):
    slot_id: Identifier
    binding_kind: ObjectiveBindingKind
    required_before: bool = True
    resource_kinds: tuple[ResourceKind, ...] = Field(default_factory=tuple)
    principal_kinds: tuple[PrincipalKind, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def target_kind_is_complete(self) -> Self:
        if self.binding_kind is ObjectiveBindingKind.RESOURCE:
            if not self.resource_kinds or self.principal_kinds:
                raise ValueError("resource binding slot requires only resource_kinds")
        elif not self.principal_kinds or self.resource_kinds:
            raise ValueError("principal binding slot requires only principal_kinds")
        return self

    @field_validator("resource_kinds")
    @classmethod
    def resources_are_canonical(cls, value: tuple[ResourceKind, ...]) -> tuple[ResourceKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("resource_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("principal_kinds")
    @classmethod
    def principals_are_canonical(
        cls, value: tuple[PrincipalKind, ...]
    ) -> tuple[PrincipalKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("principal_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))


class ObjectiveResolvedBinding(OfficeV2Contract):
    slot_id: Identifier
    resource_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    principal_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    output_resource_kind: ResourceKind | None = None
    evidence_refs: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("resource_refs")
    @classmethod
    def resources_are_canonical(cls, value: tuple[ResourceRef, ...]) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator("principal_ids", "evidence_refs")
    @classmethod
    def ids_are_canonical(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _canonical_strings(value, field_name=info.field_name)

    @model_validator(mode="after")
    def exactly_one_binding_shape(self) -> Self:
        shapes = sum(
            (
                bool(self.resource_refs),
                bool(self.principal_ids),
                self.output_resource_kind is not None,
            )
        )
        if shapes != 1:
            raise ValueError("objective binding requires exactly one binding shape")
        return self


class ObjectiveFactAssertion(OfficeV2Contract):
    assertion_id: Identifier
    fact_kind: ObjectiveFactKind
    binding_slots: tuple[Identifier, ...] = Field(default_factory=tuple)
    tool_names: tuple[Identifier, ...] = Field(default_factory=tuple)
    action_kinds: tuple[ActionKind, ...] = Field(default_factory=tuple)
    resource_kinds: tuple[ResourceKind, ...] = Field(default_factory=tuple)
    field_paths: tuple[tuple[FieldPathSegment, ...], ...] = Field(default_factory=tuple)
    relation_kinds: tuple[ResourceRelation, ...] = Field(default_factory=tuple)
    expected_result: Literal["attempted", "blocked", "succeeded", "present"]

    @field_validator("binding_slots", "tool_names")
    @classmethod
    def strings_are_canonical(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _canonical_strings(value, field_name=info.field_name)

    @field_validator("action_kinds")
    @classmethod
    def actions_are_canonical(cls, value: tuple[ActionKind, ...]) -> tuple[ActionKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("action_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("resource_kinds")
    @classmethod
    def resources_are_canonical(cls, value: tuple[ResourceKind, ...]) -> tuple[ResourceKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("resource_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("field_paths")
    @classmethod
    def paths_are_canonical(cls, value: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
        if any(not path for path in value):
            raise ValueError("field_paths must not contain empty paths")
        if len(value) != len(set(value)):
            raise ValueError("field_paths must not contain duplicates")
        return tuple(sorted(value))

    @field_validator("relation_kinds")
    @classmethod
    def relations_are_canonical(
        cls, value: tuple[ResourceRelation, ...]
    ) -> tuple[ResourceRelation, ...]:
        if len(value) != len(set(value)):
            raise ValueError("relation_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @model_validator(mode="after")
    def fact_has_a_structural_selector(self) -> Self:
        if not (
            self.binding_slots
            or self.tool_names
            or self.action_kinds
            or self.resource_kinds
            or self.field_paths
            or self.relation_kinds
        ):
            raise ValueError("objective assertion requires a structural selector")
        expected_by_kind = {
            ObjectiveFactKind.TOOL_ATTEMPT: "attempted",
            ObjectiveFactKind.POLICY_DECISION: "blocked",
            ObjectiveFactKind.OBJECT_CREATED: "succeeded",
            ObjectiveFactKind.FIELD_CHANGED: "succeeded",
            ObjectiveFactKind.RELATION_CHANGED: "succeeded",
            ObjectiveFactKind.BUSINESS_OBJECT_PRESENT: "present",
        }
        if self.expected_result != expected_by_kind[self.fact_kind]:
            raise ValueError("expected_result does not match fact_kind")
        return self


def _canonical_assertions(
    value: tuple[ObjectiveFactAssertion, ...], *, field_name: str
) -> tuple[ObjectiveFactAssertion, ...]:
    ids = tuple(item.assertion_id for item in value)
    _canonical_strings(ids, field_name=field_name)
    return tuple(sorted(value, key=lambda item: item.assertion_id))


class ObjectiveMilestone(OfficeV2Contract):
    milestone_id: Identifier
    depends_on: tuple[Identifier, ...] = Field(default_factory=tuple)
    required: bool = True
    affected_binding_slots: tuple[Identifier, ...] = Field(min_length=1)
    attempted_assertions: tuple[ObjectiveFactAssertion, ...] = Field(min_length=1)
    blocked_assertions: tuple[ObjectiveFactAssertion, ...] = Field(min_length=1)
    realized_assertions: tuple[ObjectiveFactAssertion, ...] = Field(min_length=1)

    @field_validator("depends_on", "affected_binding_slots")
    @classmethod
    def ids_are_canonical(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _canonical_strings(value, field_name=info.field_name)

    @field_validator("attempted_assertions", "blocked_assertions", "realized_assertions")
    @classmethod
    def assertions_are_canonical(
        cls, value: tuple[ObjectiveFactAssertion, ...], info: ValidationInfo
    ) -> tuple[ObjectiveFactAssertion, ...]:
        return _canonical_assertions(value, field_name=info.field_name)


class ObjectiveMilestoneGraph(OfficeV2Contract):
    milestones: tuple[ObjectiveMilestone, ...] = Field(min_length=2)

    @field_validator("milestones")
    @classmethod
    def milestones_are_canonical(
        cls, value: tuple[ObjectiveMilestone, ...]
    ) -> tuple[ObjectiveMilestone, ...]:
        ids = tuple(item.milestone_id for item in value)
        _canonical_strings(ids, field_name="milestone ids")
        return tuple(sorted(value, key=lambda item: item.milestone_id))

    @model_validator(mode="after")
    def dependencies_are_closed_and_acyclic(self) -> Self:
        by_id = {item.milestone_id: item for item in self.milestones}
        for item in self.milestones:
            if item.milestone_id in item.depends_on:
                raise ValueError("milestone must not depend on itself")
            if not set(item.depends_on).issubset(by_id):
                raise ValueError("milestone depends on unknown milestone")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(item_id: str) -> None:
            if item_id in visiting:
                raise ValueError("milestones must form a DAG")
            if item_id in visited:
                return
            visiting.add(item_id)
            for dependency in by_id[item_id].depends_on:
                visit(dependency)
            visiting.remove(item_id)
            visited.add(item_id)

        for item_id in by_id:
            visit(item_id)
        if len([item for item in self.milestones if item.required]) < 2:
            raise ValueError("compound objective requires two required milestones")
        return self


class AttackObjectiveTemplate(OfficeV2Contract):
    objective_id: Identifier
    objective_version: Identifier = OFFICE_V2_ATTACK_CONTRACT_VERSION
    title: str = Field(min_length=1, max_length=256)
    risk_category_ids: tuple[Identifier, ...] = Field(min_length=1)
    required_binding_slots: tuple[ObjectiveBindingSlot, ...] = Field(min_length=1)
    precondition_assertions: tuple[ObjectiveFactAssertion, ...] = Field(min_length=1)
    prohibited_action_scopes: tuple[ActionScope, ...] = Field(min_length=1)
    attempted_assertions: tuple[ObjectiveFactAssertion, ...] = Field(min_length=1)
    realized_assertions: tuple[ObjectiveFactAssertion, ...] = Field(default_factory=tuple)
    milestone_graph: ObjectiveMilestoneGraph | None = None
    compatible_entry_kinds: tuple[AttackEntryKind, ...] = Field(min_length=1)
    content_digest: Sha256Digest

    @field_validator("risk_category_ids")
    @classmethod
    def risks_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_strings(value, field_name="risk_category_ids")

    @field_validator("required_binding_slots")
    @classmethod
    def slots_are_canonical(
        cls, value: tuple[ObjectiveBindingSlot, ...]
    ) -> tuple[ObjectiveBindingSlot, ...]:
        ids = tuple(item.slot_id for item in value)
        _canonical_strings(ids, field_name="objective binding slots")
        return tuple(sorted(value, key=lambda item: item.slot_id))

    @field_validator("precondition_assertions", "attempted_assertions", "realized_assertions")
    @classmethod
    def assertions_are_canonical(
        cls, value: tuple[ObjectiveFactAssertion, ...], info: ValidationInfo
    ) -> tuple[ObjectiveFactAssertion, ...]:
        return _canonical_assertions(value, field_name=info.field_name)

    @field_validator("prohibited_action_scopes")
    @classmethod
    def scopes_are_canonical(cls, value: tuple[ActionScope, ...]) -> tuple[ActionScope, ...]:
        keys = tuple(item.sort_key() for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError("prohibited_action_scopes must not contain duplicates")
        return tuple(sorted(value, key=ActionScope.sort_key))

    @field_validator("compatible_entry_kinds")
    @classmethod
    def entries_are_canonical(
        cls, value: tuple[AttackEntryKind, ...]
    ) -> tuple[AttackEntryKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("compatible_entry_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def references_and_digest_match(self) -> Self:
        slot_ids = {item.slot_id for item in self.required_binding_slots}
        assertions = [
            *self.precondition_assertions,
            *self.attempted_assertions,
            *self.realized_assertions,
        ]
        if self.milestone_graph is not None:
            for milestone in self.milestone_graph.milestones:
                if not set(milestone.affected_binding_slots).issubset(slot_ids):
                    raise ValueError("milestone references unknown binding slot")
                assertions.extend(milestone.attempted_assertions)
                assertions.extend(milestone.blocked_assertions)
                assertions.extend(milestone.realized_assertions)
        elif not self.realized_assertions:
            raise ValueError("atomic objective requires realized_assertions")
        if any(not set(item.binding_slots).issubset(slot_ids) for item in assertions):
            raise ValueError("objective assertion references unknown binding slot")
        if self.content_digest != sha256_digest(self.digest_payload()):
            raise ValueError("objective content_digest does not match payload")
        return self


class AttackObjectiveCatalog(OfficeV2Contract):
    catalog_version: Identifier = OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION
    objectives: tuple[AttackObjectiveTemplate, ...] = Field(min_length=1)
    catalog_digest: Sha256Digest

    @field_validator("objectives")
    @classmethod
    def objectives_are_canonical(
        cls, value: tuple[AttackObjectiveTemplate, ...]
    ) -> tuple[AttackObjectiveTemplate, ...]:
        ids = tuple(item.objective_id for item in value)
        _canonical_strings(ids, field_name="objective ids")
        return tuple(sorted(value, key=lambda item: item.objective_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"catalog_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.catalog_digest != sha256_digest(self.digest_payload()):
            raise ValueError("objective catalog digest does not match payload")
        return self


class AttackableFieldSpec(OfficeV2Contract):
    field_spec_id: Identifier
    resource_kind: ResourceKind
    field_path: tuple[FieldPathSegment, ...] = Field(min_length=1)
    value_kind: AttackFieldValueKind
    observable_through_tools: tuple[Identifier, ...] = Field(min_length=1)
    required_access: tuple[AccessRight, ...] = Field(min_length=1)
    allowed_entry_kinds: tuple[AttackEntryKind, ...] = Field(min_length=1)
    allowed_operations: tuple[AttackFieldOperation, ...] = Field(min_length=1)
    semantic_parameter_kinds: tuple[SemanticParameterKind, ...] = Field(default_factory=tuple)
    normalizer_version: Identifier = "office-v2-attack-field-normalizer-v1"

    @field_validator("observable_through_tools")
    @classmethod
    def tools_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_strings(value, field_name="observable_through_tools")

    @field_validator("required_access")
    @classmethod
    def access_is_canonical(cls, value: tuple[AccessRight, ...]) -> tuple[AccessRight, ...]:
        if len(value) != len(set(value)):
            raise ValueError("required_access must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("allowed_entry_kinds")
    @classmethod
    def entry_kinds_are_canonical(
        cls, value: tuple[AttackEntryKind, ...]
    ) -> tuple[AttackEntryKind, ...]:
        if AttackEntryKind.DIRECT_TASK in value:
            raise ValueError("direct_task must not use an attackable content field")
        if len(value) != len(set(value)):
            raise ValueError("allowed_entry_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("allowed_operations")
    @classmethod
    def operations_are_canonical(
        cls, value: tuple[AttackFieldOperation, ...]
    ) -> tuple[AttackFieldOperation, ...]:
        if len(value) != len(set(value)):
            raise ValueError("allowed_operations must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("semantic_parameter_kinds")
    @classmethod
    def parameters_are_canonical(
        cls, value: tuple[SemanticParameterKind, ...]
    ) -> tuple[SemanticParameterKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("semantic_parameter_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @model_validator(mode="after")
    def operations_match_value_kind(self) -> Self:
        text_ops = {
            AttackFieldOperation.REPLACE,
            AttackFieldOperation.APPEND,
            AttackFieldOperation.PREPEND,
        }
        if self.value_kind is AttackFieldValueKind.TEXT:
            if not set(self.allowed_operations).issubset(text_ops):
                raise ValueError("text field uses an incompatible operation")
        elif not set(self.allowed_operations).issubset(
            {AttackFieldOperation.REPLACE, AttackFieldOperation.REPLACE_ITEM}
        ):
            raise ValueError("structured field uses an incompatible operation")
        return self


class AttackableFieldCatalog(OfficeV2Contract):
    catalog_version: Identifier = OFFICE_V2_ATTACK_FIELD_REGISTRY_VERSION
    fields: tuple[AttackableFieldSpec, ...] = Field(min_length=1)
    catalog_digest: Sha256Digest

    @field_validator("fields")
    @classmethod
    def fields_are_canonical(
        cls, value: tuple[AttackableFieldSpec, ...]
    ) -> tuple[AttackableFieldSpec, ...]:
        ids = tuple(item.field_spec_id for item in value)
        _canonical_strings(ids, field_name="attackable field ids")
        locations = tuple((item.resource_kind, item.field_path) for item in value)
        if len(locations) != len(set(locations)):
            raise ValueError("attackable field locations must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.field_spec_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"catalog_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.catalog_digest != sha256_digest(self.digest_payload()):
            raise ValueError("field catalog digest does not match payload")
        return self


class ReachableContentField(OfficeV2Contract):
    field_spec_id: Identifier
    resource_ref: ResourceRef
    field_path: tuple[FieldPathSegment, ...] = Field(min_length=1)
    value_kind: AttackFieldValueKind
    reachability_reason: str = Field(min_length=1, max_length=1024)
    source_goal_ids: tuple[Identifier, ...] = Field(min_length=1)
    source_query_ids: tuple[Identifier, ...] = Field(min_length=1)
    relation_path: tuple[ResourceRelation, ...] = Field(default_factory=tuple)
    required_access: tuple[AccessRight, ...] = Field(min_length=1)
    observation_preconditions: tuple[Identifier, ...] = Field(min_length=1)
    compatible_entry_kinds: tuple[AttackEntryKind, ...] = Field(min_length=1)
    compatible_operations: tuple[AttackFieldOperation, ...] = Field(min_length=1)
    evidence_refs: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator(
        "source_goal_ids", "source_query_ids", "observation_preconditions", "evidence_refs"
    )
    @classmethod
    def ids_are_canonical(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _canonical_strings(value, field_name=info.field_name)

    def sort_key(self) -> tuple[str, str, str, tuple[str, ...]]:
        return (*self.resource_ref.sort_key(), self.field_path)


class ReachableAttackSurface(OfficeV2Contract):
    case_id: Identifier
    case_digest: Sha256Digest
    world_digest: Sha256Digest
    actor_view_digest: Sha256Digest
    reachability_version: Identifier = OFFICE_V2_REACHABILITY_VERSION
    field_registry_digest: Sha256Digest
    fields: tuple[ReachableContentField, ...]
    surface_digest: Sha256Digest

    @field_validator("fields")
    @classmethod
    def fields_are_canonical(
        cls, value: tuple[ReachableContentField, ...]
    ) -> tuple[ReachableContentField, ...]:
        keys = tuple(item.sort_key() for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError("reachable fields must not contain duplicates")
        return tuple(sorted(value, key=ReachableContentField.sort_key))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"surface_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.surface_digest != sha256_digest(self.digest_payload()):
            raise ValueError("reachable surface digest does not match payload")
        return self


class ReachableAttackSurfaceCatalog(OfficeV2Contract):
    catalog_version: Identifier = OFFICE_V2_REACHABILITY_VERSION
    world_digest: Sha256Digest
    field_registry_digest: Sha256Digest
    surfaces: tuple[ReachableAttackSurface, ...] = Field(min_length=1)
    catalog_digest: Sha256Digest

    @field_validator("surfaces")
    @classmethod
    def surfaces_are_canonical(
        cls, value: tuple[ReachableAttackSurface, ...]
    ) -> tuple[ReachableAttackSurface, ...]:
        ids = tuple(item.case_id for item in value)
        _canonical_strings(ids, field_name="reachable surface case ids")
        return tuple(sorted(value, key=lambda item: item.case_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"catalog_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def identities_and_digest_match(self) -> Self:
        if any(item.world_digest != self.world_digest for item in self.surfaces):
            raise ValueError("surface catalog contains a different world identity")
        if any(item.field_registry_digest != self.field_registry_digest for item in self.surfaces):
            raise ValueError("surface catalog contains a different field registry")
        if self.catalog_digest != sha256_digest(self.digest_payload()):
            raise ValueError("reachable surface catalog digest does not match payload")
        return self


class ContentPlacement(OfficeV2Contract):
    resource_ref: ResourceRef
    field_path: tuple[FieldPathSegment, ...] = Field(min_length=1)

    def sort_key(self) -> tuple[str, str, str, tuple[str, ...]]:
        return (*self.resource_ref.sort_key(), self.field_path)


def _canonical_placements(
    value: tuple[ContentPlacement, ...], *, field_name: str
) -> tuple[ContentPlacement, ...]:
    keys = tuple(item.sort_key() for item in value)
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(sorted(value, key=ContentPlacement.sort_key))


class DirectTaskCondition(OfficeV2Contract):
    condition_id: Identifier
    condition_version: Identifier = OFFICE_V2_ATTACK_CONTRACT_VERSION
    entry_kind: Literal[AttackEntryKind.DIRECT_TASK] = AttackEntryKind.DIRECT_TASK
    template_id: Identifier
    objective_id: Identifier
    parent_case_id: Identifier
    instruction_variant: str = Field(min_length=1, max_length=4096)
    issuer_override: PrincipalId | None = None
    issuer_authentication: IssuerAuthentication
    inherited_task_digest: Sha256Digest
    derived_task_digest: Sha256Digest
    expression_digest: Sha256Digest
    content_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.content_digest != sha256_digest(self.digest_payload()):
            raise ValueError("direct condition content_digest does not match payload")
        return self


class IndirectContentCondition(OfficeV2Contract):
    condition_id: Identifier
    condition_version: Identifier = OFFICE_V2_ATTACK_CONTRACT_VERSION
    entry_kind: Literal[AttackEntryKind.INDIRECT_CONTENT] = AttackEntryKind.INDIRECT_CONTENT
    template_id: Identifier
    objective_id: Identifier
    parent_case_id: Identifier
    placements: tuple[ContentPlacement, ...] = Field(min_length=1)
    operation: AttackFieldOperation
    adversarial_content: str = Field(min_length=1, max_length=20_000)
    original_value_digests: tuple[Sha256Digest, ...] = Field(min_length=1)
    reachable_surface_digest: Sha256Digest
    expression_digest: Sha256Digest
    content_digest: Sha256Digest

    @field_validator("placements")
    @classmethod
    def placements_are_canonical(
        cls, value: tuple[ContentPlacement, ...]
    ) -> tuple[ContentPlacement, ...]:
        return _canonical_placements(value, field_name="placements")

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def placements_and_original_values_align(self) -> Self:
        if len(self.placements) != len(self.original_value_digests):
            raise ValueError("placements and original digests must align")
        if self.content_digest != sha256_digest(self.digest_payload()):
            raise ValueError("indirect condition content_digest does not match payload")
        return self


class ForgedAuthorizationCondition(OfficeV2Contract):
    condition_id: Identifier
    condition_version: Identifier = OFFICE_V2_ATTACK_CONTRACT_VERSION
    entry_kind: Literal[AttackEntryKind.FORGED_AUTHORIZATION] = AttackEntryKind.FORGED_AUTHORIZATION
    template_id: Identifier
    objective_id: Identifier
    parent_case_id: Identifier
    claim_placements: tuple[ContentPlacement, ...] = Field(min_length=1)
    claimed_action_scope: ActionScope
    claimed_resource_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    claimed_recipient_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    authoritative_absence_assertions: tuple[Identifier, ...] = Field(min_length=1)
    original_value_digests: tuple[Sha256Digest, ...] = Field(min_length=1)
    reachable_surface_digest: Sha256Digest
    expression_digest: Sha256Digest
    content_digest: Sha256Digest

    @field_validator("claim_placements")
    @classmethod
    def placements_are_canonical(
        cls, value: tuple[ContentPlacement, ...]
    ) -> tuple[ContentPlacement, ...]:
        return _canonical_placements(value, field_name="claim_placements")

    @field_validator("claimed_resource_refs")
    @classmethod
    def resources_are_canonical(cls, value: tuple[ResourceRef, ...]) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator("claimed_recipient_ids", "authoritative_absence_assertions")
    @classmethod
    def identifiers_are_canonical(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return _canonical_strings(value, field_name=info.field_name)

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def claim_scope_is_nonempty_and_aligned(self) -> Self:
        if not self.claimed_resource_refs and not self.claimed_recipient_ids:
            raise ValueError("forged claim requires resource or recipient scope")
        if len(self.claim_placements) != len(self.original_value_digests):
            raise ValueError("claim placements and original digests must align")
        if self.content_digest != sha256_digest(self.digest_payload()):
            raise ValueError("forged condition content_digest does not match payload")
        return self


class ParameterSourceManipulationCondition(OfficeV2Contract):
    condition_id: Identifier
    condition_version: Identifier = OFFICE_V2_ATTACK_CONTRACT_VERSION
    entry_kind: Literal[AttackEntryKind.PARAMETER_SOURCE_MANIPULATION] = (
        AttackEntryKind.PARAMETER_SOURCE_MANIPULATION
    )
    template_id: Identifier
    objective_id: Identifier
    parent_case_id: Identifier
    source_placement: ContentPlacement
    semantic_parameter: SemanticParameterKind
    original_value: (
        str | AwareDateTime | ResourceRef | tuple[PrincipalId, ...] | tuple[ResourceRef, ...]
    )
    visible_value: (
        str | AwareDateTime | ResourceRef | tuple[PrincipalId, ...] | tuple[ResourceRef, ...]
    )
    original_value_digest: Sha256Digest
    visible_value_digest: Sha256Digest
    verification_sources: tuple[ContentPlacement, ...] = Field(min_length=1)
    reachable_surface_digest: Sha256Digest
    expression_digest: Sha256Digest
    content_digest: Sha256Digest

    @model_validator(mode="before")
    @classmethod
    def temporal_values_survive_json_transport(cls, value: object) -> object:
        if not isinstance(value, dict) or value.get("semantic_parameter") not in {
            SemanticParameterKind.START_TIME,
            SemanticParameterKind.START_TIME.value,
            SemanticParameterKind.END_TIME,
            SemanticParameterKind.END_TIME.value,
        }:
            return value
        normalized = dict(value)
        for field_name in ("original_value", "visible_value"):
            field_value = normalized.get(field_name)
            if isinstance(field_value, str):
                normalized[field_name] = datetime.fromisoformat(field_value)
        return normalized

    @field_validator("verification_sources")
    @classmethod
    def verification_sources_are_canonical(
        cls, value: tuple[ContentPlacement, ...]
    ) -> tuple[ContentPlacement, ...]:
        return _canonical_placements(value, field_name="verification_sources")

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def verification_is_independent_and_digest_matches(self) -> Self:
        if self.source_placement in self.verification_sources:
            raise ValueError("verification source must be independent")
        if not _parameter_values_match_semantic_kind(
            self.semantic_parameter, self.original_value, self.visible_value
        ):
            raise ValueError("parameter values do not match semantic parameter type")
        if self.original_value == self.visible_value:
            raise ValueError("parameter manipulation requires a changed visible value")
        if self.original_value_digest != sha256_digest(self.original_value):
            raise ValueError("original_value_digest does not match original_value")
        if self.visible_value_digest != sha256_digest(self.visible_value):
            raise ValueError("visible_value_digest does not match visible_value")
        if self.content_digest != sha256_digest(self.digest_payload()):
            raise ValueError("parameter condition content_digest does not match payload")
        return self


def _parameter_values_match_semantic_kind(
    kind: SemanticParameterKind, original: object, visible: object
) -> bool:
    values = (original, visible)
    if kind is SemanticParameterKind.RECIPIENT:
        return all(
            isinstance(value, str)
            or isinstance(value, tuple)
            and all(isinstance(item, str) for item in value)
            for value in values
        )
    if kind is SemanticParameterKind.PARTICIPANT_SET:
        return all(
            isinstance(value, tuple) and all(isinstance(item, str) for item in value)
            for value in values
        )
    if kind in {SemanticParameterKind.START_TIME, SemanticParameterKind.END_TIME}:
        return all(isinstance(value, datetime) for value in values)
    return all(
        isinstance(value, ResourceRef)
        or isinstance(value, tuple)
        and all(isinstance(item, ResourceRef) for item in value)
        for value in values
    )


AdversarialCondition = Annotated[
    DirectTaskCondition
    | IndirectContentCondition
    | ForgedAuthorizationCondition
    | ParameterSourceManipulationCondition,
    Field(discriminator="entry_kind"),
]
ADVERSARIAL_CONDITION_ADAPTER = TypeAdapter(AdversarialCondition)


class PolicyFeasibilityFact(OfficeV2Contract):
    assertion_id: Identifier
    tool_name: Identifier
    action: ActionKind
    capability_available: bool
    platform_allowed: bool | None
    delegation_allowed: bool | None
    policy_allowed: bool | None
    effective_allowed: bool
    policy_decision_digest: Sha256Digest
    evidence_refs: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("evidence_refs")
    @classmethod
    def evidence_is_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_strings(value, field_name="policy feasibility evidence")


class CompatibilityDecision(OfficeV2Contract):
    status: CompatibilityStatus
    reason_code: CompatibilityReasonCode
    purpose: CompatibilityPurpose
    objective_id: Identifier
    entry_template_id: Identifier
    parent_case_id: Identifier
    resolved_objective_bindings: tuple[ObjectiveResolvedBinding, ...] = Field(default_factory=tuple)
    selected_surface_fields: tuple[ReachableContentField, ...] = Field(default_factory=tuple)
    precondition_evidence_refs: tuple[Identifier, ...] = Field(default_factory=tuple)
    policy_feasibility: tuple[PolicyFeasibilityFact, ...] = Field(default_factory=tuple)
    decision_digest: Sha256Digest

    @field_validator("resolved_objective_bindings")
    @classmethod
    def bindings_are_canonical(
        cls, value: tuple[ObjectiveResolvedBinding, ...]
    ) -> tuple[ObjectiveResolvedBinding, ...]:
        slot_ids = tuple(item.slot_id for item in value)
        _canonical_strings(slot_ids, field_name="objective binding slot ids")
        return tuple(sorted(value, key=lambda item: item.slot_id))

    @field_validator("selected_surface_fields")
    @classmethod
    def surface_fields_are_canonical(
        cls, value: tuple[ReachableContentField, ...]
    ) -> tuple[ReachableContentField, ...]:
        keys = tuple(item.sort_key() for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError("selected_surface_fields must not contain duplicates")
        return tuple(sorted(value, key=ReachableContentField.sort_key))

    @field_validator("precondition_evidence_refs")
    @classmethod
    def evidence_is_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_strings(value, field_name="precondition_evidence_refs")

    @field_validator("policy_feasibility")
    @classmethod
    def feasibility_is_canonical(
        cls, value: tuple[PolicyFeasibilityFact, ...]
    ) -> tuple[PolicyFeasibilityFact, ...]:
        assertion_ids = tuple(item.assertion_id for item in value)
        _canonical_strings(assertion_ids, field_name="policy feasibility assertion ids")
        return tuple(sorted(value, key=lambda item: item.assertion_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"decision_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def status_and_reason_align(self) -> Self:
        if (self.status is CompatibilityStatus.COMPATIBLE) != (
            self.reason_code is CompatibilityReasonCode.COMPATIBLE
        ):
            raise ValueError("compatible status and reason must align")
        if self.decision_digest != sha256_digest(self.digest_payload()):
            raise ValueError("compatibility decision digest does not match payload")
        return self


class MaterializedFieldChange(OfficeV2Contract):
    placement: ContentPlacement
    original_value_digest: Sha256Digest
    visible_value_digest: Sha256Digest

    def sort_key(self) -> tuple[str, str, str, tuple[str, ...]]:
        return self.placement.sort_key()


class AttackMaterializationRecord(OfficeV2Contract):
    materializer_version: Identifier = OFFICE_V2_ATTACK_MATERIALIZER_VERSION
    parent_case_digest: Sha256Digest
    objective_digest: Sha256Digest
    condition_digest: Sha256Digest
    surface_digest: Sha256Digest | None = None
    compatibility_digest: Sha256Digest
    before_world_digest: Sha256Digest
    after_world_digest: Sha256Digest
    before_task_digest: Sha256Digest
    after_task_digest: Sha256Digest
    changed_content_fields: tuple[MaterializedFieldChange, ...] = Field(default_factory=tuple)
    created_objects: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    removed_objects: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    authoritative_state_assertions: tuple[Identifier, ...] = Field(default_factory=tuple)
    initialization_transition_digest: Sha256Digest | None = None
    materialization_digest: Sha256Digest

    @field_validator("changed_content_fields")
    @classmethod
    def changes_are_canonical(
        cls, value: tuple[MaterializedFieldChange, ...]
    ) -> tuple[MaterializedFieldChange, ...]:
        keys = tuple(item.sort_key() for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError("changed_content_fields must not contain duplicates")
        return tuple(sorted(value, key=MaterializedFieldChange.sort_key))

    @field_validator("created_objects", "removed_objects")
    @classmethod
    def objects_are_canonical(cls, value: tuple[ResourceRef, ...]) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator("authoritative_state_assertions")
    @classmethod
    def authority_assertions_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_strings(value, field_name="authoritative_state_assertions")

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"materialization_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_and_delta_are_consistent(self) -> Self:
        if set(self.created_objects).intersection(self.removed_objects):
            raise ValueError("an object cannot be both created and removed")
        if self.materialization_digest != sha256_digest(self.digest_payload()):
            raise ValueError("materialization digest does not match payload")
        return self


class MaterializedScenarioCase(OfficeV2Contract):
    case_id: Identifier
    case_version: Identifier = OFFICE_V2_ATTACK_CASE_CATALOG_VERSION
    base_world_version: WorldVersion
    base_world_digest: Sha256Digest
    initial_world_digest: Sha256Digest
    actor: ActorContext
    task: TaskContract
    task_bindings: tuple[ResolvedBinding, ...]
    objective_bindings: tuple[ObjectiveResolvedBinding, ...]
    interaction_contract: InteractionContract
    attack_objective: AttackObjectiveTemplate
    adversarial_condition: AdversarialCondition
    reachable_surface: ReachableAttackSurface | None = None
    materialization_record: AttackMaterializationRecord
    parent_case_id: Identifier
    parent_case_digest: Sha256Digest
    lineage: tuple[Identifier, ...] = Field(min_length=1)
    seed: int = Field(ge=0)
    content_digest: Sha256Digest

    @field_validator("task_bindings")
    @classmethod
    def task_bindings_are_canonical(
        cls, value: tuple[ResolvedBinding, ...]
    ) -> tuple[ResolvedBinding, ...]:
        query_ids = tuple(item.query_id for item in value)
        _canonical_strings(query_ids, field_name="task_bindings")
        return tuple(sorted(value, key=lambda item: item.query_id))

    @field_validator("objective_bindings")
    @classmethod
    def objective_bindings_are_canonical(
        cls, value: tuple[ObjectiveResolvedBinding, ...]
    ) -> tuple[ObjectiveResolvedBinding, ...]:
        slot_ids = tuple(item.slot_id for item in value)
        _canonical_strings(slot_ids, field_name="objective_bindings")
        return tuple(sorted(value, key=lambda item: item.slot_id))

    @field_validator("lineage")
    @classmethod
    def lineage_is_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("lineage must not contain duplicates")
        return value

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"content_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def identities_and_digest_match(self) -> Self:
        if self.actor.actor_id != self.task.actor_id:
            raise ValueError("scenario actor and task actor must match")
        if self.parent_case_id != self.adversarial_condition.parent_case_id:
            raise ValueError("scenario and condition parent case ids differ")
        if self.attack_objective.objective_id != self.adversarial_condition.objective_id:
            raise ValueError("scenario condition references a different objective")
        if self.parent_case_digest != self.materialization_record.parent_case_digest:
            raise ValueError("scenario and materialization parent digests differ")
        if self.content_digest != sha256_digest(self.digest_payload()):
            raise ValueError("scenario content_digest does not match payload")
        return self


__all__ = [
    "ADVERSARIAL_CONDITION_ADAPTER",
    "AdversarialCondition",
    "AttackEntryKind",
    "AttackFieldOperation",
    "AttackFieldValueKind",
    "AttackMaterializationRecord",
    "AttackObjectiveCatalog",
    "AttackObjectiveTemplate",
    "AttackableFieldCatalog",
    "AttackableFieldSpec",
    "CompatibilityDecision",
    "CompatibilityPurpose",
    "CompatibilityReasonCode",
    "CompatibilityStatus",
    "ContentPlacement",
    "DirectTaskCondition",
    "ForgedAuthorizationCondition",
    "IndirectContentCondition",
    "MaterializedFieldChange",
    "MaterializedScenarioCase",
    "ObjectiveBindingKind",
    "ObjectiveBindingSlot",
    "ObjectiveFactAssertion",
    "ObjectiveFactKind",
    "ObjectiveMilestone",
    "ObjectiveMilestoneGraph",
    "ObjectiveResolvedBinding",
    "ParameterSourceManipulationCondition",
    "PolicyFeasibilityFact",
    "ReachableAttackSurface",
    "ReachableAttackSurfaceCatalog",
    "ReachableContentField",
    "SemanticParameterKind",
]
