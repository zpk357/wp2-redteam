from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import (
    CLEAN_CASE_BY_ID,
    CLEAN_CASE_SEEDS,
    CleanCaseMaterialization,
    _resolve_bindings,
)
from sandbox.scenarios.office_v2.models import CalendarEventStatus
from sandbox.scenarios.office_v2.task_catalog import TASK_BLUEPRINT_BY_ID
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld, OfficeDomainGraph, StateTransitionRecord
from tests.integration.test_office_v2_causal_chains import (
    FROZEN_WORLD_DIGEST,
    ReferenceClient,
    SearchHit,
    _hit_field,
)

Overlay = Callable[[OfficeDomainGraph, CleanCaseMaterialization], OfficeDomainGraph]
Probe = Callable[[ReferenceClient], tuple[object, ...]]


def _bound_id(case: CleanCaseMaterialization, query_key: str) -> str:
    binding = next(
        item for item in case.resolved_bindings if item.query_id.endswith(f".{query_key}")
    )
    return binding.resource_refs[0].resource_id


def _mail_attachments(
    graph: OfficeDomainGraph, case: CleanCaseMaterialization
) -> OfficeDomainGraph:
    message_id = _bound_id(case, "delivery-mail")
    mail = graph.mail.model_copy(
        update={
            "messages": tuple(
                item.model_copy(update={"attachment_refs": item.attachment_refs[:-1]})
                if item.message_id == message_id
                else item
                for item in graph.mail.messages
            )
        }
    )
    return graph.model_copy(update={"mail": mail})


def _current_version(
    graph: OfficeDomainGraph, case: CleanCaseMaterialization
) -> OfficeDomainGraph:
    file_id = _bound_id(case, "review-plan")
    file = next(item for item in graph.drive.files if item.file_id == file_id)
    current = next(
        item for item in graph.drive.versions if item.version_id == file.current_version_id
    )
    replacement = current.model_copy(
        update={
            "version_id": f"{current.version_id}.overlay",
            "content": current.content + "\nOverlay revision",
            "created_at": current.created_at + timedelta(minutes=1),
        }
    )
    drive = graph.drive.model_copy(
        update={
            "files": tuple(
                item.model_copy(update={"current_version_id": replacement.version_id})
                if item.file_id == file_id
                else item
                for item in graph.drive.files
            ),
            "versions": (*graph.drive.versions, replacement),
        }
    )
    return graph.model_copy(update={"drive": drive})


def _roster_content(
    graph: OfficeDomainGraph, case: CleanCaseMaterialization
) -> OfficeDomainGraph:
    file_id = _bound_id(case, "roster")
    file = next(item for item in graph.drive.files if item.file_id == file_id)
    drive = graph.drive.model_copy(
        update={
            "versions": tuple(
                item.model_copy(update={"content": item.content + "\nObserver: added"})
                if item.version_id == file.current_version_id
                else item
                for item in graph.drive.versions
            )
        }
    )
    return graph.model_copy(update={"drive": drive})


def _time_slot(graph: OfficeDomainGraph, case: CleanCaseMaterialization) -> OfficeDomainGraph:
    event_id = _bound_id(case, "planning-event")
    calendar = graph.calendar.model_copy(
        update={
            "events": tuple(
                item.model_copy(
                    update={
                        "start_at": item.start_at + timedelta(hours=2),
                        "end_at": item.end_at + timedelta(hours=2),
                    }
                )
                if item.event_id == event_id
                else item
                for item in graph.calendar.events
            )
        }
    )
    return graph.model_copy(update={"calendar": calendar})


def _conflict_status(
    graph: OfficeDomainGraph, case: CleanCaseMaterialization
) -> OfficeDomainGraph:
    binding = next(
        item
        for item in case.resolved_bindings
        if item.query_id.endswith(".calendar-set")
    )
    conflict_id = binding.resource_refs[1].resource_id
    calendar = graph.calendar.model_copy(
        update={
            "events": tuple(
                item.model_copy(update={"status": CalendarEventStatus.CANCELLED})
                if item.event_id == conflict_id
                else item
                for item in graph.calendar.events
            )
        }
    )
    return graph.model_copy(update={"calendar": calendar})


def _participants(graph: OfficeDomainGraph, case: CleanCaseMaterialization) -> OfficeDomainGraph:
    event_id = _bound_id(case, "review-event")
    event = next(item for item in graph.calendar.events if item.event_id == event_id)
    removed = event.attendee_ids[-1]
    calendar = graph.calendar.model_copy(
        update={
            "events": tuple(
                item.model_copy(update={"attendee_ids": item.attendee_ids[:-1]})
                if item.event_id == event_id
                else item
                for item in graph.calendar.events
            ),
            "attendance": tuple(
                item
                for item in graph.calendar.attendance
                if not (item.event_id == event_id and item.principal_id == removed)
            ),
        }
    )
    return graph.model_copy(update={"calendar": calendar})


def _episode(
    case: CleanCaseMaterialization, *, overlay: Overlay | None
) -> tuple[EpisodeWorld, StateTransitionRecord | None]:
    episode = EpisodeWorld(load_canonical_world(), episode_id=f"perturb-{case.case_id}")
    if overlay is None:
        return episode, None
    transaction = episode.begin_transaction(
        action_request_id=f"overlay.{case.case_id}",
        policy_decision_id=f"overlay-policy.{case.case_id}",
    )
    transaction.replace_domain_graph(overlay(transaction.staged_state.domain_graph, case))
    return episode, transaction.commit()


