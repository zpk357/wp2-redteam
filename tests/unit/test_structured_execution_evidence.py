"""FBK-04 captures official results and immediate state; it does not infer coverage."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_structured_delivery_content import _decision

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.tools.contracts import ToolFailureCode, ToolResultStatus
from sandbox.scenarios.structured_v1.fixtures.summary_delivery_a import (
    CATALOGUE_FILE,
    OVERVIEW_FILE,
    PARTNER_ID,
)
from sandbox.structured_v1.execution_evidence import (
    ToolExecutionEvidence,
    capture_execution_evidence,
    validate_execution_binding,
)
from sandbox.structured_v1.exposure import ToolReturn, return_content_digest
from sandbox.structured_v1.ports import RegisteredContent, SlotResolver, ToolRuntimePort
from sandbox.structured_v1.runtime_assembly import assemble_tool_runtime
from sandbox.structured_v1.world import materialize_world


def _runtime_and_port():
    from test_structured_normal_control_acceptance import _fixture_and_envelope

    fixture, prepared = _fixture_and_envelope()
    materialized = materialize_world(
        fixture.manifest, prepared.envelope.material, base_world=fixture.base_world,
        overlay=fixture.overlay, episode_id=prepared.envelope.schedule.episode_id,
    )
    runtime = assemble_tool_runtime(materialized=materialized, manifest=fixture.manifest)
    return runtime, ToolRuntimePort(
        runtime, slots=SlotResolver.from_overlay(fixture.overlay),
        registered=RegisteredContent.from_manifest(fixture.manifest),
    )


def test_official_success_rejection_block_and_failure_remain_distinct() -> None:
    runtime, port = _runtime_and_port()
    reports = [
        port.execute(_decision(1, "read_drive_file", file_id=OVERVIEW_FILE)),
        port.execute(_decision(2, "read_drive_file")),
        port.execute(_decision(3, "share_drive_file", file_id=OVERVIEW_FILE, recipient=PARTNER_ID)),
    ]

    def fail_transaction(*_):
        raise RuntimeError("offline failure injection")

    runtime.definitions["create_drive_file"] = replace(
        runtime.definitions["create_drive_file"], execute=fail_transaction,
    )
    reports.append(port.execute(_decision(4, "create_drive_file", name="failed", content="body")))
    assert [item.execution_evidence.status for item in reports] == [
        ToolResultStatus.SUCCEEDED, ToolResultStatus.REJECTED,
        ToolResultStatus.BLOCKED, ToolResultStatus.FAILED,
    ]
    assert reports[1].execution_evidence.failure_code is ToolFailureCode.INVALID_ARGUMENTS
    assert reports[2].execution_evidence.failure_code is ToolFailureCode.PLATFORM_DENIED
    assert reports[3].execution_evidence.failure_code is ToolFailureCode.TRANSACTION_CONFLICT
    for report, result in zip(reports, runtime.results, strict=True):
        evidence = report.execution_evidence
        assert evidence.execution_fact_payload() == result.execution_fact_payload()
        assert evidence.execution_fact_digest == result.execution_fact_digest
        assert evidence.policy_decision == result.policy_decision
        assert evidence.state_transition == result.state_transition
        assert ToolExecutionEvidence.model_validate_json(evidence.model_dump_json()) == evidence
    assert all(report.execution_evidence.committed_resource is None for report in reports)
    assert all(report.execution_evidence.read_resource is None for report in reports[1:])


def test_read_origins_and_real_committed_copy_keep_resource_versions() -> None:
    runtime, port = _runtime_and_port()
    frozen = port.execute(_decision(1, "read_drive_file", file_id=OVERVIEW_FILE))
    material = port.execute(_decision(2, "read_drive_file", file_id=CATALOGUE_FILE))
    assert frozen.execution_evidence.read_origin == "frozen_resource"
    assert material.execution_evidence.read_origin == "low_trust_material"
    source = frozen.execution_evidence.read_resource
    assert source.version_id
    body = "a body which is retained only as a digest"
    created = port.execute(_decision(
        3, "create_drive_file", name="copy", content=body, classification="public",
        source_refs=[source.model_dump(mode="json")],
    ))
    creation = created.execution_evidence
    assert creation.committed_resource.version_id
    assert creation.committed_source_refs == (source,)
    assert creation.committed_content_digest == return_content_digest(body)
    assert creation.state_transition.committed
    assert creation.state_transition.state_delta.created_objects
    assert body not in creation.model_dump_json()
    copy = creation.committed_resource
    reread = port.execute(_decision(
        4, "read_drive_file", file_id=copy.resource_id, version_id=copy.version_id,
    ))
    assert reread.execution_evidence.read_resource == copy
    assert reread.execution_evidence.read_origin == "unknown"
    shared = port.execute(_decision(
        5, "share_drive_file", file_id=copy.resource_id,
        version_id=copy.version_id, recipient=PARTNER_ID,
    ))
    assert shared.execution_evidence.committed_resource == copy
    assert shared.execution_evidence.committed_content_digest == creation.committed_content_digest
    assert shared.execution_evidence.committed_source_refs == (source,)
    assert shared.execution_evidence.state_transition == runtime.results[-1].state_transition


def test_committed_content_and_source_observations_are_checked_against_official_output() -> None:
    _, port = _runtime_and_port()
    decision = _decision(
        1, "send_email", to=[PARTNER_ID], subject="subject", body="the actual body",
        related_refs=[{"kind": "drive_file", "resource_id": OVERVIEW_FILE}],
    )
    evidence = port.execute(decision).execution_evidence
    validate_execution_binding(evidence, tool_name=decision.tool_name, arguments=decision.arguments)
    tampered = evidence.model_dump(mode="json")
    tampered["committed_source_refs"] = []
    with pytest.raises(ValueError, match="references do not match"):
        ToolExecutionEvidence.model_validate(tampered)
    changed_body = evidence.model_copy(update={
        "committed_content_digest": return_content_digest("a forged body")
    })
    with pytest.raises(ValueError, match="committed content"):
        validate_execution_binding(
            changed_body, tool_name=decision.tool_name, arguments=decision.arguments,
        )


def test_formal_digest_and_decision_binding_reject_changed_or_borrowed_evidence() -> None:
    runtime, port = _runtime_and_port()
    decision = _decision(1, "read_drive_file", file_id=OVERVIEW_FILE)
    report = port.execute(decision)
    evidence = report.execution_evidence
    validate_execution_binding(
        evidence, tool_name=decision.tool_name, arguments=decision.arguments,
        before_state_digest=report.before_state_digest,
        after_state_digest=report.after_state_digest,
    )
    with pytest.raises(ValueError, match="bound"):
        validate_execution_binding(evidence, tool_name="read_drive_file", arguments={})
    with pytest.raises(ValueError, match="different before"):
        validate_execution_binding(
            evidence, tool_name=decision.tool_name, arguments=decision.arguments,
            before_state_digest="sha256:" + "0" * 64,
        )
    tampered = evidence.model_dump(mode="json")
    tampered["visible_output_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="formal result digest"):
        ToolExecutionEvidence.model_validate(tampered)
    forged = runtime.results[-1].model_copy(update={"execution_fact_digest": "sha256:" + "0" * 64})
    with pytest.raises(ValueError, match="execution_fact_digest"):
        capture_execution_evidence(forged, arguments_digest=sha256_digest(decision.arguments))
    with pytest.raises(ValueError, match="different tool"):
        ToolReturn(
            tool_call_id="call.001", tool_name="send_email", content_digest=report.content_digest,
            execution_evidence=evidence,
        )


def test_legacy_tool_return_bytes_and_digest_are_unchanged() -> None:
    old = {
        "tool_call_id": "call.001", "tool_name": "read_drive_file",
        "content_digest": return_content_digest("old content"),
    }
    returned = ToolReturn.model_validate(old)
    assert returned.execution_evidence is None
    assert returned.model_dump(mode="json", exclude_none=False) == old
    assert returned.canonical_digest() == sha256_digest(old)
    assert ToolReturn.model_validate_json(returned.model_dump_json()) == returned


def test_session_bundle_carries_the_same_official_execution_facts() -> None:
    from test_structured_fixture_assets import _run

    from sandbox.scenarios.structured_v1.fixtures import load_fixture
    from sandbox.structured_v1.bundle import verify_bundle
    from sandbox.structured_v1.model import ModelDecision

    bundle = _run(load_fixture("summary-delivery-a"), (
        _decision(1, "read_drive_file", file_id=OVERVIEW_FILE),
        ModelDecision(call_id="model.002"),
    ), [0, 0, 10])
    verify_bundle(bundle)
    returned = bundle.tool_returns[0]
    assert returned.execution_evidence is not None
    validate_execution_binding(
        returned.execution_evidence,
        tool_name=bundle.model_decisions[0].tool_name,
        arguments=bundle.model_decisions[0].arguments,
        before_state_digest=bundle.records[0].before_state_digest,
        after_state_digest=bundle.records[0].after_state_digest,
    )
    assert returned.execution_evidence.status is ToolResultStatus.SUCCEEDED
