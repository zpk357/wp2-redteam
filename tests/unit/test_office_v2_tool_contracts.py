from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import ResourceKind, ResourceRef
from sandbox.scenarios.office_v2.tools.contracts import (
    ArgumentSource,
    ArgumentSourceMode,
    OfficeToolInvocation,
    OutputEvidence,
    ToolFailureCode,
    ToolResultStatus,
    build_tool_result,
)
from sandbox.scenarios.office_v2.tools.provenance import EvidenceLedger, ProvenanceError


def _invocation(
    *,
    sequence: int = 1,
    arguments: dict[str, object] | None = None,
    sources: tuple[ArgumentSource, ...] = (),
) -> OfficeToolInvocation:
    payload = arguments or {"message_id": "mail.001"}
    return OfficeToolInvocation(
        invocation_id=f"invocation.test.{sequence:06d}",
        sequence=sequence,
        tool_name="read_email",
        actor_id="user.test",
        task_id="task.test",
        logical_time=1000,
        arguments=payload,
        arguments_digest=sha256_digest(payload),
        argument_sources=sources,
        before_state_digest="1" * 64,
    )


def test_invocation_is_strict_canonical_and_digest_locked() -> None:
    first = ArgumentSource(
        argument_path=("b",),
        source_evidence_ids=("evidence.b", "evidence.a"),
        mode=ArgumentSourceMode.DERIVED_SUMMARY,
    )
    second = ArgumentSource(
        argument_path=("a",),
        source_evidence_ids=("evidence.a",),
        mode=ArgumentSourceMode.EXACT_VALUE,
    )
    invocation = _invocation(arguments={"a": 1, "b": "summary"}, sources=(first, second))

    assert tuple(item.argument_path for item in invocation.argument_sources) == (("a",), ("b",))
    assert invocation.argument_sources[1].source_evidence_ids == (
        "evidence.a",
        "evidence.b",
    )
    assert OfficeToolInvocation.model_validate_json(invocation.model_dump_json()) == invocation
    with pytest.raises(ValidationError, match="arguments_digest"):
        OfficeToolInvocation.model_validate(
            {**invocation.model_dump(mode="python"), "arguments_digest": "2" * 64}
        )


def test_result_statuses_are_mutually_exclusive_and_digest_locked() -> None:
    invocation = _invocation()
    success = build_tool_result(
        invocation=invocation,
        status=ToolResultStatus.SUCCEEDED,
        visible_output={"subject": "Visible subject", "body": "sensitive body"},
    )
    blocked = build_tool_result(
        invocation=invocation,
        status=ToolResultStatus.BLOCKED,
        failure_code=ToolFailureCode.PLATFORM_DENIED,
    )

    assert success.before_state_digest == success.after_state_digest
    assert blocked.status is ToolResultStatus.BLOCKED
    assert "sensitive body" not in json.dumps(success.execution_fact_payload())
    with pytest.raises(ValidationError, match="succeeded result cannot define"):
        build_tool_result(
            invocation=invocation,
            status=ToolResultStatus.SUCCEEDED,
            failure_code=ToolFailureCode.INVALID_ARGUMENTS,
        )
    with pytest.raises(ValidationError, match="execution_fact_digest"):
        type(success).model_validate(
            {**success.model_dump(mode="python"), "execution_fact_digest": "3" * 64}
        )


def test_output_evidence_never_contains_the_visible_value() -> None:
    evidence = OutputEvidence(
        evidence_id="evidence.invocation.test.000001.body",
        invocation_id="invocation.test.000001",
        invocation_sequence=1,
        field_path=("body",),
        value_digest=sha256_digest("sensitive body"),
    )

    encoded = evidence.model_dump_json()
    assert "sensitive body" not in encoded
    assert "value" not in evidence.model_fields_set


def test_provenance_requires_prior_matching_evidence() -> None:
    ledger = EvidenceLedger()
    resource = ResourceRef(kind=ResourceKind.MAIL_MESSAGE, resource_id="mail.001")
    ledger.add(
        (
            OutputEvidence(
                evidence_id="evidence.read.resource",
                invocation_id="invocation.read.000000",
                invocation_sequence=0,
                field_path=("resource",),
                resource_ref=resource,
                value_digest=sha256_digest(resource.model_dump(mode="json")),
            ),
            OutputEvidence(
                evidence_id="evidence.read.subject",
                invocation_id="invocation.read.000000",
                invocation_sequence=0,
                field_path=("subject",),
                value_digest=sha256_digest("Approved brief"),
            ),
        )
    )
    invocation = _invocation(
        arguments={"resource": resource.model_dump(mode="json"), "subject": "Approved brief"},
        sources=(
            ArgumentSource(
                argument_path=("resource",),
                source_evidence_ids=("evidence.read.resource",),
                mode=ArgumentSourceMode.RESOURCE_REFERENCE,
            ),
            ArgumentSource(
                argument_path=("subject",),
                source_evidence_ids=("evidence.read.subject",),
                mode=ArgumentSourceMode.EXACT_VALUE,
            ),
        ),
    )

    ledger.verify_sources(invocation)
    mismatch = invocation.model_copy(
        update={
            "arguments": {"resource": resource.model_dump(mode="json"), "subject": "Changed"},
            "arguments_digest": sha256_digest(
                {"resource": resource.model_dump(mode="json"), "subject": "Changed"}
            ),
        }
    )
    with pytest.raises(ProvenanceError) as mismatch_error:
        ledger.verify_sources(mismatch)
    assert mismatch_error.value.code is ToolFailureCode.ARGUMENT_SOURCE_MISMATCH

    missing = _invocation(
        sources=(
            ArgumentSource(
                argument_path=("message_id",),
                source_evidence_ids=("evidence.missing",),
                mode=ArgumentSourceMode.DERIVED_SUMMARY,
            ),
        )
    )
    with pytest.raises(ProvenanceError) as missing_error:
        ledger.verify_sources(missing)
    assert missing_error.value.code is ToolFailureCode.ARGUMENT_SOURCE_MISSING
