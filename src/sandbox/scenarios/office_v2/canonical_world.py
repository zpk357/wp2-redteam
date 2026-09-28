"""Immutable canonical world snapshot for Office Workspace Scenario V2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_bytes, sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_CANONICAL_JSON_VERSION,
    OFFICE_V2_CANONICAL_WORLD_ID,
)
from sandbox.scenarios.office_v2.models import (
    AclEntry,
    CalendarEventStatus,
    CalendarStore,
    DelegationGrant,
    DriveStore,
    Identifier,
    IdentityDirectory,
    LogicalClock,
    MailStore,
    OfficeDomainGraph,
    OfficeV2Contract,
    PrincipalKind,
    ResourceLink,
    Sensitivity,
    Sha256Digest,
    WorkspaceStore,
    WorldVersion,
)
from sandbox.scenarios.office_v2.policy import EnterprisePolicyRule

OFFICE_V2_DATA_DIR = Path(__file__).with_name("data") / OFFICE_V2_CANONICAL_WORLD_ID
_DOMAIN_FILES = (
    "organization.json",
    "drive.json",
    "mail.json",
    "calendar.json",
    "workspace.json",
    "policy.json",
)


class OfficeWorldState(OfficeV2Contract):
    """Complete mutable-at-episode-boundary state, represented immutably."""

    domain_graph: OfficeDomainGraph
    policy_rules: tuple[EnterprisePolicyRule, ...] = Field(default_factory=tuple)
    delegation_grants: tuple[DelegationGrant, ...] = Field(default_factory=tuple)
    logical_clock: LogicalClock = Field(default_factory=LogicalClock)
    next_id_sequence: int = Field(default=0, ge=0)

    @field_validator("policy_rules")
    @classmethod
    def policy_rules_are_canonical(
        cls, value: tuple[EnterprisePolicyRule, ...]
    ) -> tuple[EnterprisePolicyRule, ...]:
        rule_ids = tuple(rule.rule_id for rule in value)
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("policy_rules must not contain duplicate rule ids")
        return tuple(sorted(value, key=lambda rule: rule.rule_id))

    @field_validator("delegation_grants")
    @classmethod
    def grants_are_canonical(
        cls, value: tuple[DelegationGrant, ...]
    ) -> tuple[DelegationGrant, ...]:
        grant_ids = tuple(item.grant_id for item in value)
        source_turn_ids = tuple(item.source_turn_id for item in value)
        if len(grant_ids) != len(set(grant_ids)):
            raise ValueError("delegation grants must not contain duplicate grant ids")
        if len(source_turn_ids) != len(set(source_turn_ids)):
            raise ValueError("delegation grants must not reuse source turn ids")
        return tuple(sorted(value, key=lambda item: item.grant_id))

    @model_validator(mode="after")
    def grant_references_resolve(self) -> Self:
        principal_ids = {
            item.principal_id for item in self.domain_graph.directory.principals
        }
        for grant in self.delegation_grants:
            if grant.issuer_id not in principal_ids or grant.actor_id not in principal_ids:
                raise ValueError("delegation grant references unknown issuer or actor")
            if not set(grant.recipient_ids).issubset(principal_ids):
                raise ValueError("delegation grant references unknown recipient")
            if any(
                not self.domain_graph.resource_exists(ref)
                for ref in grant.resource_refs
            ):
                raise ValueError("delegation grant references unknown resource")
        return self


class CanonicalOfficeWorld(OfficeV2Contract):
    """Digest-locked source world from which isolated episodes are created."""

    world_id: Identifier = OFFICE_V2_CANONICAL_WORLD_ID
    world_version: WorldVersion
    state: OfficeWorldState
    world_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"world_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def digest_matches_payload(self) -> Self:
        if self.world_digest != sha256_digest(self.digest_payload()):
            raise ValueError("world_digest does not match canonical world payload")
        return self


class WorldDataFileManifest(OfficeV2Contract):
    filename: str = Field(pattern=r"^[a-z][a-z0-9-]*\.json$")
    sha256: Sha256Digest


class WorldInventory(OfficeV2Contract):
    internal_users: int = Field(ge=0)
    external_contacts: int = Field(ge=0)
    groups: int = Field(ge=0)
    mail_messages: int = Field(ge=0)
    drive_files: int = Field(ge=0)
    drive_versions: int = Field(ge=0)
    calendar_events: int = Field(ge=0)
    workspace_files: int = Field(ge=0)


class OfficeWorldManifest(OfficeV2Contract):
    world_id: Literal["office-world-v2.0"] = OFFICE_V2_CANONICAL_WORLD_ID
    world_version: WorldVersion
    canonical_json_version: Literal["canonical-json-v1"] = OFFICE_V2_CANONICAL_JSON_VERSION
    logical_clock_start: int = Field(ge=0)
    files: tuple[WorldDataFileManifest, ...] = Field(min_length=6, max_length=6)
    inventory: WorldInventory
    world_digest: Sha256Digest

    @field_validator("files")
    @classmethod
    def files_are_complete_and_canonical(
        cls, value: tuple[WorldDataFileManifest, ...]
    ) -> tuple[WorldDataFileManifest, ...]:
        filenames = tuple(item.filename for item in value)
        if len(filenames) != len(set(filenames)):
            raise ValueError("manifest files must not contain duplicates")
        if set(filenames) != set(_DOMAIN_FILES):
            raise ValueError("manifest must contain exactly the six Office V2 domain files")
        return tuple(sorted(value, key=lambda item: item.filename))


class OfficeWorldQualityReport(OfficeV2Contract):
    world_id: Literal["office-world-v2.0"] = OFFICE_V2_CANONICAL_WORLD_ID
    world_digest: Sha256Digest
    inventory: WorldInventory
    mail_threads: int = Field(ge=0)
    overlapping_group_users: int = Field(ge=0)
    drive_classifications: tuple[Sensitivity, ...]
    discover_only_acl_entries: int = Field(ge=0)
    stale_searchable_items: int = Field(ge=0)
    calendar_cancelled_events: int = Field(ge=0)
    calendar_external_events: int = Field(ge=0)
    calendar_resource_references: int = Field(ge=0)
    calendar_conflict_pairs: int = Field(ge=0)
    cross_domain_links: int = Field(ge=0)
    connected_resource_ratio: float = Field(ge=0, le=1)
    narrative_projects: tuple[str, ...]
    conflicting_source_markers: int = Field(ge=0)

    @model_validator(mode="after")
    def frozen_quality_floor_is_met(self) -> Self:
        expected = WorldInventory(
            internal_users=40,
            external_contacts=12,
            groups=8,
            mail_messages=120,
            drive_files=50,
            drive_versions=75,
            calendar_events=30,
            workspace_files=20,
        )
        if self.inventory != expected:
            raise ValueError("fixed world inventory does not match office-world-v2.0")
        if self.mail_threads < 36 or self.overlapping_group_users < 6:
            raise ValueError("mail thread or overlapping group quality floor is not met")
        if set(self.drive_classifications) != set(Sensitivity):
            raise ValueError("drive files must cover all sensitivity classifications")
        if self.discover_only_acl_entries < 8 or self.stale_searchable_items < 8:
            raise ValueError("ACL or stale-content interference floor is not met")
        if (
            self.calendar_cancelled_events < 1
            or self.calendar_external_events < 6
            or self.calendar_resource_references < 10
            or self.calendar_conflict_pairs < 6
        ):
            raise ValueError("calendar quality floor is not met")
        if self.cross_domain_links < 30 or self.connected_resource_ratio < 0.65:
            raise ValueError("cross-domain connectivity floor is not met")
        if len(self.narrative_projects) < 3 or self.conflicting_source_markers < 5:
            raise ValueError("narrative or conflicting-source quality floor is not met")
        return self


def build_canonical_world(
    state: OfficeWorldState,
    *,
    world_version: WorldVersion = "2.0",
    world_id: Identifier = OFFICE_V2_CANONICAL_WORLD_ID,
) -> CanonicalOfficeWorld:
    """Build and lock a canonical world without accepting a caller-supplied digest."""

    payload = {
        "schema_version": state.schema_version,
        "world_id": world_id,
        "world_version": world_version,
        "state": state.model_dump(mode="json", exclude_none=False),
    }
    return CanonicalOfficeWorld(
        world_id=world_id,
        world_version=world_version,
        state=state,
        world_digest=sha256_digest(payload),
    )


def load_canonical_world(data_dir: Path = OFFICE_V2_DATA_DIR) -> CanonicalOfficeWorld:
    """Load all domain files atomically after manifest and world validation."""

    manifest = OfficeWorldManifest.model_validate(_read_json(data_dir / "manifest.json"))
    raw_domains: dict[str, object] = {}
    for entry in manifest.files:
        path = data_dir / entry.filename
        payload = path.read_bytes()
        if sha256_bytes(payload) != entry.sha256:
            raise ValueError(f"domain digest mismatch: {entry.filename}")
        raw_domains[entry.filename] = json.loads(payload)

    organization = _OrganizationData.model_validate(raw_domains["organization.json"])
    state = OfficeWorldState(
        domain_graph=OfficeDomainGraph(
            directory=organization.directory,
            mail=MailStore.model_validate(raw_domains["mail.json"]),
            drive=DriveStore.model_validate(raw_domains["drive.json"]),
            calendar=CalendarStore.model_validate(raw_domains["calendar.json"]),
            workspace=WorkspaceStore.model_validate(raw_domains["workspace.json"]),
            acl_entries=organization.acl_entries,
            resource_links=organization.resource_links,
        ),
        policy_rules=_PolicyData.model_validate(raw_domains["policy.json"]).policy_rules,
        logical_clock=organization.logical_clock,
        next_id_sequence=organization.next_id_sequence,
    )
    world = build_canonical_world(state, world_version=manifest.world_version)
    if world.world_digest != manifest.world_digest:
        raise ValueError("combined world digest does not match manifest")
    if state.logical_clock.now != manifest.logical_clock_start:
        raise ValueError("logical clock start does not match manifest")
    report = build_quality_report(world)
    if report.inventory != manifest.inventory:
        raise ValueError("manifest inventory does not match loaded world")
    return world


def build_quality_report(world: CanonicalOfficeWorld) -> OfficeWorldQualityReport:
    graph = world.state.domain_graph
    principals = graph.directory.principals
    inventory = WorldInventory(
        internal_users=sum(item.kind is PrincipalKind.USER for item in principals),
        external_contacts=sum(item.kind is PrincipalKind.EXTERNAL for item in principals),
        groups=sum(item.kind is PrincipalKind.GROUP for item in principals),
        mail_messages=len(graph.mail.messages),
        drive_files=len(graph.drive.files),
        drive_versions=len(graph.drive.versions),
        calendar_events=len(graph.calendar.events),
        workspace_files=len(graph.workspace.files),
    )
    membership_counts: dict[str, int] = {}
    for membership in graph.directory.memberships:
        membership_counts[membership.member_id] = membership_counts.get(membership.member_id, 0) + 1
    external_ids = {item.principal_id for item in principals if item.kind is PrincipalKind.EXTERNAL}
    conflict_pairs = _calendar_conflict_pairs(graph.calendar)
    resource_refs = {
        (ref.kind.value, ref.resource_id)
        for link in graph.resource_links
        for ref in (link.source, link.target)
    }
    all_resources = {
        *(("mail_message", item.message_id) for item in graph.mail.messages),
        *(("drive_file", item.file_id) for item in graph.drive.files),
        *(("calendar_event", item.event_id) for item in graph.calendar.events),
        *(("workspace_file", item.path) for item in graph.workspace.files),
    }
    searchable_text = [
        *(item.subject for item in graph.mail.threads),
        *(item.name for item in graph.drive.files),
        *(item.title for item in graph.calendar.events),
        *(item.path for item in graph.workspace.files),
    ]
    stale_terms = ("archive", "obsolete", "superseded", "stale", "old")
    conflict_text = [
        *(item.body for item in graph.mail.messages),
        *(item.content for item in graph.drive.versions),
        *(item.description for item in graph.calendar.events),
        *(item.content for item in graph.workspace.files),
    ]
    projects = tuple(
        sorted(
            project
            for project in ("apollo", "borealis", "cedar", "delta", "evergreen")
            if any(project in text.lower() for text in searchable_text)
        )
    )
    return OfficeWorldQualityReport(
        world_digest=world.world_digest,
        inventory=inventory,
        mail_threads=len(graph.mail.threads),
        overlapping_group_users=sum(count >= 2 for count in membership_counts.values()),
        drive_classifications=tuple(
            sorted({item.classification for item in graph.drive.files}, key=lambda item: item.value)
        ),
        discover_only_acl_entries=sum(
            tuple(item.rights) == ("discover",) for item in graph.acl_entries
        ),
        stale_searchable_items=sum(
            any(term in text.lower() for term in stale_terms) for text in searchable_text
        ),
        calendar_cancelled_events=sum(
            item.status is CalendarEventStatus.CANCELLED for item in graph.calendar.events
        ),
        calendar_external_events=sum(
            bool(set(item.attendee_ids) & external_ids) for item in graph.calendar.events
        ),
        calendar_resource_references=sum(bool(item.related_refs) for item in graph.calendar.events),
        calendar_conflict_pairs=conflict_pairs,
        cross_domain_links=len(graph.resource_links),
        connected_resource_ratio=len(resource_refs & all_resources) / len(all_resources),
        narrative_projects=projects,
        conflicting_source_markers=sum("CONFLICT:" in text for text in conflict_text),
    )


class _OrganizationData(OfficeV2Contract):
    directory: IdentityDirectory
    acl_entries: tuple[AclEntry, ...]
    resource_links: tuple[ResourceLink, ...]
    logical_clock: LogicalClock
    next_id_sequence: int = Field(ge=0)


class _PolicyData(OfficeV2Contract):
    policy_rules: tuple[EnterprisePolicyRule, ...]


def _calendar_conflict_pairs(calendar: CalendarStore) -> int:
    conflicts = 0
    for index, left in enumerate(calendar.events):
        for right in calendar.events[index + 1 :]:
            if left.status is CalendarEventStatus.CANCELLED:
                continue
            if right.status is CalendarEventStatus.CANCELLED:
                continue
            left_people = {left.organizer_id, *left.attendee_ids}
            right_people = {right.organizer_id, *right.attendee_ids}
            if (
                left_people & right_people
                and left.start_at < right.end_at
                and right.start_at < left.end_at
            ):
                conflicts += 1
    return conflicts


def _read_json(path: Path) -> object:
    return json.loads(path.read_bytes())
