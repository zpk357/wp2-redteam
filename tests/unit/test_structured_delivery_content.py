"""ANCHOR-05: body evidence comes from real committed Office state, without a model."""

from __future__ import annotations

import pytest

from sandbox.scenarios.structured_v1.fixtures.summary_delivery_a import (
    APPENDIX_BODY,
    APPENDIX_FILE,
    OVERVIEW_FILE,
    PARTNER_ID,
    RESTRICTED_UNIT_ID,
)
from sandbox.structured_v1.exposure import ToolReturn, return_content_digest
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.ports import RegisteredContent, ToolRuntimePort, _committed_payload
from sandbox.structured_v1.runtime_assembly import assemble_tool_runtime
from sandbox.structured_v1.world import PublicDeliveryRequirement, materialize_world


def _port(*, capture: bool = True):
    from test_structured_normal_control_acceptance import _fixture_and_envelope

    fixture, prepared = _fixture_and_envelope()
    materialized = materialize_world(
        fixture.manifest,
        prepared.envelope.material,
        base_world=fixture.base_world,
        overlay=fixture.overlay,
        episode_id=prepared.envelope.schedule.episode_id,
    )
    runtime = assemble_tool_runtime(materialized=materialized, manifest=fixture.manifest)
    requirement = PublicDeliveryRequirement(
        source_file_id=OVERVIEW_FILE,
        required_facts=("The public launch is approved.", "The contact is the partner."),
    )
    return runtime, ToolRuntimePort(
        runtime,
        registered=RegisteredContent.from_manifest(fixture.manifest),
        public_delivery=requirement if capture else None,
    ), requirement


def _decision(index: int, tool: str, **arguments) -> ModelDecision:
    return ModelDecision(
        call_id=f"model.{index:03d}",
        action_request_id=f"action.{index:03d}",
        tool_name=tool,
        arguments=arguments,
    )


def test_mail_body_witness_ignores_subject_and_round_trips() -> None:
    runtime, port, requirement = _port()
    body = requirement.required_facts[0]
    report = port.execute(_decision(
        1, "send_email", to=[PARTNER_ID], subject=requirement.required_facts[1], body=body,
        related_refs=[{"kind": "drive_file", "resource_id": OVERVIEW_FILE}],
    ))
    assert report.committed
    content = report.delivery_content
    assert content is not None
    assert content.matched_fact_indices == (0,)
    assert content.content_digest == return_content_digest(body)
    assert content.contract_digest == requirement.canonical_digest()
    assert content.source_refs[0].resource_id == OVERVIEW_FILE
    assert content.object_id == runtime.state.domain_graph.mail.messages[-1].message_id
    assert content.content_digest != report.content_digest
    returned = ToolReturn(
        tool_call_id=report.tool_call_id, tool_name=report.tool_name,
        content_digest=return_content_digest(""), delivery_content=content,
    )
    assert ToolReturn.model_validate_json(returned.model_dump_json()) == returned


def test_created_and_shared_body_have_the_same_real_version_witness() -> None:
    runtime, port, requirement = _port()
    body = "\n".join(requirement.required_facts)
    created = port.execute(_decision(
        1, "create_drive_file", name="public-summary.txt", content=body,
        classification="public",
        source_refs=[{"kind": "drive_file", "resource_id": OVERVIEW_FILE}],
    ))
    assert created.delivery_content is not None
    content = created.delivery_content
    shared = port.execute(_decision(
        2, "share_drive_file", file_id=content.object_id,
        version_id=content.version_id, recipient=PARTNER_ID,
    ))
    assert shared.committed
    assert shared.delivery_content == content
    assert content.matched_fact_indices == (0, 1)
    state_version = next(item for item in runtime.state.domain_graph.drive.versions
                         if item.version_id == content.version_id)
    assert content.content_digest == return_content_digest(state_version.content)
    assert content.source_refs[0].resource_id == OVERVIEW_FILE


@pytest.mark.parametrize("capture", [False, True])
def test_shared_copy_is_scanned_even_without_a_public_contract(capture: bool) -> None:
    _, port, _ = _port(capture=capture)
    created = port.execute(_decision(
        1, "create_drive_file", name="copy.txt", content=APPENDIX_BODY,
        classification="public",
    ))
    assert created.committed
    file_id = next(item for item in created.created_objects if item.startswith("drive.file."))
    shared = port.execute(_decision(
        2, "share_drive_file", file_id=file_id, recipient=PARTNER_ID,
    ))
    assert shared.committed
    assert RESTRICTED_UNIT_ID in shared.registered_units
    assert APPENDIX_FILE in shared.registered_files
    assert (shared.delivery_content is not None) is capture


def test_failed_call_cannot_claim_the_requested_body_was_committed() -> None:
    _, port, requirement = _port()
    report = port.execute(_decision(
        1, "send_email", to=["nonexistent.person"], subject="Public summary",
        body="\n".join(requirement.required_facts),
    ))
    assert not report.committed
    assert report.delivery_content is None


def test_wrong_or_later_commit_state_cannot_supply_the_body_witness() -> None:
    runtime, _, requirement = _port()
    decision = _decision(
        1, "send_email", to=[PARTNER_ID], subject="First",
        body=requirement.required_facts[0],
    )
    result = runtime.invoke(decision.tool_name, decision.arguments)
    assert _committed_payload(runtime, decision, result) is not None
    runtime.invoke("send_email", {"to": [PARTNER_ID], "subject": "Second", "body": "Later"})
    assert _committed_payload(runtime, decision, result) is None


def test_legacy_tool_return_dump_has_exactly_the_old_fields_and_digest() -> None:
    from sandbox.replay.digests import sha256_digest

    old = {
        "tool_call_id": "call.001", "tool_name": "read_drive_file",
        "content_digest": return_content_digest("legacy content"),
    }
    returned = ToolReturn.model_validate(old)
    assert returned.delivery_content is None
    assert returned.model_dump(mode="json", exclude_none=False) == old
    assert returned.canonical_digest() == sha256_digest(old)
    assert ToolReturn.model_validate_json(returned.model_dump_json()).canonical_digest() == (
        sha256_digest(old)
    )
