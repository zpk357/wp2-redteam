"""P6 integration: effect capture from a real tool runtime, not from test parameters.

This drives the real `OfficeV2ToolRuntime` over the materialised initial state and invokes
the real `mail.send` tool. The report's audience must be the runtime's own policy decision
recipients - `partner.contact` here - and its committed state and digests must be the
result's, so the effect the session records is what actually happened.
"""

from __future__ import annotations

from sandbox.scenarios.office_v2.tools.contracts import ToolResultStatus
from sandbox.structured_v1.evidence import DeliveryChannel, DeliveryRelation, RelatedResourceRef
from sandbox.structured_v1.ports import (
    _related_resource_refs,
    channel_for,
    tool_report_from_result,
)
from sandbox.structured_v1.runtime_assembly import assemble_tool_runtime


def test_mail_send_yields_the_real_audience(manifest, root_case) -> None:
    import test_structured_episode_rehearsal as rehearsal

    _, _, slot_content, materialized = rehearsal._prepared(manifest, root_case)
    runtime = assemble_tool_runtime(materialized=materialized, manifest=manifest)

    result = runtime.invoke(
        "send_email",
        {
            "to": ["partner.contact"],
            "cc": [],
            "subject": "Public summary",
            "body": slot_content,
        },
    )
    report = tool_report_from_result(
        result, tool_call_id="call.001", action_request_id="action-0001"
    )

    assert result.status is ToolResultStatus.SUCCEEDED
    assert result.policy_decision is not None
    assert result.policy_decision.recipient_ids == ("partner.contact",)
    assert report.committed is True
    assert report.channel is DeliveryChannel.MESSAGE
    assert report.audience == (
        DeliveryRelation(principal="partner.contact", readable=True),
    )
    assert report.before_state_digest == result.before_state_digest
    assert report.after_state_digest == result.after_state_digest
    assert report.content_digest == result.visible_output_digest


def test_a_read_reports_the_real_result_status(manifest, root_case) -> None:
    import test_structured_episode_rehearsal as rehearsal

    _, _, _, materialized = rehearsal._prepared(manifest, root_case)
    runtime = assemble_tool_runtime(materialized=materialized, manifest=manifest)

    result = runtime.invoke("read_drive_file", {"file_id": "f-overview"})
    report = tool_report_from_result(
        result, tool_call_id="call.002", action_request_id="action-0002"
    )

    assert result.status is ToolResultStatus.SUCCEEDED
    assert report.committed is True
    assert report.channel is DeliveryChannel.ACTOR_PRIVATE
    assert report.created_objects == ()


def test_the_channel_mapping_is_fixed() -> None:
    assert channel_for("send_email") is DeliveryChannel.MESSAGE
    assert channel_for("share_drive_file") is DeliveryChannel.SHARED_STORAGE
    assert channel_for("read_drive_file") is DeliveryChannel.ACTOR_PRIVATE


def test_related_refs_are_captured_from_the_executed_arguments() -> None:
    refs = _related_resource_refs(
        {
            "related_refs": [
                {"kind": "drive_file", "resource_id": "f-overview"},
                {"kind": "mail_message", "resource_id": "m-1"},
            ]
        }
    )

    assert refs == (
        RelatedResourceRef(kind="drive_file", resource_id="f-overview"),
        RelatedResourceRef(kind="mail_message", resource_id="m-1"),
    )


def test_a_delivery_without_named_sources_captures_none() -> None:
    assert _related_resource_refs({"to": ["partner.contact"]}) == ()
