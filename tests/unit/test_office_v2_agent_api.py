from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.agent_api import (
    AgentToolResultProjection,
    AgentVisibleToolErrorCode,
    AgentVisibleToolResult,
    office_v2_model_tool_contract_digest,
    office_v2_model_tool_specs,
    office_v2_provider_tool_schemas,
    project_office_v2_tool_result,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_EXCLUDED_TOOL_NAMES,
    OFFICE_V2_TOOL_NAMES,
    office_v2_tool_definitions,
)
from sandbox.scenarios.office_v2.tools.contracts import (
    OfficeToolInvocation,
    ToolFailureCode,
    ToolResultStatus,
    build_tool_result,
)
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld
from sandbox.tool_contracts import (
    OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
    OFFICE_V2_TOOL_SPECS,
)


def _invocation(sequence: int = 1) -> OfficeToolInvocation:
    arguments = {"message_id": "mail.001"}
    return OfficeToolInvocation(
        invocation_id=f"invocation.agent-api.{sequence:06d}",
        sequence=sequence,
        tool_name="read_email",
        actor_id="user.agent-api",
        task_id="task.agent-api",
        logical_time=1000,
        arguments=arguments,
        arguments_digest=sha256_digest(arguments),
        before_state_digest="1" * 64,
    )


def _failure(code: ToolFailureCode, sequence: int = 1):
    return build_tool_result(
        invocation=_invocation(sequence),
        status=(
            ToolResultStatus.BLOCKED
            if code
            in {
                ToolFailureCode.CAPABILITY_UNAVAILABLE,
                ToolFailureCode.PLATFORM_DENIED,
                ToolFailureCode.POLICY_ENFORCED_DENIED,
            }
            else ToolResultStatus.FAILED
            if code
            in {
                ToolFailureCode.TRANSACTION_VALIDATION_FAILED,
                ToolFailureCode.TRANSACTION_CONFLICT,
                ToolFailureCode.INTERNAL_INTEGRITY_ERROR,
            }
            else ToolResultStatus.REJECTED
        ),
        failure_code=code,
    )


def test_model_tool_surface_reuses_exact_frozen_specs_and_provider_schemas() -> None:
    specs = office_v2_model_tool_specs()
    schemas = office_v2_provider_tool_schemas()
    definitions = office_v2_tool_definitions()

    assert specs is OFFICE_V2_TOOL_SPECS
    assert tuple(spec.name for spec in specs) == OFFICE_V2_TOOL_NAMES
    assert len(specs) == len(schemas) == 17
    assert set(OFFICE_V2_EXCLUDED_TOOL_NAMES).isdisjoint(spec.name for spec in specs)
    assert all(spec.definition is definitions[spec.name] for spec in specs)
    for spec, schema in zip(specs, schemas, strict=True):
        assert schema == {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.arguments_model.model_json_schema(),
            },
        }
    assert office_v2_model_tool_contract_digest() == (
        OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST
    )


def test_success_projection_preserves_only_model_visible_business_data() -> None:
    source = build_tool_result(
        invocation=_invocation(),
        status=ToolResultStatus.SUCCEEDED,
        visible_output={
            "items": [{"message_id": "mail.001", "version": 2}],
            "next_page_token": "opaque-page-token",
            "effective_rights": ["read", "share"],
            "related_refs": [
                {
                    "kind": "drive_file_version",
                    "resource_id": "version.brief.2",
                    "version_id": "version.brief.2",
                }
            ],
        },
    )

    projection = project_office_v2_tool_result(source)
    visible = projection.model_visible_payload()

    assert projection.trusted_result is source
    assert visible == {
        "status": "succeeded",
        "data": source.visible_output,
        "error": None,
    }
    serialized = json.dumps(visible, sort_keys=True)
    for hidden in (
        "policy_decision",
        "state_transition",
        "before_state_digest",
        "after_state_digest",
        "execution_fact_digest",
        "evidence_id",
    ):
        assert hidden not in serialized


@pytest.mark.parametrize("source_code", tuple(ToolFailureCode))
def test_every_runtime_failure_has_a_closed_non_retryable_visible_error(
    source_code: ToolFailureCode,
) -> None:
    projection = project_office_v2_tool_result(_failure(source_code))
    visible = projection.model_visible_payload()

    assert visible["status"] != "succeeded"
    assert visible["data"] == {}
    error = visible["error"]
    assert isinstance(error, dict)
    assert set(error) == {"code", "message", "retryable"}
    assert error["retryable"] is False
    assert error["code"] in {item.value for item in AgentVisibleToolErrorCode}


