"""Fixture world overlay and offline materialisation (SS-003, SS-015, SS-016; B8 groundwork).

The base world is a **parameter, never a constant**: this layer has to serve both a
future envelope that carries its own world identity and one with a dedicated frozen
asset, so it never reads the old frozen world digest and never needs the old objective
catalogue.  A caller that wants to pin the base passes ``expected_base_world_digest``
and gets a refusal when the world it handed over is not the one it declared.

One committed transaction produces everything an episode needs to start:

* ``initial_state`` - the state whose declared slot targets now hold the candidate's
  material;
* ``initialization_transition`` - the authoritative record that the materialisation was
  committed atomically, which is what makes "already materialised" checkable rather than
  asserted;
* ``written`` - per slot, the object, the **canonical** field path that was actually
  written, the content digest and the recomputed material digest, so the written text
  stays traceable to a case without re-reading the world;
* an empty ``effect_ledger`` - the persistence point the runtime will append commit-time
  effects to (B8).  **This slice delivers the structure, not the closed loop**: nothing
  here captures a real delivery, so the revocation and interruption guarantees are still
  T03 work and are not claimed.

Offline only: no agent, no container, no model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_serializer, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.canonical_world import CanonicalOfficeWorld
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    AclEntry,
    AwareDateTime,
    DriveFile,
    DriveFileVersion,
    DriveLifecycle,
    DriveStore,
    EvidenceSourceKind,
    IdentityDirectory,
    LogicalTime,
    OfficeDomainGraph,
    Principal,
    PrincipalKind,
    ResourceKind,
    ResourceRef,
    Sensitivity,
    SourceEvidence,
    WorkspaceFile,
    WorkspaceStore,
)
from sandbox.scenarios.office_v2.world import EpisodeWorld, StateTransitionRecord
from sandbox.structured_v1.evidence import EffectLedger
from sandbox.structured_v1.fixture import StructuredFixtureManifest, verify_fixture_manifest
from sandbox.structured_v1.models import (
    STRUCTURED_SCHEMA_VERSION,
    Identifier,
    Sha256Digest,
    StructuredContract,
)
from sandbox.structured_v1.redaction import normalize_match_text
from sandbox.structured_v1.rendering import REFERENCE_SYNTAX_VERSION, RenderedMaterial


class MaterializationError(ValueError):
    """A materialisation the fixture cannot express."""


class SlotFieldKind(StrEnum):
    """Where a slot's material lives in the world."""

    DRIVE_FILE_CONTENT = "drive-file-content"
    DRIVE_FILE_SECTION = "drive-file-section"
    WORKSPACE_FILE_CONTENT = "workspace-file-content"


CANONICAL_FIELD_PATHS: dict[SlotFieldKind, str] = {
    SlotFieldKind.DRIVE_FILE_CONTENT: "current_version.content",
    SlotFieldKind.DRIVE_FILE_SECTION: "current_version.content",
    SlotFieldKind.WORKSPACE_FILE_CONTENT: "content",
}

#: Written between a file's fixed content and the slot's section (`ENC-01`).  The boundary is
#: frozen here: a candidate writes the section, never the marker that separates it from the
#: fixed facts around it, so "which part is low trust" stays readable and unfakeable.
#: It names the provenance and the trust level; it does not pre-emptively tell the reader to
#: discount the section, because "仅供参考" made the note self-defeating rather than low-trust.
LOW_TRUST_SECTION_HEADER = "【协作方说明·低信任】"


def compose_low_trust_section(fixed_content: str, section: str) -> str:
    """One file body carrying its fixed content plus exactly one labelled low-trust section."""

    fixed = fixed_content.rstrip()
    prefix = f"{fixed}\n\n" if fixed else ""
    return f"{prefix}{LOW_TRUST_SECTION_HEADER}\n{section.strip()}\n"


class SlotWorldTarget(StructuredContract):
    """The world location one slot materialises into.

    The field content **is** the material: a target field is replaced, not appended to,
    so a fixture cannot hide its own text behind the candidate's.  The field path is
    fixed by the field kind - a caller may repeat it, but it cannot invent one, because
    a recorded path that is not the written path is worthless evidence.
    """

    slot_id: Identifier
    field_kind: SlotFieldKind
    object_id: str = Field(min_length=1, max_length=512)
    field_path: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def field_path_matches_its_kind(self) -> SlotWorldTarget:
        expected = CANONICAL_FIELD_PATHS[self.field_kind]
        if self.field_path is not None and self.field_path != expected:
            raise ValueError(
                f"{self.field_kind.value} is written at {expected!r}, not {self.field_path!r}"
            )
        return self

    @property
    def resolved_field_path(self) -> str:
        return CANONICAL_FIELD_PATHS[self.field_kind]

    @property
    def write_location(self) -> tuple[SlotFieldKind, str, str]:
        return (self.field_kind, self.object_id, self.resolved_field_path)


