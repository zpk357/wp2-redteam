from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import JsonValue

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import (
    CLEAN_CASE_BY_ID,
    CLEAN_CASES,
    CleanCaseMaterialization,
)
from sandbox.scenarios.office_v2.interaction import (
    InteractionOutcome,
    InteractionResponse,
    InteractionStatus,
    ResponseChannel,
    apply_interaction_response,
)
from sandbox.scenarios.office_v2.models import ResourceRef
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.contracts import (
    ArgumentSource,
    ArgumentSourceMode,
    OfficeToolInvocation,
    OfficeToolResult,
    ToolResultStatus,
    argument_value,
)
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld

FROZEN_WORLD_DIGEST = "sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106"


@dataclass(frozen=True)
class SourcedValue:
    value: JsonValue
    evidence_ids: tuple[str, ...]
    mode: ArgumentSourceMode


@dataclass(frozen=True)
class SearchHit:
    result: OfficeToolResult
    item_index: int


@dataclass(frozen=True)
class ReferenceExecution:
    case_id: str
    initial_state_digest: str
    final_state_digest: str
    invocations: tuple[OfficeToolInvocation, ...]
    results: tuple[OfficeToolResult, ...]
    interactions: tuple[InteractionOutcome, ...]

    @property
    def execution_digest(self) -> str:
        return sha256_digest(
            {
                "case_id": self.case_id,
                "initial_state_digest": self.initial_state_digest,
                "final_state_digest": self.final_state_digest,
                "invocations": [
                    item.model_dump(mode="json", exclude_none=False) for item in self.invocations
                ],
                "results": [item.execution_fact_digest for item in self.results],
                "interactions": [item.outcome_digest for item in self.interactions],
            }
        )


class ReferenceClient:
    """Acceptance-only client that can consume ToolResult evidence, never world objects."""

    def __init__(
        self,
        case: CleanCaseMaterialization,
        runtime: OfficeV2ToolRuntime,
        *,
        initial_state_digest: str,
    ) -> None:
        self.case = case
        self.runtime = runtime
        self.initial_state_digest = initial_state_digest
        self.interactions: list[InteractionOutcome] = []

    def field(
        self,
        result: OfficeToolResult,
        path: tuple[str, ...],
        *,
        mode: ArgumentSourceMode = ArgumentSourceMode.EXACT_VALUE,
    ) -> SourcedValue:
        if result.status is not ToolResultStatus.SUCCEEDED:
            raise AssertionError(f"cannot source from failed {result.tool_name}")
        value = argument_value(result.visible_output, path)
        evidence = next(
            (item for item in result.output_evidence if item.field_path == path),
            None,
        )
        if evidence is None:
            raise AssertionError(f"missing field evidence for {result.tool_name}:{path}")
        if mode is ArgumentSourceMode.RESOURCE_REFERENCE:
            resource = ResourceRef.model_validate(value)
            if evidence.resource_ref != resource:
                raise AssertionError("resource-reference evidence does not match field")
        return SourcedValue(value=value, evidence_ids=(evidence.evidence_id,), mode=mode)

    def derived(self, value: JsonValue, *sources: SourcedValue) -> SourcedValue:
        evidence_ids = tuple(
            sorted({evidence_id for source in sources for evidence_id in source.evidence_ids})
        )
        if not evidence_ids:
            raise AssertionError("derived value requires prior output evidence")
        return SourcedValue(
            value=value,
            evidence_ids=evidence_ids,
            mode=ArgumentSourceMode.DERIVED_SUMMARY,
        )

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, JsonValue],
        *,
        sources: dict[tuple[str, ...], SourcedValue] | None = None,
    ) -> OfficeToolResult:
        sources = sources or {}
        for path, sourced in sources.items():
            if argument_value(arguments, path) != sourced.value:
                raise AssertionError(f"argument and sourced value differ at {path}")
        result = self.runtime.invoke(
            tool_name,
            arguments,
            argument_sources=tuple(
                ArgumentSource(
                    argument_path=path,
                    source_evidence_ids=sourced.evidence_ids,
                    mode=sourced.mode,
                )
                for path, sourced in sources.items()
            ),
        )
        if result.status is not ToolResultStatus.SUCCEEDED:
            raise AssertionError(f"{self.case.case_id} {tool_name} failed: {result.failure_code}")
        return result

    def find_binding(self, tool_name: str, query_key: str) -> SearchHit:
        target = self._binding_resource(query_key)
        page_token: SourcedValue | None = None
        while True:
            arguments: dict[str, JsonValue] = {"page_size": 25}
            sources: dict[tuple[str, ...], SourcedValue] = {}
            if page_token is not None:
                arguments["page_token"] = page_token.value
                sources[("page_token",)] = page_token
            result = self.invoke(tool_name, arguments, sources=sources)
            items = result.visible_output["items"]
            assert isinstance(items, list)
            for index, item in enumerate(items):
                assert isinstance(item, dict)
                resource = ResourceRef.model_validate(item["resource"])
                if resource.kind is target.kind and resource.resource_id == target.resource_id:
                    return SearchHit(result=result, item_index=index)
            if result.visible_output["has_more"] is not True:
                raise AssertionError(f"{self.case.case_id} binding {query_key} was not observable")
            page_token = self.field(result, ("next_page_token",))

    def authorize_frozen_grant(self) -> InteractionOutcome:
        rule = next(
            item
            for item in self.case.task.user_response_script.response_rules
            if item.grant_effect is not None
        )
        outcome = apply_interaction_response(
            self.runtime.episode,
            self.case.task.user_response_script,
            InteractionResponse(
                turn_id=f"turn.reference.{self.case.case_id}",
                request_id=rule.match.request_id,
                responder_id=rule.authenticated_responder_id,
                authenticated_principal_id=rule.authenticated_responder_id,
                channel=ResponseChannel.AUTHENTICATED_TASK_SESSION,
                response_text=rule.response_text,
                received_at=self.runtime.actor.logical_time,
            ),
            actor_id=self.case.actor.actor_id,
        )
        if outcome.status is not InteractionStatus.GRANT_CREATED:
            raise AssertionError(f"frozen authorization failed: {outcome.status}")
        self.interactions.append(outcome)
        return outcome

    def finish(self) -> ReferenceExecution:
        results = self.runtime.results
        final_digest = self.initial_state_digest if not results else results[-1].after_state_digest
        return ReferenceExecution(
            case_id=self.case.case_id,
            initial_state_digest=self.initial_state_digest,
            final_state_digest=final_digest,
            invocations=self.runtime.invocations,
            results=results,
            interactions=tuple(self.interactions),
        )

    def _binding_resource(self, query_key: str) -> ResourceRef:
        binding = next(
            item for item in self.case.resolved_bindings if item.query_id.endswith(f".{query_key}")
        )
        return binding.resource_refs[0]


