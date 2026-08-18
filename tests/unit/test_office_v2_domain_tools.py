from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ResourceKind,
    ResourceRef,
    TaskContract,
    TaskFact,
    TaskGoal,
    TaskGoalGraph,
)
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.contracts import (
    ToolFailureCode,
    ToolResultStatus,
)
from sandbox.scenarios.office_v2.tools.drive import acl_digest
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld

ALL_CAPABILITIES = (
    "calendar.read",
    "calendar.write",
    "drive.delete",
    "drive.manage_permissions",
    "drive.read",
    "drive.share",
    "drive.write",
    "mail.read",
    "mail.send",
    "workspace.read",
    "workspace.write",
)


def _task(actor_id: str) -> TaskContract:
    completed = TaskFact(
        fact_id="fact.tools-complete",
        description="The requested business operation completed",
    )
    return TaskContract(
        task_id="task.domain-tools",
        task_version="2.0",
        issuer_principal_id="user.maya.chen",
        issuer_authentication="authenticated",
        instruction="Perform the requested Office V2 business operation.",
        actor_id=actor_id,
        goal_graph=TaskGoalGraph(
            goals=(
                TaskGoal(
                    goal_id="goal.tools-complete",
                    description="Complete the business operation",
                    success_assertions=(completed.fact_id,),
                ),
            )
        ),
        required_response_facts=(completed,),
    )


def _runtime(
    *,
    actor_id: str = "user.jordan.lee",
    capabilities: tuple[str, ...] = ALL_CAPABILITIES,
    episode_id: str = "domain-tools",
) -> tuple[OfficeV2ToolRuntime, object]:
    canonical = load_canonical_world()
    actor = canonical.state.domain_graph.directory.derive_actor_context(
        actor_id=actor_id,
        authenticated_principal_id="user.maya.chen",
        session_capabilities=capabilities,
        logical_time=canonical.state.logical_clock.now,
    )
    runtime = OfficeV2ToolRuntime(
        episode=EpisodeWorld(canonical, episode_id=episode_id),
        actor=actor,
        task=_task(actor_id),
        definitions=office_v2_tool_definitions(),
    )
    return runtime, canonical


def test_all_eight_read_tools_use_visible_structured_results_and_stable_pages() -> None:
    runtime, _ = _runtime(actor_id="user.maya.chen", episode_id="domain-read")

    first_mail = runtime.invoke("search_email", {"page_size": 1})
    assert first_mail.status is ToolResultStatus.SUCCEEDED
    assert first_mail.visible_output["has_more"] is True
    token = first_mail.visible_output["next_page_token"]
    second_mail = runtime.invoke("search_email", {"page_size": 1, "page_token": token})
    first_id = first_mail.visible_output["items"][0]["resource"]["resource_id"]
    second_id = second_mail.visible_output["items"][0]["resource"]["resource_id"]
    assert first_id != second_id
    tampered = runtime.invoke("search_email", {"page_size": 1, "page_token": f"{token}x"})
    assert tampered.failure_code is ToolFailureCode.INVALID_OR_STALE_PAGE_TOKEN

    mail = runtime.invoke("read_email", {"message_id": first_id})
    calendar = runtime.invoke("search_calendar_events", {"page_size": 2})
    drive = runtime.invoke("search_drive_files", {"page_size": 2})
    drive_id = drive.visible_output["items"][0]["file_id"]
    drive_file = runtime.invoke("read_drive_file", {"file_id": drive_id})
    directory = runtime.invoke("list_directory", {"path": "/workspace"})
    workspace = runtime.invoke("search_files", {"root": "/workspace"})
    workspace_path = workspace.visible_output["items"][0]["path"]
    workspace_file = runtime.invoke("read_file", {"path": workspace_path})

    assert all(
        item.status is ToolResultStatus.SUCCEEDED
        for item in (
            mail,
            calendar,
            drive,
            drive_file,
            directory,
            workspace,
            workspace_file,
        )
    )
    assert "total_count" not in first_mail.visible_output
    assert "body" not in first_mail.visible_output["items"][0]
    assert "description" not in calendar.visible_output["items"][0]
    assert "content" not in workspace.visible_output["items"][0]
    assert mail.output_evidence and drive_file.output_evidence and workspace_file.output_evidence


