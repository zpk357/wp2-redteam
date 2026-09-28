"""Digest-verifiable facts from the formal Office result, without visible content (FBK-04).

This is an evidence projection, not behavior coverage. Optional observations are captured
at the real invocation; absent observations stay unknown. The original execution-fact
payload remains reconstructible, including the digests of facts whose bodies are omitted.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import ResourceRef
from sandbox.scenarios.office_v2.policy import PolicyDecision
from sandbox.scenarios.office_v2.tools.contracts import (
    OfficeToolResult,
    OutputEvidence,
    ToolFailureCode,
    ToolResultStatus,
)
from sandbox.scenarios.office_v2.world import StateTransitionRecord
from sandbox.structured_v1.models import Identifier, Sha256Digest, StructuredContract

ReadOrigin = Literal["low_trust_material", "frozen_resource", "unknown"]


class ToolExecutionEvidence(StructuredContract):
    evidence_version: Literal["structured-tool-execution-v1"] = "structured-tool-execution-v1"
    schema_version: str
    invocation_id: Identifier
    sequence: int = Field(ge=0)
    tool_name: Identifier
    arguments_digest: Sha256Digest
    status: ToolResultStatus
    failure_code: ToolFailureCode | None = None
    visible_output_digest: Sha256Digest
    pre_action_knowledge_digest: Sha256Digest | None = None
    output_evidence: tuple[OutputEvidence, ...] = ()
    policy_decision: PolicyDecision | None = None
    state_transition: StateTransitionRecord | None = None
    before_state_digest: Sha256Digest
    after_state_digest: Sha256Digest
    execution_fact_digest: Sha256Digest
    read_resource: ResourceRef | None = None
    read_origin: ReadOrigin = "unknown"
    committed_resource: ResourceRef | None = None
    committed_content_digest: Sha256Digest | None = None
    committed_source_refs: tuple[ResourceRef, ...] = ()

    def execution_fact_payload(self) -> dict[str, Any]:
        """Exactly the formal result's digest-protected execution payload."""

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
            "pre_action_knowledge_digest": self.pre_action_knowledge_digest,
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
    def evidence_is_consistent(self) -> ToolExecutionEvidence:
        if sha256_digest(self.execution_fact_payload()) != self.execution_fact_digest:
            raise ValueError("execution evidence does not match the formal result digest")
        for item in self.output_evidence:
            if (item.invocation_id != self.invocation_id
                    or item.invocation_sequence != self.sequence):
                raise ValueError("output evidence belongs to a different invocation")
        transition = self.state_transition
        if transition is not None and (
            transition.before_state_digest != self.before_state_digest
            or transition.after_state_digest != self.after_state_digest
        ):
            raise ValueError("execution evidence and transaction state do not match")
        if self.policy_decision is not None and (
            self.policy_decision.before_state_digest != self.before_state_digest
        ):
            raise ValueError("execution evidence and policy state do not match")
        if self.status is ToolResultStatus.SUCCEEDED:
            if (self.failure_code is not None
                    or (transition is not None and not transition.committed)):
                raise ValueError("successful evidence cannot contain a failed transaction")
            if transition is None and self.before_state_digest != self.after_state_digest:
                raise ValueError("successful read cannot change state")
        else:
            if self.failure_code is None:
                raise ValueError("non-success evidence requires its formal failure code")
            if transition is not None and transition.committed:
                raise ValueError("non-success evidence cannot contain a committed transaction")
            if (self.status in {ToolResultStatus.REJECTED, ToolResultStatus.BLOCKED}
                    and transition is not None):
                raise ValueError("rejected or blocked evidence cannot contain a transaction")
            if self.before_state_digest != self.after_state_digest:
                raise ValueError("non-success evidence cannot change state")
            if self.read_resource is not None or self.committed_resource is not None:
                raise ValueError("non-success evidence cannot claim read or committed content")
        if self.read_resource is None and self.read_origin != "unknown":
            raise ValueError("a read origin requires an observed read resource")
        if self.read_resource is not None and not any(
            item.field_path == ("resource",)
            and item.resource_ref == self.read_resource
            and item.value_digest == sha256_digest(self.read_resource.model_dump(mode="json"))
            for item in self.output_evidence
        ):
            raise ValueError("read resource is not bound to the formal tool output")
        if self.committed_resource is None:
            if self.committed_content_digest is not None or self.committed_source_refs:
                raise ValueError("committed content requires its committed resource")
        elif (self.committed_content_digest is None or transition is None
              or not transition.committed):
            raise ValueError("committed resource requires content and its real transaction")
        if self.committed_resource is not None:
            resource = self.committed_resource
            if not any(
                item.field_path == ("resource",) and item.resource_ref is not None
                and item.resource_ref.kind is resource.kind
                and item.resource_ref.resource_id == resource.resource_id
                and (item.resource_ref.version_id is None
                     or item.resource_ref.version_id == resource.version_id)
                for item in self.output_evidence
            ):
                raise ValueError("committed resource is not bound to formal output")
            refs_field = {"send_email": "related_refs", "create_drive_file": "source_refs"}.get(
                self.tool_name
            )
            if refs_field is not None and not any(
                item.field_path == (refs_field,)
                and item.value_digest == sha256_digest([
                    ref.model_dump(mode="json") for ref in self.committed_source_refs
                ])
                for item in self.output_evidence
            ):
                raise ValueError("committed references do not match formal output")
        return self


