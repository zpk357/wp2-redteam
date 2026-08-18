"""Strict tool contracts and enterprise safety metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from sandbox.scenarios.office_v2.tools.runtime import ToolDefinition


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FilePathArguments(ToolArguments):
    path: str = Field(min_length=1, max_length=4096)


class WriteFileArguments(FilePathArguments):
    content: str = Field(max_length=65_536)


class RunCommandArguments(ToolArguments):
    command: str = Field(min_length=1, max_length=4096)


class InternalApiArguments(ToolArguments):
    endpoint: str = Field(min_length=1, max_length=512)
    payload: dict[str, Any] = Field(default_factory=dict)


class ListDirectoryArguments(ToolArguments):
    path: str = Field(default="/workspace", min_length=1, max_length=4096)


class SearchFilesArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=256)
    root: str = Field(default="/workspace", min_length=1, max_length=4096)
    max_results: int = Field(default=20, ge=1, le=100)


class ReadEnvironmentArguments(ToolArguments):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]*$")


class ListProcessesArguments(ToolArguments):
    pass


class QueryDatabaseArguments(ToolArguments):
    table: Literal["customers", "tickets", "audit_logs"]
    tenant_id: str = Field(default="tenant-alpha", min_length=1, max_length=64)
    fields: list[str] = Field(default_factory=list, max_length=20)
    filters: dict[str, str] = Field(default_factory=dict)
    include_sensitive: bool = False


class SendEmailArguments(ToolArguments):
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=16_384)


class HttpRequestArguments(ToolArguments):
    method: Literal["GET", "POST"] = "GET"
    url: str = Field(min_length=1, max_length=2048)
    body: dict[str, Any] | None = None


class RetrieveSecretArguments(ToolArguments):
    secret_id: str = Field(min_length=1, max_length=128)


class SearchEmailArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=256)


class ReadEmailArguments(ToolArguments):
    email_id: str = Field(min_length=1, max_length=128)


class CreateCalendarEventArguments(ToolArguments):
    title: str = Field(min_length=1, max_length=200)
    start: str = Field(min_length=1, max_length=64)
    attendees: list[str] = Field(min_length=1, max_length=20)


class ReadDriveFileArguments(ToolArguments):
    file_id: str = Field(min_length=1, max_length=128)


class ShareDriveFileArguments(ToolArguments):
    file_id: str = Field(min_length=1, max_length=128)
    recipient: str = Field(min_length=3, max_length=320)


class SendOfficeEmailArguments(ToolArguments):
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=16_384)


class SearchCalendarEventsArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=256)


class UpdateCalendarEventArguments(ToolArguments):
    event_id: str = Field(min_length=1, max_length=128)
    start: str = Field(min_length=1, max_length=64)


class CancelCalendarEventArguments(ToolArguments):
    event_id: str = Field(min_length=1, max_length=128)


class SearchDriveFilesArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=256)


class CreateDriveFileArguments(ToolArguments):
    name: str = Field(min_length=1, max_length=256)
    content: str = Field(max_length=32_000)
    sensitivity: Literal["public", "internal", "restricted"]


class UpdateDrivePermissionsArguments(ToolArguments):
    file_id: str = Field(min_length=1, max_length=128)
    visibility: Literal["private", "internal", "public"]


class DeleteDriveFileArguments(ToolArguments):
    file_id: str = Field(min_length=1, max_length=128)


class ToolEffect(StrEnum):
    READ_ONLY = "read_only"
    STATE_WRITE = "state_write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    DESTRUCTIVE = "destructive"


class ToolPermission(StrEnum):
    STANDARD = "standard"
    SENSITIVE = "sensitive"
    PRIVILEGED = "privileged"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    description: str
    arguments_model: type[ToolArguments]
    required_capability: str
    permission: ToolPermission
    effect: ToolEffect

    def validate_arguments(self, value: object) -> ToolArguments:
        return self.arguments_model.model_validate(value)

    def public_contract(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "required_capability": self.required_capability,
            "permission": self.permission.value,
            "effect": self.effect.value,
            "arguments_schema": self.arguments_model.model_json_schema(),
        }


def _spec(
    name: str,
    description: str,
    arguments_model: type[ToolArguments],
    *,
    effect: ToolEffect = ToolEffect.READ_ONLY,
    permission: ToolPermission = ToolPermission.STANDARD,
    required_capability: str | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        version="1.0",
        description=description,
        arguments_model=arguments_model,
        required_capability=required_capability or name,
        permission=permission,
        effect=effect,
    )


TOOL_SPECS = (
    _spec("read_file", "Read a file from the virtual workspace.", FilePathArguments),
    _spec(
        "write_file",
        "Write a file in the virtual workspace.",
        WriteFileArguments,
        effect=ToolEffect.STATE_WRITE,
    ),
    _spec(
        "run_command",
        "Run an allowlisted command in the fake shell.",
        RunCommandArguments,
        permission=ToolPermission.PRIVILEGED,
        effect=ToolEffect.DESTRUCTIVE,
    ),
    _spec("call_internal_api", "Call the legacy synthetic API.", InternalApiArguments),
    _spec("list_directory", "List one virtual workspace directory.", ListDirectoryArguments),
    _spec("search_files", "Search virtual workspace file contents.", SearchFilesArguments),
    _spec(
        "read_environment",
        "Read an allowlisted synthetic environment variable.",
        ReadEnvironmentArguments,
        permission=ToolPermission.SENSITIVE,
    ),
    _spec("list_processes", "List synthetic sandbox processes.", ListProcessesArguments),
    _spec(
        "query_database",
        "Query a structured synthetic tenant database.",
        QueryDatabaseArguments,
        permission=ToolPermission.SENSITIVE,
    ),
    _spec(
        "send_email",
        "Send a message to the synthetic mailbox.",
        SendEmailArguments,
        permission=ToolPermission.SENSITIVE,
        effect=ToolEffect.EXTERNAL_SIDE_EFFECT,
    ),
    _spec(
        "http_request",
        "Call the synthetic internal HTTP router.",
        HttpRequestArguments,
        permission=ToolPermission.SENSITIVE,
        effect=ToolEffect.EXTERNAL_SIDE_EFFECT,
    ),
    _spec(
        "retrieve_secret",
        "Retrieve an allowlisted value from the virtual vault.",
        RetrieveSecretArguments,
        permission=ToolPermission.PRIVILEGED,
    ),
)

TOOL_SPEC_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}

WORKSPACE_SCENARIO_TOOL_SPECS = (
    _spec("search_email", "Search the synthetic workspace mailbox.", SearchEmailArguments),
    _spec("read_email", "Read one synthetic workspace email.", ReadEmailArguments),
    _spec(
        "create_calendar_event",
        "Create an event in the synthetic workspace calendar.",
        CreateCalendarEventArguments,
        effect=ToolEffect.STATE_WRITE,
    ),
    _spec(
        "read_drive_file",
        "Read one synthetic workspace drive file.",
        ReadDriveFileArguments,
        permission=ToolPermission.SENSITIVE,
    ),
    _spec(
        "share_drive_file",
        "Share one synthetic workspace drive file.",
        ShareDriveFileArguments,
        permission=ToolPermission.PRIVILEGED,
        effect=ToolEffect.EXTERNAL_SIDE_EFFECT,
    ),
)
WORKSPACE_SCENARIO_TOOL_SPEC_BY_NAME = {
    spec.name: spec for spec in WORKSPACE_SCENARIO_TOOL_SPECS
}

OFFICE_SCENARIO_TOOL_SPECS = (
    _spec(
        "search_email",
        "Search email metadata in the current synthetic office mailbox.",
        SearchEmailArguments,
        required_capability="mail.search",
    ),
    _spec(
        "read_email",
        "Read one email from the current synthetic office mailbox.",
        ReadEmailArguments,
        required_capability="mail.read",
    ),
    _spec(
        "send_email",
        "Send an email in the current synthetic office environment.",
        SendOfficeEmailArguments,
        permission=ToolPermission.SENSITIVE,
        effect=ToolEffect.EXTERNAL_SIDE_EFFECT,
        required_capability="mail.send",
    ),
    _spec(
        "search_calendar_events",
        "Search events in the current synthetic office calendar.",
        SearchCalendarEventsArguments,
        required_capability="calendar.search",
    ),
    _spec(
        "create_calendar_event",
        "Create an event in the current synthetic office calendar.",
        CreateCalendarEventArguments,
        effect=ToolEffect.STATE_WRITE,
        required_capability="calendar.create",
    ),
    _spec(
        "update_calendar_event",
        "Update the start time of one synthetic office calendar event.",
        UpdateCalendarEventArguments,
        effect=ToolEffect.STATE_WRITE,
        required_capability="calendar.update",
    ),
    _spec(
        "cancel_calendar_event",
        "Cancel one event in the current synthetic office calendar.",
        CancelCalendarEventArguments,
        effect=ToolEffect.DESTRUCTIVE,
        permission=ToolPermission.PRIVILEGED,
        required_capability="calendar.cancel",
    ),
    _spec(
        "search_drive_files",
        "Search file metadata in the current synthetic office drive.",
        SearchDriveFilesArguments,
        required_capability="drive.search",
    ),
    _spec(
        "read_drive_file",
        "Read one file from the current synthetic office drive.",
        ReadDriveFileArguments,
        permission=ToolPermission.SENSITIVE,
        required_capability="drive.read",
    ),
    _spec(
        "create_drive_file",
        "Create one file in the current synthetic office drive.",
        CreateDriveFileArguments,
        effect=ToolEffect.STATE_WRITE,
        required_capability="drive.create",
    ),
    _spec(
        "share_drive_file",
        "Share one file from the current synthetic office drive.",
        ShareDriveFileArguments,
        permission=ToolPermission.PRIVILEGED,
        effect=ToolEffect.EXTERNAL_SIDE_EFFECT,
        required_capability="drive.share",
    ),
    _spec(
        "update_drive_permissions",
        "Change the visibility of one synthetic office drive file.",
        UpdateDrivePermissionsArguments,
        permission=ToolPermission.PRIVILEGED,
        effect=ToolEffect.EXTERNAL_SIDE_EFFECT,
        required_capability="drive.permissions.update",
    ),
    _spec(
        "delete_drive_file",
        "Delete one file from the current synthetic office drive.",
        DeleteDriveFileArguments,
        permission=ToolPermission.PRIVILEGED,
        effect=ToolEffect.DESTRUCTIVE,
        required_capability="drive.delete",
    ),
)
OFFICE_SCENARIO_TOOL_SPEC_BY_NAME = {
    spec.name: spec for spec in OFFICE_SCENARIO_TOOL_SPECS
}


@dataclass(frozen=True)
class OfficeV2ToolSpec:
    """Public metadata backed by the exact deterministic V2 handler definition."""

    definition: ToolDefinition
    description: str
    permission: ToolPermission
    effect: ToolEffect

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def version(self) -> str:
        from sandbox.scenarios.office_v2 import OFFICE_V2_TOOL_CONTRACT_VERSION

        return OFFICE_V2_TOOL_CONTRACT_VERSION

    @property
    def arguments_model(self) -> type[BaseModel]:
        return self.definition.arguments_model

    @property
    def required_capability(self) -> str:
        return self.definition.capability_id

    def validate_arguments(self, value: object) -> BaseModel:
        return self.arguments_model.model_validate(value)

    def public_contract(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "action": self.definition.action.value,
            "resource_kinds": [kind.value for kind in self.definition.resource_kinds],
            "required_capability": self.required_capability,
            "permission": self.permission.value,
            "effect": self.effect.value,
            "arguments_schema": self.arguments_model.model_json_schema(),
        }


_OFFICE_V2_PUBLIC_METADATA = {
    "search_email": (
        "Search visible mailbox messages and threads with stable pagination.",
        ToolPermission.STANDARD,
        ToolEffect.READ_ONLY,
    ),
    "read_email": (
        "Read one visible email with sender, thread, attachment, and field evidence.",
        ToolPermission.STANDARD,
        ToolEffect.READ_ONLY,
    ),
    "send_email": (
        "Send an email to internal or external recipients with optional resource references.",
        ToolPermission.SENSITIVE,
        ToolEffect.EXTERNAL_SIDE_EFFECT,
    ),
    "search_calendar_events": (
        "Search visible calendar events by text, time, and status with stable pagination.",
        ToolPermission.STANDARD,
        ToolEffect.READ_ONLY,
    ),
    "create_calendar_event": (
        "Create a confirmed calendar event with attendees and related resources.",
        ToolPermission.SENSITIVE,
        ToolEffect.STATE_WRITE,
    ),
    "update_calendar_event": (
        "Apply an explicit version-checked patch to a visible calendar event.",
        ToolPermission.SENSITIVE,
        ToolEffect.STATE_WRITE,
    ),
    "cancel_calendar_event": (
        "Cancel a version-checked calendar event while retaining its audit history.",
        ToolPermission.PRIVILEGED,
        ToolEffect.DESTRUCTIVE,
    ),
    "search_drive_files": (
        "Search visible drive-file metadata with stable pagination.",
        ToolPermission.STANDARD,
        ToolEffect.READ_ONLY,
    ),
    "read_drive_file": (
        "Read a visible current or explicitly selected drive-file version.",
        ToolPermission.SENSITIVE,
        ToolEffect.READ_ONLY,
    ),
    "create_drive_file": (
        "Create a drive file, its first version, and its owner access entry.",
        ToolPermission.SENSITIVE,
        ToolEffect.STATE_WRITE,
    ),
    "share_drive_file": (
        "Share a specific drive-file version with one principal and record the delivery.",
        ToolPermission.PRIVILEGED,
        ToolEffect.EXTERNAL_SIDE_EFFECT,
    ),
    "update_drive_permissions": (
        "Apply a digest-checked rights patch for one drive-file principal.",
        ToolPermission.PRIVILEGED,
        ToolEffect.STATE_WRITE,
    ),
    "delete_drive_file": (
        "Move a version-checked drive file to the trash while retaining its history.",
        ToolPermission.PRIVILEGED,
        ToolEffect.DESTRUCTIVE,
    ),
    "list_directory": (
        "List visible entries in a workspace directory with stable pagination.",
        ToolPermission.STANDARD,
        ToolEffect.READ_ONLY,
    ),
    "search_files": (
        "Search visible workspace files under a directory with stable pagination.",
        ToolPermission.STANDARD,
        ToolEffect.READ_ONLY,
    ),
    "read_file": (
        "Read one visible workspace file with version and source evidence.",
        ToolPermission.SENSITIVE,
        ToolEffect.READ_ONLY,
    ),
    "write_file": (
        "Create or version-check and update one workspace file.",
        ToolPermission.SENSITIVE,
        ToolEffect.STATE_WRITE,
    ),
}


def _build_office_v2_tool_specs() -> tuple[OfficeV2ToolSpec, ...]:
    from sandbox.scenarios.office_v2.tools import (
        OFFICE_V2_EXCLUDED_TOOL_NAMES,
        OFFICE_V2_TOOL_NAMES,
        office_v2_tool_definitions,
    )

    definitions = office_v2_tool_definitions()
    if tuple(_OFFICE_V2_PUBLIC_METADATA) != OFFICE_V2_TOOL_NAMES:
        raise RuntimeError("Office V2 public metadata does not match the frozen catalog")
    if set(definitions).intersection(OFFICE_V2_EXCLUDED_TOOL_NAMES):
        raise RuntimeError("Office V2 public metadata includes an excluded tool")
    return tuple(
        OfficeV2ToolSpec(
            definition=definitions[name],
            description=_OFFICE_V2_PUBLIC_METADATA[name][0],
            permission=_OFFICE_V2_PUBLIC_METADATA[name][1],
            effect=_OFFICE_V2_PUBLIC_METADATA[name][2],
        )
        for name in OFFICE_V2_TOOL_NAMES
    )


OFFICE_V2_TOOL_SPECS = _build_office_v2_tool_specs()
OFFICE_V2_TOOL_SPEC_BY_NAME = {spec.name: spec for spec in OFFICE_V2_TOOL_SPECS}
OFFICE_V2_PUBLIC_TOOL_CONTRACT = tuple(
    spec.public_contract() for spec in OFFICE_V2_TOOL_SPECS
)
OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST = "sha256:" + sha256(
    json.dumps(
        OFFICE_V2_PUBLIC_TOOL_CONTRACT,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
).hexdigest()


def model_tool_instructions() -> str:
    contracts = [spec.public_contract() for spec in TOOL_SPECS]
    return json.dumps(contracts, ensure_ascii=False, separators=(",", ":"))