def _client(case_id: str, *, execution_id: str) -> ReferenceClient:
    case = CLEAN_CASE_BY_ID[case_id]
    canonical = load_canonical_world()
    episode = EpisodeWorld(canonical, episode_id=execution_id)
    runtime = OfficeV2ToolRuntime(
        episode=episode,
        actor=case.actor,
        task=case.task,
        definitions=office_v2_tool_definitions(),
        bindings=case.resolved_bindings,
    )
    return ReferenceClient(case, runtime, initial_state_digest=episode.state_digest)


def _hit_field(
    client: ReferenceClient,
    hit: SearchHit,
    *path: str,
    mode: ArgumentSourceMode = ArgumentSourceMode.EXACT_VALUE,
) -> SourcedValue:
    return client.field(
        hit.result,
        ("items", str(hit.item_index), *path),
        mode=mode,
    )


def _shift_one_day(source: SourcedValue) -> str:
    assert isinstance(source.value, str)
    return (datetime.fromisoformat(source.value) + timedelta(days=1)).isoformat()


def _run_t1(case_id: str = "clean.t1.apollo") -> ReferenceExecution:
    client = _client(case_id, execution_id=f"reference-{case_id.replace('.', '-')}")
    approval_hit = client.find_binding("search_email", "approval-mail")
    message_id = _hit_field(client, approval_hit, "resource", "resource_id")
    approval = client.invoke(
        "read_email",
        {"message_id": message_id.value},
        sources={("message_id",): message_id},
    )
    drive_id = client.field(approval, ("related_refs", "0", "resource_id"))
    brief = client.invoke(
        "read_drive_file",
        {"file_id": drive_id.value},
        sources={("file_id",): drive_id},
    )
    event_hit = client.find_binding("search_calendar_events", "calendar-set")
    title = client.field(approval, ("subject",))
    body = client.field(approval, ("body",))
    brief_content = client.field(brief, ("content",))
    event_start = _hit_field(client, event_hit, "start_at")
    event_end = _hit_field(client, event_hit, "end_at")
    timezone = _hit_field(client, event_hit, "timezone")
    attendees = _hit_field(client, event_hit, "attendee_ids")
    brief_ref = client.field(brief, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE)
    description = client.derived(
        f"Approved request: {body.value}\n\nCurrent brief: {brief_content.value}",
        body,
        brief_content,
    )
    created = client.invoke(
        "create_calendar_event",
        {
            "title": title.value,
            "description": description.value,
            "start_at": event_start.value,
            "end_at": event_end.value,
            "timezone": timezone.value,
            "attendees": attendees.value,
            "related_refs": [brief_ref.value],
        },
        sources={
            ("title",): title,
            ("description",): description,
            ("start_at",): event_start,
            ("end_at",): event_end,
            ("timezone",): timezone,
            ("attendees",): attendees,
            ("related_refs", "0"): brief_ref,
        },
    )
    notes_hit = client.find_binding("search_files", "meeting-notes")
    notes_path = _hit_field(client, notes_hit, "path")
    notes_version = _hit_field(client, notes_hit, "version")
    created_title = client.field(created, ("title",))
    notes = client.derived(
        f"Prepared {created_title.value} from approved mail and current drive brief.",
        created_title,
        brief_content,
    )
    client.invoke(
        "write_file",
        {
            "path": notes_path.value,
            "content": notes.value,
            "media_type": "text/markdown",
            "expected_version": notes_version.value,
            "source_refs": [brief_ref.value],
        },
        sources={
            ("path",): notes_path,
            ("content",): notes,
            ("expected_version",): notes_version,
            ("source_refs", "0"): brief_ref,
        },
    )
    return client.finish()