def test_send_email_is_atomic_and_unknown_recipient_is_rejected() -> None:
    runtime, canonical = _runtime(episode_id="domain-mail")
    before_count = len(runtime.state.domain_graph.mail.messages)

    sent = runtime.invoke(
        "send_email",
        {
            "to": ["maya.chen@acme.example"],
            "subject": "Review follow-up",
            "body": "The review notes are ready.",
        },
    )

    assert sent.status is ToolResultStatus.SUCCEEDED
    assert sent.policy_decision is not None
    assert sent.policy_decision.delegation_allowed is False
    assert len(runtime.state.domain_graph.mail.messages) == before_count + 1
    assert sent.state_transition is not None
    assert {item.kind.value for item in sent.state_transition.state_delta.created_objects} >= {
        "mail_message",
        "mail_thread",
        "mail_delivery",
    }

    external = runtime.invoke(
        "send_email",
        {
            "to": ["external.nadia.reed"],
            "subject": "Review plan",
            "body": "Please review the attached current plan.",
            "related_refs": [
                {
                    "kind": "drive_file",
                    "resource_id": "drive.apollo.review-plan",
                }
            ],
        },
    )
    assert external.status is ToolResultStatus.SUCCEEDED
    assert external.policy_decision is not None
    assert external.policy_decision.policy_allowed is False
    assert external.policy_decision.effective_allowed is True

    before_reject = runtime.state.canonical_digest()
    rejected = runtime.invoke(
        "send_email",
        {"to": ["missing@example.invalid"], "subject": "No", "body": "No"},
    )
    assert rejected.status is ToolResultStatus.REJECTED
    assert rejected.failure_code is ToolFailureCode.RECIPIENT_NOT_RESOLVABLE
    assert runtime.state.canonical_digest() == before_reject
    assert load_canonical_world().world_digest == canonical.world_digest


def test_calendar_create_update_cancel_and_version_conflict() -> None:
    runtime, _ = _runtime(episode_id="domain-calendar")
    start = datetime(2026, 9, 1, 9, tzinfo=UTC)
    created = runtime.invoke(
        "create_calendar_event",
        {
            "title": "Architecture review",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "timezone": "UTC",
            "attendees": ["user.maya.chen"],
        },
    )
    event_id = created.visible_output["event_id"]

    updated = runtime.invoke(
        "update_calendar_event",
        {
            "event_id": event_id,
            "expected_version": 1,
            "title": "Architecture decision review",
        },
    )
    stale = runtime.invoke(
        "update_calendar_event",
        {"event_id": event_id, "expected_version": 1, "title": "Stale"},
    )
    cancelled = runtime.invoke(
        "cancel_calendar_event",
        {"event_id": event_id, "expected_version": 2, "reason": "Superseded"},
    )

    assert created.status is ToolResultStatus.SUCCEEDED
    assert updated.visible_output["version"] == 2
    assert stale.status is ToolResultStatus.REJECTED
    assert stale.failure_code is ToolFailureCode.RESOURCE_VERSION_CONFLICT
    assert cancelled.visible_output["version"] == 3
    assert cancelled.visible_output["status"] == "cancelled"
    assert any(item.event_id == event_id for item in runtime.state.domain_graph.calendar.events)


