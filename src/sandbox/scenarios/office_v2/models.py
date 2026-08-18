"""Strict shared value contracts for Office Workspace Scenario V2."""

from __future__ import annotations

import re
from collections.abc import Callable, Hashable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal, TypeVar

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from sandbox.protocol import ContractModel, normalize_sha256_digest
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import OFFICE_V2_CONTRACT_SCHEMA_VERSION

IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
WORLD_VERSION_PATTERN = r"^[0-9]+(?:\.[0-9]+){1,2}$"
TIMEZONE_PATTERN = r"^(?:UTC|[A-Za-z_]+(?:/[A-Za-z0-9_+.-]+)+)$"
FIELD_PATH_SEGMENT_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"

Identifier = Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]
WorldVersion = Annotated[str, Field(pattern=WORLD_VERSION_PATTERN)]
LogicalTime = Annotated[int, Field(ge=0)]
TimezoneName = Annotated[str, Field(pattern=TIMEZONE_PATTERN)]
FieldPathSegment = Annotated[str, Field(pattern=FIELD_PATH_SEGMENT_PATTERN)]
Sha256Digest = Annotated[str, AfterValidator(normalize_sha256_digest)]

ResourceId = Identifier
PrincipalId = Identifier
GroupId = Identifier
PolicyRuleId = Identifier
EvidenceId = Identifier
OrganizationId = Identifier
RoleId = Identifier
CapabilityId = Identifier


def _normalize_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_workspace_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("workspace path must be a string")
    if "\\" in value or "//" in value:
        raise ValueError("workspace path must use canonical POSIX separators")
    path = PurePosixPath(value)
    if not value.startswith("/workspace/") or path.as_posix() != value:
        raise ValueError("workspace file path must be canonical and below /workspace")
    if any(part in {".", ".."} for part in path.parts):
        raise ValueError("workspace file path must not contain traversal segments")
    return value


AwareDateTime = Annotated[datetime, AfterValidator(_normalize_aware_datetime)]
WorkspacePath = Annotated[
    str,
    Field(min_length=12, max_length=512),
    AfterValidator(_normalize_workspace_path),
]