class OverlayPrincipal(StructuredContract):
    principal_id: Identifier
    kind: PrincipalKind
    display_name: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=3, max_length=256)
    organization_id: Identifier | None = None


class OverlayDriveFile(StructuredContract):
    file_id: Identifier
    version_id: Identifier
    name: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(pattern=r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
    owner_id: Identifier
    classification: Sensitivity
    content: str = ""
    created_at: AwareDateTime


class OverlayWorkspaceFile(StructuredContract):
    path: str = Field(min_length=1, max_length=512)
    owner_id: Identifier
    content: str = ""
    media_type: str = Field(pattern=r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
    created_at: AwareDateTime


class OverlayAcl(StructuredContract):
    resource_file_id: Identifier
    grantee_id: Identifier
    rights: tuple[AccessRight, ...] = Field(min_length=1)
    granted_by: Identifier
    granted_at: AwareDateTime
    evidence_id: Identifier


class PublicDeliveryRequirement(StructuredContract):
    """Frozen public facts, required verbatim in this fixture's delivered body (ANCHOR-02)."""

    version: Literal["public-delivery-v1"] = "public-delivery-v1"
    source_file_id: Identifier
    required_facts: tuple[str, ...] = Field(min_length=1)

    def matching_fact_indices(self, body: str) -> tuple[int, ...]:
        """Exact public facts after the existing NFC/line-ending normalization."""
        normalized = normalize_match_text(body)
        return tuple(index for index, fact in enumerate(self.required_facts)
                     if normalize_match_text(fact) in normalized)

    @model_validator(mode="after")
    def facts_are_distinct_and_nonempty(self) -> PublicDeliveryRequirement:
        if any(not fact.strip() for fact in self.required_facts):
            raise ValueError("public delivery facts must not be empty")
        if len(set(self.required_facts)) != len(self.required_facts):
            raise ValueError("public delivery facts must not repeat")
        return self


class FixtureWorldOverlay(StructuredContract):
    """What a fixture adds on top of its base world, before any material is written."""

    principals: tuple[OverlayPrincipal, ...] = ()
    drive_files: tuple[OverlayDriveFile, ...] = ()
    workspace_files: tuple[OverlayWorkspaceFile, ...] = ()
    acls: tuple[OverlayAcl, ...] = ()
    slot_targets: tuple[SlotWorldTarget, ...] = ()
    public_delivery: PublicDeliveryRequirement | None = None
    fixed_registered_reads: tuple[Identifier, ...] = ()

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler):
        payload = handler(self)
        if self.public_delivery is None:
            payload.pop("public_delivery", None)
        if not self.fixed_registered_reads:
            payload.pop("fixed_registered_reads", None)
        return payload

    @model_validator(mode="after")
    def targets_are_unique(self) -> FixtureWorldOverlay:
        slots = [target.slot_id for target in self.slot_targets]
        if len(slots) != len(set(slots)):
            raise ValueError("an overlay must name each slot once")
        locations = [target.write_location for target in self.slot_targets]
        if len(locations) != len(set(locations)):
            raise ValueError("two slots would write to the same world field")
        drive_objects = [
            target.object_id
            for target in self.slot_targets
            if target.field_kind is not SlotFieldKind.WORKSPACE_FILE_CONTENT
        ]
        if len(drive_objects) != len(set(drive_objects)):
            raise ValueError("two slots would write to the same drive file")
        if len(self.fixed_registered_reads) != len(set(self.fixed_registered_reads)):
            raise ValueError("fixed registered reads must be unique")
        section_objects = {
            target.object_id
            for target in self.slot_targets
            if target.field_kind is SlotFieldKind.DRIVE_FILE_SECTION
        }
        if any(file_id not in {item.file_id for item in self.drive_files}
               or (file_id in {target.object_id for target in self.slot_targets}
                   and file_id not in section_objects)
               for file_id in self.fixed_registered_reads):
            raise ValueError(
                "a fixed registered read stays independent, except for a low-trust section"
            )
        requirement = self.public_delivery
        if requirement is not None:
            anchor = next(
                (file for file in self.drive_files if file.file_id == requirement.source_file_id),
                None,
            )
            if anchor is None or anchor.classification is not Sensitivity.PUBLIC:
                raise ValueError("the task anchor must be an existing public file")
            if any(
                target.object_id == anchor.file_id
                and target.field_kind is not SlotFieldKind.DRIVE_FILE_SECTION
                for target in self.slot_targets
            ):
                raise ValueError(
                    "only a low-trust section may be written into the task anchor"
                )
            if requirement.matching_fact_indices(anchor.content) != tuple(
                range(len(requirement.required_facts))
            ):
                raise ValueError("the task anchor must contain every frozen public fact")
        return self


class WrittenSlot(StructuredContract):
    """The trace of one materialised slot."""

    slot_id: Identifier
    field_kind: SlotFieldKind
    object_id: str = Field(min_length=1, max_length=512)
    field_path: str = Field(min_length=1, max_length=128)
    content_digest: Sha256Digest
    material_digest: Sha256Digest


@dataclass(frozen=True)
class MaterializedWorld:
    """The committed starting point of one episode, plus the evidence that proves it."""

    episode_id: str
    fixture_id: str
    base_world_digest: str
    initial_state: Any
    initial_state_payload: dict[str, Any]
    initial_state_digest: str
    initialization_transition: StateTransitionRecord
    initialization_transition_payload: dict[str, Any]
    initialization_transition_digest: str
    material_digest: str
    written: tuple[WrittenSlot, ...]
    effect_ledger: EffectLedger  # empty: the runtime appends commit-time effects (B8)


def materialize_world(
    manifest: StructuredFixtureManifest,
    material: RenderedMaterial,
    *,
    base_world: CanonicalOfficeWorld,
    overlay: FixtureWorldOverlay,
    episode_id: str,
    expected_base_world_digest: str | None = None,
    observed_at: LogicalTime = 0,
) -> MaterializedWorld:
    """Write the material into the fixture's slot targets in one committed transaction.

    The material is checked to belong to this fixture and its digest is recomputed
    rather than trusted, so a material rendered from another fixture, another manifest
    or another syntax cannot be passed off as this fixture's input.
    """

    if expected_base_world_digest is not None and (
        expected_base_world_digest != base_world.world_digest
    ):
        raise MaterializationError(
            "the handed-over base world is not the declared one"
        )
    material_digest = _require_material_identity(manifest, material)
    _require_targets(manifest, overlay)

    contents = {
        slot.slot_id: "\n".join(slot.contents)
        for slot in material.slots
    }
    graph = _build_graph(base_world.state.domain_graph, overlay, contents)

    episode = EpisodeWorld(base_world, episode_id=episode_id)
    transaction = episode.begin_transaction()
    transaction.replace_domain_graph(graph)
    transition = transaction.commit()
    if not transition.committed:
        raise MaterializationError(
            f"the materialisation transaction did not commit: {transition.failure_code}"
        )

    initial_state = episode.state
    written = tuple(
        WrittenSlot(
            slot_id=target.slot_id,
            field_kind=target.field_kind,
            object_id=target.object_id,
            field_path=target.resolved_field_path,
            content_digest=sha256_digest({"slot_content": contents[target.slot_id]}),
            material_digest=material_digest,
        )
        for target in sorted(overlay.slot_targets, key=lambda item: item.slot_id)
    )
    return MaterializedWorld(
        episode_id=episode_id,
        fixture_id=manifest.fixture_id,
        base_world_digest=base_world.world_digest,
        initial_state=initial_state,
        initial_state_payload=initial_state.model_dump(mode="json", exclude_none=False),
        initial_state_digest=initial_state.canonical_digest(),
        initialization_transition=transition,
        initialization_transition_payload=transition.model_dump(
            mode="json", exclude_none=False
        ),
        initialization_transition_digest=transition.transition_digest,
        material_digest=material.material_digest,
        written=written,
        effect_ledger=EffectLedger(episode_id=episode_id),
    )


def _require_material_identity(
    manifest: StructuredFixtureManifest,
    material: RenderedMaterial,
) -> str:
    """Check the material belongs to this fixture and return its recomputed digest."""

    if not verify_fixture_manifest(manifest):
        raise MaterializationError("the manifest does not match its own digest")
    if material.schema_version != STRUCTURED_SCHEMA_VERSION:
        raise MaterializationError("the material declares a different protocol identity")
    if material.fixture_id != manifest.fixture_id:
        raise MaterializationError("the material belongs to a different fixture")
    if material.manifest_digest != manifest.manifest_digest:
        raise MaterializationError("the material was rendered from a different manifest")
    if material.syntax_version != REFERENCE_SYNTAX_VERSION:
        raise MaterializationError("the material uses an unregistered reference syntax")
    declared = [slot.slot_id for slot in material.slots]
    if len(declared) != len(set(declared)):
        raise MaterializationError("the material repeats a slot")
    registered = {profile.slot_id for profile in manifest.slots}
    if set(declared) != registered:
        raise MaterializationError("the material does not cover exactly the fixture slots")
    digest = sha256_digest(material.digest_payload())
    if digest != material.material_digest:
        raise MaterializationError("the material digest does not match its content")
    return digest


def _require_targets(
    manifest: StructuredFixtureManifest,
    overlay: FixtureWorldOverlay,
) -> None:
    registered = {profile.slot_id for profile in manifest.slots}
    targeted = {target.slot_id for target in overlay.slot_targets}
    unknown = sorted(targeted - registered)
    if unknown:
        raise MaterializationError(f"overlay targets an unregistered slot: {unknown[0]}")
    missing = sorted(registered - targeted)
    if missing:
        raise MaterializationError(f"slot has no world target: {missing[0]}")
    locations = [target.write_location for target in overlay.slot_targets]
    if len(locations) != len(set(locations)):
        raise MaterializationError("two slots would write to the same world field")
    files = {item.file_id for item in overlay.drive_files}
    paths = {item.path for item in overlay.workspace_files}
    for target in overlay.slot_targets:
        if target.field_kind is SlotFieldKind.WORKSPACE_FILE_CONTENT:
            if target.object_id not in paths:
                raise MaterializationError(
                    f"slot {target.slot_id} targets an unknown workspace file"
                )
        elif target.object_id not in files:
            raise MaterializationError(
                f"slot {target.slot_id} targets an unknown drive file"
            )


def _build_graph(
    base_graph: OfficeDomainGraph,
    overlay: FixtureWorldOverlay,
    contents: dict[str, str],
) -> OfficeDomainGraph:
    directory = IdentityDirectory(
        organization=base_graph.directory.organization,
        principals=(
            *base_graph.directory.principals,
            *(_principal(item) for item in overlay.principals),
        ),
        memberships=base_graph.directory.memberships,
        role_assignments=base_graph.directory.role_assignments,
    )
    drive_contents = {
        target.object_id: contents[target.slot_id]
        for target in overlay.slot_targets
        if target.field_kind is SlotFieldKind.DRIVE_FILE_CONTENT
    }
    drive_sections = {
        target.object_id: contents[target.slot_id]
        for target in overlay.slot_targets
        if target.field_kind is SlotFieldKind.DRIVE_FILE_SECTION
    }
    workspace_contents = {
        target.object_id: contents[target.slot_id]
        for target in overlay.slot_targets
        if target.field_kind is SlotFieldKind.WORKSPACE_FILE_CONTENT
    }
    files = tuple(
        DriveFile(
            file_id=item.file_id,
            name=item.name,
            mime_type=item.mime_type,
            owner_id=item.owner_id,
            classification=item.classification,
            current_version_id=item.version_id,
            lifecycle_state=DriveLifecycle.ACTIVE,
        )
        for item in overlay.drive_files
    )
    versions = tuple(
        DriveFileVersion(
            version_id=item.version_id,
            file_id=item.file_id,
            content=(
                compose_low_trust_section(item.content, drive_sections[item.file_id])
                if item.file_id in drive_sections
                else drive_contents.get(item.file_id, item.content)
            ),
            created_by=item.owner_id,
            created_at=item.created_at,
        )
        for item in overlay.drive_files
    )
    workspace = tuple(
        WorkspaceFile(
            path=item.path,
            owner_id=item.owner_id,
            content=workspace_contents.get(item.path, item.content),
            media_type=item.media_type,
            created_at=item.created_at,
            updated_at=item.created_at,
        )
        for item in overlay.workspace_files
    )
    acls = tuple(
        AclEntry(
            resource=ResourceRef(
                kind=ResourceKind.DRIVE_FILE, resource_id=item.resource_file_id
            ),
            grantee_id=item.grantee_id,
            rights=item.rights,
            granted_by=item.granted_by,
            granted_at=item.granted_at,
            grant_source=SourceEvidence(
                evidence_id=item.evidence_id,
                source_kind=EvidenceSourceKind.CANONICAL_WORLD,
                source_id=item.resource_file_id,
                observed_at=0,
                content_digest=sha256_digest(
                    {
                        "acl": [
                            item.resource_file_id,
                            item.grantee_id,
                            [right.value for right in item.rights],
                        ]
                    }
                ),
            ),
        )
        for item in overlay.acls
    )
    return OfficeDomainGraph(
        directory=directory,
        mail=base_graph.mail,
        drive=DriveStore(
            files=(*base_graph.drive.files, *files),
            versions=(*base_graph.drive.versions, *versions),
            share_records=base_graph.drive.share_records,
        ),
        calendar=base_graph.calendar,
        workspace=WorkspaceStore(files=(*base_graph.workspace.files, *workspace)),
        acl_entries=(*base_graph.acl_entries, *acls),
        resource_links=base_graph.resource_links,
    )


def _principal(item: OverlayPrincipal) -> Principal:
    return Principal(
        principal_id=item.principal_id,
        kind=item.kind,
        display_name=item.display_name,
        email=item.email,
        organization_id=item.organization_id,
    )
