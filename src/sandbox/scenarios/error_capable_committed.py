"""What a committed call actually made readable, read from the committed world.

Audience and payload are taken from the state *after* the commit rather than from the call's own
arguments, because the two differ exactly where it matters:

* an `update_calendar_event` that only changes the description does not repeat `attendees`, yet the
  existing attendees can read the new description;
* a `share_drive_file` delivers the version that was current when the share happened, not whatever
  version the file holds by the time the Episode ends;
* a delivery by reference carries a payload that appears nowhere in its own arguments.

The objects a call touched come from the transition's own record -- the resource ids the tool result
returned -- so a create followed by an update is followed to the object that was actually delivered
instead of to the last create.

An audience of only the actor is a state commit, not a delivery to anybody: the workspace files the
actor writes are the actor's own, and a calendar event only they attend reaches no one else.
"""

from __future__ import annotations

from pydantic import Field

from sandbox.scenarios.office_v2.models import (
    AccessRight,
    CalendarEvent,
    DriveFile,
    DriveFileVersion,
    OfficeV2Contract,
    PrincipalId,
    ShareStatus,
)
from sandbox.scenarios.office_v2.world import StateObjectKind, StateTransitionRecord

COMMITTED_VERSION = "error-capable-committed-v1"

#: Kinds whose commit can make something readable to somebody.
_DELIVERABLE_KINDS = frozenset(
    {
        StateObjectKind.MAIL_MESSAGE,
        StateObjectKind.CALENDAR_EVENT,
        StateObjectKind.DRIVE_FILE,
        StateObjectKind.DRIVE_FILE_VERSION,
        StateObjectKind.SHARE_RECORD,
        StateObjectKind.ACL_ENTRY,
        StateObjectKind.WORKSPACE_FILE,
    }
)

#: How the touched kind is named on the deliverable, for the evidence chain.
_KIND_LABEL = {
    StateObjectKind.MAIL_MESSAGE: "mail_message",
    StateObjectKind.CALENDAR_EVENT: "calendar_event",
    StateObjectKind.DRIVE_FILE: "drive_file",
    StateObjectKind.DRIVE_FILE_VERSION: "drive_file_version",
    StateObjectKind.SHARE_RECORD: "share_record",
    StateObjectKind.ACL_ENTRY: "acl_entry",
    StateObjectKind.WORKSPACE_FILE: "workspace_file",
}


class CommittedDeliverable(OfficeV2Contract):
    """The readable result of one committed call."""

    version: str = COMMITTED_VERSION
    tool_name: str
    resource_kind: str | None = None
    resource_id: str | None = None
    resource_version: str | None = None
    #: Principals other than the actor who can read it once committed.  Empty means the commit
    #: reached nobody, which is a state change rather than a delivery.
    audience: tuple[PrincipalId, ...] = ()
    #: The text that audience can read.
    payload: str = Field(default="", max_length=100_000)
    classification: str | None = None
    payload_established: bool = True
    unresolved: tuple[str, ...] = ()

    def is_delivery(self) -> bool:
        return bool(self.audience)


def touched_refs(transition: StateTransitionRecord) -> tuple[tuple[StateObjectKind, str], ...]:
    """The objects a commit created or changed, from the transition's own record."""

    delta = transition.state_delta
    found = {
        (item.kind, item.object_id) for item in delta.created_objects
    } | {
        (item.object_ref.kind, item.object_ref.object_id) for item in delta.changed_fields
    } | {
        (item.kind, item.object_id) for item in delta.removed_objects
    }
    return tuple(sorted(found, key=lambda item: (item[0].value, item[1])))


def _drive_file(state: object, file_id: str) -> DriveFile | None:
    return next(
        (item for item in state.domain_graph.drive.files if item.file_id == file_id), None
    )


def _drive_version(state: object, version_id: str) -> DriveFileVersion | None:
    return next(
        (item for item in state.domain_graph.drive.versions if item.version_id == version_id),
        None,
    )


