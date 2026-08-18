"""Permission-scoped Office V2 observations with deterministic pagination."""

from __future__ import annotations

import base64
import json
from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.canonical_world import OfficeWorldState
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ActorContext,
    CalendarEvent,
    DriveFile,
    DriveFileVersion,
    DriveLifecycle,
    Identifier,
    MailMessage,
    OfficeV2Contract,
    PrincipalId,
    ResourceKind,
    ResourceRef,
    Sensitivity,
    Sha256Digest,
    ShareStatus,
    WorkspaceFile,
    canonicalize_resource_refs,
)

OBSERVATION_SORT_VERSION = "observation-sort-v1"
_CAPABILITY_BY_KIND = {
    ResourceKind.MAIL_THREAD: "mail.read",
    ResourceKind.MAIL_MESSAGE: "mail.read",
    ResourceKind.DRIVE_FILE: "drive.read",
    ResourceKind.DRIVE_FILE_VERSION: "drive.read",
    ResourceKind.CALENDAR_EVENT: "calendar.read",
    ResourceKind.WORKSPACE_FILE: "workspace.read",
}


class ObservationAccessLevel(StrEnum):
    DISCOVER = "discover"
    READ = "read"


class DriveVersionView(StrEnum):
    CURRENT = "current"
    ALL = "all"


class ObservationFailureCode(StrEnum):
    INVALID_PAGE_TOKEN = "invalid_page_token"
    STALE_PAGE_TOKEN = "stale_page_token"
    ACTOR_MISMATCH = "page_token_actor_mismatch"
    QUERY_MISMATCH = "page_token_query_mismatch"
    SORT_VERSION_MISMATCH = "page_token_sort_version_mismatch"
    PAGE_SIZE_EXCEEDED = "page_size_exceeded"


class ObservationError(ValueError):
    def __init__(self, code: ObservationFailureCode):
        self.code = code
        super().__init__(code.value)


class ObservationPolicy(OfficeV2Contract):
    default_page_size: int = Field(default=10, ge=1, le=100)
    maximum_page_size: int = Field(default=25, ge=1, le=100)
    sort_version: Identifier = OBSERVATION_SORT_VERSION

    @model_validator(mode="after")
    def default_does_not_exceed_maximum(self) -> Self:
        if self.default_page_size > self.maximum_page_size:
            raise ValueError("default_page_size must not exceed maximum_page_size")
        return self


class ObservationQuery(OfficeV2Contract):
    resource_kinds: tuple[ResourceKind, ...] = Field(
        default_factory=lambda: tuple(ResourceKind), min_length=1
    )
    text: str | None = Field(default=None, min_length=1, max_length=512)
    drive_version_view: DriveVersionView = DriveVersionView.CURRENT
    page_size: int | None = Field(default=None, ge=1, le=100)
    page_token: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("resource_kinds")
    @classmethod
    def kinds_are_canonical(
        cls, value: tuple[ResourceKind, ...]
    ) -> tuple[ResourceKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("resource_kinds must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = " ".join(value.split()).casefold()
            if not normalized:
                raise ValueError("text must not be blank")
            return normalized
        return value

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"page_token"},
            exclude_none=False,
        )


class ObservedResource(OfficeV2Contract):
    resource: ResourceRef
    access_level: ObservationAccessLevel
    display_name: str = Field(min_length=1, max_length=512)
    content: str | None = None
    owner_id: PrincipalId | None = None
    participant_ids: tuple[PrincipalId, ...] = Field(default_factory=tuple)
    sensitivity: Sensitivity | None = None
    lifecycle_state: DriveLifecycle | None = None
    project_key: Identifier | None = None
    start_time: int | None = None
    end_time: int | None = None
    related_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)

    @field_validator("participant_ids")
    @classmethod
    def participants_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("participant_ids must not contain duplicates")
        return tuple(sorted(value))

    @field_validator("related_refs")
    @classmethod
    def refs_are_canonical(
        cls, value: tuple[ResourceRef, ...]
    ) -> tuple[ResourceRef, ...]:
        return canonicalize_resource_refs(value)

    @model_validator(mode="after")
    def discover_only_has_no_read_fields(self) -> Self:
        if self.access_level is ObservationAccessLevel.DISCOVER and (
            self.content is not None
            or self.owner_id is not None
            or self.participant_ids
            or self.sensitivity is not None
            or self.lifecycle_state is not None
            or self.project_key is not None
            or self.start_time is not None
            or self.end_time is not None
            or self.related_refs
        ):
            raise ValueError("discover-only observation contains read-protected fields")
        return self

    def sort_key(self) -> tuple[str, str, str]:
        return self.resource.sort_key()


