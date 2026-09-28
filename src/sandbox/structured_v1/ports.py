"""The real tool port: a `ToolPort` that drives the office_v2 runtime and captures its facts.

This is the piece that makes effect capture come from **actual execution** rather than from
a test-fabricated audience: the report's audience is the runtime's own
``policy_decision.recipient_ids``, its digests are the runtime's, and its committed/blocked
state is the runtime's result status. The session then records those facts verbatim.

It also answers the content half of the same question: which **registered restricted
content** a committed action carried (`SOC-SAF-09`/`-11`).  The match is exact - full
registered unit under NFC/CRLF normalisation, or a whole registered file - and it is made
against the arguments the runtime actually executed, so the recorded effect says what was
delivered rather than what a test asserted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.agent_api import project_office_v2_tool_result
from sandbox.scenarios.office_v2.models import AccessRight, ResourceKind, ResourceRef, ShareStatus
from sandbox.scenarios.office_v2.tools.contracts import ToolResultStatus
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.structured_v1.diagnostics import append_tool_result, tool_result_record
from sandbox.structured_v1.evidence import DeliveryChannel, DeliveryRelation, RelatedResourceRef
from sandbox.structured_v1.execution_evidence import (
    ReadOrigin,
    ToolExecutionEvidence,
    capture_execution_evidence,
)
from sandbox.structured_v1.exposure import CommittedDeliveryContent, return_content_digest
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.redaction import (
    registered_file_digest,
    registered_units_present,
)
from sandbox.structured_v1.session import ModelDecision, ToolCallReport
from sandbox.structured_v1.world import (
    FixtureWorldOverlay,
    PublicDeliveryRequirement,
    SlotWorldTarget,
)


def channel_for(tool_name: str, recipients: Iterable[str] = ()) -> DeliveryChannel:
    """The delivery channel a tool commits through, or actor-private when it is not one.

    ``create_drive_file`` commits through the actor's own drive: it is a **delivery** only
    when the commit made it readable to someone else (`SOC-SAF-08`).  A file created with no
    readers is the actor-private draft the safety contract counts as behaviour, not as a
    release, so the channel follows the audience the runtime actually granted.
    """

    if tool_name in {"send_email", "forward_email"}:
        return DeliveryChannel.MESSAGE
    if tool_name == "share_drive_file":
        return DeliveryChannel.SHARED_STORAGE
    if tool_name == "create_drive_file":
        return (
            DeliveryChannel.SHARED_STORAGE
            if tuple(recipients)
            else DeliveryChannel.ACTOR_PRIVATE
        )
    return DeliveryChannel.ACTOR_PRIVATE


#: A read tool, mapped to (the argument that names the source object, the visible-output
#: key that carries the content, the canonical field path of that content). Only these
#: tools can bind a read to a material slot; everything else has no source to bind.
_READ_TOOLS: dict[str, tuple[str, str, str]] = {
    "read_drive_file": ("file_id", "content", "current_version.content"),
    "read_file": ("path", "content", "content"),
    "read_email": ("message_id", "body", "body"),
}


class SlotResolver:
    """Maps a world object id to the slot whose material it holds, from the overlay.

    A read only becomes an exposure when the object it read is one of the fixture's slot
    targets; the resolver is what turns the read's source object into a slot id, so the
    session binds the returned content to the right material rather than a label match.
    """

    def __init__(self, targets: Iterable[SlotWorldTarget]) -> None:
        self._by_object: dict[str, SlotWorldTarget] = {
            target.object_id: target for target in targets
        }

    @classmethod
    def from_overlay(cls, overlay: FixtureWorldOverlay) -> SlotResolver:
        return cls(overlay.slot_targets)

    def resolve(self, object_id: str) -> SlotWorldTarget | None:
        return self._by_object.get(object_id)


def read_source(tool_name: str, arguments: dict[str, Any]) -> tuple[str | None, str | None]:
    """The source object and its field a read named, or ``(None, None)`` for a non-read."""

    spec = _READ_TOOLS.get(tool_name)
    if spec is None:
        return None, None
    argument_key, _output_key, field_path = spec
    value = arguments.get(argument_key)
    return (None if value is None else str(value)), field_path


def read_content(tool_name: str, result: object) -> str:
    """The text a read returned, taken from its real visible output (never invented)."""

    spec = _READ_TOOLS.get(tool_name)
    if spec is None:
        return ""
    output_key = spec[1]
    visible = getattr(result, "visible_output", None) or {}
    value = visible.get(output_key)
    return "" if value is None else str(value)


#: Tool arguments that carry the payload a committing action delivers.  Only text that the
#: runtime really accepted is scanned, so a rejected call cannot "deliver" anything.
_PAYLOAD_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "send_email": ("subject", "body"),
    "create_drive_file": ("name", "content"),
}

#: Tool arguments that name an existing world object; a registered restricted file named
#: here is delivered by reference (`SOC-SAF-09`'s original-file channel).
_FILE_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "share_drive_file": ("file_id",),
    "read_drive_file": ("file_id",),
    "delete_drive_file": ("file_id",),
}


@dataclass(frozen=True)
class RegisteredContent:
    """The fixture's registered restricted content, as commit-time matchers."""

    units: tuple[tuple[str, str], ...] = ()
    files: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_manifest(cls, manifest: StructuredFixtureManifest) -> RegisteredContent:
        return cls(
            units=tuple((unit.unit_id, unit.value) for unit in manifest.registered_units),
            files=tuple(
                (item.file_id, item.content_digest) for item in manifest.registered_files
            ),
        )


