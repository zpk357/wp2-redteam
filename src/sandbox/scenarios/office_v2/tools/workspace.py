"""Workspace-domain Office V2 tool argument models and handlers."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Self

from pydantic import Field, JsonValue, model_validator

from sandbox.scenarios.office_v2.models import (
    ActionKind,
    OfficeV2Contract,
    ResourceKind,
    ResourceRef,
    WorkspaceFile,
    WorkspaceStore,
)
from sandbox.scenarios.office_v2.tools.contracts import ToolFailureCode
from sandbox.scenarios.office_v2.tools.runtime import (
    OfficeV2ToolRuntime,
    PreparedAction,
    ToolDefinition,
    ToolRejected,
)
from sandbox.scenarios.office_v2.world import EpisodeTransaction


class ListDirectoryArguments(OfficeV2Contract):
    path: str = "/workspace"
    page_size: int = Field(default=10, ge=1, le=25)
    page_token: str | None = Field(default=None, min_length=1, max_length=4096)


class SearchFilesArguments(OfficeV2Contract):
    query: str | None = Field(default=None, min_length=1, max_length=512)
    root: str = "/workspace"
    page_size: int = Field(default=10, ge=1, le=25)
    page_token: str | None = Field(default=None, min_length=1, max_length=4096)


class ReadFileArguments(OfficeV2Contract):
    path: str


class WriteFileArguments(OfficeV2Contract):
    path: str
    content: str = Field(max_length=500_000)
    media_type: str = "text/plain"
    expected_version: int | None = Field(default=None, ge=1)
    source_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def path_is_canonical(self) -> Self:
        _canonical_path(self.path)
        return self


def _canonical_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value.startswith("/workspace/")
        or path.as_posix() != value
        or ".." in path.parts
        or "." in path.parts
    ):
        raise ToolRejected(ToolFailureCode.INVALID_ARGUMENTS)
    return value


def _file_output(
    file: WorkspaceFile, *, include_content: bool = True
) -> dict[str, JsonValue]:
    output: dict[str, JsonValue] = {
        "resource": ResourceRef(kind=ResourceKind.WORKSPACE_FILE, resource_id=file.path).model_dump(
            mode="json"
        ),
        "path": file.path,
        "owner_id": file.owner_id,
        "media_type": file.media_type,
        "version": file.version,
        "created_at": file.created_at.isoformat(),
        "updated_at": file.updated_at.isoformat(),
        "source_refs": [item.model_dump(mode="json") for item in file.source_refs],
    }
    if include_content:
        output["content"] = file.content
    return output


def _prepare_list(
    runtime: OfficeV2ToolRuntime, arguments: ListDirectoryArguments
) -> PreparedAction:
    if arguments.path != "/workspace":
        candidate = arguments.path.rstrip("/") + "/placeholder"
        _canonical_path(candidate)
    return PreparedAction()


def _list_directory(
    runtime: OfficeV2ToolRuntime,
    arguments: ListDirectoryArguments,
    _: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    visible_paths = {
        item.resource.resource_id
        for item in runtime.visible_resources((ResourceKind.WORKSPACE_FILE,))
    }
    prefix = arguments.path.rstrip("/") + "/"
    children: set[str] = set()
    for path in visible_paths:
        if not path.startswith(prefix):
            continue
        remainder = path[len(prefix) :]
        first = remainder.split("/", 1)[0]
        children.add(prefix + first)
    items = tuple(
        {
            "path": path,
            "kind": "file" if path in visible_paths else "directory",
        }
        for path in sorted(children)
    )
    return runtime.paginate(
        items,
        query_payload={"path": arguments.path},
        page_size=arguments.page_size,
        page_token=arguments.page_token,
    )


def _prepare_search(
    runtime: OfficeV2ToolRuntime, arguments: SearchFilesArguments
) -> PreparedAction:
    if arguments.root != "/workspace":
        _canonical_path(arguments.root.rstrip("/") + "/placeholder")
    return PreparedAction()


def _search(
    runtime: OfficeV2ToolRuntime,
    arguments: SearchFilesArguments,
    _: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    visible_ids = {
        item.resource.resource_id
        for item in runtime.visible_resources((ResourceKind.WORKSPACE_FILE,), text=arguments.query)
    }
    root_prefix = arguments.root.rstrip("/") + "/"
    files = tuple(
        file
        for file in runtime.state.domain_graph.workspace.files
        if file.path in visible_ids and file.path.startswith(root_prefix)
    )
    items = tuple(_file_output(file, include_content=False) for file in files)
    return runtime.paginate(
        items,
        query_payload={"query": arguments.query, "root": arguments.root},
        page_size=arguments.page_size,
        page_token=arguments.page_token,
    )


def _prepare_read(runtime: OfficeV2ToolRuntime, arguments: ReadFileArguments) -> PreparedAction:
    resource = ResourceRef(
        kind=ResourceKind.WORKSPACE_FILE,
        resource_id=_canonical_path(arguments.path),
    )
    runtime.visible_resource(resource)
    return PreparedAction(resources=(resource,))


def _read(
    runtime: OfficeV2ToolRuntime,
    arguments: ReadFileArguments,
    _: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    file = next(
        item for item in runtime.state.domain_graph.workspace.files if item.path == arguments.path
    )
    return _file_output(file)


def _prepare_write(runtime: OfficeV2ToolRuntime, arguments: WriteFileArguments) -> PreparedAction:
    _canonical_path(arguments.path)
    runtime.require_visible_refs(arguments.source_refs)
    existing = next(
        (
            item
            for item in runtime.state.domain_graph.workspace.files
            if item.path == arguments.path
        ),
        None,
    )
    if existing is None:
        if arguments.expected_version is not None:
            raise ToolRejected(ToolFailureCode.RESOURCE_VERSION_CONFLICT)
        return PreparedAction(action=ActionKind.CREATE)
    resource = ResourceRef(kind=ResourceKind.WORKSPACE_FILE, resource_id=arguments.path)
    runtime.visible_resource(resource)
    if arguments.expected_version != existing.version:
        raise ToolRejected(ToolFailureCode.RESOURCE_VERSION_CONFLICT)
    return PreparedAction(resources=(resource,), action=ActionKind.UPDATE)


def _write(
    runtime: OfficeV2ToolRuntime,
    arguments: WriteFileArguments,
    transaction: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    assert transaction is not None
    workspace = transaction.staged_state.domain_graph.workspace
    existing = next((item for item in workspace.files if item.path == arguments.path), None)
    timestamp = runtime.logical_datetime
    if existing is None:
        file = WorkspaceFile(
            path=arguments.path,
            owner_id=runtime.actor.actor_id,
            content=arguments.content,
            media_type=arguments.media_type,
            created_at=timestamp,
            updated_at=timestamp,
            source_refs=arguments.source_refs,
        )
        files = (*workspace.files, file)
    else:
        file = existing.model_copy(
            update={
                "content": arguments.content,
                "media_type": arguments.media_type,
                "version": existing.version + 1,
                "updated_at": timestamp,
                "source_refs": arguments.source_refs,
            }
        )
        files = tuple(file if item.path == existing.path else item for item in workspace.files)
    runtime.replace_graph(transaction, workspace=WorkspaceStore(files=files))
    return _file_output(file)


DEFINITIONS = (
    ToolDefinition(
        name="list_directory",
        arguments_model=ListDirectoryArguments,
        action=ActionKind.DISCOVER,
        capability_id="workspace.read",
        resource_kinds=(ResourceKind.WORKSPACE_FILE,),
        prepare=_prepare_list,
        execute=_list_directory,
    ),
    ToolDefinition(
        name="search_files",
        arguments_model=SearchFilesArguments,
        action=ActionKind.DISCOVER,
        capability_id="workspace.read",
        resource_kinds=(ResourceKind.WORKSPACE_FILE,),
        prepare=_prepare_search,
        execute=_search,
    ),
    ToolDefinition(
        name="read_file",
        arguments_model=ReadFileArguments,
        action=ActionKind.READ,
        capability_id="workspace.read",
        resource_kinds=(ResourceKind.WORKSPACE_FILE,),
        prepare=_prepare_read,
        execute=_read,
    ),
    ToolDefinition(
        name="write_file",
        arguments_model=WriteFileArguments,
        action=ActionKind.CREATE,
        capability_id="workspace.write",
        resource_kinds=(ResourceKind.WORKSPACE_FILE,),
        prepare=_prepare_write,
        execute=_write,
        writes_state=True,
    ),
)


__all__ = [
    "DEFINITIONS",
    "ListDirectoryArguments",
    "ReadFileArguments",
    "SearchFilesArguments",
    "WriteFileArguments",
]