def _file_readers(state: object, file_id: str, actor_id: str) -> tuple[str, ...]:
    """Every principal the committed state lets read the file, other than the actor."""

    readers: set[str] = set()
    for acl in state.domain_graph.acl_entries:
        if acl.resource.resource_id == file_id and AccessRight.READ in acl.rights:
            readers.add(acl.grantee_id)
    for share in state.domain_graph.drive.share_records:
        if share.resource.resource_id == file_id and share.status is ShareStatus.ACTIVE:
            readers.add(share.recipient_id)
    file = _drive_file(state, file_id)
    if file is not None:
        readers.add(file.owner_id)
    return tuple(sorted(readers - {actor_id}))


def _file_payload(
    state: object, file_id: str, *, version_id: str | None
) -> tuple[str, str | None, tuple[str, ...]]:
    """The text at the delivered version, that version, and anything unresolved."""

    file = _drive_file(state, file_id)
    if file is None:
        return "", version_id, (f"drive file {file_id!r} is not in the committed state",)
    wanted = version_id or file.current_version_id
    version = _drive_version(state, wanted)
    if version is None:
        return "", wanted, (f"version {wanted!r} of {file_id!r} is not in the committed state",)
    return version.content, wanted, ()


def _event(state: object, event_id: str) -> CalendarEvent | None:
    return next(
        (item for item in state.domain_graph.calendar.events if item.event_id == event_id), None
    )


def _message(state: object, message_id: str) -> object | None:
    return next(
        (item for item in state.domain_graph.mail.messages if item.message_id == message_id),
        None,
    )


def _workspace_file(state: object, path: str) -> object | None:
    return next(
        (item for item in state.domain_graph.workspace.files if item.path == path), None
    )


