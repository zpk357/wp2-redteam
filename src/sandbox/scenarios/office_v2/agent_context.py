"""Strict, evidence-bound contracts for the Office V2 Agent context."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_AGENT_CONTEXT_VERSION,
    OFFICE_V2_AGENT_SURFACE_VERSION,
    OFFICE_V2_CONTRACT_SCHEMA_VERSION,
)
from sandbox.scenarios.office_v2.canonical_world import OfficeWorldState
from sandbox.scenarios.office_v2.models import (
    ActorContext,
    DecisionMode,
    IssuerAuthentication,
    OfficeV2Contract,
    Principal,
    PrincipalKind,
    Sha256Digest,
    TaskContract,
    TaskDelegation,
    TimezoneName,
)
from sandbox.scenarios.office_v2.policy import EnterprisePolicyRule
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_TOOL_NAMES,
    office_v2_tool_definitions,
)
from sandbox.scenarios.office_v2.tools.runtime import ToolDefinition

VISIBLE_POLICY_SUMMARY_VERSION = "office-v2-visible-policy-summary-v1"
CONTEXT_FIELD_EVIDENCE_VERSION = "office-v2-context-field-evidence-v1"
AGENT_CONTEXT_EVIDENCE_VERSION = "office-v2-agent-context-evidence-v1"
AGENT_PROMPT_ENVELOPE_VERSION = "office-v2-agent-prompt-envelope-v1"
AGENT_PROMPT_RENDER_VERSION = "office-v2-agent-prompt-render-v1"
AGENT_IDENTITY_CONTEXT_FRAGMENT_VERSION = "office-v2-agent-identity-context-v1"
AGENT_POLICY_CAPABILITY_FRAGMENT_VERSION = (
    "office-v2-agent-policy-capability-context-v1"
)

VisibleText = Annotated[str, Field(min_length=1, max_length=512)]
LongVisibleText = Annotated[str, Field(min_length=1, max_length=2048)]
VisibleFieldPath = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9_]*(?:\.(?:[a-z][a-z0-9_]*|[0-9]+))*$"),
]


class ContextEvidenceSourceKind(StrEnum):
    DIRECTORY = "directory"
    ACTOR = "actor"
    TASK = "task"
    POLICY = "policy"
    CLOCK = "clock"
    SESSION_SURFACE = "session_surface"


def _canonical_texts(values: tuple[str, ...], *, unique: bool) -> tuple[str, ...]:
    if any(not value or value != value.strip() for value in values):
        raise ValueError("visible text values must be non-empty and trimmed")
    if unique and len(values) != len(set(values)):
        raise ValueError("visible text values must not contain duplicates")
    return tuple(sorted(values))


class VisiblePolicySummary(OfficeV2Contract):
    summary_version: Literal["office-v2-visible-policy-summary-v1"] = (
        VISIBLE_POLICY_SUMMARY_VERSION
    )
    policy_category: str = Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")
    decision_mode: DecisionMode
    resource_scope: VisibleText
    recipient_scope: VisibleText | None = None
    description: LongVisibleText

    def sort_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.policy_category,
            self.decision_mode.value,
            self.resource_scope,
            self.recipient_scope or "",
            self.description,
        )


class ContextFieldEvidence(OfficeV2Contract):
    evidence_version: Literal["office-v2-context-field-evidence-v1"] = (
        CONTEXT_FIELD_EVIDENCE_VERSION
    )
    visible_field_path: VisibleFieldPath
    source_kind: ContextEvidenceSourceKind
    source_object_id: str = Field(min_length=1, max_length=512)
    source_field_path: VisibleFieldPath
    value_digest: Sha256Digest

    def sort_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.visible_field_path,
            self.source_kind.value,
            self.source_object_id,
            self.source_field_path,
            self.value_digest,
        )


class AgentContextEvidence(OfficeV2Contract):
    evidence_version: Literal["office-v2-agent-context-evidence-v1"] = (
        AGENT_CONTEXT_EVIDENCE_VERSION
    )
    context_version: Literal["office-v2-agent-context-v1"] = (
        OFFICE_V2_AGENT_CONTEXT_VERSION
    )
    fields: tuple[ContextFieldEvidence, ...]
    evidence_digest: Sha256Digest

    @field_validator("fields")
    @classmethod
    def fields_are_canonical(
        cls, value: tuple[ContextFieldEvidence, ...]
    ) -> tuple[ContextFieldEvidence, ...]:
        paths = tuple(item.visible_field_path for item in value)
        if len(paths) != len(set(paths)):
            raise ValueError("each visible context leaf must have exactly one evidence source")
        return tuple(sorted(value, key=ContextFieldEvidence.sort_key))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"evidence_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def digest_is_valid(self) -> Self:
        if self.evidence_digest != sha256_digest(self.digest_payload()):
            raise ValueError("evidence_digest does not match Agent context evidence")
        return self


class AgentWorkspaceContext(OfficeV2Contract):
    context_version: Literal["office-v2-agent-context-v1"] = (
        OFFICE_V2_AGENT_CONTEXT_VERSION
    )
    organization_name: VisibleText
    actor_display_name: VisibleText
    actor_email: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    )
    role_names: tuple[VisibleText, ...] = Field(default_factory=tuple)
    group_names: tuple[VisibleText, ...] = Field(default_factory=tuple)
    logical_time: int = Field(ge=0)
    timezone: TimezoneName
    mailbox_identity: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    )
    workspace_root: Literal["/workspace"] = "/workspace"
    task_issuer_display_name: VisibleText
    task_issuer_authentication: IssuerAuthentication
    delegated_action_summaries: tuple[LongVisibleText, ...] = Field(
        default_factory=tuple
    )
    visible_policy_summaries: tuple[VisiblePolicySummary, ...] = Field(
        default_factory=tuple
    )
    available_business_tool_names: tuple[str, ...] = Field(min_length=1)
    evidence: AgentContextEvidence
    context_digest: Sha256Digest

    @field_validator("role_names", "group_names")
    @classmethod
    def display_names_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_texts(value, unique=False)

    @field_validator("delegated_action_summaries")
    @classmethod
    def delegated_actions_are_canonical(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _canonical_texts(value, unique=True)

    @field_validator("visible_policy_summaries")
    @classmethod
    def policy_summaries_are_canonical(
        cls, value: tuple[VisiblePolicySummary, ...]
    ) -> tuple[VisiblePolicySummary, ...]:
        keys = tuple(item.sort_key() for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError("visible policy summaries must not contain duplicates")
        return tuple(sorted(value, key=VisiblePolicySummary.sort_key))

    @field_validator("available_business_tool_names")
    @classmethod
    def business_tools_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not name
            or name != name.strip()
            or not name.replace("_", "a").isalnum()
            or name.lower() != name
            for name in value
        ):
            raise ValueError("business tool names must be canonical snake_case names")
        return _canonical_texts(value, unique=True)

    def model_visible_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={
                "schema_version",
                "context_version",
                "evidence",
                "context_digest",
            },
            exclude_none=False,
        )

    def digest_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "context_version": self.context_version,
            "visible_context": self.model_visible_payload(),
            "evidence_digest": self.evidence.evidence_digest,
        }

    @model_validator(mode="after")
    def evidence_and_digest_are_valid(self) -> Self:
        if self.evidence.context_version != self.context_version:
            raise ValueError("context and evidence versions do not match")
        visible_leaves = _visible_leaf_values(self.model_visible_payload())
        evidence_by_path = {
            item.visible_field_path: item for item in self.evidence.fields
        }
        if set(evidence_by_path) != set(visible_leaves):
            raise ValueError("context evidence paths do not match visible context leaves")
        for path, value in visible_leaves.items():
            if evidence_by_path[path].value_digest != sha256_digest(value):
                raise ValueError(f"context evidence value does not match {path}")
        if self.context_digest != sha256_digest(self.digest_payload()):
            raise ValueError("context_digest does not match Agent workspace context")
        return self


class AgentIdentityContextFragment(OfficeV2Contract):
    fragment_version: Literal["office-v2-agent-identity-context-v1"] = (
        AGENT_IDENTITY_CONTEXT_FRAGMENT_VERSION
    )
    context_version: Literal["office-v2-agent-context-v1"] = (
        OFFICE_V2_AGENT_CONTEXT_VERSION
    )
    organization_name: VisibleText
    actor_display_name: VisibleText
    actor_email: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    )
    role_names: tuple[VisibleText, ...] = Field(default_factory=tuple)
    group_names: tuple[VisibleText, ...] = Field(default_factory=tuple)
    logical_time: int = Field(ge=0)
    timezone: TimezoneName
    mailbox_identity: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    )
    workspace_root: Literal["/workspace"] = "/workspace"
    task_issuer_display_name: VisibleText
    task_issuer_authentication: IssuerAuthentication
    evidence_fields: tuple[ContextFieldEvidence, ...]
    fragment_digest: Sha256Digest

    @field_validator("role_names", "group_names")
    @classmethod
    def display_names_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _canonical_texts(value, unique=False)

    @field_validator("evidence_fields")
    @classmethod
    def evidence_is_canonical(
        cls, value: tuple[ContextFieldEvidence, ...]
    ) -> tuple[ContextFieldEvidence, ...]:
        paths = tuple(item.visible_field_path for item in value)
        if len(paths) != len(set(paths)):
            raise ValueError("identity context leaves require one evidence source")
        return tuple(sorted(value, key=ContextFieldEvidence.sort_key))

    def model_visible_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={
                "schema_version",
                "fragment_version",
                "context_version",
                "evidence_fields",
                "fragment_digest",
            },
            exclude_none=False,
        )

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"fragment_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def evidence_and_digest_are_valid(self) -> Self:
        visible_leaves = _visible_leaf_values(self.model_visible_payload())
        evidence_by_path = {
            item.visible_field_path: item for item in self.evidence_fields
        }
        if set(evidence_by_path) != set(visible_leaves):
            raise ValueError("identity evidence paths do not match visible leaves")
        for path, value in visible_leaves.items():
            if evidence_by_path[path].value_digest != sha256_digest(value):
                raise ValueError(f"identity evidence value does not match {path}")
        if self.fragment_digest != sha256_digest(self.digest_payload()):
            raise ValueError("fragment_digest does not match identity context")
        return self


class AgentPolicyCapabilityFragment(OfficeV2Contract):
    fragment_version: Literal[
        "office-v2-agent-policy-capability-context-v1"
    ] = AGENT_POLICY_CAPABILITY_FRAGMENT_VERSION
    context_version: Literal["office-v2-agent-context-v1"] = (
        OFFICE_V2_AGENT_CONTEXT_VERSION
    )
    delegated_action_summaries: tuple[LongVisibleText, ...] = Field(
        default_factory=tuple
    )
    visible_policy_summaries: tuple[VisiblePolicySummary, ...] = Field(
        default_factory=tuple
    )
    available_business_tool_names: tuple[str, ...] = Field(min_length=1)
    evidence_fields: tuple[ContextFieldEvidence, ...]
    fragment_digest: Sha256Digest

    @field_validator("delegated_action_summaries")
    @classmethod
    def delegated_actions_are_canonical(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _canonical_texts(value, unique=True)

    @field_validator("visible_policy_summaries")
    @classmethod
    def policy_summaries_are_canonical(
        cls, value: tuple[VisiblePolicySummary, ...]
    ) -> tuple[VisiblePolicySummary, ...]:
        keys = tuple(item.sort_key() for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError("visible policy summaries must not contain duplicates")
        return tuple(sorted(value, key=VisiblePolicySummary.sort_key))

    @field_validator("available_business_tool_names")
    @classmethod
    def business_tools_are_frozen(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _canonical_texts(value, unique=True)
        if set(normalized) != set(OFFICE_V2_TOOL_NAMES):
            raise ValueError("Agent capability context requires all 17 Office V2 tools")
        return normalized

    @field_validator("evidence_fields")
    @classmethod
    def evidence_is_canonical(
        cls, value: tuple[ContextFieldEvidence, ...]
    ) -> tuple[ContextFieldEvidence, ...]:
        paths = tuple(item.visible_field_path for item in value)
        if len(paths) != len(set(paths)):
            raise ValueError("policy capability leaves require one evidence source")
        return tuple(sorted(value, key=ContextFieldEvidence.sort_key))

    def model_visible_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={
                "schema_version",
                "fragment_version",
                "context_version",
                "evidence_fields",
                "fragment_digest",
            },
            exclude_none=False,
        )

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"fragment_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def evidence_and_digest_are_valid(self) -> Self:
        visible_leaves = _visible_leaf_values(self.model_visible_payload())
        evidence_by_path = {
            item.visible_field_path: item for item in self.evidence_fields
        }
        if set(evidence_by_path) != set(visible_leaves):
            raise ValueError("policy capability evidence paths do not match visible leaves")
        for path, value in visible_leaves.items():
            if evidence_by_path[path].value_digest != sha256_digest(value):
                raise ValueError(f"policy capability evidence value does not match {path}")
        if self.fragment_digest != sha256_digest(self.digest_payload()):
            raise ValueError("fragment_digest does not match policy capability context")
        return self


class AgentPromptEnvelope(OfficeV2Contract):
    envelope_version: Literal["office-v2-agent-prompt-envelope-v1"] = (
        AGENT_PROMPT_ENVELOPE_VERSION
    )
    surface_version: Literal["office-v2-agent-surface-v1"] = (
        OFFICE_V2_AGENT_SURFACE_VERSION
    )
    render_version: Literal["office-v2-agent-prompt-render-v1"] = (
        AGENT_PROMPT_RENDER_VERSION
    )
    base_version: str = Field(min_length=1, max_length=128)
    base_digest: Sha256Digest
    context_version: Literal["office-v2-agent-context-v1"] = (
        OFFICE_V2_AGENT_CONTEXT_VERSION
    )
    context_digest: Sha256Digest
    tool_spec_digest: Sha256Digest
    system_message_digest: Sha256Digest
    envelope_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"envelope_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def digest_is_valid(self) -> Self:
        if self.envelope_digest != sha256_digest(self.digest_payload()):
            raise ValueError("envelope_digest does not match Agent prompt envelope")
        return self


class AgentRenderedSystemPrompt(OfficeV2Contract):
    render_version: Literal["office-v2-agent-prompt-render-v1"] = (
        AGENT_PROMPT_RENDER_VERSION
    )
    system_message: str = Field(min_length=1, max_length=32768)
    envelope: AgentPromptEnvelope

    @model_validator(mode="after")
    def message_matches_envelope(self) -> Self:
        if self.envelope.render_version != self.render_version:
            raise ValueError("rendered system message and envelope versions do not match")
        if sha256_digest(self.system_message) != self.envelope.system_message_digest:
            raise ValueError("system message does not match prompt envelope")
        return self


def _visible_leaf_values(payload: dict[str, Any]) -> dict[str, Any]:
    leaves: dict[str, Any] = {}
    for field_name, value in payload.items():
        if isinstance(value, list):
            for index, item in enumerate(value):
                leaves[f"{field_name}.{index}"] = item
        else:
            leaves[field_name] = value
    return leaves


def build_context_field_evidence(
    *,
    visible_field_path: str,
    visible_value: Any,
    source_kind: ContextEvidenceSourceKind,
    source_object_id: str,
    source_field_path: str,
) -> ContextFieldEvidence:
    return ContextFieldEvidence(
        visible_field_path=visible_field_path,
        source_kind=source_kind,
        source_object_id=source_object_id,
        source_field_path=source_field_path,
        value_digest=sha256_digest(visible_value),
    )


def build_agent_context_evidence(
    fields: tuple[ContextFieldEvidence, ...],
) -> AgentContextEvidence:
    canonical_fields = tuple(sorted(fields, key=ContextFieldEvidence.sort_key))
    payload = {
        "schema_version": OFFICE_V2_CONTRACT_SCHEMA_VERSION,
        "evidence_version": AGENT_CONTEXT_EVIDENCE_VERSION,
        "context_version": OFFICE_V2_AGENT_CONTEXT_VERSION,
        "fields": [item.model_dump(mode="json") for item in canonical_fields],
    }
    return AgentContextEvidence(
        fields=canonical_fields,
        evidence_digest=sha256_digest(payload),
    )


def build_agent_workspace_context(
    *,
    evidence: AgentContextEvidence,
    organization_name: str,
    actor_display_name: str,
    actor_email: str,
    role_names: tuple[str, ...],
    group_names: tuple[str, ...],
    logical_time: int,
    timezone: str,
    mailbox_identity: str,
    workspace_root: Literal["/workspace"] = "/workspace",
    task_issuer_display_name: str,
    task_issuer_authentication: IssuerAuthentication,
    delegated_action_summaries: tuple[str, ...],
    visible_policy_summaries: tuple[VisiblePolicySummary, ...],
    available_business_tool_names: tuple[str, ...],
) -> AgentWorkspaceContext:
    normalized = {
        "organization_name": organization_name,
        "actor_display_name": actor_display_name,
        "actor_email": actor_email,
        "role_names": tuple(sorted(role_names)),
        "group_names": tuple(sorted(group_names)),
        "logical_time": logical_time,
        "timezone": timezone,
        "mailbox_identity": mailbox_identity,
        "workspace_root": workspace_root,
        "task_issuer_display_name": task_issuer_display_name,
        "task_issuer_authentication": task_issuer_authentication,
        "delegated_action_summaries": tuple(sorted(delegated_action_summaries)),
        "visible_policy_summaries": tuple(
            sorted(visible_policy_summaries, key=VisiblePolicySummary.sort_key)
        ),
        "available_business_tool_names": tuple(sorted(available_business_tool_names)),
    }
    visible_payload = {
        key: [item.model_dump(mode="json") for item in value]
        if key == "visible_policy_summaries"
        else list(value)
        if isinstance(value, tuple)
        else value.value
        if isinstance(value, StrEnum)
        else value
        for key, value in normalized.items()
    }
    digest_payload = {
        "schema_version": OFFICE_V2_CONTRACT_SCHEMA_VERSION,
        "context_version": OFFICE_V2_AGENT_CONTEXT_VERSION,
        "visible_context": visible_payload,
        "evidence_digest": evidence.evidence_digest,
    }
    return AgentWorkspaceContext(
        **normalized,
        evidence=evidence,
        context_digest=sha256_digest(digest_payload),
    )


def build_agent_prompt_envelope(
    *,
    base_version: str,
    base_digest: str,
    context_digest: str,
    tool_spec_digest: str,
    system_message_digest: str,
) -> AgentPromptEnvelope:
    payload = {
        "schema_version": OFFICE_V2_CONTRACT_SCHEMA_VERSION,
        "envelope_version": AGENT_PROMPT_ENVELOPE_VERSION,
        "surface_version": OFFICE_V2_AGENT_SURFACE_VERSION,
        "render_version": AGENT_PROMPT_RENDER_VERSION,
        "base_version": base_version,
        "base_digest": base_digest,
        "context_version": OFFICE_V2_AGENT_CONTEXT_VERSION,
        "context_digest": context_digest,
        "tool_spec_digest": tool_spec_digest,
        "system_message_digest": system_message_digest,
    }
    return AgentPromptEnvelope(**payload, envelope_digest=sha256_digest(payload))


def derive_agent_identity_context(
    state: OfficeWorldState,
    actor: ActorContext,
    task: TaskContract,
) -> AgentIdentityContextFragment:
    directory = state.domain_graph.directory
    expected_actor = directory.derive_actor_context(
        actor_id=actor.actor_id,
        authenticated_principal_id=actor.authenticated_principal_id,
        session_capabilities=actor.session_capabilities,
        logical_time=state.logical_clock.now,
    )
    if actor != expected_actor:
        raise ValueError("ActorContext does not match the current directory and clock")
    if task.actor_id != actor.actor_id:
        raise ValueError("TaskContract actor does not match Agent ActorContext")

    principals = {item.principal_id: item for item in directory.principals}
    actor_principal = _required_principal(principals, actor.actor_id, "actor")
    mailbox_principal = _required_principal(
        principals, actor.mailbox_owner_id, "mailbox owner"
    )
    issuer_principal = _required_principal(
        principals, task.issuer_principal_id, "task issuer"
    )
    role_values = tuple(
        sorted(
            ((_display_role_id(role_id), role_id) for role_id in actor.active_role_ids),
            key=lambda item: (item[0], item[1]),
        )
    )
    group_values = tuple(
        sorted(
            (
                (
                    _required_group(principals, group_id).display_name,
                    group_id,
                )
                for group_id in actor.active_group_ids
            ),
            key=lambda item: (item[0], item[1]),
        )
    )
    visible = {
        "organization_name": directory.organization.name,
        "actor_display_name": actor_principal.display_name,
        "actor_email": actor_principal.email,
        "role_names": tuple(item[0] for item in role_values),
        "group_names": tuple(item[0] for item in group_values),
        "logical_time": state.logical_clock.now,
        "timezone": state.logical_clock.timezone,
        "mailbox_identity": mailbox_principal.email,
        "workspace_root": actor.workspace_root,
        "task_issuer_display_name": issuer_principal.display_name,
        "task_issuer_authentication": task.issuer_authentication,
    }
    evidence_fields = (
        build_context_field_evidence(
            visible_field_path="organization_name",
            visible_value=visible["organization_name"],
            source_kind=ContextEvidenceSourceKind.DIRECTORY,
            source_object_id=directory.organization.organization_id,
            source_field_path="organization.name",
        ),
        build_context_field_evidence(
            visible_field_path="actor_display_name",
            visible_value=visible["actor_display_name"],
            source_kind=ContextEvidenceSourceKind.DIRECTORY,
            source_object_id=actor_principal.principal_id,
            source_field_path="principal.display_name",
        ),
        build_context_field_evidence(
            visible_field_path="actor_email",
            visible_value=visible["actor_email"],
            source_kind=ContextEvidenceSourceKind.DIRECTORY,
            source_object_id=actor_principal.principal_id,
            source_field_path="principal.email",
        ),
        *(
            build_context_field_evidence(
                visible_field_path=f"role_names.{index}",
                visible_value=display_name,
                source_kind=ContextEvidenceSourceKind.DIRECTORY,
                source_object_id=role_id,
                source_field_path="role_assignment.role_id",
            )
            for index, (display_name, role_id) in enumerate(role_values)
        ),
        *(
            build_context_field_evidence(
                visible_field_path=f"group_names.{index}",
                visible_value=display_name,
                source_kind=ContextEvidenceSourceKind.DIRECTORY,
                source_object_id=group_id,
                source_field_path="principal.display_name",
            )
            for index, (display_name, group_id) in enumerate(group_values)
        ),
        build_context_field_evidence(
            visible_field_path="logical_time",
            visible_value=visible["logical_time"],
            source_kind=ContextEvidenceSourceKind.CLOCK,
            source_object_id="logical_clock",
            source_field_path="logical_clock.now",
        ),
        build_context_field_evidence(
            visible_field_path="timezone",
            visible_value=visible["timezone"],
            source_kind=ContextEvidenceSourceKind.CLOCK,
            source_object_id="logical_clock",
            source_field_path="logical_clock.timezone",
        ),
        build_context_field_evidence(
            visible_field_path="mailbox_identity",
            visible_value=visible["mailbox_identity"],
            source_kind=ContextEvidenceSourceKind.DIRECTORY,
            source_object_id=mailbox_principal.principal_id,
            source_field_path="principal.email",
        ),
        build_context_field_evidence(
            visible_field_path="workspace_root",
            visible_value=visible["workspace_root"],
            source_kind=ContextEvidenceSourceKind.ACTOR,
            source_object_id=actor.actor_id,
            source_field_path="actor_context.workspace_root",
        ),
        build_context_field_evidence(
            visible_field_path="task_issuer_display_name",
            visible_value=visible["task_issuer_display_name"],
            source_kind=ContextEvidenceSourceKind.DIRECTORY,
            source_object_id=issuer_principal.principal_id,
            source_field_path="principal.display_name",
        ),
        build_context_field_evidence(
            visible_field_path="task_issuer_authentication",
            visible_value=task.issuer_authentication.value,
            source_kind=ContextEvidenceSourceKind.TASK,
            source_object_id=task.task_id,
            source_field_path="task_contract.issuer_authentication",
        ),
    )
    payload = {
        "schema_version": OFFICE_V2_CONTRACT_SCHEMA_VERSION,
        "fragment_version": AGENT_IDENTITY_CONTEXT_FRAGMENT_VERSION,
        "context_version": OFFICE_V2_AGENT_CONTEXT_VERSION,
        **visible,
        "evidence_fields": tuple(
            sorted(evidence_fields, key=ContextFieldEvidence.sort_key)
        ),
    }
    return AgentIdentityContextFragment(
        **payload,
        fragment_digest=sha256_digest(payload),
    )


def derive_agent_policy_capability_context(
    state: OfficeWorldState,
    task: TaskContract,
    tool_definitions: Mapping[str, ToolDefinition],
) -> AgentPolicyCapabilityFragment:
    _validate_frozen_tool_definitions(tool_definitions)

    delegated_values = tuple(
        sorted(
            (
                (_delegation_summary(index, delegation), delegation.delegation_id)
                for index, delegation in enumerate(task.delegated_actions, start=1)
            ),
            key=lambda item: item[0],
        )
    )
    policy_values = tuple(
        sorted(
            (
                (_visible_policy_summary(index, rule), rule.rule_id)
                for index, rule in enumerate(state.policy_rules, start=1)
                if rule.is_active(state.logical_clock.now)
            ),
            key=lambda item: item[0].sort_key(),
        )
    )
    tool_names = tuple(sorted(tool_definitions))
    evidence_fields = (
        *(
            build_context_field_evidence(
                visible_field_path=f"delegated_action_summaries.{index}",
                visible_value=summary,
                source_kind=ContextEvidenceSourceKind.TASK,
                source_object_id=delegation_id,
                source_field_path="task_delegation.action_scope",
            )
            for index, (summary, delegation_id) in enumerate(delegated_values)
        ),
        *(
            build_context_field_evidence(
                visible_field_path=f"visible_policy_summaries.{index}",
                visible_value=summary.model_dump(mode="json"),
                source_kind=ContextEvidenceSourceKind.POLICY,
                source_object_id=rule_id,
                source_field_path="enterprise_policy_rule",
            )
            for index, (summary, rule_id) in enumerate(policy_values)
        ),
        *(
            build_context_field_evidence(
                visible_field_path=f"available_business_tool_names.{index}",
                visible_value=name,
                source_kind=ContextEvidenceSourceKind.SESSION_SURFACE,
                source_object_id=name,
                source_field_path="tool_definition.name",
            )
            for index, name in enumerate(tool_names)
        ),
    )
    payload = {
        "schema_version": OFFICE_V2_CONTRACT_SCHEMA_VERSION,
        "fragment_version": AGENT_POLICY_CAPABILITY_FRAGMENT_VERSION,
        "context_version": OFFICE_V2_AGENT_CONTEXT_VERSION,
        "delegated_action_summaries": tuple(item[0] for item in delegated_values),
        "visible_policy_summaries": tuple(item[0] for item in policy_values),
        "available_business_tool_names": tool_names,
        "evidence_fields": tuple(
            sorted(evidence_fields, key=ContextFieldEvidence.sort_key)
        ),
    }
    return AgentPolicyCapabilityFragment(
        **payload,
        fragment_digest=sha256_digest(payload),
    )


def assemble_agent_workspace_context(
    identity: AgentIdentityContextFragment,
    policy_capability: AgentPolicyCapabilityFragment,
) -> AgentWorkspaceContext:
    if identity.context_version != policy_capability.context_version:
        raise ValueError("Agent context fragments use different context versions")
    evidence = build_agent_context_evidence(
        (*identity.evidence_fields, *policy_capability.evidence_fields)
    )
    return build_agent_workspace_context(
        evidence=evidence,
        organization_name=identity.organization_name,
        actor_display_name=identity.actor_display_name,
        actor_email=identity.actor_email,
        role_names=identity.role_names,
        group_names=identity.group_names,
        logical_time=identity.logical_time,
        timezone=identity.timezone,
        mailbox_identity=identity.mailbox_identity,
        workspace_root=identity.workspace_root,
        task_issuer_display_name=identity.task_issuer_display_name,
        task_issuer_authentication=identity.task_issuer_authentication,
        delegated_action_summaries=policy_capability.delegated_action_summaries,
        visible_policy_summaries=policy_capability.visible_policy_summaries,
        available_business_tool_names=(
            policy_capability.available_business_tool_names
        ),
    )


def _delegation_summary(index: int, delegation: TaskDelegation) -> str:
    scope = delegation.action_scope
    resources = ", ".join(item.value for item in scope.resource_kinds)
    resource_scope = (
        f"{len(delegation.resource_query_ids)} task resource constraint(s)"
        if delegation.resource_query_ids
        else "any task resource of these types"
    )
    recipient_scope = (
        f"{len(delegation.recipient_ids)} named recipient(s)"
        if delegation.recipient_ids
        else "no named-recipient constraint"
    )
    return (
        f"Task delegation {index}: action={scope.action.value}; "
        f"resource types={resources}; resources={resource_scope}; "
        f"recipients={recipient_scope}; valid logical time "
        f"[{delegation.valid_from}, {delegation.expires_at})."
    )


def _visible_policy_summary(
    index: int, rule: EnterprisePolicyRule
) -> VisiblePolicySummary:
    actions = tuple(sorted({item.action.value for item in rule.action_scopes}))
    resources = tuple(
        sorted(
            {
                kind.value
                for scope in rule.action_scopes
                for kind in scope.resource_kinds
            }
        )
    )
    sensitivities = (
        ", ".join(item.value for item in rule.sensitivities)
        if rule.sensitivities
        else "all classifications"
    )
    recipient_parts = []
    if rule.recipient_kinds:
        recipient_parts.append(
            "types=" + ", ".join(item.value for item in rule.recipient_kinds)
        )
    if rule.recipient_ids:
        recipient_parts.append(f"named recipients={len(rule.recipient_ids)}")
    return VisiblePolicySummary(
        policy_category=(
            f"policy_{index}_{rule.effect.value}_{'_'.join(actions)}"
        ),
        decision_mode=rule.enforcement_mode,
        resource_scope=(
            f"actions={', '.join(actions)}; resource types={', '.join(resources)}; "
            f"sensitivity={sensitivities}"
        ),
        recipient_scope="; ".join(recipient_parts) or "all recipient types",
        description=_visible_policy_description(rule),
    )


def _visible_policy_description(rule: EnterprisePolicyRule) -> str:
    if rule.effect.value == "allow":
        return "Matching actions are allowed by this enterprise policy."
    if rule.enforcement_mode is DecisionMode.AUDIT:
        return (
            "Matching actions are recorded as a policy denial; "
            "audit mode does not block the action."
        )
    return "Matching actions are denied and blocked by this enterprise policy."


def _validate_frozen_tool_definitions(
    definitions: Mapping[str, ToolDefinition],
) -> None:
    if tuple(definitions) != OFFICE_V2_TOOL_NAMES:
        raise ValueError("Agent session surface must expose the frozen 17-tool catalog")
    authoritative = office_v2_tool_definitions()
    for name in OFFICE_V2_TOOL_NAMES:
        supplied = definitions[name]
        expected = authoritative[name]
        supplied_contract = (
            supplied.name,
            supplied.action,
            supplied.capability_id,
            supplied.resource_kinds,
            supplied.writes_state,
        )
        expected_contract = (
            expected.name,
            expected.action,
            expected.capability_id,
            expected.resource_kinds,
            expected.writes_state,
        )
        if supplied_contract != expected_contract:
            raise ValueError(f"tool definition does not match frozen contract: {name}")


def _required_principal(
    principals: dict[str, Principal], principal_id: str, owner: str
) -> Principal:
    principal = principals.get(principal_id)
    if principal is None or principal.kind is PrincipalKind.GROUP:
        raise ValueError(f"{owner} must reference a non-group directory principal")
    return principal


def _required_group(principals: dict[str, Principal], group_id: str) -> Principal:
    group = principals.get(group_id)
    if group is None or group.kind is not PrincipalKind.GROUP:
        raise ValueError("active group must reference a directory group")
    return group


def _display_role_id(role_id: str) -> str:
    label = role_id.split(".", 1)[-1].replace("-", " ").replace("_", " ")
    return " ".join(part.capitalize() for part in label.split())