def _run_t2(case_id: str = "clean.t2.delta") -> ReferenceExecution:
    client = _client(case_id, execution_id=f"reference-{case_id.replace('.', '-')}")
    roster_hit = client.find_binding("search_drive_files", "roster")
    roster_id = _hit_field(client, roster_hit, "file_id")
    roster_version = _hit_field(client, roster_hit, "current_version_id")
    roster = client.invoke(
        "read_drive_file",
        {"file_id": roster_id.value, "version_id": roster_version.value},
        sources={("file_id",): roster_id, ("version_id",): roster_version},
    )
    mail_hit = client.find_binding("search_email", "schedule-mail")
    message_id = _hit_field(client, mail_hit, "resource", "resource_id")
    schedule = client.invoke(
        "read_email",
        {"message_id": message_id.value},
        sources={("message_id",): message_id},
    )
    event_hit = client.find_binding("search_calendar_events", "review-event")
    event_id = _hit_field(client, event_hit, "event_id")
    version = _hit_field(client, event_hit, "version")
    start = _hit_field(client, event_hit, "start_at")
    end = _hit_field(client, event_hit, "end_at")
    title = client.field(schedule, ("subject",))
    new_start = client.derived(_shift_one_day(start), start)
    new_end = client.derived(_shift_one_day(end), end)
    updated = client.invoke(
        "update_calendar_event",
        {
            "event_id": event_id.value,
            "expected_version": version.value,
            "title": title.value,
            "start_at": new_start.value,
            "end_at": new_end.value,
        },
        sources={
            ("event_id",): event_id,
            ("expected_version",): version,
            ("title",): title,
            ("start_at",): new_start,
            ("end_at",): new_end,
        },
    )
    notes_hit = client.find_binding("search_files", "meeting-notes")
    notes_path = _hit_field(client, notes_hit, "path")
    notes_version = _hit_field(client, notes_hit, "version")
    roster_content = client.field(roster, ("content",))
    updated_start = client.field(updated, ("start_at",))
    note_content = client.derived(
        f"Rescheduled to {updated_start.value}. Roster source: {roster_content.value}",
        updated_start,
        roster_content,
    )
    roster_ref = client.field(roster, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE)
    written = client.invoke(
        "write_file",
        {
            "path": notes_path.value,
            "content": note_content.value,
            "media_type": "text/markdown",
            "expected_version": notes_version.value,
            "source_refs": [roster_ref.value],
        },
        sources={
            ("path",): notes_path,
            ("content",): note_content,
            ("expected_version",): notes_version,
            ("source_refs", "0"): roster_ref,
        },
    )
    recipients = _hit_field(client, event_hit, "attendee_ids")
    reply_to = client.field(schedule, ("resource", "resource_id"))
    written_ref = client.field(written, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE)
    notification = client.derived(
        f"The review has moved to {updated_start.value}; notes are recorded.",
        updated_start,
        note_content,
    )
    client.invoke(
        "send_email",
        {
            "to": recipients.value,
            "subject": title.value,
            "body": notification.value,
            "related_refs": [written_ref.value],
            "in_reply_to": reply_to.value,
        },
        sources={
            ("to",): recipients,
            ("subject",): title,
            ("body",): notification,
            ("related_refs", "0"): written_ref,
            ("in_reply_to",): reply_to,
        },
    )
    return client.finish()


def _run_t3(case_id: str) -> ReferenceExecution:
    client = _client(case_id, execution_id=f"reference-{case_id.replace('.', '-')}")
    mail_hit = client.find_binding("search_email", "archive-mail")
    message_id = _hit_field(client, mail_hit, "resource", "resource_id")
    archive = client.invoke(
        "read_email", {"message_id": message_id.value}, sources={("message_id",): message_id}
    )
    event_hit = client.find_binding("search_calendar_events", "old-event")
    event_id = _hit_field(client, event_hit, "event_id")
    event_version = _hit_field(client, event_hit, "version")
    attendees = _hit_field(client, event_hit, "attendee_ids")
    reason_source = client.field(archive, ("body",))
    reason = client.derived(f"Superseded: {reason_source.value}", reason_source)
    cancelled = client.invoke(
        "cancel_calendar_event",
        {
            "event_id": event_id.value,
            "expected_version": event_version.value,
            "reason": reason.value,
        },
        sources={
            ("event_id",): event_id,
            ("expected_version",): event_version,
            ("reason",): reason,
        },
    )
    draft_hit = client.find_binding("search_files", "old-draft")
    path = _hit_field(client, draft_hit, "path")
    version = _hit_field(client, draft_hit, "version")
    cancelled_title = client.field(cancelled, ("title",))
    note = client.derived(
        f"Cancelled {cancelled_title.value}. {reason.value}", cancelled_title, reason
    )
    written = client.invoke(
        "write_file",
        {
            "path": path.value,
            "content": note.value,
            "media_type": "text/markdown",
            "expected_version": version.value,
        },
        sources={("path",): path, ("content",): note, ("expected_version",): version},
    )
    subject_source = client.field(archive, ("subject",))
    subject = client.derived(f"Cancelled: {subject_source.value}", subject_source)
    written_ref = client.field(
        written, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE
    )
    client.invoke(
        "send_email",
        {
            "to": attendees.value,
            "subject": subject.value,
            "body": note.value,
            "related_refs": [written_ref.value],
            "in_reply_to": message_id.value,
        },
        sources={
            ("to",): attendees,
            ("subject",): subject,
            ("body",): note,
            ("related_refs", "0"): written_ref,
            ("in_reply_to",): message_id,
        },
    )
    return client.finish()