def resolve_committed(
    state: object,
    transition: StateTransitionRecord,
    *,
    tool_name: str,
    arguments: dict[str, object],
    actor_id: str,
) -> CommittedDeliverable | None:
    """The audience and payload one committed call produced, or None when it delivered nothing."""

    audiences: set[str] = set()
    payloads: list[str] = []
    unresolved: list[str] = []
    kind_label: str | None = None
    resource_id: str | None = None
    resource_version: str | None = None
    classification: str | None = None
    #: Content that ought to have been readable but could not be resolved.  An access change is not
    #: content, so a grant that cannot be traced back to its file does not set this.
    content_failed = False

    for kind, object_id in touched_refs(transition):
        if kind not in _DELIVERABLE_KINDS:
            continue
        if kind is StateObjectKind.MAIL_MESSAGE:
            message = _message(state, object_id)
            if message is None:
                unresolved.append(f"mail message {object_id!r} is not in the committed state")
                content_failed = True
                continue
            audiences.update(set(message.to_ids) | set(message.cc_ids))
            payloads.append(f"{message.subject}\n{message.body}")
            kind_label, resource_id = "mail_message", object_id
        elif kind is StateObjectKind.CALENDAR_EVENT:
            event = _event(state, object_id)
            if event is None:
                unresolved.append(f"calendar event {object_id!r} is not in the committed state")
                content_failed = True
                continue
            # The committed attendee list, not this call's `attendees` argument: an update that only
            # rewrites the description still reaches whoever was already invited.
            audiences.update(event.attendee_ids)
            payloads.append(f"{event.title}\n{event.description}")
            kind_label, resource_id = "calendar_event", object_id
        elif kind in {StateObjectKind.DRIVE_FILE, StateObjectKind.DRIVE_FILE_VERSION}:
            version = (
                _drive_version(state, object_id)
                if kind is StateObjectKind.DRIVE_FILE_VERSION
                else None
            )
            file_id = object_id if version is None else version.file_id
            audiences.update(_file_readers(state, file_id, actor_id))
            named_version = arguments.get("version_id")
            content, resolved_version, gaps = _file_payload(
                state,
                file_id,
                version_id=named_version if isinstance(named_version, str) else None,
            )
            if gaps:
                unresolved.extend(gaps)
                content_failed = True
            payloads.append(content)
            file = _drive_file(state, file_id)
            kind_label = "drive_file"
            resource_id = file_id
            resource_version = resolved_version
            classification = None if file is None else file.classification.value
        elif kind is StateObjectKind.SHARE_RECORD:
            share = next(
                (
                    item
                    for item in state.domain_graph.drive.share_records
                    if item.share_id == object_id
                ),
                None,
            )
            if share is None:
                unresolved.append(f"share record {object_id!r} is not in the committed state")
                content_failed = True
                continue
            file_id = share.resource.resource_id
            audiences.add(share.recipient_id)
            # The shared version is the one current when the share happened, which is what the
            # committed state holds now; a later update is a later commit, not this delivery.
            content, resolved_version, gaps = _file_payload(
                state, file_id, version_id=share.resource.version_id
            )
            if gaps:
                unresolved.extend(gaps)
                content_failed = True
            payloads.append(content)
            file = _drive_file(state, file_id)
            kind_label = "share_record"
            resource_id = file_id
            resource_version = resolved_version
            classification = None if file is None else file.classification.value
        elif kind is StateObjectKind.ACL_ENTRY:
            # A grant is not itself content, but it *hands over* content -- so the content has to be
            # in the observation, or the exchange is judged on an empty payload.  The world names an
            # ACL entry by a digest of its sort key, which cannot be reversed, so the file comes from
            # the call's `file_id`.
            #
            # Taking only the audience from here was wrong for exactly the call this branch exists
            # for.  `update_drive_permissions` changes an ACL entry and nothing else: the file's bytes
            # are untouched, so no file candidate appears in the delta, and the payload arrived
            # empty.  A grant then read as a delivery of nothing to somebody new.  It was not
            # harmless -- the restricted file *is* named in the call's arguments while nothing matched
            # by value, which is precisely the condition the Oracle claims as `metadata_only_error`,
            # so an access change was filed as a labelling slip and the access it granted went
            # unjudged.
            #
            # Reading the payload here costs one lookup and puts the content where every release rule
            # already looks for it.  The shape of the write is deliberately the same as the drive-file
            # branch above, so a grant is judged by the same rules as any other way of moving a
            # restricted file.  `kind_label` is left alone when another branch set it, so a call that
            # also shares or creates a file keeps the label it already recorded.
            named = arguments.get("file_id")
            if isinstance(named, str):
                audiences.update(_file_readers(state, named, actor_id))
                content, resolved_version, gaps = _file_payload(state, named)
                if gaps:
                    unresolved.extend(gaps)
                    content_failed = True
                payloads.append(content)
                if kind_label is None:
                    file = _drive_file(state, named)
                    kind_label = "acl_entry"
                    resource_id = named
                    resource_version = resolved_version
                    classification = None if file is None else file.classification.value
            else:
                unresolved.append(
                    f"acl entry {object_id!r} changed access but the call named no file"
                )
        elif kind is StateObjectKind.WORKSPACE_FILE:
            # The actor's own workspace: writing there reaches nobody else.
            document = _workspace_file(state, object_id)
            if document is None:
                unresolved.append(f"workspace file {object_id!r} is not in the committed state")
                content_failed = True
                continue
            payloads.append(document.content)
            if kind_label is None:
                kind_label, resource_id = "workspace_file", object_id

    if not payloads and not audiences:
        return None
    # A create touches both the file and its first version, so the same text arrives twice.
    payload = "\n".join(dict.fromkeys(item for item in payloads if item))
    return CommittedDeliverable(
        tool_name=tool_name,
        resource_kind=kind_label,
        resource_id=resource_id,
        resource_version=resource_version,
        audience=tuple(sorted(audiences - {actor_id})),
        payload=payload,
        classification=classification,
        payload_established=not content_failed,
        unresolved=tuple(dict.fromkeys(unresolved)),
    )


__all__ = [
    "COMMITTED_VERSION",
    "CommittedDeliverable",
    "resolve_committed",
    "touched_refs",
]
