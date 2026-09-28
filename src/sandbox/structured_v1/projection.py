"""Public projection: what the generator may see and may never see (SS-004, SOC-SAF-12).

The projection is a versioned input, not something a runtime model decides.  It
carries the permitted public part of the task, the general obligations, the
registered slot descriptions, the materials the mutator is allowed to know (the
parent candidate's own low-trust text plus its resolved reference labels) and the
registered public aliases.

Excluded by construction and re-checked afterwards: restricted fact values, Oracle
hidden labels, expected answers and private resource bodies.  The check walks every
string of the payload rather than named fields, because ``SS-004`` also forbids
smuggling hidden content through feedback excerpts or rationales.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.fixture import PLACEHOLDER_DIGEST, StructuredFixtureManifest
from sandbox.structured_v1.models import (
    STRUCTURED_SCHEMA_VERSION,
    Identifier,
    NodeRole,
    OperationKind,
    Sha256Digest,
    StructuredCase,
    StructuredContract,
)
from sandbox.structured_v1.redaction import find_registered_value_leaks
from sandbox.structured_v1.rendering import build_effective_case, resolve_references
from sandbox.structured_v1.validation import (
    DEFAULT_CASE_BOUNDS,
    CaseBounds,
    slot_limits,
)

PROJECTION_VERSION: Final[str] = "structured-projection-v1"

PublicText = Annotated[str, Field(min_length=1)]


class ProjectionLeakError(RuntimeError):
    """Raised when a projection would carry a restricted value or a hidden label."""


class ProjectionIntegrityError(ValueError):
    """Raised when a serialized public projection was changed after it was stamped."""


class ProjectedSlot(StructuredContract):
    """A registered editable slot as the generator sees it.

    ``host_*`` names the resource the slot is written into when the fixture registers it (`ENC-03`):
    without it the generator sees only ``s1``/``s2`` labels and cannot tell which slot sits in a
    resource the task actually reads.  It is public, carries no value and no hidden label.
    """

    slot_id: Identifier
    allowed_roles: tuple[NodeRole, ...]
    allowed_operations: tuple[OperationKind, ...]
    max_nodes: int = Field(ge=0)
    max_code_points: int = Field(ge=0)
    host_name: str | None = None
    host_purpose: str | None = None
    host_role: str | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler: SerializerFunctionWrapHandler) -> dict:
        payload = handler(self)
        for key in ("host_name", "host_purpose", "host_role"):
            if payload.get(key) is None:
                payload.pop(key, None)
        return payload


class ProjectedNode(StructuredContract):
    """One visible material fragment of the parent candidate."""

    node_id: Identifier
    slot_id: Identifier
    role: NodeRole
    text: str
    references: tuple[str, ...] = ()


class ProjectedRecipient(StructuredContract):
    """A publicly nameable recipient as the generator sees it (`ENC-03`).

    Only what the Agent could discover itself: a name, its registered aliases and the channel it is
    reached through.  Whether it lies inside the task audience is deliberately absent -- that is the
    Oracle's answer, not the generator's input.
    """

    name: str
    aliases: tuple[str, ...] = ()
    channel: str


class PublicProjection(StructuredContract):
    schema_version: Literal["structured-v1.0"] = STRUCTURED_SCHEMA_VERSION
    projection_version: Identifier = PROJECTION_VERSION
    fixture_id: Identifier
    manifest_digest: Sha256Digest
    task_family: Identifier
    public_task_text: str
    public_obligations: tuple[str, ...]
    slots: tuple[ProjectedSlot, ...]
    aliases: tuple[Identifier, ...] = ()
    parent_nodes: tuple[ProjectedNode, ...] = ()
    public_recipients: tuple[ProjectedRecipient, ...] = ()
    #: The identity this task acts as (`ENC-03`).  Public in exactly the way the task text is: an
    #: Agent always knows who it is acting as, so delivering to itself is not a change of audience.
    #: Whether the *other* recipients lie inside the task audience stays absent, because that is the
    #: Oracle's answer.
    actor_id: Identifier | None = None
    projection_digest: Sha256Digest

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler: SerializerFunctionWrapHandler) -> dict:
        payload = handler(self)
        if not self.public_recipients:
            payload.pop("public_recipients", None)
        if self.actor_id is None:
            payload.pop("actor_id", None)
        return payload

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"projection_digest"}, exclude_none=False)


def build_public_projection(
    *,
    manifest: StructuredFixtureManifest,
    parent: StructuredCase | None = None,
    bounds: CaseBounds = DEFAULT_CASE_BOUNDS,
) -> PublicProjection:
    """Build the projection for a parent candidate; ``parent=None`` is the root view."""

    effective = build_effective_case(parent, manifest)
    references = resolve_references(effective)
    parent_nodes = tuple(
        ProjectedNode(
            node_id=node.node_id,
            slot_id=slot.slot_id,
            role=node.role,
            text=node.text,
            references=tuple(
                reference.label for reference in references.get(node.node_id, ())
            ),
        )
        for slot in effective.slots
        for node in slot.nodes
    )
    hosts = {host.slot_id: host for host in manifest.slot_hosts}
    slots = tuple(
        ProjectedSlot(
            slot_id=profile.slot_id,
            allowed_roles=profile.allowed_roles,
            allowed_operations=profile.allowed_operations,
            max_nodes=slot_limits(profile, bounds)[0],
            max_code_points=slot_limits(profile, bounds)[1],
            host_name=hosts[profile.slot_id].host_name if profile.slot_id in hosts else None,
            host_purpose=(
                hosts[profile.slot_id].host_purpose if profile.slot_id in hosts else None
            ),
            host_role=hosts[profile.slot_id].host_role if profile.slot_id in hosts else None,
        )
        for profile in manifest.slots
    )
    probe = PublicProjection(
        fixture_id=manifest.fixture_id,
        manifest_digest=manifest.manifest_digest,
        task_family=manifest.task_family,
        public_task_text=manifest.public_task_text,
        public_obligations=manifest.public_obligations,
        slots=slots,
        aliases=tuple(alias.alias for alias in manifest.aliases),
        parent_nodes=parent_nodes,
        public_recipients=tuple(
            ProjectedRecipient(
                name=recipient.name,
                aliases=recipient.aliases,
                channel=recipient.channel,
            )
            for recipient in manifest.public_recipients
        ),
        actor_id=manifest.actor_id,
        projection_digest=PLACEHOLDER_DIGEST,
    )
    return probe.model_copy(update={"projection_digest": sha256_digest(probe.digest_payload())})


def projection_leaks(
    projection: PublicProjection,
    *,
    manifest: StructuredFixtureManifest,
) -> tuple[str, ...]:
    """Registered values or hidden labels present anywhere in the projection payload."""

    forbidden = tuple(manifest.private_values) + tuple(manifest.hidden_labels)
    return find_registered_value_leaks(_iter_strings(projection.digest_payload()), forbidden)


def verify_public_projection(
    projection: PublicProjection,
    *,
    manifest: StructuredFixtureManifest,
) -> None:
    """Raise before a request is built when the projection is not clean."""

    if projection.fixture_id != manifest.fixture_id:
        raise ProjectionIntegrityError("projection fixture identity does not match manifest")
    if projection.manifest_digest != manifest.manifest_digest:
        raise ProjectionIntegrityError("projection manifest identity does not match manifest")
    if projection.projection_digest != sha256_digest(projection.digest_payload()):
        raise ProjectionIntegrityError("projection digest does not match its content")
    leaks = projection_leaks(projection, manifest=manifest)
    if leaks:
        raise ProjectionLeakError(
            f"projection carries {len(leaks)} restricted value(s) or hidden label(s)"
        )


def _iter_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        collected: list[str] = []
        for key, item in value.items():
            collected.extend(_iter_strings(key))
            collected.extend(_iter_strings(item))
        return tuple(collected)
    if isinstance(value, list | tuple):
        collected = []
        for item in value:
            collected.extend(_iter_strings(item))
        return tuple(collected)
    return ()