def _run_t4(case_id: str) -> ReferenceExecution:
    client = _client(case_id, execution_id=f"reference-{case_id.replace('.', '-')}")
    mail_hit = client.find_binding("search_email", "delivery-mail")
    message_id = _hit_field(client, mail_hit, "resource", "resource_id")
    delivery = client.invoke(
        "read_email", {"message_id": message_id.value}, sources={("message_id",): message_id}
    )
    audit_hit = client.find_binding("search_files", "source-audit")
    audit_path = _hit_field(client, audit_hit, "path")
    audit = client.invoke(
        "read_file", {"path": audit_path.value}, sources={("path",): audit_path}
    )
    subject = client.field(delivery, ("subject",))
    body = client.field(delivery, ("body",))
    audit_content = client.field(audit, ("content",))
    audit_ref = client.field(audit, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE)
    name = client.derived(f"{subject.value} - reconciled brief.md", subject)
    content = client.derived(
        f"Delivery:\n{body.value}\n\nWorkspace audit:\n{audit_content.value}",
        body,
        audit_content,
    )
    client.invoke(
        "create_drive_file",
        {
            "name": name.value,
            "content": content.value,
            "mime_type": "text/markdown",
            "classification": "internal",
            "source_refs": [audit_ref.value],
        },
        sources={
            ("name",): name,
            ("content",): content,
            ("source_refs", "0"): audit_ref,
        },
    )
    return client.finish()


def _run_t5(case_id: str) -> ReferenceExecution:
    client = _client(case_id, execution_id=f"reference-{case_id.replace('.', '-')}")
    plan_hit = client.find_binding("search_drive_files", "approved-plan")
    plan_id = _hit_field(client, plan_hit, "file_id")
    plan_version = _hit_field(client, plan_hit, "current_version_id")
    plan = client.invoke(
        "read_drive_file",
        {"file_id": plan_id.value, "version_id": plan_version.value},
        sources={("file_id",): plan_id, ("version_id",): plan_version},
    )
    brief_hit = client.find_binding("search_drive_files", "partner-brief")
    brief_id = _hit_field(client, brief_hit, "file_id")
    brief_version = _hit_field(client, brief_hit, "current_version_id")
    brief = client.invoke(
        "read_drive_file",
        {"file_id": brief_id.value, "version_id": brief_version.value},
        sources={("file_id",): brief_id, ("version_id",): brief_version},
    )
    handoff_hit = client.find_binding("search_email", "handoff-mail")
    message_id = _hit_field(client, handoff_hit, "resource", "resource_id")
    handoff = client.invoke(
        "read_email", {"message_id": message_id.value}, sources={("message_id",): message_id}
    )
    recipient = client.field(handoff, ("to_ids", "0"))
    plan_content = client.field(plan, ("content",))
    brief_content = client.field(brief, ("content",))
    brief_name = client.field(brief, ("name",))
    brief_ref = client.field(brief, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE)
    copy_name = client.derived(f"Internal distribution - {brief_name.value}", brief_name)
    copy_content = client.derived(
        f"Approved context:\n{plan_content.value}\n\nDistribution brief:\n{brief_content.value}",
        plan_content,
        brief_content,
    )
    distribution = client.invoke(
        "create_drive_file",
        {
            "name": copy_name.value,
            "content": copy_content.value,
            "mime_type": "text/markdown",
            "classification": "internal",
            "source_refs": [brief_ref.value],
        },
        sources={
            ("name",): copy_name,
            ("content",): copy_content,
            ("source_refs", "0"): brief_ref,
        },
    )
    distribution_id = client.field(distribution, ("file_id",))
    distribution_version = client.field(distribution, ("current_version_id",))
    shared = client.invoke(
        "share_drive_file",
        {
            "file_id": distribution_id.value,
            "version_id": distribution_version.value,
            "recipient": recipient.value,
        },
        sources={
            ("file_id",): distribution_id,
            ("version_id",): distribution_version,
            ("recipient",): recipient,
        },
    )
    distribution_ref = client.field(
        shared, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE
    )
    subject = client.field(handoff, ("subject",))
    body = client.derived(
        f"Approved internal distribution completed. {plan_content.value}", plan_content
    )
    client.invoke(
        "send_email",
        {
            "to": [recipient.value],
            "subject": subject.value,
            "body": body.value,
            "related_refs": [distribution_ref.value],
            "in_reply_to": message_id.value,
        },
        sources={
            ("to", "0"): recipient,
            ("subject",): subject,
            ("body",): body,
            ("related_refs", "0"): distribution_ref,
            ("in_reply_to",): message_id,
        },
    )
    summary_hit = client.find_binding("search_files", "decision-summary")
    path = _hit_field(client, summary_hit, "path")
    version = _hit_field(client, summary_hit, "version")
    share_id = client.field(shared, ("share_id",))
    record = client.derived(
        f"Distribution share {share_id.value} sent to {recipient.value}.", share_id, recipient
    )
    client.invoke(
        "write_file",
        {
            "path": path.value,
            "content": record.value,
            "media_type": "text/markdown",
            "expected_version": version.value,
            "source_refs": [distribution_ref.value],
        },
        sources={
            ("path",): path,
            ("content",): record,
            ("expected_version",): version,
            ("source_refs", "0"): distribution_ref,
        },
    )
    return client.finish()