class ObservationPage(OfficeV2Contract):
    items: tuple[ObservedResource, ...]
    next_page_token: str | None = None
    has_more: bool
    state_digest: Sha256Digest
    actor_digest: Sha256Digest
    query_digest: Sha256Digest
    sort_version: Identifier


class _PageTokenPayload(OfficeV2Contract):
    state_digest: Sha256Digest
    actor_digest: Sha256Digest
    query_digest: Sha256Digest
    sort_version: Identifier
    offset: int = Field(ge=0)


class _PageTokenEnvelope(OfficeV2Contract):
    payload: _PageTokenPayload
    payload_digest: Sha256Digest

    @model_validator(mode="after")
    def digest_matches_payload(self) -> Self:
        if self.payload_digest != sha256_digest(self.payload):
            raise ValueError("page token payload digest mismatch")
        return self


def observe(
    state: OfficeWorldState,
    actor: ActorContext,
    query: ObservationQuery,
    *,
    policy: ObservationPolicy | None = None,
) -> ObservationPage:
    """Project a stable page of resources visible to one actor."""

    policy = policy or ObservationPolicy()
    page_size = query.page_size or policy.default_page_size
    if page_size > policy.maximum_page_size:
        raise ObservationError(ObservationFailureCode.PAGE_SIZE_EXCEEDED)
    state_digest = state.canonical_digest()
    actor_digest = actor.canonical_digest()
    query_digest = sha256_digest(query.digest_payload())
    offset = 0
    if query.page_token is not None:
        token = _decode_page_token(query.page_token)
        _validate_page_token(
            token,
            state_digest=state_digest,
            actor_digest=actor_digest,
            query_digest=query_digest,
            sort_version=policy.sort_version,
        )
        offset = token.offset

    visible = tuple(
        item
        for item in sorted(_visible_resources(state, actor, query), key=ObservedResource.sort_key)
        if _matches_text(item, query.text)
    )
    items = visible[offset : offset + page_size]
    next_offset = offset + len(items)
    has_more = next_offset < len(visible)
    next_token = None
    if has_more:
        next_token = _encode_page_token(
            _PageTokenPayload(
                state_digest=state_digest,
                actor_digest=actor_digest,
                query_digest=query_digest,
                sort_version=policy.sort_version,
                offset=next_offset,
            )
        )
    return ObservationPage(
        items=items,
        next_page_token=next_token,
        has_more=has_more,
        state_digest=state_digest,
        actor_digest=actor_digest,
        query_digest=query_digest,
        sort_version=policy.sort_version,
    )


def _visible_resources(
    state: OfficeWorldState, actor: ActorContext, query: ObservationQuery
) -> tuple[ObservedResource, ...]:
    kinds = set(query.resource_kinds)
    visible: list[ObservedResource] = []
    if ResourceKind.MAIL_THREAD in kinds:
        visible.extend(_observe_mail_threads(state, actor))
    if ResourceKind.MAIL_MESSAGE in kinds:
        visible.extend(_observe_mail_messages(state, actor))
    if ResourceKind.DRIVE_FILE in kinds:
        visible.extend(_observe_drive_files(state, actor))
    if ResourceKind.DRIVE_FILE_VERSION in kinds:
        visible.extend(_observe_drive_versions(state, actor, query.drive_version_view))
    if ResourceKind.CALENDAR_EVENT in kinds:
        visible.extend(_observe_calendar(state, actor))
    if ResourceKind.WORKSPACE_FILE in kinds:
        visible.extend(_observe_workspace(state, actor))
    return tuple(visible)


def _has_capability(actor: ActorContext, kind: ResourceKind) -> bool:
    return _CAPABILITY_BY_KIND[kind] in actor.session_capabilities


