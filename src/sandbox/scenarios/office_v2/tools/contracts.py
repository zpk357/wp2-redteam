"""Neutral invocation, provenance, result, and failure contracts for Office V2 tools."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import OFFICE_V2_TOOL_CONTRACT_VERSION
from sandbox.scenarios.office_v2.models import (
    EvidenceId,
    Identifier,
    LogicalTime,
    OfficeV2Contract,
    PrincipalId,
    ResourceRef,
    Sha256Digest,
    canonicalize_identifiers,
)
from sandbox.scenarios.office_v2.policy import PolicyDecision
from sandbox.scenarios.office_v2.world import StateTransitionRecord


class ArgumentSourceMode(StrEnum):
    EXACT_VALUE = "exact_value"
    RESOURCE_REFERENCE = "resource_reference"
    DERIVED_SUMMARY = "derived_summary"


class ToolResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    FAILED = "failed"


class ToolFailureCode(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    UNSUPPORTED_TOOL_CONTRACT_VERSION = "unsupported_tool_contract_version"
    INVALID_ARGUMENTS = "invalid_arguments"
    INVALID_OR_STALE_PAGE_TOKEN = "invalid_or_stale_page_token"
    RESOURCE_NOT_FOUND_OR_HIDDEN = "resource_not_found_or_hidden"
    RESOURCE_VERSION_CONFLICT = "resource_version_conflict"
    BINDING_STALE = "binding_stale"
    INVALID_RESOURCE_REFERENCE = "invalid_resource_reference"
    RECIPIENT_NOT_RESOLVABLE = "recipient_not_resolvable"
    ARGUMENT_SOURCE_MISSING = "argument_source_missing"
    ARGUMENT_SOURCE_MISMATCH = "argument_source_mismatch"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    PLATFORM_DENIED = "platform_denied"
    POLICY_ENFORCED_DENIED = "policy_enforced_denied"
    TRANSACTION_VALIDATION_FAILED = "transaction_validation_failed"
    TRANSACTION_CONFLICT = "transaction_conflict"
    INTERNAL_INTEGRITY_ERROR = "internal_integrity_error"


class EvidenceOrigin(StrEnum):
    TOOL_OUTPUT = "tool_output"
    FROZEN_BINDING = "frozen_binding"


class ArgumentSource(OfficeV2Contract):
    argument_path: tuple[str, ...] = Field(min_length=1, max_length=16)
    source_evidence_ids: tuple[EvidenceId, ...] = Field(min_length=1)
    mode: ArgumentSourceMode

    @field_validator("source_evidence_ids")
    @classmethod
    def evidence_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="source_evidence_ids")


class OutputEvidence(OfficeV2Contract):
    evidence_id: EvidenceId
    invocation_id: Identifier
    invocation_sequence: int = Field(ge=-1)
    field_path: tuple[str, ...] = Field(min_length=1, max_length=16)
    resource_ref: ResourceRef | None = None
    value_digest: Sha256Digest
    visibility: str = Field(default="actor_visible", pattern="^actor_visible$")
    origin: EvidenceOrigin = EvidenceOrigin.TOOL_OUTPUT

    def sort_key(self) -> tuple[int, str, tuple[str, ...], str]:
        resource_key = "" if self.resource_ref is None else ":".join(self.resource_ref.sort_key())
        return (self.invocation_sequence, self.invocation_id, self.field_path, resource_key)


class OfficeToolInvocation(OfficeV2Contract):
    invocation_id: Identifier
    sequence: int = Field(ge=0)
    tool_name: Identifier
    tool_contract_version: str = OFFICE_V2_TOOL_CONTRACT_VERSION
    actor_id: PrincipalId
    task_id: Identifier
    logical_time: LogicalTime
    arguments: dict[str, JsonValue]
    arguments_digest: Sha256Digest
    argument_sources: tuple[ArgumentSource, ...] = Field(default_factory=tuple)
    before_state_digest: Sha256Digest

    @field_validator("argument_sources")
    @classmethod
    def sources_are_canonical(cls, value: tuple[ArgumentSource, ...]) -> tuple[ArgumentSource, ...]:
        paths = tuple(item.argument_path for item in value)
        if len(paths) != len(set(paths)):
            raise ValueError("argument_sources must not repeat argument_path")
        return tuple(sorted(value, key=lambda item: item.argument_path))

    @model_validator(mode="after")
    def arguments_digest_matches(self) -> Self:
        if self.arguments_digest != sha256_digest(self.arguments):
            raise ValueError("arguments_digest does not match arguments")
        return self


class OfficeToolResult(OfficeV2Contract):
    invocation_id: Identifier
    sequence: int = Field(ge=0)
    tool_name: Identifier
    status: ToolResultStatus
    visible_output: dict[str, JsonValue]
    visible_output_digest: Sha256Digest
    output_evidence: tuple[OutputEvidence, ...] = Field(default_factory=tuple)
    policy_decision: PolicyDecision | None = None
    state_transition: StateTransitionRecord | None = None
    before_state_digest: Sha256Digest
    after_state_digest: Sha256Digest
    failure_code: ToolFailureCode | None = None
    execution_fact_digest: Sha256Digest

    @field_validator("output_evidence")
    @classmethod
    def evidence_is_canonical(cls, value: tuple[OutputEvidence, ...]) -> tuple[OutputEvidence, ...]:
        ids = tuple(item.evidence_id for item in value)
        canonicalize_identifiers(ids, field_name="output_evidence ids")
        return tuple(sorted(value, key=OutputEvidence.sort_key))

    def execution_fact_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "invocation_id": self.invocation_id,
            "sequence": self.sequence,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "visible_output_digest": self.visible_output_digest,
            "output_evidence": [
                item.model_dump(mode="json", exclude_none=False) for item in self.output_evidence
            ],
            "policy_decision_digest": (
                None if self.policy_decision is None else self.policy_decision.decision_digest
            ),
            "state_transition_digest": (
                None if self.state_transition is None else self.state_transition.transition_digest
            ),
            "before_state_digest": self.before_state_digest,
            "after_state_digest": self.after_state_digest,
            "failure_code": None if self.failure_code is None else self.failure_code.value,
        }

    @model_validator(mode="after")
    def result_state_and_digests_are_consistent(self) -> Self:
        if self.visible_output_digest != sha256_digest(self.visible_output):
            raise ValueError("visible_output_digest does not match visible_output")
        if self.execution_fact_digest != sha256_digest(self.execution_fact_payload()):
            raise ValueError("execution_fact_digest does not match execution fact")
        if self.status is ToolResultStatus.SUCCEEDED:
            if self.failure_code is not None:
                raise ValueError("succeeded result cannot define failure_code")
            if self.state_transition is None:
                if self.before_state_digest != self.after_state_digest:
                    raise ValueError("successful read cannot change state digest")
            elif not self.state_transition.committed:
                raise ValueError("successful write requires committed transition")
        else:
            if self.failure_code is None:
                raise ValueError("non-success result requires failure_code")
            if self.status in {ToolResultStatus.REJECTED, ToolResultStatus.BLOCKED}:
                if self.state_transition is not None:
                    raise ValueError("rejected or blocked result cannot have transition")
                if self.before_state_digest != self.after_state_digest:
                    raise ValueError("rejected or blocked result cannot change state")
            if (
                self.status is ToolResultStatus.FAILED
                and self.state_transition is not None
                and self.state_transition.committed
            ):
                raise ValueError("failed result cannot have committed transition")
        if self.state_transition is not None and (
            self.state_transition.before_state_digest != self.before_state_digest
            or self.state_transition.after_state_digest != self.after_state_digest
        ):
            raise ValueError("result and transition state digests must match")
        for item in self.output_evidence:
            if (
                item.invocation_id != self.invocation_id
                or item.invocation_sequence != self.sequence
            ):
                raise ValueError("output evidence must belong to this invocation")
        return self


def build_tool_result(
    *,
    invocation: OfficeToolInvocation,
    status: ToolResultStatus,
    visible_output: dict[str, JsonValue] | None = None,
    output_evidence: tuple[OutputEvidence, ...] = (),
    policy_decision: PolicyDecision | None = None,
    state_transition: StateTransitionRecord | None = None,
    after_state_digest: str | None = None,
    failure_code: ToolFailureCode | None = None,
) -> OfficeToolResult:
    output = visible_output or {}
    after_digest = after_state_digest or invocation.before_state_digest
    payload = {
        "schema_version": invocation.schema_version,
        "invocation_id": invocation.invocation_id,
        "sequence": invocation.sequence,
        "tool_name": invocation.tool_name,
        "status": status,
        "visible_output": output,
        "visible_output_digest": sha256_digest(output),
        "output_evidence": tuple(sorted(output_evidence, key=OutputEvidence.sort_key)),
        "policy_decision": policy_decision,
        "state_transition": state_transition,
        "before_state_digest": invocation.before_state_digest,
        "after_state_digest": after_digest,
        "failure_code": failure_code,
    }
    draft = OfficeToolResult.model_construct(**payload, execution_fact_digest="0" * 64)
    return OfficeToolResult(
        **payload,
        execution_fact_digest=sha256_digest(draft.execution_fact_payload()),
    )


def argument_value(arguments: dict[str, JsonValue], path: tuple[str, ...]) -> JsonValue:
    value: JsonValue = arguments
    for segment in path:
        if isinstance(value, dict):
            if segment not in value:
                raise KeyError(segment)
            value = value[segment]
        elif isinstance(value, list):
            value = value[int(segment)]
        else:
            raise KeyError(segment)
    return value


__all__ = [
    "ArgumentSource",
    "ArgumentSourceMode",
    "EvidenceOrigin",
    "OfficeToolInvocation",
    "OfficeToolResult",
    "OutputEvidence",
    "ToolFailureCode",
    "ToolResultStatus",
    "argument_value",
    "build_tool_result",
]
