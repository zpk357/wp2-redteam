from __future__ import annotations

import pytest
from app.adapter.base import AdapterConfigurationError, AdapterExecutionError
from app.adapter.factory import (
    STAGE7_DETERMINISTIC_MODEL_DIGEST,
    STAGE7_DETERMINISTIC_MODEL_NAME,
    AdapterFactory,
)
from app.adapter.langgraph_react_runtime import LangGraphReactRuntime
from app.agent.office_v2_stage7_provider import OfficeV2Stage7Provider
from app.agent.react_contract import ReactMessage

from sandbox.protocol import (
    OFFICE_V2_SCENARIO_ID,
    ExecutionRequest,
    ModelOptions,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scenarios.office_v2.interaction_session import ScriptedResponseDirective


def _request(case_id: str, *, max_steps: int = 40) -> ExecutionRequest:
    case = CLEAN_CASE_BY_ID[case_id]
    contract = case.task.user_response_script
    request_contract = contract.requests[0]
    rule = contract.response_rules[0]
    directive = ScriptedResponseDirective(
        request_id=request_contract.request_id,
        rule_id=rule.rule_id,
        turn_id=f"turn.stage7.9.{case_id}",
        responder_id=rule.authenticated_responder_id,
        authenticated_principal_id=rule.authenticated_responder_id,
    )
    model = ModelOptions(
        provider="fake",
        model_name=STAGE7_DETERMINISTIC_MODEL_NAME,
        model_digest=STAGE7_DETERMINISTIC_MODEL_DIGEST,
    )
    envelope = build_v2_execution_envelope(
        case,
        initial_state=load_canonical_world().state,
        model_identity=model,
        response_directives=(directive,),
    )
    return ExecutionRequest(
        execution_id=f"episode.stage7.9.{case_id}",
        case_id=case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=case.task.instruction,
        max_steps=max_steps,
        timeout_seconds=30,
        model=model,
        office_v2_execution=envelope,
    )


async def _execute(case_id: str) -> tuple[LangGraphReactRuntime, list]:
    runtime = LangGraphReactRuntime(
        provider_factory=OfficeV2Stage7Provider.from_request
    )
    events = [event async for event in runtime.execute(_request(case_id))]
    return runtime, events


async def test_stage7_long_chain_consumes_paginated_results_and_completes() -> None:
    runtime, events = await _execute("clean.t2.delta")

    tools = [
        event.data["name"] for event in events if event.event_type == "tool_result"
    ]
    assert tools == [
        *("search_drive_files" for _ in range(5)),
        *("read_drive_file" for _ in range(10)),
        "request_clarification",
        "read_drive_file",
        "search_email",
        "read_email",
        "search_calendar_events",
        "update_calendar_event",
        "search_files",
        "write_file",
        "send_email",
    ]
    assert any(event.event_type == "execution_finished" for event in events)
    assert runtime.last_v2_oracle_artifact is not None
    oracle = runtime.last_v2_oracle_artifact.oracle_result
    assert oracle.utility.disposition.value == "completed"
    assert oracle.security.planned_objectives == ()


async def test_stage7_authorization_chain_creates_grant_before_delegated_send() -> None:
    runtime, events = await _execute("clean.t9.apollo")

    tools = [
        event.data["name"] for event in events if event.event_type == "tool_result"
    ]
    assert tools == [
        "search_calendar_events",
        "search_drive_files",
        "read_drive_file",
        "request_clarification",
        "search_files",
        "write_file",
        "search_email",
        "send_email",
    ]
    assert runtime.last_v2_session is not None
    assert len(runtime.last_v2_session.episode.state.delegation_grants) == 1
    sent = runtime.last_v2_session.trusted_tool_results[-1]
    assert sent.policy_decision is not None
    assert sent.policy_decision.delegation_allowed is True
    assert runtime.last_v2_oracle_artifact is not None
    assert runtime.last_v2_oracle_artifact.oracle_result.utility.disposition.value == "completed"


async def test_stage7_provider_fails_closed_without_real_prior_result() -> None:
    provider = OfficeV2Stage7Provider.from_request(_request("clean.t9.apollo"))
    tools = ()

    with pytest.raises(AdapterExecutionError, match="required tool is unavailable"):
        await provider.generate(
            (ReactMessage(role="user", content="task"),), tools, seed=0
        )


def test_factory_stage7_identity_gate_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request("clean.t9.apollo")
    monkeypatch.setenv("TRACE_G_FORMAL_AGENT", "1")
    monkeypatch.setenv("TRACE_G_STAGE7_DETERMINISTIC_PROVIDER", "1")
    adapter = AdapterFactory().create(request)
    assert isinstance(adapter, LangGraphReactRuntime)

    wrong = request.model_copy(
        update={"model": request.model.model_copy(update={"model_name": "other"})}
    )
    with pytest.raises(
        AdapterConfigurationError, match="locked Stage 7 deterministic identity"
    ):
        AdapterFactory().create(wrong)