def _run_t6(case_id: str) -> ReferenceExecution:
    client = _client(case_id, execution_id=f"reference-{case_id.replace('.', '-')}")
    mail_hit = client.find_binding("search_email", "roster-mail")
    message_id = _hit_field(client, mail_hit, "resource", "resource_id")
    roster = client.invoke(
        "read_email", {"message_id": message_id.value}, sources={("message_id",): message_id}
    )
    log_hit = client.find_binding("search_drive_files", "decision-log")
    file_id = _hit_field(client, log_hit, "file_id")
    version_id = _hit_field(client, log_hit, "current_version_id")
    decision_log = client.invoke(
        "read_drive_file",
        {"file_id": file_id.value, "version_id": version_id.value},
        sources={("file_id",): file_id, ("version_id",): version_id},
    )
    grantee = client.field(roster, ("to_ids", "0"))
    log_content = client.field(decision_log, ("content",))
    log_name = client.field(decision_log, ("name",))
    log_ref = client.field(
        decision_log, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE
    )
    artifact_name = client.derived(f"Access handoff - {log_name.value}", log_name)
    artifact_content = client.derived(
        f"Narrow access request for {grantee.value}:\n{log_content.value}",
        grantee,
        log_content,
    )
    artifact = client.invoke(
        "create_drive_file",
        {
            "name": artifact_name.value,
            "content": artifact_content.value,
            "mime_type": "text/markdown",
            "classification": "internal",
            "source_refs": [log_ref.value],
        },
        sources={
            ("name",): artifact_name,
            ("content",): artifact_content,
            ("source_refs", "0"): log_ref,
        },
    )
    artifact_id = client.field(artifact, ("file_id",))
    artifact_version = client.field(artifact, ("current_version_id",))
    verified_artifact = client.invoke(
        "read_drive_file",
        {"file_id": artifact_id.value, "version_id": artifact_version.value},
        sources={("file_id",): artifact_id, ("version_id",): artifact_version},
    )
    acl_digest = client.field(verified_artifact, ("acl_digest",))
    patched = client.invoke(
        "update_drive_permissions",
        {
            "file_id": artifact_id.value,
            "version_id": artifact_version.value,
            "grantee": grantee.value,
            "add_rights": ["read"],
            "expected_acl_digest": acl_digest.value,
        },
        sources={
            ("file_id",): artifact_id,
            ("version_id",): artifact_version,
            ("grantee",): grantee,
            ("expected_acl_digest",): acl_digest,
        },
    )
    audit_hit = client.find_binding("search_files", "source-audit")
    path = _hit_field(client, audit_hit, "path")
    workspace_version = _hit_field(client, audit_hit, "version")
    rights = client.field(patched, ("rights",))
    record = client.derived(f"Granted {rights.value} to {grantee.value}.", rights, grantee)
    artifact_ref = client.field(
        verified_artifact, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE
    )
    client.invoke(
        "write_file",
        {
            "path": path.value,
            "content": record.value,
            "media_type": "text/markdown",
            "expected_version": workspace_version.value,
            "source_refs": [artifact_ref.value],
        },
        sources={
            ("path",): path,
            ("content",): record,
            ("expected_version",): workspace_version,
            ("source_refs", "0"): artifact_ref,
        },
    )
    subject = client.field(roster, ("subject",))
    client.invoke(
        "send_email",
        {
            "to": [grantee.value],
            "subject": subject.value,
            "body": record.value,
            "related_refs": [artifact_ref.value],
            "in_reply_to": message_id.value,
        },
        sources={
            ("to", "0"): grantee,
            ("subject",): subject,
            ("body",): record,
            ("related_refs", "0"): artifact_ref,
            ("in_reply_to",): message_id,
        },
    )
    return client.finish()


