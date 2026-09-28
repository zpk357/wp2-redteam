"""Frozen Office V2 tool-catalog identity; tool behavior is added in later steps."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sandbox.scenarios.office_v2 import (
    OFFICE_V2_TOOL_CATALOG_VERSION,
    OFFICE_V2_TOOL_CONTRACT_VERSION,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sandbox.scenarios.office_v2.tools.runtime import ToolDefinition

OFFICE_V2_TOOL_NAMES = (
    "search_email",
    "read_email",
    "send_email",
    "search_calendar_events",
    "create_calendar_event",
    "update_calendar_event",
    "cancel_calendar_event",
    "search_drive_files",
    "read_drive_file",
    "create_drive_file",
    "share_drive_file",
    "update_drive_permissions",
    "delete_drive_file",
    "list_directory",
    "search_files",
    "read_file",
    "write_file",
)

OFFICE_V2_EXCLUDED_TOOL_NAMES = (
    "run_command",
    "call_internal_api",
    "read_environment",
    "list_processes",
    "query_database",
    "http_request",
    "retrieve_secret",
)


def office_v2_tool_definitions() -> Mapping[str, ToolDefinition]:
    """Load the deterministic V2 handler catalog without enabling an Agent registry."""

    from sandbox.scenarios.office_v2.tools.calendar import DEFINITIONS as CALENDAR
    from sandbox.scenarios.office_v2.tools.drive import DEFINITIONS as DRIVE
    from sandbox.scenarios.office_v2.tools.mail import DEFINITIONS as MAIL
    from sandbox.scenarios.office_v2.tools.workspace import DEFINITIONS as WORKSPACE

    definitions = (*MAIL, *CALENDAR, *DRIVE, *WORKSPACE)
    by_name = {item.name: item for item in definitions}
    if len(by_name) != len(definitions) or tuple(by_name) != OFFICE_V2_TOOL_NAMES:
        raise RuntimeError("Office V2 tool definitions do not match the frozen catalog")
    return by_name


__all__ = [
    "OFFICE_V2_EXCLUDED_TOOL_NAMES",
    "OFFICE_V2_TOOL_CATALOG_VERSION",
    "OFFICE_V2_TOOL_CONTRACT_VERSION",
    "OFFICE_V2_TOOL_NAMES",
    "office_v2_tool_definitions",
]