def capture_execution_evidence(
    result: OfficeToolResult,
    *,
    arguments_digest: str,
    read_resource: ResourceRef | None = None,
    read_origin: ReadOrigin = "unknown",
    committed_resource: ResourceRef | None = None,
    committed_content_digest: str | None = None,
    committed_source_refs: tuple[ResourceRef, ...] = (),
) -> ToolExecutionEvidence:
    """Keep formal fields and immediate observations, never a second body or credential."""

    # Revalidate at the capture boundary rather than accepting unchecked model_copy updates.
    result = OfficeToolResult.model_validate(result.model_dump(mode="json"))
    return ToolExecutionEvidence(
        schema_version=result.schema_version,
        invocation_id=result.invocation_id,
        sequence=result.sequence,
        tool_name=result.tool_name,
        arguments_digest=arguments_digest,
        status=result.status,
        failure_code=result.failure_code,
        visible_output_digest=result.visible_output_digest,
        pre_action_knowledge_digest=(
            None if result.pre_action_knowledge is None
            else result.pre_action_knowledge.knowledge_digest
        ),
        output_evidence=result.output_evidence,
        policy_decision=result.policy_decision,
        state_transition=result.state_transition,
        before_state_digest=result.before_state_digest,
        after_state_digest=result.after_state_digest,
        execution_fact_digest=result.execution_fact_digest,
        read_resource=read_resource,
        read_origin=read_origin,
        committed_resource=committed_resource,
        committed_content_digest=committed_content_digest,
        committed_source_refs=committed_source_refs,
    )


def validate_execution_binding(
    evidence: ToolExecutionEvidence,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    before_state_digest: str | None = None,
    after_state_digest: str | None = None,
) -> None:
    """Reject evidence borrowed from another decision or structured transition."""

    ToolExecutionEvidence.model_validate(evidence.model_dump(mode="json"))
    if evidence.tool_name != tool_name or evidence.arguments_digest != sha256_digest(arguments):
        raise ValueError("execution evidence is not bound to the executed decision")
    if before_state_digest is not None and evidence.before_state_digest != before_state_digest:
        raise ValueError("execution evidence has a different before state")
    if after_state_digest is not None and evidence.after_state_digest != after_state_digest:
        raise ValueError("execution evidence has a different after state")
    if evidence.committed_resource is not None:
        content_field = {"send_email": "body", "create_drive_file": "content"}.get(tool_name)
        if content_field is not None:
            content = arguments.get(content_field)
            if (not isinstance(content, str)
                    or sha256_digest({"content": content}) != evidence.committed_content_digest
                    or not any(
                        item.field_path == (content_field,)
                        and item.value_digest == sha256_digest(content)
                        for item in evidence.output_evidence
                    )):
                raise ValueError("committed content does not match the formal output and decision")