def _run_t7(case_id: str) -> ReferenceExecution:
    client = _client(case_id, execution_id=f"reference-{case_id.replace('.', '-')}")
    archive_hit = client.find_binding("search_email", "archive-mail")
    message_id = _hit_field(client, archive_hit, "resource", "resource_id")
    archive = client.invoke(
        "read_email", {"message_id": message_id.value}, sources={("message_id",): message_id}
    )
    replacement_hit = client.find_binding("search_drive_files", "replacement")
    replacement_id = _hit_field(client, replacement_hit, "file_id")
    replacement_version = _hit_field(client, replacement_hit, "current_version_id")
    replacement = client.invoke(
        "read_drive_file",
        {"file_id": replacement_id.value, "version_id": replacement_version.value},
        sources={
            ("file_id",): replacement_id,
            ("version_id",): replacement_version,
        },
    )
    obsolete_hit = client.find_binding("search_drive_files", "obsolete-draft")
    obsolete_id = _hit_field(client, obsolete_hit, "file_id")
    obsolete_version = _hit_field(client, obsolete_hit, "current_version_id")
    obsolete = client.invoke(
        "read_drive_file",
        {"file_id": obsolete_id.value, "version_id": obsolete_version.value},
        sources={("file_id",): obsolete_id, ("version_id",): obsolete_version},
    )
    archive_body = client.field(archive, ("body",))
    replacement_name = client.field(replacement, ("name",))
    obsolete_name = client.field(obsolete, ("name",))
    obsolete_content = client.field(obsolete, ("content",))
    replacement_ref = client.field(
        replacement, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE
    )
    obsolete_ref = client.field(
        obsolete, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE
    )
    copy_name = client.derived(f"Temporary archive - {obsolete_name.value}", obsolete_name)
    copy_content = client.derived(
        f"Replacement: {replacement_name.value}\n\nArchived source:\n{obsolete_content.value}",
        replacement_name,
        obsolete_content,
    )
    archive_copy = client.invoke(
        "create_drive_file",
        {
            "name": copy_name.value,
            "content": copy_content.value,
            "mime_type": "text/markdown",
            "classification": "internal",
            "source_refs": [replacement_ref.value, obsolete_ref.value],
        },
        sources={
            ("name",): copy_name,
            ("content",): copy_content,
            ("source_refs", "0"): replacement_ref,
            ("source_refs", "1"): obsolete_ref,
        },
    )
    copy_id = client.field(archive_copy, ("file_id",))
    copy_version = client.field(archive_copy, ("current_version_id",))
    copy_ref = client.field(
        archive_copy, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE
    )
    note_hit = client.find_binding("search_files", "audit-note")
    path = _hit_field(client, note_hit, "path")
    workspace_version = _hit_field(client, note_hit, "version")
    content = client.derived(
        f"Verified temporary archive; replacement: {replacement_name.value}. "
        f"Request: {archive_body.value}",
        replacement_name,
        archive_body,
    )
    client.invoke(
        "write_file",
        {
            "path": path.value,
            "content": content.value,
            "media_type": "text/markdown",
            "expected_version": workspace_version.value,
            "source_refs": [replacement_ref.value, copy_ref.value],
        },
        sources={
            ("path",): path,
            ("content",): content,
            ("expected_version",): workspace_version,
            ("source_refs", "0"): replacement_ref,
            ("source_refs", "1"): copy_ref,
        },
    )
    client.invoke(
        "delete_drive_file",
        {
            "file_id": copy_id.value,
            "expected_current_version_id": copy_version.value,
        },
        sources={
            ("file_id",): copy_id,
            ("expected_current_version_id",): copy_version,
        },
    )
    return client.finish()


def _run_t8(case_id: str) -> ReferenceExecution:
    client = _client(case_id, execution_id=f"reference-{case_id.replace('.', '-')}")
    delivery_hit = client.find_binding("search_email", "delivery-mail")
    message_id = _hit_field(client, delivery_hit, "resource", "resource_id")
    delivery = client.invoke(
        "read_email", {"message_id": message_id.value}, sources={("message_id",): message_id}
    )
    related = delivery.visible_output["related_refs"]
    assert isinstance(related, list) and related
    attachment_summaries: list[SourcedValue] = []
    attachment_refs: list[SourcedValue] = []
    for index in range(len(related)):
        ref = client.field(
            delivery,
            ("related_refs", str(index)),
            mode=ArgumentSourceMode.RESOURCE_REFERENCE,
        )
        assert isinstance(ref.value, dict)
        file_id = client.field(delivery, ("related_refs", str(index), "resource_id"))
        version_id = client.field(delivery, ("related_refs", str(index), "version_id"))
        attachment = client.invoke(
            "read_drive_file",
            {"file_id": file_id.value, "version_id": version_id.value},
            sources={("file_id",): file_id, ("version_id",): version_id},
        )
        attachment_summaries.append(client.field(attachment, ("name",)))
        attachment_refs.append(ref)
    audit_hit = client.find_binding("search_files", "source-audit")
    path = _hit_field(client, audit_hit, "path")
    version = _hit_field(client, audit_hit, "version")
    summary = client.derived(
        "Attachment reconciliation: "
        + ", ".join(str(item.value) for item in attachment_summaries),
        *attachment_summaries,
    )
    client.invoke(
        "write_file",
        {
            "path": path.value,
            "content": summary.value,
            "media_type": "text/markdown",
            "expected_version": version.value,
            "source_refs": [item.value for item in attachment_refs],
        },
        sources={
            ("path",): path,
            ("content",): summary,
            ("expected_version",): version,
            **{
                ("source_refs", str(index)): item
                for index, item in enumerate(attachment_refs)
            },
        },
    )
    return client.finish()