def _observe_mail_threads(
    state: OfficeWorldState, actor: ActorContext
) -> tuple[ObservedResource, ...]:
    if not _has_capability(actor, ResourceKind.MAIL_THREAD):
        return ()
    messages = {item.message_id: item for item in state.domain_graph.mail.messages}
    result = []
    for thread in state.domain_graph.mail.threads:
        thread_messages = tuple(messages[item] for item in thread.message_ids)
        if not any(_mail_participant(message, actor) for message in thread_messages):
            continue
        participants = {
            principal
            for message in thread_messages
            for principal in (message.sender_id, *message.to_ids, *message.cc_ids)
        }
        result.append(
            ObservedResource(
                resource=ResourceRef(
                    kind=ResourceKind.MAIL_THREAD, resource_id=thread.thread_id
                ),
                access_level=ObservationAccessLevel.READ,
                display_name=thread.subject,
                participant_ids=tuple(participants),
                project_key=_resource_project_key(state, thread.thread_id),
            )
        )
    return tuple(result)


def _observe_mail_messages(
    state: OfficeWorldState, actor: ActorContext
) -> tuple[ObservedResource, ...]:
    if not _has_capability(actor, ResourceKind.MAIL_MESSAGE):
        return ()
    return tuple(
        ObservedResource(
            resource=ResourceRef(
                kind=ResourceKind.MAIL_MESSAGE, resource_id=message.message_id
            ),
            access_level=ObservationAccessLevel.READ,
            display_name=message.subject,
            content=message.body,
            owner_id=message.sender_id,
            participant_ids=(message.sender_id, *message.to_ids, *message.cc_ids),
            project_key=_resource_project_key(state, message.message_id),
            related_refs=message.attachment_refs,
        )
        for message in state.domain_graph.mail.messages
        if _mail_participant(message, actor)
    )


def _mail_participant(message: MailMessage, actor: ActorContext) -> bool:
    return actor.mailbox_owner_id in {
        message.sender_id,
        *message.to_ids,
        *message.cc_ids,
    }


def _observe_drive_files(
    state: OfficeWorldState, actor: ActorContext
) -> tuple[ObservedResource, ...]:
    if not _has_capability(actor, ResourceKind.DRIVE_FILE):
        return ()
    versions = {item.version_id: item for item in state.domain_graph.drive.versions}
    result = []
    for file in state.domain_graph.drive.files:
        rights = _drive_rights(state, actor, file)
        access = _access_level(rights)
        if access is None:
            continue
        readable = access is ObservationAccessLevel.READ
        current = versions[file.current_version_id]
        result.append(
            ObservedResource(
                resource=ResourceRef(
                    kind=ResourceKind.DRIVE_FILE,
                    resource_id=file.file_id,
                    version_id=file.current_version_id,
                ),
                access_level=access,
                display_name=file.name,
                content=current.content if readable else None,
                owner_id=file.owner_id if readable else None,
                sensitivity=file.classification if readable else None,
                lifecycle_state=file.lifecycle_state if readable else None,
                project_key=(
                    _resource_project_key(state, file.file_id) if readable else None
                ),
                related_refs=current.source_refs if readable else (),
            )
        )
    return tuple(result)


def _observe_drive_versions(
    state: OfficeWorldState, actor: ActorContext, version_view: DriveVersionView
) -> tuple[ObservedResource, ...]:
    if not _has_capability(actor, ResourceKind.DRIVE_FILE_VERSION):
        return ()
    files = {item.file_id: item for item in state.domain_graph.drive.files}
    result = []
    for version in state.domain_graph.drive.versions:
        file = files[version.file_id]
        if (
            version_view is DriveVersionView.CURRENT
            and version.version_id != file.current_version_id
        ):
            continue
        rights = _drive_rights(state, actor, file, version=version)
        access = _access_level(rights)
        if access is None:
            continue
        readable = access is ObservationAccessLevel.READ
        result.append(
            ObservedResource(
                resource=ResourceRef(
                    kind=ResourceKind.DRIVE_FILE_VERSION,
                    resource_id=version.version_id,
                ),
                access_level=access,
                display_name=f"{file.name} [{version.version_id}]",
                content=version.content if readable else None,
                owner_id=version.created_by if readable else None,
                sensitivity=file.classification if readable else None,
                lifecycle_state=file.lifecycle_state if readable else None,
                project_key=(
                    _resource_project_key(state, version.version_id) if readable else None
                ),
                related_refs=version.source_refs if readable else (),
            )
        )
    return tuple(result)


