"""Shared Office V2 execution-request construction without catalog selection."""

from __future__ import annotations

from typing import Any

from sandbox.protocol import (
    OFFICE_V2_SCENARIO_ID,
    ExecutionRequest,
    ModelInferenceOptions,
    ModelOptions,
    ModelProvider,
)
from sandbox.scenarios.office_v2.attack_models import MaterializedScenarioCase
from sandbox.scenarios.office_v2.canonical_world import OfficeWorldState
from sandbox.scenarios.office_v2.clean_cases import CleanCaseMaterialization
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scenarios.office_v2.interaction_session import ScriptedResponseDirective
from sandbox.scenarios.office_v2.world import StateTransitionRecord

IN_CONTAINER_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"


def build_office_v2_execution_request(
    case: CleanCaseMaterialization | MaterializedScenarioCase,
    *,
    initial_state: OfficeWorldState,
    initialization_transition: StateTransitionRecord | None,
    execution_id: str,
    model_name: str,
    seed: int,
    max_steps: int,
    timeout_seconds: int,
    max_tool_calls: int = 24,
    metadata: dict[str, Any] | None = None,
    use_frozen_response: bool = False,
    model_provider: ModelProvider = ModelProvider.OLLAMA,
    model_endpoint: str | None = IN_CONTAINER_OLLAMA_ENDPOINT,
    model_inference: ModelInferenceOptions | None = None,
) -> ExecutionRequest:
    """Build one request from an already selected and materialized case."""

    model = ModelOptions(
        provider=model_provider,
        model_name=model_name,
        endpoint=model_endpoint,
        timeout_seconds=timeout_seconds,
        inference=model_inference,
    )
    directives = (
        _first_frozen_response(case, execution_id) if use_frozen_response else ()
    )
    envelope = build_v2_execution_envelope(
        case,
        initial_state=initial_state,
        initialization_transition=initialization_transition,
        model_identity=model,
        response_directives=directives,
    )
    return ExecutionRequest(
        execution_id=execution_id,
        case_id=case.case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=case.task.instruction,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
        metadata={} if metadata is None else metadata,
        seed=seed,
        model=model,
        office_v2_execution=envelope,
    )


def _first_frozen_response(
    case: CleanCaseMaterialization | MaterializedScenarioCase,
    execution_id: str,
) -> tuple[ScriptedResponseDirective, ...]:
    contract = (
        case.interaction_contract
        if isinstance(case, MaterializedScenarioCase)
        else case.task.user_response_script
    )
    if not contract.requests or not contract.response_rules:
        raise ValueError("case has no frozen trusted response")
    request = contract.requests[0]
    rule = contract.response_rules[0]
    return (
        ScriptedResponseDirective(
            request_id=request.request_id,
            rule_id=rule.rule_id,
            turn_id=f"turn.{execution_id}.frozen-response",
            responder_id=rule.authenticated_responder_id,
            authenticated_principal_id=rule.authenticated_responder_id,
        ),
    )


__all__ = [
    "IN_CONTAINER_OLLAMA_ENDPOINT",
    "build_office_v2_execution_request",
]