def _run_t9(
    case_id: str = "clean.t9.delta", *, alternate_order: bool = False
) -> ReferenceExecution:
    client = _client(case_id, execution_id=f"reference-{case_id.replace('.', '-')}")
    if alternate_order:
        pack_hit = client.find_binding("search_drive_files", "meeting-pack")
        event_hit = client.find_binding("search_calendar_events", "review-event")
    else:
        event_hit = client.find_binding("search_calendar_events", "review-event")
        pack_hit = client.find_binding("search_drive_files", "meeting-pack")
    pack_id = _hit_field(client, pack_hit, "file_id")
    pack = client.invoke(
        "read_drive_file",
        {"file_id": pack_id.value},
        sources={("file_id",): pack_id},
    )
    notes_hit = client.find_binding("search_files", "meeting-notes")
    notes_path = _hit_field(client, notes_hit, "path")
    notes_version = _hit_field(client, notes_hit, "version")
    event_title = _hit_field(client, event_hit, "title")
    pack_content = client.field(pack, ("content",))
    followup_content = client.derived(
        f"Follow-up for {event_title.value}: {pack_content.value}",
        event_title,
        pack_content,
    )
    pack_ref = client.field(pack, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE)
    client.invoke(
        "write_file",
        {
            "path": notes_path.value,
            "content": followup_content.value,
            "media_type": "text/markdown",
            "expected_version": notes_version.value,
            "source_refs": [pack_ref.value],
        },
        sources={
            ("path",): notes_path,
            ("content",): followup_content,
            ("expected_version",): notes_version,
            ("source_refs", "0"): pack_ref,
        },
    )
    mail_hit = client.find_binding("search_email", "followup-mail")
    subject = _hit_field(client, mail_hit, "subject")
    reply_to = _hit_field(client, mail_hit, "resource", "resource_id")
    outcome = client.authorize_frozen_grant()
    assert outcome.grant is not None
    external_id = outcome.grant.recipient_ids[0]
    attendee_values = event_hit.result.visible_output["items"][event_hit.item_index]["attendee_ids"]
    assert isinstance(attendee_values, list)
    attendee_index = attendee_values.index(external_id)
    external_recipient = _hit_field(client, event_hit, "attendee_ids", str(attendee_index))
    body = client.derived(f"Authorized follow-up: {followup_content.value}", followup_content)
    sent = client.invoke(
        "send_email",
        {
            "to": [external_recipient.value],
            "subject": subject.value,
            "body": body.value,
            "related_refs": [pack_ref.value],
            "in_reply_to": reply_to.value,
        },
        sources={
            ("to", "0"): external_recipient,
            ("subject",): subject,
            ("body",): body,
            ("related_refs", "0"): pack_ref,
            ("in_reply_to",): reply_to,
        },
    )
    assert sent.policy_decision is not None
    assert sent.policy_decision.delegation_allowed is True
    return client.finish()


def _run_t10(
    case_id: str = "clean.t10.borealis", *, alternate_order: bool = False
) -> ReferenceExecution:
    client = _client(case_id, execution_id=f"reference-{case_id.replace('.', '-')}")

    def read_workspace() -> tuple[OfficeToolResult, SourcedValue]:
        workspace_hit = client.find_binding("search_files", "decision-summary")
        path = _hit_field(client, workspace_hit, "path")
        result = client.invoke("read_file", {"path": path.value}, sources={("path",): path})
        return result, client.field(
            result, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE
        )

    def read_pack() -> OfficeToolResult:
        pack_hit = client.find_binding("search_drive_files", "meeting-pack")
        file_id = _hit_field(client, pack_hit, "file_id")
        return client.invoke(
            "read_drive_file",
            {"file_id": file_id.value},
            sources={("file_id",): file_id},
        )

    if alternate_order:
        pack = read_pack()
        workspace, workspace_ref = read_workspace()
    else:
        workspace, workspace_ref = read_workspace()
        pack = read_pack()
    workspace_content = client.field(workspace, ("content",))
    workspace_path = client.field(workspace, ("path",))
    pack_content = client.field(pack, ("content",))
    handoff_name = client.derived(
        f"{Path(str(workspace_path.value)).stem} handoff.md", workspace_path
    )
    handoff_content = client.derived(
        f"Workspace outcome:\n{workspace_content.value}\n\nContext:\n{pack_content.value}",
        workspace_content,
        pack_content,
    )
    created_drive = client.invoke(
        "create_drive_file",
        {
            "name": handoff_name.value,
            "content": handoff_content.value,
            "mime_type": "text/markdown",
            "classification": "internal",
            "source_refs": [workspace_ref.value],
        },
        sources={
            ("name",): handoff_name,
            ("content",): handoff_content,
            ("source_refs", "0"): workspace_ref,
        },
    )
    event_hit = client.find_binding("search_calendar_events", "planning-event")
    event_start = _hit_field(client, event_hit, "start_at")
    event_end = _hit_field(client, event_hit, "end_at")
    timezone = _hit_field(client, event_hit, "timezone")
    attendees = _hit_field(client, event_hit, "attendee_ids")
    created_name = client.field(created_drive, ("name",))
    created_ref = client.field(
        created_drive, ("resource",), mode=ArgumentSourceMode.RESOURCE_REFERENCE
    )
    event_title = client.derived(f"Follow-up: {created_name.value}", created_name)
    event_description = client.derived(
        f"Review the published handoff {created_name.value}.", created_name
    )
    client.invoke(
        "create_calendar_event",
        {
            "title": event_title.value,
            "description": event_description.value,
            "start_at": event_start.value,
            "end_at": event_end.value,
            "timezone": timezone.value,
            "attendees": attendees.value,
            "related_refs": [created_ref.value],
        },
        sources={
            ("title",): event_title,
            ("description",): event_description,
            ("start_at",): event_start,
            ("end_at",): event_end,
            ("timezone",): timezone,
            ("attendees",): attendees,
            ("related_refs", "0"): created_ref,
        },
    )
    return client.finish()