def _text_arguments(tool_name: str, arguments: Mapping[str, Any]) -> tuple[str, ...]:
    keys = _PAYLOAD_ARGUMENTS.get(tool_name, ())
    return tuple(
        str(arguments[key]) for key in keys if isinstance(arguments.get(key), str)
    )


def _referenced_file_ids(tool_name: str, arguments: Mapping[str, Any]) -> tuple[str, ...]:
    """World drive files a call named, whether directly or as an attachment reference."""

    named: list[str] = []
    for key in _FILE_ARGUMENTS.get(tool_name, ()):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            named.append(value)
    for ref in arguments.get("related_refs") or ():
        kind = getattr(ref, "kind", None)
        resource_id = getattr(ref, "resource_id", None)
        if kind is None and isinstance(ref, dict):
            kind, resource_id = ref.get("kind"), ref.get("resource_id")
        if str(getattr(kind, "value", kind)) == "drive_file" and isinstance(resource_id, str):
            named.append(resource_id)
    return tuple(dict.fromkeys(named))


def _related_resource_refs(arguments: Mapping[str, Any]) -> tuple[RelatedResourceRef, ...]:
    """The resources a delivery named as sources, taken from the arguments actually executed.

    This is the capture point for `DeliveryEffect.related_refs`: nothing is inferred from
    later state, and nothing is written by the generator.
    """

    refs: list[RelatedResourceRef] = []
    for ref in arguments.get("related_refs") or ():
        kind = getattr(ref, "kind", None)
        resource_id = getattr(ref, "resource_id", None)
        if kind is None and isinstance(ref, dict):
            kind, resource_id = ref.get("kind"), ref.get("resource_id")
        kind_value = str(getattr(kind, "value", kind))
        if kind_value and isinstance(resource_id, str):
            refs.append(RelatedResourceRef(kind=kind_value, resource_id=resource_id))
    return tuple(dict.fromkeys(refs))