def _client(case: CleanCaseMaterialization, episode: EpisodeWorld) -> ReferenceClient:
    seed = next(item for item in CLEAN_CASE_SEEDS if item.case_id == case.case_id)
    blueprint = TASK_BLUEPRINT_BY_ID[case.blueprint_id]
    bindings, _, _ = _resolve_bindings(
        episode.state,
        case.actor,
        seed,
        blueprint,
        case.task.resource_queries,
    )
    runtime = OfficeV2ToolRuntime(
        episode=episode,
        actor=case.actor,
        task=case.task,
        definitions=office_v2_tool_definitions(),
        bindings=bindings,
    )
    return ReferenceClient(case, runtime, initial_state_digest=episode.state_digest)


def _read_hit(client: ReferenceClient, hit: SearchHit, tool_name: str) -> object:
    id_field = "message_id" if tool_name == "read_email" else "file_id"
    path = ("resource", "resource_id") if tool_name == "read_email" else ("file_id",)
    resource_id = _hit_field(client, hit, *path)
    arguments = {id_field: resource_id.value}
    sources = {(id_field,): resource_id}
    if tool_name == "read_drive_file":
        version = _hit_field(client, hit, "current_version_id")
        arguments["version_id"] = version.value
        sources[("version_id",)] = version
    return client.invoke(tool_name, arguments, sources=sources)


def _probe_attachments(client: ReferenceClient) -> tuple[object, ...]:
    hit = client.find_binding("search_email", "delivery-mail")
    delivery = _read_hit(client, hit, "read_email")
    assert hasattr(delivery, "visible_output")
    refs = delivery.visible_output["related_refs"]
    assert isinstance(refs, list)
    names = []
    for index in range(len(refs)):
        file_id = client.field(delivery, ("related_refs", str(index), "resource_id"))
        version_id = client.field(delivery, ("related_refs", str(index), "version_id"))
        item = client.invoke(
            "read_drive_file",
            {"file_id": file_id.value, "version_id": version_id.value},
            sources={("file_id",): file_id, ("version_id",): version_id},
        )
        names.append(item.visible_output["name"])
    return tuple(names)


def _probe_drive(client: ReferenceClient, query_key: str) -> tuple[object, ...]:
    hit = client.find_binding("search_drive_files", query_key)
    result = _read_hit(client, hit, "read_drive_file")
    assert hasattr(result, "visible_output")
    return (
        result.visible_output["version_id"],
        sha256_digest(result.visible_output["content"]),
    )


def _probe_time_slot(client: ReferenceClient) -> tuple[object, ...]:
    hit = client.find_binding("search_calendar_events", "planning-event")
    start = _hit_field(client, hit, "start_at")
    end = _hit_field(client, hit, "end_at")
    timezone = _hit_field(client, hit, "timezone")
    attendees = _hit_field(client, hit, "attendee_ids")
    title = client.derived(f"Follow-up at {start.value}", start)
    created = client.invoke(
        "create_calendar_event",
        {
            "title": title.value,
            "start_at": start.value,
            "end_at": end.value,
            "timezone": timezone.value,
            "attendees": attendees.value,
        },
        sources={
            ("title",): title,
            ("start_at",): start,
            ("end_at",): end,
            ("timezone",): timezone,
            ("attendees",): attendees,
        },
    )
    return (created.visible_output["start_at"], created.visible_output["end_at"])


def _probe_conflicts(client: ReferenceClient) -> tuple[object, ...]:
    binding = next(
        item
        for item in client.case.resolved_bindings
        if item.query_id.endswith(".calendar-set")
    )
    statuses = []
    for resource in binding.resource_refs:
        result = client.invoke(
            "search_calendar_events",
            {"text": resource.resource_id.split(".")[1]},
        )
        items = result.visible_output["items"]
        assert isinstance(items, list)
        match = next(item for item in items if item["event_id"] == resource.resource_id)
        statuses.append(match["status"])
    return tuple(statuses)


def _probe_participants(client: ReferenceClient) -> tuple[object, ...]:
    hit = client.find_binding("search_calendar_events", "review-event")
    attendees = _hit_field(client, hit, "attendee_ids")
    assert isinstance(attendees.value, list)
    summary = client.derived(
        f"Confirmed participants: {', '.join(attendees.value)}", attendees
    )
    return (tuple(attendees.value), sha256_digest(summary.value))


PERTURBATIONS: tuple[tuple[str, str, Overlay, Probe], ...] = (
    ("attachment_relation", "clean.t8.borealis", _mail_attachments, _probe_attachments),
    (
        "current_version",
        "clean.t1.apollo",
        _current_version,
        lambda client: _probe_drive(client, "review-plan"),
    ),
    (
        "roster",
        "clean.t2.delta",
        _roster_content,
        lambda client: _probe_drive(client, "roster"),
    ),
    ("time_slot", "clean.t10.borealis", _time_slot, _probe_time_slot),
    ("conflict", "clean.t1.apollo", _conflict_status, _probe_conflicts),
    ("participants", "clean.t9.delta", _participants, _probe_participants),
)


@pytest.mark.parametrize(("dimension", "case_id", "overlay", "probe"), PERTURBATIONS)
def test_single_variable_episode_perturbations_change_downstream_facts(
    dimension: str,
    case_id: str,
    overlay: Overlay,
    probe: Probe,
) -> None:
    case = CLEAN_CASE_BY_ID[case_id]
    parent_case_digest = case.canonical_digest()
    baseline_episode, _ = _episode(case, overlay=None)
    perturbed_episode, transition = _episode(case, overlay=overlay)

    baseline = probe(_client(case, baseline_episode))
    perturbed = probe(_client(case, perturbed_episode))

    assert baseline != perturbed, dimension
    assert transition is not None and transition.committed is True
    assert not transition.state_delta.is_empty()
    assert case.canonical_digest() == parent_case_digest
    assert baseline_episode.base_world_digest == perturbed_episode.base_world_digest
    assert load_canonical_world().world_digest == FROZEN_WORLD_DIGEST
