"""Public Office V2 case selection and execution-request construction."""

from __future__ import annotations

from dataclasses import dataclass

from sandbox.protocol import (
    OFFICE_V2_SCENARIO_ID,
    ExecutionRequest,
    ModelOptions,
    ModelProvider,
)
from sandbox.scenarios.office_v2.attack_cases import (
    RepresentativeScenarioFixture,
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.attack_models import MaterializedScenarioCase
from sandbox.scenarios.office_v2.canonical_world import OfficeWorldState, load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import (
    CLEAN_CASES,
    CleanCaseMaterialization,
)
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scenarios.office_v2.interaction_session import ScriptedResponseDirective
from sandbox.scenarios.office_v2.world import StateTransitionRecord

IN_CONTAINER_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"


class OfficeV2PublicEntryError(ValueError):
    """The requested public V2 case or option is invalid."""


@dataclass(frozen=True, slots=True)
class OfficeV2PublicCase:
    public_id: str
    case: CleanCaseMaterialization | MaterializedScenarioCase
    initial_state: OfficeWorldState
    initialization_transition: StateTransitionRecord | None

    @property
    def kind(self) -> str:
        return "attack" if isinstance(self.case, MaterializedScenarioCase) else "clean"


def office_v2_public_cases() -> tuple[OfficeV2PublicCase, ...]:
    clean = tuple(
        OfficeV2PublicCase(
            public_id=case.case_id,
            case=case,
            initial_state=load_canonical_world().state,
            initialization_transition=None,
        )
        for case in CLEAN_CASES
    )
    representative = tuple(
        _public_attack_case(item) for item in build_representative_scenario_fixtures()
    )
    return (*clean, *representative)


def office_v2_public_case(public_id: str) -> OfficeV2PublicCase:
    matches = tuple(item for item in office_v2_public_cases() if item.public_id == public_id)
    if len(matches) != 1:
        raise OfficeV2PublicEntryError(f"unknown Office V2 case: {public_id}")
    return matches[0]


def build_office_v2_public_request(
    selected: OfficeV2PublicCase,
    *,
    execution_id: str,
    model_name: str,
    model_digest: str,
    seed: int,
    max_steps: int,
    timeout_seconds: int,
    use_frozen_response: bool = False,
) -> ExecutionRequest:
    model = ModelOptions(
        provider=ModelProvider.OLLAMA,
        model_name=model_name,
        model_digest=model_digest,
        endpoint=IN_CONTAINER_OLLAMA_ENDPOINT,
        timeout_seconds=timeout_seconds,
    )
    directives = (
        _first_frozen_response(selected.case, execution_id)
        if use_frozen_response
        else ()
    )
    envelope = build_v2_execution_envelope(
        selected.case,
        initial_state=selected.initial_state,
        initialization_transition=selected.initialization_transition,
        model_identity=model,
        response_directives=directives,
    )
    return ExecutionRequest(
        execution_id=execution_id,
        case_id=selected.case.case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=selected.case.task.instruction,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
        metadata={
            "public_scenario_entry": "office-workspace-v2",
            "public_case_id": selected.public_id,
        },
        seed=seed,
        model=model,
        office_v2_execution=envelope,
    )


def _public_attack_case(fixture: RepresentativeScenarioFixture) -> OfficeV2PublicCase:
    return OfficeV2PublicCase(
        public_id=fixture.fixture_id,
        case=fixture.scenario_case,
        initial_state=fixture.materialization.initial_state,
        initialization_transition=fixture.materialization.initialization_transition,
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
        raise OfficeV2PublicEntryError("case has no frozen trusted response")
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
    "OfficeV2PublicCase",
    "OfficeV2PublicEntryError",
    "build_office_v2_public_request",
    "office_v2_public_case",
    "office_v2_public_cases",
]
