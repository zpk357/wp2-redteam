"""Drive-domain Office V2 tool argument models and handlers."""

from __future__ import annotations

from typing import Self

from pydantic import Field, JsonValue, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    AclEntry,
    ActionKind,
    DriveFile,
    DriveFileVersion,
    DriveLifecycle,
    DriveStore,
    EvidenceSourceKind,
    OfficeV2Contract,
    ResourceKind,
    ResourceRef,
    Sensitivity,
    ShareRecord,
    ShareStatus,
    SourceEvidence,
)
from sandbox.scenarios.office_v2.observation import resource_rights
from sandbox.scenarios.office_v2.tools.contracts import ToolFailureCode
from sandbox.scenarios.office_v2.tools.runtime import (
    OfficeV2ToolRuntime,
    PreparedAction,
    ToolDefinition,
    ToolRejected,
)
from sandbox.scenarios.office_v2.world import EpisodeTransaction


class SearchDriveFilesArguments(OfficeV2Contract):
    text: str | None = Field(default=None, min_length=1, max_length=512)
    classification: Sensitivity | None = None
    lifecycle: DriveLifecycle | None = None
    page_size: int = Field(default=10, ge=1, le=25)
    page_token: str | None = Field(default=None, min_length=1, max_length=4096)


class ReadDriveFileArguments(OfficeV2Contract):
    file_id: str
    version_id: str | None = None


class CreateDriveFileArguments(OfficeV2Contract):
    name: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=500_000)
    mime_type: str = Field(default="text/plain")
    classification: Sensitivity = Sensitivity.INTERNAL
    source_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)


class ShareDriveFileArguments(OfficeV2Contract):
    file_id: str
    version_id: str | None = None
    recipient: str


class UpdateDrivePermissionsArguments(OfficeV2Contract):
    file_id: str
    version_id: str | None = None
    grantee: str
    add_rights: tuple[AccessRight, ...] = Field(default_factory=tuple)
    remove_rights: tuple[AccessRight, ...] = Field(default_factory=tuple)
    expected_acl_digest: str

    @model_validator(mode="after")
    def patch_is_valid(self) -> Self:
        if not self.add_rights and not self.remove_rights:
            raise ValueError("permission patch must not be empty")
        if len(self.add_rights) != len(set(self.add_rights)):
            raise ValueError("add_rights must not contain duplicates")
        if len(self.remove_rights) != len(set(self.remove_rights)):
            raise ValueError("remove_rights must not contain duplicates")
        if set(self.add_rights).intersection(self.remove_rights):
            raise ValueError("add_rights and remove_rights must not overlap")
        return self


class DeleteDriveFileArguments(OfficeV2Contract):
    file_id: str
    expected_current_version_id: str


def _file_ref(file_id: str, version_id: str | None = None) -> ResourceRef:
    return ResourceRef(
        kind=ResourceKind.DRIVE_FILE,
        resource_id=file_id,
        version_id=version_id,
    )


def acl_digest(
    runtime: OfficeV2ToolRuntime, resource: ResourceRef, grantee_id: str | None = None
) -> str:
    entries = tuple(
        item
        for item in runtime.state.domain_graph.acl_entries
        if item.resource == resource and (grantee_id is None or item.grantee_id == grantee_id)
    )
    return sha256_digest([item.model_dump(mode="json", exclude_none=False) for item in entries])


def _drive_output(
    runtime: OfficeV2ToolRuntime,
    file: DriveFile,
    version: DriveFileVersion | None = None,
) -> dict[str, JsonValue]:
    resource = _file_ref(file.file_id, None if version is None else version.version_id)
    rights_resource = (
        resource
        if version is None
        else ResourceRef(
            kind=ResourceKind.DRIVE_FILE_VERSION,
            resource_id=version.version_id,
        )
    )
    rights = sorted(
        item.value
        for item in resource_rights(runtime.state, runtime.actor, rights_resource)
    )
    output: dict[str, JsonValue] = {
        "resource": resource.model_dump(mode="json"),
        "file_id": file.file_id,
        "name": file.name,
        "mime_type": file.mime_type,
        "owner_id": file.owner_id,
        "classification": file.classification.value,
        "lifecycle": file.lifecycle_state.value,
        "current_version_id": file.current_version_id,
        "rights": rights,
    }
    if AccessRight.MANAGE_PERMISSIONS.value in rights:
        output["acl_digest"] = acl_digest(runtime, resource)
    if version is not None:
        output.update(
            {
                "version_id": version.version_id,
                "content": version.content,
                "created_by": version.created_by,
                "created_at": version.created_at.isoformat(),
                "source_refs": [item.model_dump(mode="json") for item in version.source_refs],
            }
        )
    return output


