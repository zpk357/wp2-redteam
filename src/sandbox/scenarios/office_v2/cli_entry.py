"""Public Office V2 case selection and execution-request construction."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from sandbox.protocol import ExecutionRequest, ModelInferenceOptions, ModelProvider
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
from sandbox.scenarios.office_v2.execution_request import (
    IN_CONTAINER_OLLAMA_ENDPOINT,
    build_office_v2_execution_request,
)
from sandbox.scenarios.office_v2.world import StateTransitionRecord


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


@lru_cache(maxsize=1)
def office_v2_public_cases() -> tuple[OfficeV2PublicCase, ...]:
    """Return the frozen public case catalog without rematerializing it per lookup."""
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
    seed: int,
    max_steps: int,
    timeout_seconds: int,
    use_frozen_response: bool = False,
    model_provider: ModelProvider = ModelProvider.OLLAMA,
    model_endpoint: str | None = IN_CONTAINER_OLLAMA_ENDPOINT,
    model_inference: ModelInferenceOptions | None = None,
) -> ExecutionRequest:
    return build_office_v2_execution_request(
        selected.case,
        initial_state=selected.initial_state,
        initialization_transition=selected.initialization_transition,
        execution_id=execution_id,
        model_name=model_name,
        seed=seed,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
        metadata={
            "public_scenario_entry": "office-workspace-v2",
            "public_case_id": selected.public_id,
        },
        use_frozen_response=use_frozen_response,
        model_provider=model_provider,
        model_endpoint=model_endpoint,
        model_inference=model_inference,
    )


def _public_attack_case(fixture: RepresentativeScenarioFixture) -> OfficeV2PublicCase:
    return OfficeV2PublicCase(
        public_id=fixture.fixture_id,
        case=fixture.scenario_case,
        initial_state=fixture.materialization.initial_state,
        initialization_transition=fixture.materialization.initialization_transition,
    )


__all__ = [
    "IN_CONTAINER_OLLAMA_ENDPOINT",
    "OfficeV2PublicCase",
    "OfficeV2PublicEntryError",
    "build_office_v2_public_request",
    "office_v2_public_case",
    "office_v2_public_cases",
]
