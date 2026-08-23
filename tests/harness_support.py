from __future__ import annotations

from app.adapter.deepseek_harness_adapter import (
    HARNESS_MODEL_DIGEST,
    HARNESS_MODEL_NAME,
)
from app.protocol import ExecutionRequest, ModelOptions, ModelProvider

from sandbox.protocol import OFFICE_V2_SCENARIO_ID
from sandbox.scenarios.office_v2.attack_cases import (
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scenarios.office_v2.interaction_session import ScriptedResponseDirective


def harness_request(
    case_id: str = "clean.t10.borealis",
    *,
    trusted_reply: bool = False,
    fixture_flow: str | None = None,
) -> ExecutionRequest:
    case = CLEAN_CASE_BY_ID[case_id]
    model = ModelOptions(
        provider=ModelProvider.FAKE,
        model_name=HARNESS_MODEL_NAME,
        model_digest=HARNESS_MODEL_DIGEST,
    )
    directives: tuple[ScriptedResponseDirective, ...] = ()
    if trusted_reply:
        contract = case.task.user_response_script
        request = contract.requests[0]
        rule = contract.response_rules[0]
        directives = (
            ScriptedResponseDirective(
                request_id=request.request_id,
                rule_id=rule.rule_id,
                turn_id=f"turn.h4.{case_id}",
                responder_id=rule.authenticated_responder_id,
                authenticated_principal_id=rule.authenticated_responder_id,
            ),
        )
    envelope = build_v2_execution_envelope(
        case,
        initial_state=load_canonical_world().state,
        model_identity=model,
        response_directives=directives,
    )
    return ExecutionRequest(
        execution_id=f"episode.h4.{case_id}",
        case_id=case.case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=case.task.instruction,
        max_steps=15,
        timeout_seconds=30,
        metadata=(
            {"harness_fixture_flow": fixture_flow}
            if fixture_flow is not None
            else {}
        ),
        model=model,
        office_v2_execution=envelope,
    )


def harness_compound_request(mode: str) -> ExecutionRequest:
    if mode not in {"partial", "full"}:
        raise ValueError("compound mode must be partial or full")
    fixture = build_representative_scenario_fixtures()[7]
    case = fixture.scenario_case
    model = ModelOptions(
        provider=ModelProvider.FAKE,
        model_name=HARNESS_MODEL_NAME,
        model_digest=HARNESS_MODEL_DIGEST,
    )
    envelope = build_v2_execution_envelope(
        case,
        initial_state=fixture.materialization.initial_state,
        initialization_transition=fixture.materialization.initialization_transition,
        model_identity=model,
    )
    return ExecutionRequest(
        execution_id=f"episode.h4.compound.{mode}",
        case_id=case.case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=case.task.instruction,
        max_steps=15,
        timeout_seconds=30,
        metadata={"harness_fixture_flow": f"compound_{mode}"},
        model=model,
        office_v2_execution=envelope,
    )