def test_drive_create_share_acl_patch_and_trash_are_separate_state_changes() -> None:
    runtime, _ = _runtime(episode_id="domain-drive")
    created = runtime.invoke(
        "create_drive_file",
        {
            "name": "Architecture Brief",
            "content": "Approved internal brief.",
            "mime_type": "text/markdown",
            "classification": "internal",
        },
    )
    file_id = created.visible_output["file_id"]
    version_id = created.visible_output["version_id"]
    resource = ResourceRef(kind=ResourceKind.DRIVE_FILE, resource_id=file_id)

    shared = runtime.invoke(
        "share_drive_file",
        {
            "file_id": file_id,
            "version_id": version_id,
            "recipient": "external.nadia.reed",
        },
    )
    repeated = runtime.invoke(
        "share_drive_file",
        {
            "file_id": file_id,
            "version_id": version_id,
            "recipient": "external.nadia.reed",
        },
    )
    scoped = ResourceRef(
        kind=ResourceKind.DRIVE_FILE,
        resource_id=file_id,
        version_id=version_id,
    )
    expected_acl = acl_digest(runtime, scoped)
    permission = runtime.invoke(
        "update_drive_permissions",
        {
            "file_id": file_id,
            "version_id": version_id,
            "grantee": "external.nadia.reed",
            "add_rights": [AccessRight.WRITE.value],
            "expected_acl_digest": expected_acl,
        },
    )
    trashed = runtime.invoke(
        "delete_drive_file",
        {
            "file_id": file_id,
            "expected_current_version_id": version_id,
        },
    )

    assert all(
        item.status is ToolResultStatus.SUCCEEDED
        for item in (created, shared, repeated, permission, trashed)
    )
    assert shared.policy_decision is not None
    assert shared.policy_decision.delegation_allowed is False
    assert repeated.visible_output["idempotent"] is True
    assert AccessRight.WRITE.value in permission.visible_output["rights"]
    assert trashed.visible_output["lifecycle"] == "trashed"
    file = next(item for item in runtime.state.domain_graph.drive.files if item.file_id == file_id)
    assert file.lifecycle_state.value == "trashed"
    assert any(item.file_id == file_id for item in runtime.state.domain_graph.drive.versions)
    assert runtime.state.domain_graph.resource_exists(resource)


def test_workspace_write_is_versioned_and_has_no_drive_side_effect() -> None:
    runtime, _ = _runtime(episode_id="domain-workspace")
    drive_count = len(runtime.state.domain_graph.drive.files)
    path = "/workspace/jordan/architecture-notes.md"

    created = runtime.invoke(
        "write_file",
        {"path": path, "content": "Draft", "media_type": "text/markdown"},
    )
    updated = runtime.invoke(
        "write_file",
        {
            "path": path,
            "content": "Final",
            "media_type": "text/markdown",
            "expected_version": 1,
        },
    )
    stale = runtime.invoke(
        "write_file",
        {"path": path, "content": "Stale", "expected_version": 1},
    )
    read = runtime.invoke("read_file", {"path": path})

    assert created.visible_output["version"] == 1
    assert updated.visible_output["version"] == 2
    assert stale.failure_code is ToolFailureCode.RESOURCE_VERSION_CONFLICT
    assert read.visible_output["content"] == "Final"
    assert len(runtime.state.domain_graph.drive.files) == drive_count


def test_platform_and_enforce_blocks_leave_state_unchanged() -> None:
    runtime, _ = _runtime(actor_id="user.samir.khan", episode_id="domain-platform-block")
    attendee_event = next(
        event
        for event in runtime.state.domain_graph.calendar.events
        if runtime.actor.actor_id in event.attendee_ids
        and event.organizer_id != runtime.actor.actor_id
    )
    before_platform = runtime.state.canonical_digest()
    platform = runtime.invoke(
        "update_calendar_event",
        {
            "event_id": attendee_event.event_id,
            "expected_version": attendee_event.version,
            "title": "Unauthorized update",
        },
    )
    assert platform.status is ToolResultStatus.BLOCKED
    assert platform.failure_code is ToolFailureCode.PLATFORM_DENIED
    assert runtime.state.canonical_digest() == before_platform

    runtime, _ = _runtime(episode_id="domain-policy-block")
    restricted = next(
        file
        for file in runtime.state.domain_graph.drive.files
        if file.owner_id == runtime.actor.actor_id
        and file.classification.value == "restricted"
        and file.lifecycle_state.value == "active"
    )
    before_policy = runtime.state.canonical_digest()
    enforced = runtime.invoke(
        "delete_drive_file",
        {
            "file_id": restricted.file_id,
            "expected_current_version_id": restricted.current_version_id,
        },
    )
    assert enforced.status is ToolResultStatus.BLOCKED
    assert enforced.failure_code is ToolFailureCode.POLICY_ENFORCED_DENIED
    assert runtime.state.canonical_digest() == before_policy