def test_hidden_and_absent_resources_share_one_visible_error() -> None:
    hidden = project_office_v2_tool_result(
        _failure(ToolFailureCode.RESOURCE_NOT_FOUND_OR_HIDDEN, 1)
    ).model_visible_payload()
    absent = project_office_v2_tool_result(
        _failure(ToolFailureCode.INVALID_RESOURCE_REFERENCE, 2)
    ).model_visible_payload()

    assert hidden["error"] == absent["error"]
    assert isinstance(hidden["error"], dict)
    assert hidden["error"]["code"] == "resource_not_found_or_hidden"


def test_platform_and_enforced_policy_denials_remain_distinct() -> None:
    platform = project_office_v2_tool_result(
        _failure(ToolFailureCode.PLATFORM_DENIED)
    ).model_visible_payload()
    policy = project_office_v2_tool_result(
        _failure(ToolFailureCode.POLICY_ENFORCED_DENIED)
    ).model_visible_payload()

    assert platform["status"] == policy["status"] == "blocked"
    assert isinstance(platform["error"], dict)
    assert isinstance(policy["error"], dict)
    assert platform["error"]["code"] == "platform_denied"
    assert policy["error"]["code"] == "policy_enforced_denied"


def test_delegation_missing_success_is_not_rewritten_as_blocked() -> None:
    canonical = load_canonical_world()
    case = CLEAN_CASE_BY_ID["clean.t2.evergreen"]
    runtime = OfficeV2ToolRuntime(
        episode=EpisodeWorld(canonical, episode_id="agent-api-delegation-missing"),
        actor=case.actor,
        task=case.task,
        definitions=office_v2_tool_definitions(),
        bindings=case.resolved_bindings,
    )
    source = runtime.invoke(
        "read_drive_file",
        {
            "file_id": "drive.evergreen.public-brief",
            "version_id": "version.evergreen.public-brief.1",
        },
    )

    assert source.policy_decision is not None
    assert source.policy_decision.delegation_allowed is False
    assert source.policy_decision.effective_allowed is True
    visible = project_office_v2_tool_result(source).model_visible_payload()
    assert visible["status"] == "succeeded"
    assert visible["error"] is None
    assert "delegation" not in json.dumps(visible)


def test_rollback_fact_stays_trusted_and_visible_result_is_transaction_failed() -> None:
    canonical = load_canonical_world()
    episode = EpisodeWorld(canonical, episode_id="agent-api-rollback")
    transaction = episode.begin_transaction(
        action_request_id="request.agent-api.rollback",
        policy_decision_id="decision.agent-api.rollback",
    )
    transition = transaction.rollback("transaction_validation_failed")
    invocation = _invocation()
    invocation = invocation.model_copy(
        update={"before_state_digest": transition.before_state_digest}
    )
    source = build_tool_result(
        invocation=invocation,
        status=ToolResultStatus.FAILED,
        state_transition=transition,
        after_state_digest=transition.after_state_digest,
        failure_code=ToolFailureCode.TRANSACTION_VALIDATION_FAILED,
    )

    projection = project_office_v2_tool_result(source)
    assert projection.trusted_result.state_transition == transition
    assert transition.committed is False
    assert transition.state_delta.is_empty()
    assert projection.model_visible_payload() == {
        "status": "failed",
        "data": {},
        "error": {
            "code": "transaction_failed",
            "message": (
                "The operation could not be completed and no requested change "
                "was committed."
            ),
            "retryable": False,
        },
    }


def test_visible_result_and_projection_reject_forged_content() -> None:
    source = build_tool_result(
        invocation=_invocation(),
        status=ToolResultStatus.SUCCEEDED,
        visible_output={"subject": "Current subject"},
    )
    visible = project_office_v2_tool_result(source).visible_result
    forged = visible.model_copy(update={"data": {"subject": "Forged subject"}})

    with pytest.raises(ValueError, match="does not match trusted execution fact"):
        AgentToolResultProjection(source, forged)
    with pytest.raises(ValidationError, match="requires an error"):
        AgentVisibleToolResult(status=ToolResultStatus.BLOCKED)