class OfficeV2Contract(ContractModel):
    """Immutable, strict base for persisted Office V2 contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")
    schema_version: Literal["office-v2.0"] = OFFICE_V2_CONTRACT_SCHEMA_VERSION

    def canonical_digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json", exclude_none=False))


class ResourceKind(StrEnum):
    MAIL_THREAD = "mail_thread"
    MAIL_MESSAGE = "mail_message"
    DRIVE_FILE = "drive_file"
    DRIVE_FILE_VERSION = "drive_file_version"
    CALENDAR_EVENT = "calendar_event"
    WORKSPACE_FILE = "workspace_file"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class PrincipalKind(StrEnum):
    USER = "user"
    GROUP = "group"
    EXTERNAL = "external"
    SERVICE = "service"


class PrincipalStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class AccessRight(StrEnum):
    DISCOVER = "discover"
    READ = "read"
    WRITE = "write"
    SHARE = "share"
    DELETE = "delete"
    MANAGE_PERMISSIONS = "manage_permissions"


class ActionKind(StrEnum):
    DISCOVER = "discover"
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    SEND = "send"
    SHARE = "share"
    DELETE = "delete"
    MANAGE_PERMISSIONS = "manage_permissions"


class DecisionMode(StrEnum):
    ENFORCE = "enforce"
    AUDIT = "audit"


class EvidenceSourceKind(StrEnum):
    CANONICAL_WORLD = "canonical_world"
    DIRECTORY = "directory"
    RESOURCE = "resource"
    INTERACTION = "interaction"
    POLICY = "policy"
    TASK = "task"
    RESOLUTION = "resolution"


class RoleScopeKind(StrEnum):
    ORGANIZATION = "organization"
    RESOURCE = "resource"


class ResourceRef(OfficeV2Contract):
    kind: ResourceKind
    resource_id: str = Field(min_length=1, max_length=512)
    version_id: ResourceId | None = None

    @model_validator(mode="after")
    def locator_matches_resource_kind(self) -> ResourceRef:
        if self.kind is ResourceKind.WORKSPACE_FILE:
            _normalize_workspace_path(self.resource_id)
        elif re.fullmatch(IDENTIFIER_PATTERN, self.resource_id) is None:
            raise ValueError("non-workspace resource_id must be a valid identifier")
        if self.version_id is not None and self.kind is not ResourceKind.DRIVE_FILE:
            raise ValueError("version_id is only valid for drive_file references")
        return self

    def sort_key(self) -> tuple[str, str, str]:
        return (self.kind.value, self.resource_id, self.version_id or "")


def canonicalize_resource_refs(values: Iterable[ResourceRef]) -> tuple[ResourceRef, ...]:
    refs = tuple(values)
    keys = tuple(ref.sort_key() for ref in refs)
    if len(keys) != len(set(keys)):
        raise ValueError("resource references must not contain duplicates")
    return tuple(sorted(refs, key=ResourceRef.sort_key))


def canonicalize_identifiers(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    identifiers = tuple(values)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(sorted(identifiers))


class LogicalClock(OfficeV2Contract):
    now: LogicalTime = 0
    timezone: TimezoneName = "UTC"


DomainName = Annotated[
    str,
    Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"),
]
EmailAddress = Annotated[
    str,
    Field(pattern=r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}$"),
]


class Organization(OfficeV2Contract):
    organization_id: OrganizationId
    name: str = Field(min_length=1, max_length=128)
    primary_domain: DomainName
    external_domains: tuple[DomainName, ...] = Field(default_factory=tuple)

    @field_validator("primary_domain", mode="before")
    @classmethod
    def normalize_primary_domain(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @field_validator("external_domains", mode="before")
    @classmethod
    def normalize_external_domains(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return tuple(item.lower() if isinstance(item, str) else item for item in value)
        return value

    @field_validator("external_domains")
    @classmethod
    def external_domains_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="external_domains")

    @model_validator(mode="after")
    def domains_do_not_overlap(self) -> Organization:
        if self.primary_domain in self.external_domains:
            raise ValueError("primary_domain must not appear in external_domains")
        return self


class Principal(OfficeV2Contract):
    principal_id: PrincipalId
    kind: PrincipalKind
    display_name: str = Field(min_length=1, max_length=128)
    email: EmailAddress
    organization_id: OrganizationId | None = None
    status: PrincipalStatus = PrincipalStatus.ACTIVE

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def organization_matches_principal_kind(self) -> Principal:
        if self.kind is PrincipalKind.EXTERNAL and self.organization_id is not None:
            raise ValueError("external principal cannot belong to the internal organization")
        if self.kind is not PrincipalKind.EXTERNAL and self.organization_id is None:
            raise ValueError("internal principal requires organization_id")
        return self


class Group(Principal):
    kind: Literal[PrincipalKind.GROUP] = PrincipalKind.GROUP

    @property
    def group_id(self) -> GroupId:
        return self.principal_id


class GroupMembership(OfficeV2Contract):
    group_id: GroupId
    member_id: PrincipalId

    @model_validator(mode="after")
    def group_cannot_contain_itself(self) -> GroupMembership:
        if self.group_id == self.member_id:
            raise ValueError("group cannot contain itself")
        return self

    def sort_key(self) -> tuple[str, str]:
        return (self.group_id, self.member_id)


class RoleScope(OfficeV2Contract):
    kind: RoleScopeKind
    organization_id: OrganizationId | None = None
    resource: ResourceRef | None = None

    @model_validator(mode="after")
    def scope_target_matches_kind(self) -> RoleScope:
        if self.kind is RoleScopeKind.ORGANIZATION:
            if self.organization_id is None or self.resource is not None:
                raise ValueError("organization role scope requires only organization_id")
        elif self.organization_id is not None or self.resource is None:
            raise ValueError("resource role scope requires only resource")
        return self

    def sort_key(self) -> tuple[str, str, str, str]:
        resource_key = self.resource.sort_key() if self.resource else ("", "", "")
        return (self.kind.value, self.organization_id or "", *resource_key)


class RoleAssignment(OfficeV2Contract):
    principal_id: PrincipalId
    role_id: RoleId
    scope: RoleScope
    valid_from: LogicalTime = 0
    valid_until: LogicalTime | None = None

    @model_validator(mode="after")
    def validity_window_is_ordered(self) -> RoleAssignment:
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be greater than valid_from")
        return self

    def is_active(self, logical_time: int) -> bool:
        return self.valid_from <= logical_time and (
            self.valid_until is None or logical_time < self.valid_until
        )

    def sort_key(self) -> tuple[str, str, str, str, str, str, int, int]:
        return (
            self.principal_id,
            self.role_id,
            *self.scope.sort_key(),
            self.valid_from,
            self.valid_until if self.valid_until is not None else 2**63 - 1,
        )


class ActorContext(OfficeV2Contract):
    actor_id: PrincipalId
    authenticated_principal_id: PrincipalId
    active_role_ids: tuple[RoleId, ...] = Field(default_factory=tuple)
    active_group_ids: tuple[GroupId, ...] = Field(default_factory=tuple)
    session_capabilities: tuple[CapabilityId, ...] = Field(default_factory=tuple)
    mailbox_owner_id: PrincipalId
    workspace_root: Literal["/workspace"] = "/workspace"
    logical_time: LogicalTime
    directory_digest: Sha256Digest

    @field_validator("active_role_ids", "active_group_ids", "session_capabilities")
    @classmethod
    def set_like_ids_are_canonical(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name=info.field_name)


class IdentityDirectory(OfficeV2Contract):
    organization: Organization
    principals: tuple[Principal, ...] = Field(min_length=1)
    memberships: tuple[GroupMembership, ...] = Field(default_factory=tuple)
    role_assignments: tuple[RoleAssignment, ...] = Field(default_factory=tuple)

    @field_validator("principals")
    @classmethod
    def principals_are_canonical(cls, value: tuple[Principal, ...]) -> tuple[Principal, ...]:
        ids = tuple(item.principal_id for item in value)
        canonicalize_identifiers(ids, field_name="principal ids")
        emails = tuple(item.email for item in value)
        canonicalize_identifiers(emails, field_name="principal emails")
        return tuple(sorted(value, key=lambda item: item.principal_id))

    @field_validator("memberships")
    @classmethod
    def memberships_are_canonical(
        cls, value: tuple[GroupMembership, ...]
    ) -> tuple[GroupMembership, ...]:
        keys = tuple(item.sort_key() for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError("memberships must not contain duplicates")
        return tuple(sorted(value, key=GroupMembership.sort_key))

    @field_validator("role_assignments")
    @classmethod
    def role_assignments_are_canonical(
        cls, value: tuple[RoleAssignment, ...]
    ) -> tuple[RoleAssignment, ...]:
        keys = tuple(item.sort_key() for item in value)
        if len(keys) != len(set(keys)):
            raise ValueError("role_assignments must not contain duplicates")
        return tuple(sorted(value, key=RoleAssignment.sort_key))

    @model_validator(mode="after")
    def directory_references_are_valid(self) -> IdentityDirectory:
        principals = {item.principal_id: item for item in self.principals}
        internal_kinds = {PrincipalKind.USER, PrincipalKind.GROUP, PrincipalKind.SERVICE}
        for principal in self.principals:
            domain = principal.email.rsplit("@", 1)[1]
            if principal.kind in internal_kinds:
                if principal.organization_id != self.organization.organization_id:
                    raise ValueError("internal principal references another organization")
                if domain != self.organization.primary_domain:
                    raise ValueError("internal principal email must use primary_domain")
            elif domain not in self.organization.external_domains:
                raise ValueError("external principal email must use a registered external domain")

        for membership in self.memberships:
            group = principals.get(membership.group_id)
            if group is None or group.kind is not PrincipalKind.GROUP:
                raise ValueError("membership group_id must reference a group principal")
            if membership.member_id not in principals:
                raise ValueError("membership member_id references unknown principal")
        self._validate_group_graph(principals)

        for assignment in self.role_assignments:
            principal = principals.get(assignment.principal_id)
            if principal is None:
                raise ValueError("role assignment references unknown principal")
            if assignment.scope.kind is RoleScopeKind.ORGANIZATION:
                if assignment.scope.organization_id != self.organization.organization_id:
                    raise ValueError("role assignment references another organization")
                if principal.kind is PrincipalKind.EXTERNAL:
                    raise ValueError("external principal cannot receive an organization role")
        return self

    def derive_actor_context(
        self,
        *,
        actor_id: str,
        authenticated_principal_id: str,
        session_capabilities: Iterable[str],
        logical_time: int,
    ) -> ActorContext:
        principals = {item.principal_id: item for item in self.principals}
        actor = self._active_non_group_principal(principals, actor_id, owner="actor")
        self._active_non_group_principal(
            principals,
            authenticated_principal_id,
            owner="authenticated principal",
        )
        active_groups = self._group_closure(actor.principal_id, principals)
        role_subjects = {actor.principal_id, *active_groups}
        role_ids = (
            assignment.role_id
            for assignment in self.role_assignments
            if assignment.principal_id in role_subjects and assignment.is_active(logical_time)
        )
        return ActorContext(
            actor_id=actor.principal_id,
            authenticated_principal_id=authenticated_principal_id,
            active_role_ids=canonicalize_identifiers(role_ids, field_name="active_role_ids"),
            active_group_ids=tuple(sorted(active_groups)),
            session_capabilities=canonicalize_identifiers(
                session_capabilities,
                field_name="session_capabilities",
            ),
            mailbox_owner_id=actor.principal_id,
            logical_time=logical_time,
            directory_digest=self.canonical_digest(),
        )

    def _validate_group_graph(self, principals: dict[str, Principal]) -> None:
        groups = {
            principal_id
            for principal_id, principal in principals.items()
            if principal.kind is PrincipalKind.GROUP
        }
        parents: dict[str, set[str]] = {group_id: set() for group_id in groups}
        for membership in self.memberships:
            if membership.member_id in groups:
                parents[membership.member_id].add(membership.group_id)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(group_id: str) -> None:
            if group_id in visiting:
                raise ValueError("group memberships must not contain cycles")
            if group_id in visited:
                return
            visiting.add(group_id)
            for parent_id in sorted(parents[group_id]):
                visit(parent_id)
            visiting.remove(group_id)
            visited.add(group_id)

        for group_id in sorted(groups):
            visit(group_id)

    def _group_closure(
        self, member_id: str, principals: dict[str, Principal]
    ) -> set[str]:
        groups: set[str] = set()
        frontier = [member_id]
        while frontier:
            member = frontier.pop()
            parents = sorted(
                membership.group_id
                for membership in self.memberships
                if membership.member_id == member
                and membership.group_id not in groups
                and principals[membership.group_id].status is PrincipalStatus.ACTIVE
            )
            groups.update(parents)
            frontier.extend(parents)
        return groups

    @staticmethod
    def _active_non_group_principal(
        principals: dict[str, Principal], principal_id: str, *, owner: str
    ) -> Principal:
        principal = principals.get(principal_id)
        if principal is None:
            raise ValueError(f"{owner} references unknown principal")
        if principal.kind is PrincipalKind.GROUP:
            raise ValueError(f"{owner} cannot be a group")
        if principal.status is not PrincipalStatus.ACTIVE:
            raise ValueError(f"{owner} is not active")
        return principal


class SourceEvidence(OfficeV2Contract):
    evidence_id: EvidenceId
    source_kind: EvidenceSourceKind
    source_id: Identifier
    resource: ResourceRef | None = None
    field_path: tuple[FieldPathSegment, ...] = Field(default_factory=tuple, max_length=16)
    observed_at: LogicalTime
    content_digest: Sha256Digest

    @model_validator(mode="after")
    def resource_source_requires_resource_ref(self) -> SourceEvidence:
        if self.source_kind is EvidenceSourceKind.RESOURCE and self.resource is None:
            raise ValueError("resource evidence requires resource")
        return self


class StableFailure(OfficeV2Contract):
    error_code: Identifier
    public_message: str = Field(min_length=1, max_length=512)
    internal_evidence_refs: tuple[EvidenceId, ...] = Field(default_factory=tuple)

    @field_validator("internal_evidence_refs")
    @classmethod
    def evidence_refs_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="internal_evidence_refs")


class MailFolder(StrEnum):
    INBOX = "inbox"
    SENT = "sent"
    ARCHIVE = "archive"


class DriveLifecycle(StrEnum):
    ACTIVE = "active"
    TRASHED = "trashed"


class ShareStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class CalendarEventStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class AttendanceResponse(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TENTATIVE = "tentative"


class ResourceRelation(StrEnum):
    ATTACHMENT = "attachment"
    RESOURCE_REFERENCE = "resource_reference"
    MEETING_REQUEST = "meeting_request"
    MEETING_MATERIAL = "meeting_material"
    DERIVED_FROM = "derived_from"
    TASK_OUTPUT = "task_output"


ModelContractT = TypeVar("ModelContractT", bound=OfficeV2Contract)
ModelKeyT = TypeVar("ModelKeyT", bound=Hashable)


def _canonicalize_models(
    values: Iterable[ModelContractT],
    *,
    field_name: str,
    key: Callable[[ModelContractT], ModelKeyT],
) -> tuple[ModelContractT, ...]:
    items = tuple(values)
    keys = tuple(key(item) for item in items)
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(sorted(items, key=key))


def _canonicalize_rights(
    values: tuple[AccessRight, ...], *, field_name: str
) -> tuple[AccessRight, ...]:
    if not values:
        raise ValueError(f"{field_name} must not be empty")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(sorted(values, key=lambda item: item.value))


class MailThread(OfficeV2Contract):
    thread_id: ResourceId
    subject: str = Field(min_length=1, max_length=512)
    message_ids: tuple[ResourceId, ...] = Field(min_length=1)

    @field_validator("message_ids")
    @classmethod
    def message_ids_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("message_ids must not contain duplicates")
        return value


class MailMessage(OfficeV2Contract):
    message_id: ResourceId
    thread_id: ResourceId
    sender_id: PrincipalId
    to_ids: tuple[PrincipalId, ...] = Field(min_length=1)
    cc_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    subject: str = Field(min_length=1, max_length=512)
    body: str = Field(max_length=200_000)
    sent_at: AwareDateTime
    received_at: AwareDateTime
    attachment_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    in_reply_to: ResourceId | None = None

    @field_validator("to_ids", "cc_ids")
    @classmethod
    def recipient_ids_are_canonical(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name=info.field_name)

    @field_validator("attachment_refs")
    @classmethod
    def attachments_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        allowed = {
            ResourceKind.CALENDAR_EVENT,
            ResourceKind.DRIVE_FILE,
            ResourceKind.DRIVE_FILE_VERSION,
            ResourceKind.WORKSPACE_FILE,
        }
        if any(item.kind not in allowed for item in value):
            raise ValueError("attachment_refs contain an incompatible resource kind")
        return canonicalize_resource_refs(value)

    @model_validator(mode="after")
    def delivery_times_and_reply_are_valid(self) -> MailMessage:
        if self.received_at < self.sent_at:
            raise ValueError("received_at must not be earlier than sent_at")
        if set(self.to_ids).intersection(self.cc_ids):
            raise ValueError("to_ids and cc_ids must not overlap")
        if self.in_reply_to == self.message_id:
            raise ValueError("message cannot reply to itself")
        return self


class MailDelivery(OfficeV2Contract):
    message_id: ResourceId
    mailbox_owner_id: PrincipalId
    folder: MailFolder
    read_at: AwareDateTime | None = None

    def sort_key(self) -> tuple[str, str]:
        return (self.message_id, self.mailbox_owner_id)


class MailStore(OfficeV2Contract):
    threads: tuple[MailThread, ...] = Field(default_factory=tuple)
    messages: tuple[MailMessage, ...] = Field(default_factory=tuple)
    deliveries: tuple[MailDelivery, ...] = Field(default_factory=tuple)

    @field_validator("threads")
    @classmethod
    def threads_are_canonical(cls, value: tuple[MailThread, ...]) -> tuple[MailThread, ...]:
        return tuple(
            _canonicalize_models(value, field_name="threads", key=lambda item: item.thread_id)
        )

    @field_validator("messages")
    @classmethod
    def messages_are_canonical(
        cls, value: tuple[MailMessage, ...]
    ) -> tuple[MailMessage, ...]:
        return tuple(
            _canonicalize_models(value, field_name="messages", key=lambda item: item.message_id)
        )

    @field_validator("deliveries")
    @classmethod
    def deliveries_are_canonical(
        cls, value: tuple[MailDelivery, ...]
    ) -> tuple[MailDelivery, ...]:
        return tuple(
            _canonicalize_models(value, field_name="deliveries", key=MailDelivery.sort_key)
        )

    @model_validator(mode="after")
    def thread_and_delivery_references_are_valid(self) -> MailStore:
        messages = {item.message_id: item for item in self.messages}
        threads = {item.thread_id: item for item in self.threads}
        for message in self.messages:
            if message.thread_id not in threads:
                raise ValueError("mail message references unknown thread")
            if message.in_reply_to is not None:
                parent = messages.get(message.in_reply_to)
                if parent is None or parent.thread_id != message.thread_id:
                    raise ValueError(
                        "in_reply_to must reference an earlier message in the same thread"
                    )
                if (parent.sent_at, parent.message_id) >= (message.sent_at, message.message_id):
                    raise ValueError(
                        "in_reply_to must reference an earlier message in the same thread"
                    )
        for thread in self.threads:
            expected = tuple(
                item.message_id
                for item in sorted(
                    (message for message in self.messages if message.thread_id == thread.thread_id),
                    key=lambda item: (item.sent_at, item.message_id),
                )
            )
            if thread.message_ids != expected:
                raise ValueError("thread message_ids must exactly match chronological messages")
        for delivery in self.deliveries:
            if delivery.message_id not in messages:
                raise ValueError("mail delivery references unknown message")
            message = messages[delivery.message_id]
            earliest_read = (
                message.sent_at
                if delivery.mailbox_owner_id == message.sender_id
                else message.received_at
            )
            if delivery.read_at is not None and delivery.read_at < earliest_read:
                raise ValueError("mail delivery read_at is earlier than delivery time")
        for message in self.messages:
            expected_owners = {message.sender_id, *message.to_ids, *message.cc_ids}
            actual_owners = {
                item.mailbox_owner_id
                for item in self.deliveries
                if item.message_id == message.message_id
            }
            if actual_owners != expected_owners:
                raise ValueError("mail deliveries must exactly cover sender and recipients")
        return self


MimeType = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")]


class DriveFile(OfficeV2Contract):
    file_id: ResourceId
    name: str = Field(min_length=1, max_length=512)
    mime_type: MimeType
    owner_id: PrincipalId
    classification: Sensitivity
    current_version_id: ResourceId
    lifecycle_state: DriveLifecycle = DriveLifecycle.ACTIVE


class DriveFileVersion(OfficeV2Contract):
    version_id: ResourceId
    file_id: ResourceId
    content: str = Field(max_length=500_000)
    created_by: PrincipalId
    created_at: AwareDateTime
    source_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)

    @field_validator("source_refs")
    @classmethod
    def source_refs_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)


class AclEntry(OfficeV2Contract):
    resource: ResourceRef
    grantee_id: PrincipalId
    rights: tuple[AccessRight, ...] = Field(min_length=1)
    granted_by: PrincipalId
    granted_at: AwareDateTime
    grant_source: SourceEvidence

    @field_validator("rights")
    @classmethod
    def rights_are_canonical(cls, value: tuple[AccessRight, ...]) -> tuple[AccessRight, ...]:
        return _canonicalize_rights(value, field_name="rights")

    @model_validator(mode="after")
    def acl_applies_to_drive_resource(self) -> AclEntry:
        if self.resource.kind not in {ResourceKind.DRIVE_FILE, ResourceKind.DRIVE_FILE_VERSION}:
            raise ValueError("ACL resource must be a drive file or version")
        return self

    def sort_key(self) -> tuple[str, str, str, str]:
        return (*self.resource.sort_key(), self.grantee_id)


class ShareRecord(OfficeV2Contract):
    share_id: ResourceId
    resource: ResourceRef
    recipient_id: PrincipalId
    rights: tuple[AccessRight, ...] = Field(min_length=1)
    created_by: PrincipalId
    created_at: AwareDateTime
    status: ShareStatus = ShareStatus.ACTIVE

    @field_validator("rights")
    @classmethod
    def rights_are_canonical(cls, value: tuple[AccessRight, ...]) -> tuple[AccessRight, ...]:
        return _canonicalize_rights(value, field_name="rights")

    @model_validator(mode="after")
    def share_applies_to_drive_file(self) -> ShareRecord:
        if self.resource.kind is not ResourceKind.DRIVE_FILE:
            raise ValueError("share resource must be a drive file")
        return self


class DriveStore(OfficeV2Contract):
    files: tuple[DriveFile, ...] = Field(default_factory=tuple)
    versions: tuple[DriveFileVersion, ...] = Field(default_factory=tuple)
    share_records: tuple[ShareRecord, ...] = Field(default_factory=tuple)

    @field_validator("files")
    @classmethod
    def files_are_canonical(cls, value: tuple[DriveFile, ...]) -> tuple[DriveFile, ...]:
        return tuple(
            _canonicalize_models(value, field_name="files", key=lambda item: item.file_id)
        )

    @field_validator("versions")
    @classmethod
    def versions_are_canonical(
        cls, value: tuple[DriveFileVersion, ...]
    ) -> tuple[DriveFileVersion, ...]:
        return tuple(
            _canonicalize_models(value, field_name="versions", key=lambda item: item.version_id)
        )

    @field_validator("share_records")
    @classmethod
    def shares_are_canonical(
        cls, value: tuple[ShareRecord, ...]
    ) -> tuple[ShareRecord, ...]:
        return tuple(
            _canonicalize_models(
                value,
                field_name="share_records",
                key=lambda item: item.share_id,
            )
        )

    @model_validator(mode="after")
    def file_versions_are_valid(self) -> DriveStore:
        versions = {item.version_id: item for item in self.versions}
        files = {item.file_id: item for item in self.files}
        for version in self.versions:
            if version.file_id not in files:
                raise ValueError("drive version references unknown file")
        for file in self.files:
            file_versions = tuple(item for item in self.versions if item.file_id == file.file_id)
            if not file_versions:
                raise ValueError("drive file must have at least one version")
            current = versions.get(file.current_version_id)
            if current is None or current.file_id != file.file_id:
                raise ValueError("current_version_id must reference a version of the file")
            latest = max(file_versions, key=lambda item: (item.created_at, item.version_id))
            if current.version_id != latest.version_id:
                raise ValueError("current_version_id must reference the latest version")
        for share in self.share_records:
            if share.resource.resource_id not in files:
                raise ValueError("share record references unknown drive file")
        return self


class CalendarEvent(OfficeV2Contract):
    event_id: ResourceId
    organizer_id: PrincipalId
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(max_length=100_000)
    start_at: AwareDateTime
    end_at: AwareDateTime
    timezone: TimezoneName
    attendee_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    status: CalendarEventStatus = CalendarEventStatus.CONFIRMED
    related_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    version: int = Field(default=1, ge=1)

    @field_validator("attendee_ids")
    @classmethod
    def attendees_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="attendee_ids")

    @field_validator("related_refs")
    @classmethod
    def related_refs_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @model_validator(mode="after")
    def event_interval_is_valid(self) -> CalendarEvent:
        if self.start_at >= self.end_at:
            raise ValueError("calendar event requires start_at before end_at")
        return self


class Attendance(OfficeV2Contract):
    event_id: ResourceId
    principal_id: PrincipalId
    response_status: AttendanceResponse = AttendanceResponse.PENDING

    def sort_key(self) -> tuple[str, str]:
        return (self.event_id, self.principal_id)


class CalendarStore(OfficeV2Contract):
    events: tuple[CalendarEvent, ...] = Field(default_factory=tuple)
    attendance: tuple[Attendance, ...] = Field(default_factory=tuple)

    @field_validator("events")
    @classmethod
    def events_are_canonical(
        cls, value: tuple[CalendarEvent, ...]
    ) -> tuple[CalendarEvent, ...]:
        return tuple(
            _canonicalize_models(value, field_name="events", key=lambda item: item.event_id)
        )

    @field_validator("attendance")
    @classmethod
    def attendance_is_canonical(
        cls, value: tuple[Attendance, ...]
    ) -> tuple[Attendance, ...]:
        return tuple(
            _canonicalize_models(value, field_name="attendance", key=Attendance.sort_key)
        )

    @model_validator(mode="after")
    def attendance_matches_events(self) -> CalendarStore:
        events = {item.event_id: item for item in self.events}
        attendance_by_event: dict[str, set[str]] = {event_id: set() for event_id in events}
        for item in self.attendance:
            if item.event_id not in events:
                raise ValueError("attendance references unknown event")
            attendance_by_event[item.event_id].add(item.principal_id)
        for event in self.events:
            if attendance_by_event[event.event_id] != set(event.attendee_ids):
                raise ValueError("attendance records must exactly match event attendee_ids")
        return self


class WorkspaceFile(OfficeV2Contract):
    path: WorkspacePath
    owner_id: PrincipalId
    content: str = Field(max_length=500_000)
    media_type: MimeType
    version: int = Field(default=1, ge=1)
    created_at: AwareDateTime
    updated_at: AwareDateTime
    source_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)

    @field_validator("source_refs")
    @classmethod
    def source_refs_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @model_validator(mode="after")
    def update_time_is_valid(self) -> WorkspaceFile:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        return self


class WorkspaceDirectory(OfficeV2Contract):
    path: str = Field(min_length=10, max_length=512)
    child_paths: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("path")
    @classmethod
    def directory_path_is_canonical(cls, value: str) -> str:
        if value == "/workspace":
            return value
        candidate = f"{value}/.directory-placeholder"
        _normalize_workspace_path(candidate)
        return value

    @field_validator("child_paths")
    @classmethod
    def children_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for child_path in value:
            _normalize_workspace_path(child_path)
        return canonicalize_identifiers(value, field_name="child_paths")


class WorkspaceStore(OfficeV2Contract):
    files: tuple[WorkspaceFile, ...] = Field(default_factory=tuple)

    @field_validator("files")
    @classmethod
    def files_are_canonical(
        cls, value: tuple[WorkspaceFile, ...]
    ) -> tuple[WorkspaceFile, ...]:
        return tuple(
            _canonicalize_models(value, field_name="workspace files", key=lambda item: item.path)
        )

    def directories(self) -> tuple[WorkspaceDirectory, ...]:
        children: dict[str, set[str]] = {"/workspace": set()}
        for file in self.files:
            path = PurePosixPath(file.path)
            parent = PurePosixPath("/workspace")
            for part in path.parts[2:-1]:
                child = parent / part
                children.setdefault(parent.as_posix(), set()).add(child.as_posix())
                children.setdefault(child.as_posix(), set())
                parent = child
            children.setdefault(parent.as_posix(), set()).add(file.path)
        return tuple(
            WorkspaceDirectory(path=path, child_paths=tuple(sorted(child_paths)))
            for path, child_paths in sorted(children.items())
        )


class ResourceLink(OfficeV2Contract):
    link_id: ResourceId
    source: ResourceRef
    target: ResourceRef
    relation: ResourceRelation
    created_by: PrincipalId
    created_at: AwareDateTime

    @model_validator(mode="after")
    def endpoints_match_relation(self) -> ResourceLink:
        if self.source == self.target:
            raise ValueError("resource link cannot reference the same endpoint twice")
        content = {
            ResourceKind.MAIL_MESSAGE,
            ResourceKind.DRIVE_FILE,
            ResourceKind.DRIVE_FILE_VERSION,
            ResourceKind.CALENDAR_EVENT,
            ResourceKind.WORKSPACE_FILE,
        }
        compatibility = {
            ResourceRelation.ATTACHMENT: (
                {ResourceKind.MAIL_MESSAGE},
                {
                    ResourceKind.DRIVE_FILE,
                    ResourceKind.DRIVE_FILE_VERSION,
                    ResourceKind.WORKSPACE_FILE,
                },
            ),
            ResourceRelation.MEETING_REQUEST: (
                {ResourceKind.MAIL_MESSAGE},
                {ResourceKind.CALENDAR_EVENT},
            ),
            ResourceRelation.MEETING_MATERIAL: (
                {ResourceKind.CALENDAR_EVENT},
                {
                    ResourceKind.DRIVE_FILE,
                    ResourceKind.DRIVE_FILE_VERSION,
                    ResourceKind.WORKSPACE_FILE,
                },
            ),
            ResourceRelation.DERIVED_FROM: (content, content),
            ResourceRelation.TASK_OUTPUT: (content, {ResourceKind.WORKSPACE_FILE}),
            ResourceRelation.RESOURCE_REFERENCE: (content, content),
        }
        source_kinds, target_kinds = compatibility[self.relation]
        if self.source.kind not in source_kinds or self.target.kind not in target_kinds:
            raise ValueError("resource link endpoints are incompatible with relation")
        return self


class OfficeDomainGraph(OfficeV2Contract):
    directory: IdentityDirectory
    mail: MailStore = Field(default_factory=MailStore)
    drive: DriveStore = Field(default_factory=DriveStore)
    calendar: CalendarStore = Field(default_factory=CalendarStore)
    workspace: WorkspaceStore = Field(default_factory=WorkspaceStore)
    acl_entries: tuple[AclEntry, ...] = Field(default_factory=tuple)
    resource_links: tuple[ResourceLink, ...] = Field(default_factory=tuple)

    @field_validator("acl_entries")
    @classmethod
    def acl_entries_are_canonical(cls, value: tuple[AclEntry, ...]) -> tuple[AclEntry, ...]:
        return tuple(
            _canonicalize_models(value, field_name="acl_entries", key=AclEntry.sort_key)
        )

    @field_validator("resource_links")
    @classmethod
    def links_are_canonical(
        cls, value: tuple[ResourceLink, ...]
    ) -> tuple[ResourceLink, ...]:
        return tuple(
            _canonicalize_models(value, field_name="resource_links", key=lambda item: item.link_id)
        )

    @model_validator(mode="after")
    def all_references_resolve(self) -> OfficeDomainGraph:
        principal_ids = {item.principal_id for item in self.directory.principals}
        self._validate_mail_principals(principal_ids)
        self._validate_drive_principals(principal_ids)
        self._validate_calendar_principals(principal_ids)
        self._validate_workspace_principals(principal_ids)

        refs: list[ResourceRef] = []
        refs.extend(ref for message in self.mail.messages for ref in message.attachment_refs)
        refs.extend(ref for version in self.drive.versions for ref in version.source_refs)
        refs.extend(ref for event in self.calendar.events for ref in event.related_refs)
        refs.extend(ref for file in self.workspace.files for ref in file.source_refs)
        for assignment in self.directory.role_assignments:
            if assignment.scope.resource is not None:
                refs.append(assignment.scope.resource)
        for acl in self.acl_entries:
            refs.append(acl.resource)
            if acl.grant_source.resource is not None:
                refs.append(acl.grant_source.resource)
        for share in self.drive.share_records:
            refs.append(share.resource)
        for link in self.resource_links:
            refs.extend((link.source, link.target))
        for ref in refs:
            if not self.resource_exists(ref):
                raise ValueError(
                    f"resource reference does not resolve: {ref.kind.value}/{ref.resource_id}"
                )
        return self

    def resource_exists(self, ref: ResourceRef) -> bool:
        if ref.kind is ResourceKind.MAIL_THREAD:
            return any(item.thread_id == ref.resource_id for item in self.mail.threads)
        if ref.kind is ResourceKind.MAIL_MESSAGE:
            return any(item.message_id == ref.resource_id for item in self.mail.messages)
        if ref.kind is ResourceKind.DRIVE_FILE:
            file_exists = any(item.file_id == ref.resource_id for item in self.drive.files)
            if not file_exists or ref.version_id is None:
                return file_exists
            return any(
                item.version_id == ref.version_id and item.file_id == ref.resource_id
                for item in self.drive.versions
            )
        if ref.kind is ResourceKind.DRIVE_FILE_VERSION:
            return any(item.version_id == ref.resource_id for item in self.drive.versions)
        if ref.kind is ResourceKind.CALENDAR_EVENT:
            return any(item.event_id == ref.resource_id for item in self.calendar.events)
        if ref.kind is ResourceKind.WORKSPACE_FILE:
            return any(item.path == ref.resource_id for item in self.workspace.files)
        return False

    def _validate_mail_principals(self, principal_ids: set[str]) -> None:
        messages = {item.message_id: item for item in self.mail.messages}
        for message in self.mail.messages:
            referenced = {message.sender_id, *message.to_ids, *message.cc_ids}
            if not referenced.issubset(principal_ids):
                raise ValueError("mail message references unknown principal")
        for delivery in self.mail.deliveries:
            if delivery.mailbox_owner_id not in principal_ids:
                raise ValueError("mail delivery references unknown mailbox owner")
            message = messages[delivery.message_id]
            recipients = {*message.to_ids, *message.cc_ids}
            if delivery.folder is MailFolder.SENT:
                if delivery.mailbox_owner_id != message.sender_id:
                    raise ValueError("sent delivery must belong to the sender")
            elif delivery.mailbox_owner_id not in recipients:
                raise ValueError("received delivery must belong to a recipient")

    def _validate_drive_principals(self, principal_ids: set[str]) -> None:
        for file in self.drive.files:
            if file.owner_id not in principal_ids:
                raise ValueError("drive file references unknown owner")
        for version in self.drive.versions:
            if version.created_by not in principal_ids:
                raise ValueError("drive version references unknown creator")
        for share in self.drive.share_records:
            if share.recipient_id not in principal_ids or share.created_by not in principal_ids:
                raise ValueError("share record references unknown principal")
        for acl in self.acl_entries:
            if acl.grantee_id not in principal_ids or acl.granted_by not in principal_ids:
                raise ValueError("ACL entry references unknown principal")

    def _validate_calendar_principals(self, principal_ids: set[str]) -> None:
        for event in self.calendar.events:
            referenced = {event.organizer_id, *event.attendee_ids}
            if not referenced.issubset(principal_ids):
                raise ValueError("calendar event references unknown principal")
        for attendance in self.calendar.attendance:
            if attendance.principal_id not in principal_ids:
                raise ValueError("attendance references unknown principal")

    def _validate_workspace_principals(self, principal_ids: set[str]) -> None:
        for file in self.workspace.files:
            if file.owner_id not in principal_ids:
                raise ValueError("workspace file references unknown owner")
        for link in self.resource_links:
            if link.created_by not in principal_ids:
                raise ValueError("resource link references unknown creator")


class IssuerAuthentication(StrEnum):
    AUTHENTICATED = "authenticated"
    UNAUTHENTICATED = "unauthenticated"
    CLAIMED_ONLY = "claimed_only"


class QueryCardinality(StrEnum):
    EXACTLY_ONE = "exactly_one"
    ONE_OR_MORE = "one_or_more"


class QueryTiePolicy(StrEnum):
    UNIQUE_REQUIRED = "unique_required"
    CLARIFICATION_REQUIRED = "clarification_required"


class PredicateField(StrEnum):
    PROJECT = "project"
    SUBJECT = "subject"
    OWNER = "owner"
    CLASSIFICATION = "classification"
    LIFECYCLE = "lifecycle"
    VERSION_STATE = "version_state"
    START_TIME = "start_time"
    END_TIME = "end_time"


class PredicateOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS_TOKEN = "contains_token"
    BEFORE = "before"
    AFTER = "after"


class RelationDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"
    EITHER = "either"


class QuestionKind(StrEnum):
    DISAMBIGUATION = "disambiguation"
    MISSING_VALUE = "missing_value"
    AUTHORIZATION = "authorization"


class BindingResolutionStatus(StrEnum):
    RESOLVED_UNIQUE = "resolved_unique"
    RESOLVED_SET = "resolved_set"
    RESOLVED_AFTER_CLARIFICATION = "resolved_after_clarification"


class BranchOperator(StrEnum):
    EQUALS = "equals"
    PRESENT = "present"
    ABSENT = "absent"


class ActionScope(OfficeV2Contract):
    action: ActionKind
    resource_kinds: tuple[ResourceKind, ...] = Field(min_length=1)

    @field_validator("resource_kinds")
    @classmethod
    def resource_kinds_are_canonical(
        cls, value: tuple[ResourceKind, ...]
    ) -> tuple[ResourceKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("resource_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    def sort_key(self) -> tuple[str, tuple[str, ...]]:
        return (self.action.value, tuple(item.value for item in self.resource_kinds))


class ResourcePredicate(OfficeV2Contract):
    field: PredicateField
    operator: PredicateOperator = PredicateOperator.EQUALS
    value: str | int = Field(union_mode="left_to_right")

    @model_validator(mode="after")
    def operator_matches_value(self) -> ResourcePredicate:
        if self.operator is PredicateOperator.CONTAINS_TOKEN and not isinstance(
            self.value, str
        ):
            raise ValueError("contains_token requires a string value")
        if self.operator in {PredicateOperator.BEFORE, PredicateOperator.AFTER} and (
            self.field not in {PredicateField.START_TIME, PredicateField.END_TIME}
            or not isinstance(self.value, int)
        ):
            raise ValueError("before/after require an integer time predicate")
        return self

    def sort_key(self) -> tuple[str, str, str]:
        return (self.field.value, self.operator.value, str(self.value))


class ResourceRelationConstraint(OfficeV2Contract):
    relation: ResourceRelation
    direction: RelationDirection
    related_query_id: Identifier

    def sort_key(self) -> tuple[str, str, str]:
        return (self.relation.value, self.direction.value, self.related_query_id)


class ResourceQuery(OfficeV2Contract):
    query_id: Identifier
    binding_name: Identifier
    resource_kind: ResourceKind
    predicates: tuple[ResourcePredicate, ...] = Field(default_factory=tuple)
    actor_access: tuple[AccessRight, ...] = Field(min_length=1)
    relation_constraints: tuple[ResourceRelationConstraint, ...] = Field(
        default_factory=tuple
    )
    cardinality: QueryCardinality
    tie_policy: QueryTiePolicy

    @field_validator("predicates")
    @classmethod
    def predicates_are_canonical(
        cls, value: tuple[ResourcePredicate, ...]
    ) -> tuple[ResourcePredicate, ...]:
        return tuple(
            _canonicalize_models(
                value, field_name="predicates", key=ResourcePredicate.sort_key
            )
        )

    @field_validator("actor_access")
    @classmethod
    def actor_access_is_canonical(
        cls, value: tuple[AccessRight, ...]
    ) -> tuple[AccessRight, ...]:
        return _canonicalize_rights(value, field_name="actor_access")

    @field_validator("relation_constraints")
    @classmethod
    def relations_are_canonical(
        cls, value: tuple[ResourceRelationConstraint, ...]
    ) -> tuple[ResourceRelationConstraint, ...]:
        return tuple(
            _canonicalize_models(
                value,
                field_name="relation_constraints",
                key=ResourceRelationConstraint.sort_key,
            )
        )


class ResolvedBinding(OfficeV2Contract):
    query_id: Identifier
    binding_name: Identifier
    resource_refs: tuple[ResourceRef, ...] = Field(min_length=1)
    matched_fact_refs: tuple[EvidenceId, ...] = Field(min_length=1)
    candidate_evidence_refs: tuple[EvidenceId, ...] = Field(min_length=1)
    resolution_status: BindingResolutionStatus
    resolver_version: Identifier
    world_digest: Sha256Digest
    actor_view_digest: Sha256Digest
    resolution_digest: Sha256Digest

    @field_validator("resource_refs")
    @classmethod
    def resources_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator("matched_fact_refs", "candidate_evidence_refs")
    @classmethod
    def evidence_is_canonical(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name=info.field_name)

    @model_validator(mode="after")
    def evidence_and_status_are_consistent(self) -> ResolvedBinding:
        if not set(self.matched_fact_refs).issubset(self.candidate_evidence_refs):
            raise ValueError("matched facts must be included in candidate evidence")
        if (
            self.resolution_status is BindingResolutionStatus.RESOLVED_UNIQUE
            and len(self.resource_refs) != 1
        ):
            raise ValueError("resolved_unique binding must contain exactly one resource")
        if (
            self.resolution_status is BindingResolutionStatus.RESOLVED_SET
            and len(self.resource_refs) < 1
        ):
            raise ValueError("resolved_set binding must contain at least one resource")
        return self

    def assert_matches_query(self, query: ResourceQuery) -> None:
        if self.query_id != query.query_id or self.binding_name != query.binding_name:
            raise ValueError("binding does not identify the supplied query")
        if any(ref.kind is not query.resource_kind for ref in self.resource_refs):
            raise ValueError("binding resource kind does not match query")
        if query.cardinality is QueryCardinality.EXACTLY_ONE and len(self.resource_refs) != 1:
            raise ValueError("exactly_one query must resolve to one resource")


class TaskFact(OfficeV2Contract):
    fact_id: Identifier
    description: str = Field(min_length=1, max_length=512)
    query_ids: tuple[Identifier, ...] = Field(default_factory=tuple)

    @field_validator("query_ids")
    @classmethod
    def query_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="query_ids")


class BranchCondition(OfficeV2Contract):
    fact_id: Identifier
    operator: BranchOperator
    expected_value: str | int | bool | None = Field(
        default=None, union_mode="left_to_right"
    )

    @model_validator(mode="after")
    def expected_value_matches_operator(self) -> BranchCondition:
        if self.operator is BranchOperator.EQUALS and self.expected_value is None:
            raise ValueError("equals branch requires expected_value")
        if self.operator is not BranchOperator.EQUALS and self.expected_value is not None:
            raise ValueError("present/absent branch must not define expected_value")
        return self


class ClarificationGate(OfficeV2Contract):
    question_kind: QuestionKind
    fact_ids: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("fact_ids")
    @classmethod
    def fact_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="fact_ids")


class TaskGoal(OfficeV2Contract):
    goal_id: Identifier
    description: str = Field(min_length=1, max_length=512)
    depends_on: tuple[Identifier, ...] = Field(default_factory=tuple)
    required: bool = True
    preconditions: tuple[Identifier, ...] = Field(default_factory=tuple)
    success_assertions: tuple[Identifier, ...] = Field(min_length=1)
    allowed_action_scopes: tuple[ActionScope, ...] = Field(default_factory=tuple)
    branch_condition: BranchCondition | None = None
    clarification_gate: ClarificationGate | None = None

    @field_validator("depends_on", "preconditions", "success_assertions")
    @classmethod
    def identifiers_are_canonical(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name=info.field_name)

    @field_validator("allowed_action_scopes")
    @classmethod
    def action_scopes_are_canonical(
        cls, value: tuple[ActionScope, ...]
    ) -> tuple[ActionScope, ...]:
        return tuple(
            _canonicalize_models(
                value, field_name="allowed_action_scopes", key=ActionScope.sort_key
            )
        )


class TaskGoalGraph(OfficeV2Contract):
    goals: tuple[TaskGoal, ...] = Field(min_length=1)

    @field_validator("goals")
    @classmethod
    def goals_are_canonical(cls, value: tuple[TaskGoal, ...]) -> tuple[TaskGoal, ...]:
        return tuple(
            _canonicalize_models(value, field_name="goals", key=lambda item: item.goal_id)
        )

    @model_validator(mode="after")
    def dependencies_form_a_dag(self) -> TaskGoalGraph:
        goals = {goal.goal_id: goal for goal in self.goals}
        for goal in self.goals:
            unknown = set(goal.depends_on).difference(goals)
            if unknown:
                raise ValueError(f"goal depends on unknown goals: {sorted(unknown)}")
            if goal.goal_id in goal.depends_on:
                raise ValueError("goal must not depend on itself")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(goal_id: str) -> None:
            if goal_id in visiting:
                raise ValueError("goal dependencies must form a DAG")
            if goal_id in visited:
                return
            visiting.add(goal_id)
            for dependency_id in goals[goal_id].depends_on:
                visit(dependency_id)
            visiting.remove(goal_id)
            visited.add(goal_id)

        for goal_id in goals:
            visit(goal_id)
        return self


class TaskDelegation(OfficeV2Contract):
    delegation_id: Identifier
    issuer_id: PrincipalId
    actor_id: PrincipalId
    action_scope: ActionScope
    resource_query_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    recipient_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    valid_from: LogicalTime
    expires_at: LogicalTime
    source_evidence_ref: EvidenceId

    @field_validator("resource_query_ids", "recipient_ids")
    @classmethod
    def scope_ids_are_canonical(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validity_window_is_nonempty(self) -> TaskDelegation:
        if self.expires_at <= self.valid_from:
            raise ValueError("delegation expires_at must be after valid_from")
        return self


class ClarificationRequest(OfficeV2Contract):
    request_id: Identifier
    question_kind: QuestionKind
    missing_fact_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    candidate_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    requested_action_scope: ActionScope | None = None
    requested_recipient_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    allowed_responder_ids: tuple[PrincipalId, ...] = Field(min_length=1)
    requested_at: LogicalTime

    @field_validator(
        "missing_fact_ids", "requested_recipient_ids", "allowed_responder_ids"
    )
    @classmethod
    def identifiers_are_canonical(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name=info.field_name)

    @field_validator("candidate_refs")
    @classmethod
    def candidates_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @model_validator(mode="after")
    def payload_matches_question(self) -> ClarificationRequest:
        if self.question_kind is QuestionKind.DISAMBIGUATION:
            if len(self.candidate_refs) < 2:
                raise ValueError("disambiguation requires at least two candidates")
            if self.requested_action_scope is not None:
                raise ValueError("disambiguation must not request authorization")
        elif self.question_kind is QuestionKind.MISSING_VALUE:
            if not self.missing_fact_ids:
                raise ValueError("missing_value requires missing facts")
            if self.requested_action_scope is not None:
                raise ValueError("missing_value must not request authorization")
        else:
            if self.requested_action_scope is None:
                raise ValueError("authorization requires requested_action_scope")
            if not self.candidate_refs and not self.requested_recipient_ids:
                raise ValueError("authorization requires resource or recipient scope")
        return self


class ResponseMatch(OfficeV2Contract):
    request_id: Identifier
    question_kind: QuestionKind
    candidate_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    requested_action_scope: ActionScope | None = None
    requested_recipient_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)

    @field_validator("candidate_refs")
    @classmethod
    def candidates_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator("requested_recipient_ids")
    @classmethod
    def recipients_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="requested_recipient_ids")


class GrantTemplate(OfficeV2Contract):
    action_scope: ActionScope
    resource_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    recipient_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    duration_ticks: int = Field(gt=0)

    @field_validator("resource_refs")
    @classmethod
    def resources_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator("recipient_ids")
    @classmethod
    def recipients_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="recipient_ids")

    @model_validator(mode="after")
    def grant_is_narrow_and_typed(self) -> GrantTemplate:
        if not self.resource_refs and not self.recipient_ids:
            raise ValueError("grant template requires resource or recipient scope")
        allowed_kinds = set(self.action_scope.resource_kinds)
        if any(ref.kind not in allowed_kinds for ref in self.resource_refs):
            raise ValueError("grant resource is outside action scope")
        return self


class UserResponseRule(OfficeV2Contract):
    rule_id: Identifier
    match: ResponseMatch
    authenticated_responder_id: PrincipalId
    response_channel: Literal["authenticated_task_session"] = (
        "authenticated_task_session"
    )
    response_text: str = Field(min_length=1, max_length=2048)
    selected_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    grant_effect: GrantTemplate | None = None

    @field_validator("selected_refs")
    @classmethod
    def selected_refs_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)


class InteractionContract(OfficeV2Contract):
    requests: tuple[ClarificationRequest, ...] = Field(default_factory=tuple)
    response_rules: tuple[UserResponseRule, ...] = Field(default_factory=tuple)

    @field_validator("requests")
    @classmethod
    def requests_are_canonical(
        cls, value: tuple[ClarificationRequest, ...]
    ) -> tuple[ClarificationRequest, ...]:
        return tuple(
            _canonicalize_models(
                value, field_name="requests", key=lambda item: item.request_id
            )
        )

    @field_validator("response_rules")
    @classmethod
    def rules_are_canonical(
        cls, value: tuple[UserResponseRule, ...]
    ) -> tuple[UserResponseRule, ...]:
        return tuple(
            _canonicalize_models(
                value, field_name="response_rules", key=lambda item: item.rule_id
            )
        )

    @model_validator(mode="after")
    def rules_match_authorized_requests(self) -> InteractionContract:
        requests = {request.request_id: request for request in self.requests}
        for rule in self.response_rules:
            request = requests.get(rule.match.request_id)
            if request is None:
                raise ValueError("response rule references unknown request")
            if (
                rule.match.question_kind is not request.question_kind
                or rule.match.candidate_refs != request.candidate_refs
                or rule.match.requested_action_scope != request.requested_action_scope
                or rule.match.requested_recipient_ids
                != request.requested_recipient_ids
            ):
                raise ValueError("response rule match does not equal frozen request")
            if rule.authenticated_responder_id not in request.allowed_responder_ids:
                raise ValueError("response rule uses unauthorized responder")
            if not set(rule.selected_refs).issubset(request.candidate_refs):
                raise ValueError("response selection is outside request candidates")
            if request.question_kind is QuestionKind.DISAMBIGUATION and len(
                rule.selected_refs
            ) != 1:
                raise ValueError("disambiguation response must select one candidate")
            if rule.grant_effect is not None:
                if request.question_kind is not QuestionKind.AUTHORIZATION:
                    raise ValueError("only authorization response may create grant template")
                if rule.grant_effect.action_scope != request.requested_action_scope:
                    raise ValueError("grant action scope must equal requested scope")
                if not set(rule.grant_effect.resource_refs).issubset(
                    request.candidate_refs
                ):
                    raise ValueError("grant resources are outside request candidates")
                if not set(rule.grant_effect.recipient_ids).issubset(
                    request.requested_recipient_ids
                ):
                    raise ValueError("grant recipients are outside requested recipients")
        return self


class DelegationGrant(OfficeV2Contract):
    grant_id: Identifier
    issuer_id: PrincipalId
    actor_id: PrincipalId
    action_scope: ActionScope
    resource_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    recipient_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    valid_from: LogicalTime
    expires_at: LogicalTime
    source_turn_id: Identifier
    source_request_id: Identifier
    source_rule_id: Identifier
    source_evidence: SourceEvidence

    @field_validator("resource_refs")
    @classmethod
    def resources_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @field_validator("recipient_ids")
    @classmethod
    def recipients_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return canonicalize_identifiers(value, field_name="recipient_ids")

    @model_validator(mode="after")
    def grant_is_scoped_current_and_trusted(self) -> DelegationGrant:
        if not self.resource_refs and not self.recipient_ids:
            raise ValueError("grant requires resource or recipient scope")
        if self.expires_at <= self.valid_from:
            raise ValueError("grant expires_at must be after valid_from")
        if self.source_evidence.source_kind is not EvidenceSourceKind.INTERACTION:
            raise ValueError("grant requires trusted interaction evidence")
        if self.source_evidence.resource is not None:
            raise ValueError("grant evidence must not originate from business content")
        allowed_kinds = set(self.action_scope.resource_kinds)
        if any(ref.kind not in allowed_kinds for ref in self.resource_refs):
            raise ValueError("grant resource is outside action scope")
        return self

    def is_active(self, logical_time: LogicalTime) -> bool:
        return self.valid_from <= logical_time < self.expires_at


class TaskContract(OfficeV2Contract):
    task_id: Identifier
    task_version: WorldVersion
    issuer_principal_id: PrincipalId
    issuer_authentication: IssuerAuthentication
    instruction: str = Field(min_length=1, max_length=4096)
    actor_id: PrincipalId
    preconditions: tuple[TaskFact, ...] = Field(default_factory=tuple)
    goal_graph: TaskGoalGraph
    resource_queries: tuple[ResourceQuery, ...] = Field(default_factory=tuple)
    delegated_actions: tuple[TaskDelegation, ...] = Field(default_factory=tuple)
    allowed_side_effects: tuple[ActionScope, ...] = Field(default_factory=tuple)
    required_response_facts: tuple[TaskFact, ...] = Field(default_factory=tuple)
    user_response_script: InteractionContract = Field(default_factory=InteractionContract)

    @field_validator("preconditions", "required_response_facts")
    @classmethod
    def facts_are_canonical(
        cls, value: tuple[TaskFact, ...], info: ValidationInfo
    ) -> tuple[TaskFact, ...]:
        return tuple(
            _canonicalize_models(
                value, field_name=info.field_name, key=lambda item: item.fact_id
            )
        )

    @field_validator("resource_queries")
    @classmethod
    def queries_are_canonical(
        cls, value: tuple[ResourceQuery, ...]
    ) -> tuple[ResourceQuery, ...]:
        items = tuple(
            _canonicalize_models(
                value, field_name="resource_queries", key=lambda item: item.query_id
            )
        )
        binding_names = tuple(item.binding_name for item in items)
        canonicalize_identifiers(binding_names, field_name="binding_names")
        return items

    @field_validator("delegated_actions")
    @classmethod
    def delegations_are_canonical(
        cls, value: tuple[TaskDelegation, ...]
    ) -> tuple[TaskDelegation, ...]:
        return tuple(
            _canonicalize_models(
                value,
                field_name="delegated_actions",
                key=lambda item: item.delegation_id,
            )
        )

    @field_validator("allowed_side_effects")
    @classmethod
    def side_effects_are_canonical(
        cls, value: tuple[ActionScope, ...]
    ) -> tuple[ActionScope, ...]:
        return tuple(
            _canonicalize_models(
                value, field_name="allowed_side_effects", key=ActionScope.sort_key
            )
        )

    @model_validator(mode="after")
    def references_are_closed(self) -> TaskContract:
        query_ids = {query.query_id for query in self.resource_queries}
        facts = {fact.fact_id: fact for fact in self.preconditions}
        for fact in self.required_response_facts:
            if fact.fact_id in facts:
                raise ValueError("task fact ids must be unique across fact collections")
            facts[fact.fact_id] = fact
        for fact in facts.values():
            if not set(fact.query_ids).issubset(query_ids):
                raise ValueError("task fact references unknown resource query")
        for goal in self.goal_graph.goals:
            referenced_facts = {
                *goal.preconditions,
                *goal.success_assertions,
                *(goal.clarification_gate.fact_ids if goal.clarification_gate else ()),
                *(
                    (goal.branch_condition.fact_id,)
                    if goal.branch_condition is not None
                    else ()
                ),
            }
            if not referenced_facts.issubset(facts):
                raise ValueError("task goal references unknown fact")
        for delegation in self.delegated_actions:
            if delegation.issuer_id != self.issuer_principal_id:
                raise ValueError("delegation issuer must equal task issuer")
            if delegation.actor_id != self.actor_id:
                raise ValueError("delegation actor must equal task actor")
            if not set(delegation.resource_query_ids).issubset(query_ids):
                raise ValueError("delegation references unknown resource query")
        return self


__all__ = [
    "AccessRight",
    "ActionScope",
    "ActionKind",
    "ActorContext",
    "AclEntry",
    "Attendance",
    "AttendanceResponse",
    "AwareDateTime",
    "BindingResolutionStatus",
    "BranchCondition",
    "BranchOperator",
    "CalendarEvent",
    "CalendarEventStatus",
    "CalendarStore",
    "CapabilityId",
    "ClarificationGate",
    "ClarificationRequest",
    "DecisionMode",
    "DomainName",
    "DriveFile",
    "DriveFileVersion",
    "DriveLifecycle",
    "DriveStore",
    "EmailAddress",
    "EvidenceId",
    "EvidenceSourceKind",
    "FieldPathSegment",
    "GroupId",
    "Group",
    "GroupMembership",
    "GrantTemplate",
    "Identifier",
    "IdentityDirectory",
    "InteractionContract",
    "IssuerAuthentication",
    "LogicalClock",
    "LogicalTime",
    "MailDelivery",
    "MailFolder",
    "MailMessage",
    "MailStore",
    "MailThread",
    "MimeType",
    "OfficeV2Contract",
    "OfficeDomainGraph",
    "Organization",
    "OrganizationId",
    "PolicyRuleId",
    "PrincipalId",
    "PrincipalKind",
    "PrincipalStatus",
    "PredicateField",
    "PredicateOperator",
    "Principal",
    "QueryCardinality",
    "QueryTiePolicy",
    "QuestionKind",
    "RelationDirection",
    "ResolvedBinding",
    "ResourceId",
    "ResourceKind",
    "ResourceLink",
    "ResourcePredicate",
    "ResourceQuery",
    "ResourceRelationConstraint",
    "ResourceRelation",
    "ResourceRef",
    "RoleAssignment",
    "RoleId",
    "RoleScope",
    "RoleScopeKind",
    "Sensitivity",
    "Sha256Digest",
    "ShareRecord",
    "ShareStatus",
    "SourceEvidence",
    "StableFailure",
    "TaskContract",
    "TaskDelegation",
    "TaskFact",
    "TaskGoal",
    "TaskGoalGraph",
    "TimezoneName",
    "WorkspaceDirectory",
    "WorkspaceFile",
    "WorkspacePath",
    "WorkspaceStore",
    "WorldVersion",
    "DelegationGrant",
    "ResponseMatch",
    "UserResponseRule",
    "canonicalize_identifiers",
    "canonicalize_resource_refs",
]
