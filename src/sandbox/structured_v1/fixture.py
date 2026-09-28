"""The fixture manifest surface the generation boundary may depend on (SS-003, SS-004).

``FixtureManifest`` in the search contract is the whole versioned world: task,
initial world, obligations, observable channels, injection slots, citable objects,
common material and execution bounds.  This module carries only the part that
admission and the public projection need to decide admissibility and visibility:

* registered low-trust slots and their local permissions;
* public aliases and which reference edges may target them;
* the fixture's original slot content (the no-injection material);
* the public part of the task plus a public obligation summary;
* registered private values and hidden labels that must never reach a candidate or
  the provider request (SS-004, SOC-SAF-12).

World materialisation, ACLs and obligation instances arrive in later tasks; they
are referenced here only through the frozen ``manifest_digest``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.models import (
    CaseNode,
    EdgeType,
    Identifier,
    NodeRole,
    OperationKind,
    ReferenceEdge,
    Sha256Digest,
    StructuredContract,
)
from sandbox.structured_v1.phases import TwoPhaseProtocol

PLACEHOLDER_DIGEST: Final[str] = "sha256:" + "0" * 64

PublicObligation = Annotated[str, Field(min_length=1, max_length=512)]


class DataPolicyClass(StrEnum):
    """How far a piece of content may travel (SAF ``SOC-SAF-05``/``06``)."""

    PUBLIC = "public"
    LIMITED = "limited-audience"
    ACTOR_PRIVATE = "actor-private"


class DataAudienceRule(StructuredContract):
    """Who may read a content class; ``public`` ignores the list."""

    policy_class: DataPolicyClass
    allowed: tuple[Identifier, ...] = ()


class RegisteredUnit(StructuredContract):
    """A registered high-entropy unit and the class it belongs to.

    The raw value never leaves the host: it is used for exact matching (``SOC-SAF-09``)
    and for leak checks, and only its digest reaches a judgment record.
    """

    unit_id: Identifier
    value: str = Field(min_length=1)
    policy_class: DataPolicyClass = DataPolicyClass.LIMITED


class RegisteredFile(StructuredContract):
    """A registered original file, matched byte-exactly (``SOC-SAF-09``)."""

    file_id: Identifier
    content_digest: Sha256Digest
    policy_class: DataPolicyClass = DataPolicyClass.LIMITED


class SlotProfile(StructuredContract):
    """One registered editable slot and its local permissions (SS-003, SS-011)."""

    slot_id: Identifier
    allowed_roles: tuple[NodeRole, ...] = (
        NodeRole.CONTEXT,
        NodeRole.REFERENCE,
        NodeRole.DELIVERY_NOTE,
    )
    max_nodes: int = Field(default=4, ge=0, le=4)
    max_code_points: int = Field(default=3000, ge=0)
    allowed_operations: tuple[OperationKind, ...] = (
        OperationKind.EDIT_TEXT,
        OperationKind.MOVE_NODE,
        OperationKind.EDIT_REFERENCE,
        OperationKind.SPLIT_NODE,
        OperationKind.MERGE_NODES,
        OperationKind.TOGGLE_NODE,
    )

    @field_validator("allowed_roles", "allowed_operations")
    @classmethod
    def lists_are_unique(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        if len(value) != len(set(value)):
            raise ValueError("slot permissions must not repeat an entry")
        return value


class AliasProfile(StructuredContract):
    """A registered synthetic object the material may point at (SS-006).

    The alias carries no oracle label and no restricted content; it is a name in
    the public projection plus the allowed edge types.
    """

    alias: Identifier
    allowed_edge_types: tuple[EdgeType, ...] = (EdgeType.POINTS_TO,)

    @field_validator("allowed_edge_types")
    @classmethod
    def edge_types_are_unique(cls, value: tuple[EdgeType, ...]) -> tuple[EdgeType, ...]:
        if len(value) != len(set(value)):
            raise ValueError("alias must not repeat an allowed edge type")
        if not value:
            raise ValueError("alias must allow at least one edge type")
        return value


class OriginalSlotContent(StructuredContract):
    """Fixture-authored content of one slot, used when the candidate omits it.

    A slot is materialised either entirely from the candidate patch or entirely
    from this record; references never cross that boundary, so the material has a
    single interpretation (SS-005).
    """

    slot_id: Identifier
    nodes: tuple[CaseNode, ...] = ()
    edges: tuple[ReferenceEdge, ...] = ()

    @field_validator("nodes")
    @classmethod
    def nodes_are_unique(cls, value: tuple[CaseNode, ...]) -> tuple[CaseNode, ...]:
        node_ids = [node.node_id for node in value]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("original slot content must use unique node ids")
        return value

    @field_validator("edges")
    @classmethod
    def edges_are_unique(cls, value: tuple[ReferenceEdge, ...]) -> tuple[ReferenceEdge, ...]:
        edge_ids = [edge.edge_id for edge in value]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("original slot content must use unique edge ids")
        return value


class SlotHost(StructuredContract):
    """The public semantics of the resource a slot is written into (`ENC-03`).

    The generator otherwise sees only ``s1``/``s2``/``s3`` labels and cannot tell which slot sits
    in a resource the task actually reads.  Everything named here is already publicly discoverable
    by the Agent; it carries no value, no hidden label and no authority answer.
    """

    slot_id: Identifier
    host_name: str = Field(min_length=1, max_length=200)
    host_purpose: str = Field(min_length=1, max_length=400)
    host_role: str = Field(min_length=1, max_length=200)


class PublicRecipient(StructuredContract):
    """A recipient the scenario can publicly name, and how it is reached (`ENC-03`).

    Deliberately carries **no** authorization flag: whether a recipient is inside the task audience
    is decided by the trusted task and the Oracle, never handed to the generator as an answer.
    """

    name: str = Field(min_length=1, max_length=200)
    aliases: tuple[str, ...] = ()
    channel: str = Field(min_length=1, max_length=200)

    @field_validator("aliases")
    @classmethod
    def aliases_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("a public recipient must not repeat an alias")
        return value


class StructuredFixtureManifest(StructuredContract):
    """Versioned fixture identity plus the fields the generator boundary may read."""

    fixture_id: Identifier
    fixture_version: Identifier
    manifest_digest: Sha256Digest
    task_family: Identifier
    public_task_text: Annotated[str, Field(min_length=1, max_length=2000)]
    public_obligations: tuple[PublicObligation, ...] = Field(min_length=1)
    slots: tuple[SlotProfile, ...] = Field(min_length=1)
    aliases: tuple[AliasProfile, ...] = ()
    original_slots: tuple[OriginalSlotContent, ...] = ()
    actor_id: Identifier | None = None
    task_authorized: tuple[Identifier, ...] = ()
    required_delivery: tuple[Identifier, ...] = ()
    data_audience: tuple[DataAudienceRule, ...] = ()
    registered_units: tuple[RegisteredUnit, ...] = ()
    registered_files: tuple[RegisteredFile, ...] = ()
    fixed_registered_files: tuple[Identifier, ...] = ()
    hidden_labels: tuple[str, ...] = ()
    slot_hosts: tuple[SlotHost, ...] = ()
    public_recipients: tuple[PublicRecipient, ...] = ()
    session_protocol: TwoPhaseProtocol | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler: SerializerFunctionWrapHandler) -> dict:
        payload = handler(self)
        if not self.fixed_registered_files:
            payload.pop("fixed_registered_files", None)
        # Empty additions are dropped so a fixture that does not use them keeps its exact digest:
        # every frozen archive is addressed by that digest.
        if not self.slot_hosts:
            payload.pop("slot_hosts", None)
        if not self.public_recipients:
            payload.pop("public_recipients", None)
        if self.session_protocol is None:
            payload.pop("session_protocol", None)
        return payload

    @property
    def private_values(self) -> tuple[str, ...]:
        """The values a leak check must catch; derived from the registered units."""

        return tuple(unit.value for unit in self.registered_units if unit.value)

    @field_validator("slots")
    @classmethod
    def slots_are_unique(cls, value: tuple[SlotProfile, ...]) -> tuple[SlotProfile, ...]:
        slot_ids = [slot.slot_id for slot in value]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("manifest must register each slot once")
        return value

    @field_validator("aliases")
    @classmethod
    def aliases_are_unique(cls, value: tuple[AliasProfile, ...]) -> tuple[AliasProfile, ...]:
        names = [alias.alias for alias in value]
        if len(names) != len(set(names)):
            raise ValueError("manifest must register each alias once")
        return value

    @field_validator("slot_hosts")
    @classmethod
    def slot_hosts_are_unique(cls, value: tuple[SlotHost, ...]) -> tuple[SlotHost, ...]:
        slot_ids = [host.slot_id for host in value]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("manifest must describe each slot host once")
        return value

    @field_validator("public_recipients")
    @classmethod
    def public_recipients_are_unique(
        cls, value: tuple[PublicRecipient, ...]
    ) -> tuple[PublicRecipient, ...]:
        names = [recipient.name for recipient in value]
        if len(names) != len(set(names)):
            raise ValueError("manifest must register each public recipient once")
        return value

    @field_validator("original_slots")
    @classmethod
    def original_slots_are_unique(
        cls, value: tuple[OriginalSlotContent, ...]
    ) -> tuple[OriginalSlotContent, ...]:
        slot_ids = [content.slot_id for content in value]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("manifest must describe each original slot once")
        return value

    @field_validator("public_obligations")
    @classmethod
    def obligations_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("manifest must not repeat a public obligation")
        return value

    @field_validator("registered_units")
    @classmethod
    def units_are_unique(cls, value: tuple[RegisteredUnit, ...]) -> tuple[RegisteredUnit, ...]:
        unit_ids = [unit.unit_id for unit in value]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("manifest must register each unit once")
        return value

    @field_validator("registered_files")
    @classmethod
    def files_are_unique(cls, value: tuple[RegisteredFile, ...]) -> tuple[RegisteredFile, ...]:
        file_ids = [item.file_id for item in value]
        if len(file_ids) != len(set(file_ids)):
            raise ValueError("manifest must register each file once")
        return value

    @field_validator("fixed_registered_files")
    @classmethod
    def protected_files_are_unique(cls, value: tuple[Identifier, ...]) -> tuple[Identifier, ...]:
        if len(value) != len(set(value)):
            raise ValueError("fixed registered files must be unique")
        return value

    @field_validator("data_audience")
    @classmethod
    def audience_rules_are_unique(
        cls, value: tuple[DataAudienceRule, ...]
    ) -> tuple[DataAudienceRule, ...]:
        classes = [rule.policy_class for rule in value]
        if len(classes) != len(set(classes)):
            raise ValueError("manifest must state each data policy class once")
        return value

    @model_validator(mode="after")
    def original_slots_are_registered(self) -> Self:
        registered = {slot.slot_id for slot in self.slots}
        unknown = sorted(
            content.slot_id for content in self.original_slots if content.slot_id not in registered
        )
        if unknown:
            raise ValueError(f"original slot content names unregistered slots: {unknown}")
        if not set(self.fixed_registered_files).issubset(
            {file.file_id for file in self.registered_files}
        ):
            raise ValueError("fixed registered files must be registered")
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"manifest_digest"}, exclude_none=False)

    def slot_profile(self, slot_id: str) -> SlotProfile | None:
        for slot in self.slots:
            if slot.slot_id == slot_id:
                return slot
        return None

    def alias_profile(self, alias: str) -> AliasProfile | None:
        for profile in self.aliases:
            if profile.alias == alias:
                return profile
        return None

    def original_content(self, slot_id: str) -> OriginalSlotContent | None:
        for content in self.original_slots:
            if content.slot_id == slot_id:
                return content
        return None


def build_fixture_manifest(**fields: object) -> StructuredFixtureManifest:
    """Validate the fields twice so ``manifest_digest`` is the manifest's own digest."""

    probe = StructuredFixtureManifest(manifest_digest=PLACEHOLDER_DIGEST, **fields)  # type: ignore[arg-type]
    digest = sha256_digest(probe.digest_payload())
    return StructuredFixtureManifest(manifest_digest=digest, **fields)  # type: ignore[arg-type]


def verify_fixture_manifest(manifest: StructuredFixtureManifest) -> bool:
    """True when the manifest is internally consistent and its digest is its own."""

    return manifest.manifest_digest == sha256_digest(manifest.digest_payload())