def delivered_registered_content(
    tool_name: str,
    arguments: Mapping[str, Any],
    registered: RegisteredContent,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Which registered units and files this call delivered, by exact matching.

    ``(registered_units, registered_files)``, both sorted and free of duplicates.  A
    delivered payload that is byte-equal to a registered original file also counts as that
    file; nothing here infers semantics, paraphrases or partial matches.
    """

    texts = _text_arguments(tool_name, arguments)
    units = registered_units_present(texts, registered.units) if registered.units else ()
    digest_by_file = dict(registered.files)
    named = _referenced_file_ids(tool_name, arguments)
    files: list[str] = [file_id for file_id in named if file_id in digest_by_file]
    if digest_by_file:
        for text in texts:
            digest = registered_file_digest(text)
            files.extend(
                file_id
                for file_id, registered_digest in digest_by_file.items()
                if registered_digest == digest
            )
    return tuple(sorted(units)), tuple(sorted(dict.fromkeys(files)))


def tool_report_from_result(
    result: object,
    *,
    tool_call_id: str,
    action_request_id: str,
    returned_content: str = "",
    ordinal: int = 0,
    registered_units: tuple[str, ...] = (),
    registered_files: tuple[str, ...] = (),
    related_refs: tuple[RelatedResourceRef, ...] = (),
    source_object_id: str | None = None,
    source_field: str | None = None,
    slot_id: str | None = None,
    delivery_content: CommittedDeliveryContent | None = None,
    execution_evidence: ToolExecutionEvidence | None = None,
) -> ToolCallReport:
    """Build a report from a real result: nothing here is invented by the caller."""

    status = result.status
    policy = result.policy_decision
    readable = tuple(policy.recipient_ids if policy is not None else ())
    audience = tuple(
        DeliveryRelation(principal=principal, readable=True) for principal in readable
    )
    transition = result.state_transition
    created = (
        tuple(sorted(item.object_id for item in transition.state_delta.created_objects))
        if transition is not None
        else ()
    )
    return ToolCallReport(
        tool_call_id=tool_call_id,
        tool_name=result.tool_name,
        action_request_id=action_request_id,
        ordinal=ordinal,
        channel=channel_for(result.tool_name, readable),
        committed=status is ToolResultStatus.SUCCEEDED,
        blocked=status in {ToolResultStatus.BLOCKED, ToolResultStatus.REJECTED},
        world_transition_digest=(
            transition.transition_digest if transition is not None else result.after_state_digest
        ),
        before_state_digest=result.before_state_digest,
        after_state_digest=result.after_state_digest,
        created_objects=created,
        content_digest=result.visible_output_digest,
        proof_digest=result.execution_fact_digest,
        returned_content=returned_content,
        registered_units=registered_units,
        registered_files=registered_files,
        related_refs=related_refs,
        audience=audience,
        source_object_id=source_object_id,
        source_field=source_field,
        slot_id=slot_id,
        model_visible_result=project_office_v2_tool_result(result).model_visible_payload(),
        delivery_content=delivery_content,
        execution_evidence=execution_evidence,
    )


@dataclass(frozen=True)
class _CommittedPayload:
    """Transient access to the actual committed body; never serialized."""

    object_id: str
    version_id: str | None
    content: str
    source_refs: tuple[RelatedResourceRef, ...]
    resource: ResourceRef
    source_resource_refs: tuple[ResourceRef, ...]


def _state_refs(refs: Iterable[object]) -> tuple[RelatedResourceRef, ...]:
    return tuple(dict.fromkeys(
        RelatedResourceRef(kind=ref.kind.value, resource_id=ref.resource_id)
        for ref in refs
    ))


def _committed_payload(
    runtime: OfficeV2ToolRuntime,
    decision: ModelDecision,
    result: object,
) -> _CommittedPayload | None:
    """Resolve one result against its commit state, not arguments or later state.

    A missing or inconsistent association is left unproven. These observations
    never invoke another tool and never alter permissions or world contents.
    """

    if decision.tool_name not in {"send_email", "create_drive_file", "share_drive_file"}:
        return None
    transition = result.state_transition
    if (result.status is not ToolResultStatus.SUCCEEDED
            or result.tool_name != decision.tool_name
            or transition is None or not transition.committed
            or transition.after_state_digest != result.after_state_digest
            or runtime.state.canonical_digest() != result.after_state_digest):
        return None
    output = result.visible_output
    resource = output.get("resource")
    if not isinstance(resource, dict):
        return None
    object_id = resource.get("resource_id")
    graph = runtime.state.domain_graph
    created = {item.object_id for item in transition.state_delta.created_objects}
    if decision.tool_name == "send_email":
        if resource.get("kind") != "mail_message" or object_id not in created:
            return None
        message = next((item for item in graph.mail.messages
                        if item.message_id == object_id), None)
        if message is None or message.body != output.get("body"):
            return None
        return _CommittedPayload(
            message.message_id, None, message.body, _state_refs(message.attachment_refs),
            ResourceRef(kind=ResourceKind.MAIL_MESSAGE, resource_id=message.message_id),
            message.attachment_refs,
        )
    if resource.get("kind") != "drive_file":
        return None
    file = next((item for item in graph.drive.files if item.file_id == object_id), None)
    if file is None:
        return None
    if decision.tool_name == "create_drive_file":
        if file.file_id not in created:
            return None
        version_id = file.current_version_id
    else:
        if file.file_id != decision.arguments.get("file_id"):
            return None
        named_version = decision.arguments.get("version_id")
        if resource.get("version_id") != named_version:
            return None
        version_id = named_version or file.current_version_id
        share = next((item for item in graph.drive.share_records
                      if item.share_id == output.get("share_id")), None)
        if (share is None or share.status is not ShareStatus.ACTIVE
                or share.resource.resource_id != file.file_id
                or share.resource.version_id != named_version
                or share.recipient_id != output.get("recipient_id")
                or AccessRight.READ not in share.rights):
            return None
    version = next((item for item in graph.drive.versions
                    if item.version_id == version_id and item.file_id == file.file_id), None)
    if version is None:
        return None
    if decision.tool_name == "create_drive_file" and version.content != output.get("content"):
        return None
    return _CommittedPayload(
        file.file_id, version.version_id, version.content, _state_refs(version.source_refs),
        ResourceRef(
            kind=ResourceKind.DRIVE_FILE, resource_id=file.file_id, version_id=version.version_id
        ),
        version.source_refs,
    )


def _content_witness(
    payload: _CommittedPayload | None,
    requirement: PublicDeliveryRequirement | None,
) -> CommittedDeliveryContent | None:
    if payload is None or requirement is None:
        return None
    return CommittedDeliveryContent(
        object_id=payload.object_id,
        version_id=payload.version_id,
        content_digest=return_content_digest(payload.content),
        matched_fact_indices=requirement.matching_fact_indices(payload.content),
        source_refs=payload.source_refs,
        contract_digest=requirement.canonical_digest(),
    )


class ToolRuntimePort:
    """A `ToolPort` over a real `OfficeV2ToolRuntime`; used by the session in the container.

    It carries the fixture's slot resolver, so a read that returned one of the fixture's
    materials is bound to it - source object, canonical field and slot id - rather than
    only labelled. The content itself comes from the runtime's visible output.
    """

    def __init__(
        self,
        runtime: OfficeV2ToolRuntime,
        slots: SlotResolver | None = None,
        registered: RegisteredContent | None = None,
        episode_id: str | None = None,
        public_delivery: PublicDeliveryRequirement | None = None,
    ) -> None:
        self._runtime = runtime
        self._slots = slots
        self._registered = registered or RegisteredContent()
        self._sequence = 0
        self._episode_id = episode_id or "episode"
        self._public_delivery = public_delivery
        # A later create/update must not be labelled a frozen original merely because its
        # identifier is not a material slot. Keep only initial content digests, never bodies.
        graph = runtime.state.domain_graph
        self._initial_read_digests = {
            (ResourceKind.DRIVE_FILE.value, item.file_id, item.version_id):
                return_content_digest(item.content)
            for item in graph.drive.versions
        }
        self._initial_read_digests.update({
            (ResourceKind.MAIL_MESSAGE.value, item.message_id, ""): return_content_digest(item.body)
            for item in graph.mail.messages
        })
        self._initial_read_digests.update({
            (ResourceKind.WORKSPACE_FILE.value, item.path, ""): return_content_digest(item.content)
            for item in graph.workspace.files
        })

    def _read_observation(
        self, decision: ModelDecision, result: object, content: str
    ) -> tuple[ResourceRef | None, ReadOrigin]:
        if (decision.tool_name not in _READ_TOOLS
                or result.tool_name != decision.tool_name
                or result.status is not ToolResultStatus.SUCCEEDED
                or result.after_state_digest != self._runtime.state.canonical_digest()):
            return None, "unknown"
        raw = result.visible_output.get("resource")
        if not isinstance(raw, dict):
            return None, "unknown"
        try:
            resource = ResourceRef.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            return None, "unknown"
        expected_kinds = {
            "read_drive_file": ResourceKind.DRIVE_FILE,
            "read_file": ResourceKind.WORKSPACE_FILE,
            "read_email": ResourceKind.MAIL_MESSAGE,
        }
        source_id, _ = read_source(decision.tool_name, decision.arguments)
        if (resource.kind is not expected_kinds[decision.tool_name]
                or resource.resource_id != source_id):
            return None, "unknown"
        requested_version = decision.arguments.get("version_id")
        if requested_version is not None and resource.version_id != requested_version:
            return None, "unknown"
        target = self._slots.resolve(resource.resource_id) if self._slots is not None else None
        if target is not None:
            # A slot materialises into the current drive version, not every historical version.
            if resource.kind is ResourceKind.DRIVE_FILE:
                file = next((item for item in self._runtime.state.domain_graph.drive.files
                             if item.file_id == resource.resource_id), None)
                if file is None or resource.version_id != file.current_version_id:
                    return resource, "unknown"
            return resource, "low_trust_material"
        if (self._slots is not None
                and self._initial_read_digests.get(resource.sort_key())
                == return_content_digest(content)):
            return resource, "frozen_resource"
        return resource, "unknown"

    def execute(self, decision: ModelDecision) -> ToolCallReport:
        assert decision.action_request_id is not None and decision.tool_name is not None
        self._sequence += 1
        result = self._runtime.invoke(decision.tool_name, decision.arguments)
        payload = _committed_payload(self._runtime, decision, result)
        returned_content = read_content(decision.tool_name, result)
        source_object_id, source_field = read_source(decision.tool_name, decision.arguments)
        slot_id = None
        if source_object_id is not None and self._slots is not None:
            target = self._slots.resolve(source_object_id)
            slot_id = None if target is None else target.slot_id
        registered_units, registered_files = delivered_registered_content(
            decision.tool_name, decision.arguments, self._registered
        )
        if decision.tool_name == "share_drive_file" and payload is not None:
            # A new file id must not conceal restricted content copied into it.
            payload_units, payload_files = delivered_registered_content(
                "create_drive_file", {"content": payload.content}, self._registered
            )
            registered_units = tuple(sorted(set(registered_units) | set(payload_units)))
            registered_files = tuple(sorted(set(registered_files) | set(payload_files)))
        related_refs = _related_resource_refs(decision.arguments)
        invocation = self._runtime.invocations[-1]
        if (invocation.invocation_id != result.invocation_id
                or invocation.sequence != result.sequence
                or invocation.tool_name != decision.tool_name
                or invocation.arguments_digest != sha256_digest(decision.arguments)):
            raise ValueError("formal tool result is not bound to this invocation")
        read_resource, read_origin = self._read_observation(decision, result, returned_content)
        execution_evidence = capture_execution_evidence(
            result,
            arguments_digest=invocation.arguments_digest,
            read_resource=read_resource,
            read_origin=read_origin,
            committed_resource=None if payload is None else payload.resource,
            committed_content_digest=(
                None if payload is None else return_content_digest(payload.content)
            ),
            committed_source_refs=() if payload is None else payload.source_resource_refs,
        )
        report = tool_report_from_result(
            result,
            tool_call_id=f"call.{self._sequence:03d}",
            action_request_id=decision.action_request_id,
            returned_content=returned_content,
            registered_units=registered_units,
            registered_files=registered_files,
            related_refs=related_refs,
            source_object_id=source_object_id,
            source_field=source_field,
            slot_id=slot_id,
            delivery_content=_content_witness(payload, self._public_delivery),
            execution_evidence=execution_evidence,
        )
        # The bundle keeps only the result's digest, which is exactly what cannot be reviewed
        # when a call is refused, so the official result is also written as it is produced.
        append_tool_result(self._episode_id, tool_result_record(report))
        return report
