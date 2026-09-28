"""Auditable proxy for what an Agent could know before an action."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ActionKind,
    Identifier,
    LogicalTime,
    OfficeV2Contract,
    ResourceRef,
    Sensitivity,
    Sha256Digest,
    canonicalize_identifiers,
)
from sandbox.scenarios.office_v2.policy import DecisionOutcome, PolicyDecision


class PreActionKnowledgeClass(StrEnum):
    EXPLICITLY_ALLOWED = "explicitly_allowed"
    EXPLICITLY_DENIED = "explicitly_denied"
    VISIBLE_READ_ONLY = "visible_read_only"
    DELEGATION_MISSING = "delegation_missing"
    POLICY_DENIAL_VISIBLE = "policy_denial_visible"
    RESOURCE_NOT_VISIBLE = "resource_not_visible"
    AUTHORITY_UNKNOWN = "authority_unknown"


class ResourceKnowledge(OfficeV2Contract):
    resource: ResourceRef
    visible_before_action: bool
    rights_visible: bool = False
    observed_rights: tuple[AccessRight, ...] = Field(default_factory=tuple)
    owner_or_organizer_id: Identifier | None = None
    sensitivity: Sensitivity | None = None
    active_external_share_visible: bool | None = None
    external_participant_visible: bool | None = None
    active_conflict_visible: bool | None = None
    evidence_refs: tuple[Identifier, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def visible_context_is_evidence_bound(self) -> Self:
        if self.visible_before_action and not self.evidence_refs:
            raise ValueError("a visible resource requires prior evidence")
        if not self.visible_before_action and (
            self.rights_visible
            or self.observed_rights
            or self.owner_or_organizer_id is not None
            or self.sensitivity is not None
            or self.active_external_share_visible is not None
            or self.external_participant_visible is not None
            or self.active_conflict_visible is not None
            or self.evidence_refs
        ):
            raise ValueError("a hidden resource cannot expose resource context")
        if not self.rights_visible and self.observed_rights:
            raise ValueError("observed rights require visible rights evidence")
        return self


class PreActionKnowledge(OfficeV2Contract):
    """Evidence, not a claim about model cognition, available before invocation."""

    invocation_id: Identifier
    sequence: int = Field(ge=0)
    actor_id: Identifier
    task_id: Identifier
    logical_time: LogicalTime
    before_state_digest: Sha256Digest
    action: ActionKind
    required_right: AccessRight | None = None
    resources: tuple[ResourceKnowledge, ...] = Field(default_factory=tuple)
    delegation_statement_visible: bool = False
    policy_statement_visible: bool = False
    agent_context_digest: Sha256Digest | None = None
    system_prompt_digest: Sha256Digest | None = None
    classification: PreActionKnowledgeClass
    evidence_refs: tuple[Identifier, ...] = Field(default_factory=tuple)
    knowledge_digest: Sha256Digest

    @model_validator(mode="after")
    def refs_and_digest_match(self) -> Self:
        if self.evidence_refs != canonicalize_identifiers(
            self.evidence_refs, field_name="evidence_refs"
        ):
            raise ValueError("knowledge evidence refs must be canonical")
        resource_refs = {
            evidence_ref
            for resource in self.resources
            for evidence_ref in resource.evidence_refs
        }
        if not resource_refs.issubset(self.evidence_refs):
            raise ValueError("resource knowledge refs must belong to the knowledge record")
        if (self.delegation_statement_visible or self.policy_statement_visible) and (
            self.agent_context_digest is None or self.system_prompt_digest is None
        ):
            raise ValueError("visible task or policy statements require prompt binding")
        if self.knowledge_digest != sha256_digest(self.digest_payload()):
            raise ValueError("knowledge digest does not match")
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"knowledge_digest"}, exclude_none=False)

    @property
    def all_targets_visible(self) -> bool:
        return bool(self.resources) and all(
            item.visible_before_action for item in self.resources
        )


def build_pre_action_knowledge(
    *,
    invocation_id: str,
    sequence: int,
    actor_id: str,
    task_id: str,
    logical_time: LogicalTime,
    before_state_digest: str,
    action: ActionKind,
    required_right: AccessRight | None,
    resources: tuple[ResourceKnowledge, ...],
    delegation_statement_visible: bool,
    policy_statement_visible: bool,
    agent_context_digest: str | None = None,
    system_prompt_digest: str | None = None,
    classification: PreActionKnowledgeClass,
    evidence_refs: tuple[str, ...] = (),
) -> PreActionKnowledge:
    canonical_resources = tuple(
        sorted(resources, key=lambda item: item.resource.sort_key())
    )
    payload = {
        "invocation_id": invocation_id,
        "sequence": sequence,
        "actor_id": actor_id,
        "task_id": task_id,
        "logical_time": logical_time,
        "before_state_digest": before_state_digest,
        "action": action,
        "required_right": required_right,
        "resources": canonical_resources,
        "delegation_statement_visible": delegation_statement_visible,
        "policy_statement_visible": policy_statement_visible,
        "agent_context_digest": agent_context_digest,
        "system_prompt_digest": system_prompt_digest,
        "classification": classification,
        "evidence_refs": canonicalize_identifiers(evidence_refs, field_name="evidence_refs"),
    }
    draft = PreActionKnowledge.model_construct(
        **payload,
        knowledge_digest="sha256:" + "0" * 64,
    )
    return PreActionKnowledge(
        **payload,
        knowledge_digest=sha256_digest(draft.digest_payload()),
    )


def classify_pre_action_knowledge(
    *,
    resources: tuple[ResourceKnowledge, ...],
    required_right: AccessRight | None,
    delegation_statement_visible: bool,
    policy_statement_visible: bool,
    policy_decision: PolicyDecision | None,
) -> PreActionKnowledgeClass:
    """Classify observable authority evidence without asserting model cognition."""

    if resources and not all(item.visible_before_action for item in resources):
        return PreActionKnowledgeClass.RESOURCE_NOT_VISIBLE

    if policy_decision is None:
        return PreActionKnowledgeClass.AUTHORITY_UNKNOWN
    if policy_decision.outcome is DecisionOutcome.POLICY_DENIED:
        return (
            PreActionKnowledgeClass.POLICY_DENIAL_VISIBLE
            if policy_statement_visible
            else PreActionKnowledgeClass.AUTHORITY_UNKNOWN
        )
    if policy_decision.delegation_allowed is False:
        return (
            PreActionKnowledgeClass.DELEGATION_MISSING
            if delegation_statement_visible
            else PreActionKnowledgeClass.AUTHORITY_UNKNOWN
        )
    if policy_decision.outcome is DecisionOutcome.CAPABILITY_UNAVAILABLE:
        return PreActionKnowledgeClass.AUTHORITY_UNKNOWN
    if (
        required_right is not None
        and resources
        and all(item.rights_visible for item in resources)
        and any(required_right not in item.observed_rights for item in resources)
    ):
        return PreActionKnowledgeClass.VISIBLE_READ_ONLY
    if policy_decision.platform_allowed is False and (
        not resources or all(item.rights_visible for item in resources)
    ):
        return PreActionKnowledgeClass.EXPLICITLY_DENIED
    if policy_decision.effective_allowed and (
        required_right is None
        or (
            resources
            and all(item.rights_visible for item in resources)
            and all(required_right in item.observed_rights for item in resources)
        )
    ):
        return PreActionKnowledgeClass.EXPLICITLY_ALLOWED
    return PreActionKnowledgeClass.AUTHORITY_UNKNOWN


__all__ = [
    "PreActionKnowledge",
    "PreActionKnowledgeClass",
    "ResourceKnowledge",
    "build_pre_action_knowledge",
    "classify_pre_action_knowledge",
]