def _prepare_search(*_: object) -> PreparedAction:
    return PreparedAction()


def _search(
    runtime: OfficeV2ToolRuntime,
    arguments: SearchDriveFilesArguments,
    _: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    visible_ids = {
        item.resource.resource_id
        for item in runtime.visible_resources((ResourceKind.DRIVE_FILE,), text=arguments.text)
    }
    files = tuple(
        file
        for file in runtime.state.domain_graph.drive.files
        if file.file_id in visible_ids
        and (arguments.classification is None or file.classification is arguments.classification)
        and (arguments.lifecycle is None or file.lifecycle_state is arguments.lifecycle)
    )
    items = tuple(_drive_output(runtime, file) for file in files)
    return runtime.paginate(
        items,
        query_payload={
            "text": arguments.text,
            "classification": (
                None if arguments.classification is None else arguments.classification.value
            ),
            "lifecycle": (None if arguments.lifecycle is None else arguments.lifecycle.value),
        },
        page_size=arguments.page_size,
        page_token=arguments.page_token,
    )


def _prepare_read(
    runtime: OfficeV2ToolRuntime, arguments: ReadDriveFileArguments
) -> PreparedAction:
    resource = _file_ref(arguments.file_id, arguments.version_id)
    runtime.visible_resource(resource)
    return PreparedAction(resources=(resource,))


def _read(
    runtime: OfficeV2ToolRuntime,
    arguments: ReadDriveFileArguments,
    _: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    drive = runtime.state.domain_graph.drive
    file = next(item for item in drive.files if item.file_id == arguments.file_id)
    version_id = arguments.version_id or file.current_version_id
    version = next(item for item in drive.versions if item.version_id == version_id)
    return _drive_output(runtime, file, version)


def _prepare_create(
    runtime: OfficeV2ToolRuntime, arguments: CreateDriveFileArguments
) -> PreparedAction:
    runtime.require_visible_refs(arguments.source_refs)
    return PreparedAction()


def _source_evidence(
    runtime: OfficeV2ToolRuntime, source_id: str, payload: object
) -> SourceEvidence:
    return SourceEvidence(
        evidence_id=f"evidence.{source_id}",
        source_kind=EvidenceSourceKind.TASK,
        source_id=runtime.task.task_id,
        observed_at=runtime.actor.logical_time,
        content_digest=sha256_digest(payload),
    )


def _create(
    runtime: OfficeV2ToolRuntime,
    arguments: CreateDriveFileArguments,
    transaction: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    assert transaction is not None
    file_id = transaction.allocate_id("drive.file")
    version_id = transaction.allocate_id("drive.version")
    timestamp = runtime.logical_datetime
    file = DriveFile(
        file_id=file_id,
        name=arguments.name,
        mime_type=arguments.mime_type,
        owner_id=runtime.actor.actor_id,
        classification=arguments.classification,
        current_version_id=version_id,
    )
    version = DriveFileVersion(
        version_id=version_id,
        file_id=file_id,
        content=arguments.content,
        created_by=runtime.actor.actor_id,
        created_at=timestamp,
        source_refs=arguments.source_refs,
    )
    owner_acl = AclEntry(
        resource=_file_ref(file_id),
        grantee_id=runtime.actor.actor_id,
        rights=tuple(AccessRight),
        granted_by=runtime.actor.actor_id,
        granted_at=timestamp,
        grant_source=_source_evidence(runtime, f"acl.{file_id}", arguments.name),
    )
    graph = transaction.staged_state.domain_graph
    drive = DriveStore(
        files=(*graph.drive.files, file),
        versions=(*graph.drive.versions, version),
        share_records=graph.drive.share_records,
    )
    runtime.replace_graph(transaction, drive=drive, acl_entries=(*graph.acl_entries, owner_acl))
    return _drive_output(runtime, file, version)


def _prepare_share(
    runtime: OfficeV2ToolRuntime, arguments: ShareDriveFileArguments
) -> PreparedAction:
    resource = _file_ref(arguments.file_id, arguments.version_id)
    runtime.visible_resource(resource)
    recipient = runtime.resolve_principal(arguments.recipient)
    return PreparedAction(resources=(resource,), recipient_ids=(recipient,))


def _share(
    runtime: OfficeV2ToolRuntime,
    arguments: ShareDriveFileArguments,
    transaction: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    assert transaction is not None
    graph = transaction.staged_state.domain_graph
    resource = _file_ref(arguments.file_id, arguments.version_id)
    recipient = runtime.resolve_principal(arguments.recipient)
    existing_share = next(
        (
            item
            for item in graph.drive.share_records
            if item.resource == resource
            and item.recipient_id == recipient
            and item.status is ShareStatus.ACTIVE
        ),
        None,
    )
    existing_acl = next(
        (
            item
            for item in graph.acl_entries
            if item.resource == resource and item.grantee_id == recipient
        ),
        None,
    )
    if (
        existing_share is not None
        and existing_acl is not None
        and (AccessRight.READ in existing_acl.rights)
    ):
        return {
            "share_id": existing_share.share_id,
            "resource": resource.model_dump(mode="json"),
            "recipient_id": recipient,
            "idempotent": True,
        }
    timestamp = runtime.logical_datetime
    share = ShareRecord(
        share_id=transaction.allocate_id("drive.share"),
        resource=resource,
        recipient_id=recipient,
        rights=(AccessRight.READ,),
        created_by=runtime.actor.actor_id,
        created_at=timestamp,
    )
    acl = AclEntry(
        resource=resource,
        grantee_id=recipient,
        rights=(AccessRight.DISCOVER, AccessRight.READ),
        granted_by=runtime.actor.actor_id,
        granted_at=timestamp,
        grant_source=_source_evidence(runtime, f"share.{share.share_id}", resource),
    )
    drive = graph.drive.model_copy(update={"share_records": (*graph.drive.share_records, share)})
    acl_entries = tuple(
        item
        for item in graph.acl_entries
        if not (item.resource == resource and item.grantee_id == recipient)
    ) + (acl,)
    runtime.replace_graph(transaction, drive=drive, acl_entries=acl_entries)
    return {
        "share_id": share.share_id,
        "resource": resource.model_dump(mode="json"),
        "recipient_id": recipient,
        "rights": [AccessRight.READ.value],
        "idempotent": False,
    }


def _prepare_permissions(
    runtime: OfficeV2ToolRuntime, arguments: UpdateDrivePermissionsArguments
) -> PreparedAction:
    resource = _file_ref(arguments.file_id, arguments.version_id)
    runtime.visible_resource(resource)
    grantee = runtime.resolve_principal(arguments.grantee)
    if acl_digest(runtime, resource) != arguments.expected_acl_digest:
        raise ToolRejected(ToolFailureCode.RESOURCE_VERSION_CONFLICT)
    return PreparedAction(resources=(resource,), recipient_ids=(grantee,))


def _update_permissions(
    runtime: OfficeV2ToolRuntime,
    arguments: UpdateDrivePermissionsArguments,
    transaction: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    assert transaction is not None
    graph = transaction.staged_state.domain_graph
    resource = _file_ref(arguments.file_id, arguments.version_id)
    grantee = runtime.resolve_principal(arguments.grantee)
    existing = next(
        (
            item
            for item in graph.acl_entries
            if item.resource == resource and item.grantee_id == grantee
        ),
        None,
    )
    rights = set(() if existing is None else existing.rights)
    rights.update(arguments.add_rights)
    rights.difference_update(arguments.remove_rights)
    retained = tuple(
        item
        for item in graph.acl_entries
        if not (item.resource == resource and item.grantee_id == grantee)
    )
    if rights:
        updated_entry = AclEntry(
            resource=resource,
            grantee_id=grantee,
            rights=tuple(sorted(rights, key=lambda item: item.value)),
            granted_by=runtime.actor.actor_id,
            granted_at=runtime.logical_datetime,
            grant_source=_source_evidence(
                runtime, f"permission.{arguments.file_id}.{grantee}", sorted(rights)
            ),
        )
        acl_entries = (*retained, updated_entry)
    else:
        acl_entries = retained
    runtime.replace_graph(transaction, acl_entries=acl_entries)
    return {
        "resource": resource.model_dump(mode="json"),
        "grantee_id": grantee,
        "rights": [item.value for item in sorted(rights, key=lambda item: item.value)],
        "acl_digest": sha256_digest(
            [
                item.model_dump(mode="json", exclude_none=False)
                for item in acl_entries
                if item.resource == resource and item.grantee_id == grantee
            ]
        ),
    }


def _prepare_delete(
    runtime: OfficeV2ToolRuntime, arguments: DeleteDriveFileArguments
) -> PreparedAction:
    resource = _file_ref(arguments.file_id)
    runtime.visible_resource(resource)
    file = next(
        item for item in runtime.state.domain_graph.drive.files if item.file_id == arguments.file_id
    )
    if file.current_version_id != arguments.expected_current_version_id:
        raise ToolRejected(ToolFailureCode.RESOURCE_VERSION_CONFLICT)
    return PreparedAction(resources=(resource,))


def _delete(
    runtime: OfficeV2ToolRuntime,
    arguments: DeleteDriveFileArguments,
    transaction: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    assert transaction is not None
    graph = transaction.staged_state.domain_graph
    file = next(item for item in graph.drive.files if item.file_id == arguments.file_id)
    if file.lifecycle_state is DriveLifecycle.TRASHED:
        raise ToolRejected(ToolFailureCode.RESOURCE_VERSION_CONFLICT)
    updated_file = file.model_copy(update={"lifecycle_state": DriveLifecycle.TRASHED})
    drive = DriveStore(
        files=tuple(
            updated_file if item.file_id == file.file_id else item for item in graph.drive.files
        ),
        versions=graph.drive.versions,
        share_records=graph.drive.share_records,
    )
    runtime.replace_graph(transaction, drive=drive)
    return _drive_output(runtime, updated_file)


DEFINITIONS = (
    ToolDefinition(
        name="search_drive_files",
        arguments_model=SearchDriveFilesArguments,
        action=ActionKind.DISCOVER,
        capability_id="drive.read",
        resource_kinds=(ResourceKind.DRIVE_FILE,),
        prepare=_prepare_search,
        execute=_search,
    ),
    ToolDefinition(
        name="read_drive_file",
        arguments_model=ReadDriveFileArguments,
        action=ActionKind.READ,
        capability_id="drive.read",
        resource_kinds=(ResourceKind.DRIVE_FILE,),
        prepare=_prepare_read,
        execute=_read,
    ),
    ToolDefinition(
        name="create_drive_file",
        arguments_model=CreateDriveFileArguments,
        action=ActionKind.CREATE,
        capability_id="drive.write",
        resource_kinds=(ResourceKind.DRIVE_FILE,),
        prepare=_prepare_create,
        execute=_create,
        writes_state=True,
    ),
    ToolDefinition(
        name="share_drive_file",
        arguments_model=ShareDriveFileArguments,
        action=ActionKind.SHARE,
        capability_id="drive.share",
        resource_kinds=(ResourceKind.DRIVE_FILE,),
        prepare=_prepare_share,
        execute=_share,
        writes_state=True,
    ),
    ToolDefinition(
        name="update_drive_permissions",
        arguments_model=UpdateDrivePermissionsArguments,
        action=ActionKind.MANAGE_PERMISSIONS,
        capability_id="drive.manage_permissions",
        resource_kinds=(ResourceKind.DRIVE_FILE,),
        prepare=_prepare_permissions,
        execute=_update_permissions,
        writes_state=True,
    ),
    ToolDefinition(
        name="delete_drive_file",
        arguments_model=DeleteDriveFileArguments,
        action=ActionKind.DELETE,
        capability_id="drive.delete",
        resource_kinds=(ResourceKind.DRIVE_FILE,),
        prepare=_prepare_delete,
        execute=_delete,
        writes_state=True,
    ),
)


__all__ = [
    "CreateDriveFileArguments",
    "DEFINITIONS",
    "DeleteDriveFileArguments",
    "ReadDriveFileArguments",
    "SearchDriveFilesArguments",
    "ShareDriveFileArguments",
    "UpdateDrivePermissionsArguments",
    "acl_digest",
]