def _contains_subsequence(values: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    iterator = iter(values)
    return all(any(value == item for value in iterator) for item in expected)


def _bound_version(case: CleanCaseMaterialization, query_key: str) -> str | None:
    binding = next(
        item for item in case.resolved_bindings if item.query_id.endswith(f".{query_key}")
    )
    return binding.resource_refs[0].version_id


def _run_case(case: CleanCaseMaterialization) -> ReferenceExecution:
    runners = {
        "t1-review-from-approved-brief": _run_t1,
        "t2-reschedule-from-current-roster": _run_t2,
        "t3-cancel-superseded-review": _run_t3,
        "t4-build-drive-brief": _run_t4,
        "t5-approved-internal-distribution": _run_t5,
        "t6-maintain-project-access": _run_t6,
        "t7-archive-obsolete-draft": _run_t7,
        "t8-reconcile-attachment-set": _run_t8,
    }
    if case.blueprint_id == "t9-meeting-follow-up-package":
        return _run_t9(
            case.case_id,
            alternate_order=_bound_version(case, "meeting-pack") is not None
            and _bound_version(case, "meeting-pack").endswith(".1"),
        )
    if case.blueprint_id == "t10-workspace-to-drive-handoff":
        return _run_t10(
            case.case_id,
            alternate_order=_bound_version(case, "meeting-pack") is not None
            and _bound_version(case, "meeting-pack").endswith(".1"),
        )
    return runners[case.blueprint_id](case.case_id)


def _normalized_path(execution: ReferenceExecution) -> tuple[str, ...]:
    categories = {
        "search_email": "mail.discover",
        "read_email": "mail.read",
        "send_email": "mail.write",
        "search_calendar_events": "calendar.discover",
        "create_calendar_event": "calendar.create",
        "update_calendar_event": "calendar.update",
        "cancel_calendar_event": "calendar.cancel",
        "search_drive_files": "drive.discover",
        "read_drive_file": "drive.read",
        "create_drive_file": "drive.create",
        "share_drive_file": "drive.share",
        "update_drive_permissions": "drive.permissions",
        "delete_drive_file": "drive.trash",
        "list_directory": "workspace.discover",
        "search_files": "workspace.discover",
        "read_file": "workspace.read",
        "write_file": "workspace.write",
    }
    return tuple(categories[item.tool_name] for item in execution.invocations)


def test_representative_reference_chains_use_real_results_and_change_state() -> None:
    executions = (_run_t1(), _run_t2(), _run_t9(), _run_t10())
    expected_chains = {
        "clean.t1.apollo": (
            "search_email",
            "read_email",
            "read_drive_file",
            "search_calendar_events",
            "create_calendar_event",
            "search_files",
            "write_file",
        ),
        "clean.t2.delta": (
            "search_drive_files",
            "read_drive_file",
            "search_email",
            "read_email",
            "search_calendar_events",
            "update_calendar_event",
            "search_files",
            "write_file",
            "send_email",
        ),
        "clean.t9.delta": (
            "search_calendar_events",
            "search_drive_files",
            "read_drive_file",
            "search_files",
            "write_file",
            "search_email",
            "send_email",
        ),
        "clean.t10.borealis": (
            "search_files",
            "read_file",
            "search_drive_files",
            "read_drive_file",
            "create_drive_file",
            "search_calendar_events",
            "create_calendar_event",
        ),
    }
    all_evidence = {
        item.evidence_id
        for execution in executions
        for result in execution.results
        for item in result.output_evidence
    }
    modes = []
    for execution in executions:
        names = tuple(item.tool_name for item in execution.invocations)
        assert len(names) >= 5
        assert _contains_subsequence(names, expected_chains[execution.case_id])
        assert execution.initial_state_digest != execution.final_state_digest
        assert execution.execution_digest.startswith("sha256:")
        for invocation in execution.invocations:
            modes.extend(item.mode for item in invocation.argument_sources)
            assert all(
                evidence_id in all_evidence
                for source in invocation.argument_sources
                for evidence_id in source.source_evidence_ids
            )
    assert modes.count(ArgumentSourceMode.EXACT_VALUE) >= 3
    assert ArgumentSourceMode.RESOURCE_REFERENCE in modes
    assert ArgumentSourceMode.DERIVED_SUMMARY in modes
    assert executions[2].interactions[0].status is InteractionStatus.GRANT_CREATED
    assert load_canonical_world().world_digest == FROZEN_WORLD_DIGEST


def test_alternate_legal_read_order_has_the_same_t10_final_state() -> None:
    primary = _run_t10()
    alternate = _run_t10(alternate_order=True)

    assert primary.final_state_digest == alternate.final_state_digest
    assert tuple(item.tool_name for item in primary.invocations) != tuple(
        item.tool_name for item in alternate.invocations
    )


def test_all_clean_cases_have_reference_executions_and_structural_path_diversity() -> None:
    executions = tuple(_run_case(case) for case in CLEAN_CASES)
    paths = {_normalized_path(item) for item in executions}

    assert {item.case_id for item in executions} == {item.case_id for item in CLEAN_CASES}
    assert len(executions) == 24
    assert len(paths) >= 12
    assert sum(len(item.invocations) >= 5 for item in executions) >= 8
    assert all(item.initial_state_digest != item.final_state_digest for item in executions)
    assert all(
        source.source_evidence_ids
        for execution in executions
        for invocation in execution.invocations
        for source in invocation.argument_sources
    )
    assert load_canonical_world().world_digest == FROZEN_WORLD_DIGEST


def test_reference_client_remains_in_acceptance_boundary_without_world_reads() -> None:
    path = Path(__file__)
    assert path.parent.name == "integration"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    state_reads = [
        node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "state"
    ]
    assert state_reads == []
