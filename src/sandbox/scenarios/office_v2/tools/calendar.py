"""Calendar-domain Office V2 tool argument models and handlers."""

from __future__ import annotations

from typing import Self

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from sandbox.scenarios.office_v2.models import (
    ActionKind,
    Attendance,
    AttendanceResponse,
    CalendarEvent,
    CalendarEventStatus,
    CalendarStore,
    OfficeV2Contract,
    ResourceKind,
    ResourceRef,
)
from sandbox.scenarios.office_v2.tools.contracts import ToolFailureCode
from sandbox.scenarios.office_v2.tools.runtime import (
    OfficeV2ToolRuntime,
    PreparedAction,
    ToolDefinition,
    ToolRejected,
)
from sandbox.scenarios.office_v2.world import EpisodeTransaction


class SearchCalendarArguments(OfficeV2Contract):
    text: str | None = Field(default=None, min_length=1, max_length=512)
    start_at_or_after: AwareDatetime | None = None
    end_at_or_before: AwareDatetime | None = None
    status: CalendarEventStatus | None = None
    page_size: int = Field(default=10, ge=1, le=25)
    page_token: str | None = Field(default=None, min_length=1, max_length=4096)


class CreateCalendarEventArguments(OfficeV2Contract):
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=100_000)
    start_at: AwareDatetime
    end_at: AwareDatetime
    timezone: str = Field(min_length=1, max_length=128)
    attendees: tuple[str, ...] = Field(default_factory=tuple)
    related_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)


class UpdateCalendarEventArguments(OfficeV2Contract):
    event_id: str
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=100_000)
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=128)
    attendees: tuple[str, ...] | None = None
    related_refs: tuple[ResourceRef, ...] | None = None

    @model_validator(mode="after")
    def patch_is_not_empty(self) -> Self:
        if all(
            value is None
            for value in (
                self.title,
                self.description,
                self.start_at,
                self.end_at,
                self.timezone,
                self.attendees,
                self.related_refs,
            )
        ):
            raise ValueError("calendar update patch must not be empty")
        return self


class CancelCalendarEventArguments(OfficeV2Contract):
    event_id: str
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


def _event_output(
    event: CalendarEvent, *, include_description: bool = True
) -> dict[str, JsonValue]:
    output: dict[str, JsonValue] = {
        "resource": ResourceRef(
            kind=ResourceKind.CALENDAR_EVENT, resource_id=event.event_id
        ).model_dump(mode="json"),
        "event_id": event.event_id,
        "version": event.version,
        "organizer_id": event.organizer_id,
        "title": event.title,
        "start_at": event.start_at.isoformat(),
        "end_at": event.end_at.isoformat(),
        "timezone": event.timezone,
        "attendee_ids": list(event.attendee_ids),
        "status": event.status.value,
        "related_refs": [item.model_dump(mode="json") for item in event.related_refs],
    }
    if include_description:
        output["description"] = event.description
    return output


def _prepare_search(*_: object) -> PreparedAction:
    return PreparedAction()