def _drive_rights(
    state: OfficeWorldState,
    actor: ActorContext,
    file: DriveFile,
    *,
    version: DriveFileVersion | None = None,
) -> set[AccessRight]:
    if file.owner_id == actor.actor_id:
        return set(AccessRight)
    if file.classification is Sensitivity.PUBLIC:
        rights = {AccessRight.DISCOVER, AccessRight.READ}
    else:
        rights = set()
    subjects = {actor.actor_id, *actor.active_group_ids}
    for entry in state.domain_graph.acl_entries:
        applies_to_file = (
            entry.resource.kind is ResourceKind.DRIVE_FILE
            and entry.resource.resource_id == file.file_id
            and (
                entry.resource.version_id is None
                or entry.resource.version_id
                == (version.version_id if version else file.current_version_id)
            )
        )
        applies_to_version = (
            version is not None
            and entry.resource.kind is ResourceKind.DRIVE_FILE_VERSION
            and entry.resource.resource_id == version.version_id
        )
        if entry.grantee_id in subjects and (applies_to_file or applies_to_version):
            rights.update(entry.rights)
    for share in state.domain_graph.drive.share_records:
        if (
            share.status is ShareStatus.ACTIVE
            and share.recipient_id == actor.actor_id
            and share.resource.resource_id == file.file_id
        ):
            rights.update(share.rights)
    return rights


def _access_level(rights: set[AccessRight]) -> ObservationAccessLevel | None:
    if AccessRight.READ in rights:
        return ObservationAccessLevel.READ
    if AccessRight.DISCOVER in rights:
        return ObservationAccessLevel.DISCOVER
    return None


def resource_rights(
    state: OfficeWorldState, actor: ActorContext, resource: ResourceRef
) -> frozenset[AccessRight]:
    """Return effective rights for a resource already considered by observation."""

    if not _has_capability(actor, resource.kind):
        return frozenset()
    graph = state.domain_graph
    if resource.kind in {ResourceKind.DRIVE_FILE, ResourceKind.DRIVE_FILE_VERSION}:
        if resource.kind is ResourceKind.DRIVE_FILE:
            file = next(
                (item for item in graph.drive.files if item.file_id == resource.resource_id),
                None,
            )
            version = None
        else:
            version = next(
                (
                    item
                    for item in graph.drive.versions
                    if item.version_id == resource.resource_id
                ),
                None,
            )
            file = (
                next(
                    (item for item in graph.drive.files if item.file_id == version.file_id),
                    None,
                )
                if version is not None
                else None
            )
        if file is None:
            return frozenset()
        return frozenset(_drive_rights(state, actor, file, version=version))
    if resource.kind is ResourceKind.CALENDAR_EVENT:
        event = next(
            (item for item in graph.calendar.events if item.event_id == resource.resource_id),
            None,
        )
        if event is None or not _calendar_participant(event, actor):
            return frozenset()
        if event.organizer_id == actor.actor_id:
            return frozenset(
                {AccessRight.DISCOVER, AccessRight.READ, AccessRight.WRITE, AccessRight.DELETE}
            )
        return frozenset({AccessRight.DISCOVER, AccessRight.READ})
    if resource.kind is ResourceKind.WORKSPACE_FILE:
        file = next(
            (item for item in graph.workspace.files if item.path == resource.resource_id),
            None,
        )
        if file is None or not _workspace_owner(file, actor):
            return frozenset()
        return frozenset(
            {AccessRight.DISCOVER, AccessRight.READ, AccessRight.WRITE, AccessRight.DELETE}
        )
    if resource.kind is ResourceKind.MAIL_MESSAGE:
        message = next(
            (item for item in graph.mail.messages if item.message_id == resource.resource_id),
            None,
        )
        if message is not None and _mail_participant(message, actor):
            return frozenset({AccessRight.DISCOVER, AccessRight.READ})
        return frozenset()
    if resource.kind is ResourceKind.MAIL_THREAD:
        visible_ids = {
            item.resource.resource_id for item in _observe_mail_threads(state, actor)
        }
        if resource.resource_id in visible_ids:
            return frozenset({AccessRight.DISCOVER, AccessRight.READ})
    return frozenset()