def _search(
    runtime: OfficeV2ToolRuntime,
    arguments: SearchCalendarArguments,
    _: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    visible_ids = {
        item.resource.resource_id
        for item in runtime.visible_resources((ResourceKind.CALENDAR_EVENT,), text=arguments.text)
    }
    events = tuple(
        event
        for event in runtime.state.domain_graph.calendar.events
        if event.event_id in visible_ids
        and (arguments.start_at_or_after is None or event.start_at >= arguments.start_at_or_after)
        and (arguments.end_at_or_before is None or event.end_at <= arguments.end_at_or_before)
        and (arguments.status is None or event.status is arguments.status)
    )
    items = tuple(_event_output(event, include_description=False) for event in events)
    return runtime.paginate(
        items,
        query_payload={
            "text": arguments.text,
            "start_at_or_after": (
                None
                if arguments.start_at_or_after is None
                else arguments.start_at_or_after.isoformat()
            ),
            "end_at_or_before": (
                None
                if arguments.end_at_or_before is None
                else arguments.end_at_or_before.isoformat()
            ),
            "status": None if arguments.status is None else arguments.status.value,
        },
        page_size=arguments.page_size,
        page_token=arguments.page_token,
    )


def _resolved_attendees(runtime: OfficeV2ToolRuntime, values: tuple[str, ...]) -> tuple[str, ...]:
    resolved = tuple(sorted(runtime.resolve_principal(item) for item in values))
    if len(resolved) != len(set(resolved)):
        raise ToolRejected(ToolFailureCode.RECIPIENT_NOT_RESOLVABLE)
    return resolved


def _prepare_create(
    runtime: OfficeV2ToolRuntime, arguments: CreateCalendarEventArguments
) -> PreparedAction:
    if arguments.start_at >= arguments.end_at:
        raise ToolRejected(ToolFailureCode.INVALID_ARGUMENTS)
    attendees = _resolved_attendees(runtime, arguments.attendees)
    runtime.require_visible_refs(arguments.related_refs)
    return PreparedAction(recipient_ids=attendees)


def _create(
    runtime: OfficeV2ToolRuntime,
    arguments: CreateCalendarEventArguments,
    transaction: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    assert transaction is not None
    attendees = _resolved_attendees(runtime, arguments.attendees)
    event = CalendarEvent(
        event_id=transaction.allocate_id("calendar.event"),
        organizer_id=runtime.actor.actor_id,
        title=arguments.title,
        description=arguments.description,
        start_at=arguments.start_at,
        end_at=arguments.end_at,
        timezone=arguments.timezone,
        attendee_ids=attendees,
        related_refs=arguments.related_refs,
    )
    calendar = transaction.staged_state.domain_graph.calendar
    updated = CalendarStore(
        events=(*calendar.events, event),
        attendance=(
            *calendar.attendance,
            *(
                Attendance(
                    event_id=event.event_id,
                    principal_id=principal_id,
                    response_status=AttendanceResponse.PENDING,
                )
                for principal_id in attendees
            ),
        ),
    )
    runtime.replace_graph(transaction, calendar=updated)
    return _event_output(event)


def _event_for_update(
    runtime: OfficeV2ToolRuntime, event_id: str, expected_version: int
) -> CalendarEvent:
    resource = ResourceRef(kind=ResourceKind.CALENDAR_EVENT, resource_id=event_id)
    runtime.visible_resource(resource)
    event = next(
        item for item in runtime.state.domain_graph.calendar.events if item.event_id == event_id
    )
    if event.version != expected_version:
        raise ToolRejected(ToolFailureCode.RESOURCE_VERSION_CONFLICT)
    return event


def _prepare_update(
    runtime: OfficeV2ToolRuntime, arguments: UpdateCalendarEventArguments
) -> PreparedAction:
    event = _event_for_update(runtime, arguments.event_id, arguments.expected_version)
    attendees = (
        event.attendee_ids
        if arguments.attendees is None
        else _resolved_attendees(runtime, arguments.attendees)
    )
    if arguments.related_refs is not None:
        runtime.require_visible_refs(arguments.related_refs)
    start = arguments.start_at or event.start_at
    end = arguments.end_at or event.end_at
    if start >= end:
        raise ToolRejected(ToolFailureCode.INVALID_ARGUMENTS)
    return PreparedAction(
        resources=(ResourceRef(kind=ResourceKind.CALENDAR_EVENT, resource_id=arguments.event_id),),
        recipient_ids=attendees,
    )


def _update(
    runtime: OfficeV2ToolRuntime,
    arguments: UpdateCalendarEventArguments,
    transaction: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    assert transaction is not None
    calendar = transaction.staged_state.domain_graph.calendar
    event = next(item for item in calendar.events if item.event_id == arguments.event_id)
    attendees = (
        event.attendee_ids
        if arguments.attendees is None
        else _resolved_attendees(runtime, arguments.attendees)
    )
    changes: dict[str, object] = {"version": event.version + 1}
    for name in ("title", "description", "start_at", "end_at", "timezone"):
        value = getattr(arguments, name)
        if value is not None:
            changes[name] = value
    if arguments.attendees is not None:
        changes["attendee_ids"] = attendees
    if arguments.related_refs is not None:
        changes["related_refs"] = arguments.related_refs
    updated_event = event.model_copy(update=changes)
    existing_attendance = {
        item.principal_id: item for item in calendar.attendance if item.event_id == event.event_id
    }
    attendance = tuple(
        item for item in calendar.attendance if item.event_id != event.event_id
    ) + tuple(
        existing_attendance.get(
            principal_id,
            Attendance(event_id=event.event_id, principal_id=principal_id),
        )
        for principal_id in attendees
    )
    updated = CalendarStore(
        events=tuple(
            updated_event if item.event_id == event.event_id else item for item in calendar.events
        ),
        attendance=attendance,
    )
    runtime.replace_graph(transaction, calendar=updated)
    return _event_output(updated_event)


def _prepare_cancel(
    runtime: OfficeV2ToolRuntime, arguments: CancelCalendarEventArguments
) -> PreparedAction:
    _event_for_update(runtime, arguments.event_id, arguments.expected_version)
    return PreparedAction(
        resources=(ResourceRef(kind=ResourceKind.CALENDAR_EVENT, resource_id=arguments.event_id),)
    )


def _cancel(
    runtime: OfficeV2ToolRuntime,
    arguments: CancelCalendarEventArguments,
    transaction: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    assert transaction is not None
    calendar = transaction.staged_state.domain_graph.calendar
    event = next(item for item in calendar.events if item.event_id == arguments.event_id)
    if event.status is CalendarEventStatus.CANCELLED:
        raise ToolRejected(ToolFailureCode.RESOURCE_VERSION_CONFLICT)
    updated_event = event.model_copy(
        update={
            "status": CalendarEventStatus.CANCELLED,
            "version": event.version + 1,
        }
    )
    updated = CalendarStore(
        events=tuple(
            updated_event if item.event_id == event.event_id else item for item in calendar.events
        ),
        attendance=calendar.attendance,
    )
    runtime.replace_graph(transaction, calendar=updated)
    output = _event_output(updated_event)
    output["cancellation_reason_digest"] = arguments.canonical_digest()
    return output


DEFINITIONS = (
    ToolDefinition(
        name="search_calendar_events",
        arguments_model=SearchCalendarArguments,
        action=ActionKind.DISCOVER,
        capability_id="calendar.read",
        resource_kinds=(ResourceKind.CALENDAR_EVENT,),
        prepare=_prepare_search,
        execute=_search,
    ),
    ToolDefinition(
        name="create_calendar_event",
        arguments_model=CreateCalendarEventArguments,
        action=ActionKind.CREATE,
        capability_id="calendar.write",
        resource_kinds=(ResourceKind.CALENDAR_EVENT,),
        prepare=_prepare_create,
        execute=_create,
        writes_state=True,
    ),
    ToolDefinition(
        name="update_calendar_event",
        arguments_model=UpdateCalendarEventArguments,
        action=ActionKind.UPDATE,
        capability_id="calendar.write",
        resource_kinds=(ResourceKind.CALENDAR_EVENT,),
        prepare=_prepare_update,
        execute=_update,
        writes_state=True,
    ),
    ToolDefinition(
        name="cancel_calendar_event",
        arguments_model=CancelCalendarEventArguments,
        action=ActionKind.DELETE,
        capability_id="calendar.write",
        resource_kinds=(ResourceKind.CALENDAR_EVENT,),
        prepare=_prepare_cancel,
        execute=_cancel,
        writes_state=True,
    ),
)


__all__ = [
    "CancelCalendarEventArguments",
    "CreateCalendarEventArguments",
    "DEFINITIONS",
    "SearchCalendarArguments",
    "UpdateCalendarEventArguments",
]