def _observe_calendar(
    state: OfficeWorldState, actor: ActorContext
) -> tuple[ObservedResource, ...]:
    if not _has_capability(actor, ResourceKind.CALENDAR_EVENT):
        return ()
    return tuple(
        ObservedResource(
            resource=ResourceRef(
                kind=ResourceKind.CALENDAR_EVENT, resource_id=event.event_id
            ),
            access_level=ObservationAccessLevel.READ,
            display_name=event.title,
            content=event.description,
            owner_id=event.organizer_id,
            participant_ids=(event.organizer_id, *event.attendee_ids),
            project_key=_resource_project_key(state, event.event_id),
            start_time=int(event.start_at.timestamp()),
            end_time=int(event.end_at.timestamp()),
            related_refs=event.related_refs,
        )
        for event in state.domain_graph.calendar.events
        if _calendar_participant(event, actor)
    )


def _calendar_participant(event: CalendarEvent, actor: ActorContext) -> bool:
    return actor.actor_id == event.organizer_id or actor.actor_id in event.attendee_ids


def _observe_workspace(
    state: OfficeWorldState, actor: ActorContext
) -> tuple[ObservedResource, ...]:
    if not _has_capability(actor, ResourceKind.WORKSPACE_FILE):
        return ()
    return tuple(
        ObservedResource(
            resource=ResourceRef(
                kind=ResourceKind.WORKSPACE_FILE, resource_id=file.path
            ),
            access_level=ObservationAccessLevel.READ,
            display_name=file.path.rsplit("/", 1)[-1],
            content=file.content,
            owner_id=file.owner_id,
            project_key=_resource_project_key(state, file.path),
            related_refs=file.source_refs,
        )
        for file in state.domain_graph.workspace.files
        if _workspace_owner(file, actor)
    )


def _workspace_owner(file: WorkspaceFile, actor: ActorContext) -> bool:
    return file.owner_id == actor.actor_id


def _resource_project_key(state: OfficeWorldState, resource_id: str) -> str | None:
    """Resolve project identity from exact resource namespace and directory facts."""

    segments = {
        segment.casefold()
        for segment in resource_id.replace("/", ".").split(".")
        if segment
    }
    project_keys = {
        principal.principal_id.removeprefix("group.").casefold()
        for principal in state.domain_graph.directory.principals
        if principal.principal_id.startswith("group.")
        and principal.display_name.casefold().endswith(" project team")
    }
    matched = sorted(segments & project_keys)
    return matched[0] if len(matched) == 1 else None


def _matches_text(item: ObservedResource, text: str | None) -> bool:
    if text is None:
        return True
    haystack = item.display_name
    if item.content is not None:
        haystack = f"{haystack}\n{item.content}"
    return text in haystack.casefold()


def _encode_page_token(payload: _PageTokenPayload) -> str:
    envelope = _PageTokenEnvelope(payload=payload, payload_digest=sha256_digest(payload))
    encoded = base64.urlsafe_b64encode(canonical_json_bytes(envelope)).decode("ascii")
    return encoded.rstrip("=")


def _decode_page_token(value: str) -> _PageTokenPayload:
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        envelope = _PageTokenEnvelope.model_validate(json.loads(raw))
    except Exception as exc:
        raise ObservationError(ObservationFailureCode.INVALID_PAGE_TOKEN) from exc
    return envelope.payload


def _validate_page_token(
    token: _PageTokenPayload,
    *,
    state_digest: str,
    actor_digest: str,
    query_digest: str,
    sort_version: str,
) -> None:
    if token.state_digest != state_digest:
        raise ObservationError(ObservationFailureCode.STALE_PAGE_TOKEN)
    if token.actor_digest != actor_digest:
        raise ObservationError(ObservationFailureCode.ACTOR_MISMATCH)
    if token.query_digest != query_digest:
        raise ObservationError(ObservationFailureCode.QUERY_MISMATCH)
    if token.sort_version != sort_version:
        raise ObservationError(ObservationFailureCode.SORT_VERSION_MISMATCH)


__all__ = [
    "DriveVersionView",
    "OBSERVATION_SORT_VERSION",
    "ObservationAccessLevel",
    "ObservationError",
    "ObservationFailureCode",
    "ObservationPage",
    "ObservationPolicy",
    "ObservationQuery",
    "ObservedResource",
    "observe",
    "resource_rights",
]
