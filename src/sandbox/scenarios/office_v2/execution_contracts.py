"""Build the strict Office V2 input carried by the existing execution RPC."""

from __future__ import annotations

from sandbox.protocol import ModelOptions, V2ExecutionEnvelope, V2ScenarioCaseKind
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_ORACLE_CONTRACT_VERSION,
    OFFICE_V2_ORACLE_EVIDENCE_VERSION,
)
from sandbox.scenarios.office_v2.attack_models import MaterializedScenarioCase
from sandbox.scenarios.office_v2.attack_objectives import ATTACK_OBJECTIVE_CATALOG_DIGEST
from sandbox.scenarios.office_v2.canonical_world import OfficeWorldState
from sandbox.scenarios.office_v2.clean_cases import CleanCaseMaterialization
from sandbox.scenarios.office_v2.interaction_session import ScriptedResponseDirective
from sandbox.scenarios.office_v2.world import StateTransitionRecord
from sandbox.tool_contracts import OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST

ScenarioCase = MaterializedScenarioCase | CleanCaseMaterialization


def build_v2_execution_envelope(
    scenario_case: ScenarioCase,
    *,
    initial_state: OfficeWorldState,
    model_identity: ModelOptions,
    initialization_transition: StateTransitionRecord | None = None,
    response_directives: tuple[ScriptedResponseDirective, ...] = (),
) -> V2ExecutionEnvelope:
    """Freeze one already-materialized case without deriving a tool plan."""

    is_attack = isinstance(scenario_case, MaterializedScenarioCase)
    scenario_case_digest = (
        scenario_case.content_digest if is_attack else scenario_case.case_digest
    )
    initial_state_digest = initial_state.canonical_digest()
    if is_attack:
        interaction_contract = scenario_case.interaction_contract
        materialization_digest = scenario_case.materialization_record.materialization_digest
    else:
        interaction_contract = scenario_case.task.user_response_script
        materialization_digest = scenario_case.case_digest

    directive_payload = tuple(
        item.model_dump(mode="json", exclude_none=False)
        for item in response_directives
    )
    return V2ExecutionEnvelope(
        scenario_case_kind=(
            V2ScenarioCaseKind.ATTACK if is_attack else V2ScenarioCaseKind.CLEAN
        ),
        scenario_case_id=scenario_case.case_id,
        scenario_case_digest=scenario_case_digest,
        scenario_case_payload=scenario_case.model_dump(mode="json", exclude_none=False),
        actor_id=scenario_case.actor.actor_id,
        task_id=scenario_case.task.task_id,
        task_digest=scenario_case.task.canonical_digest(),
        base_world_digest=scenario_case.base_world_digest,
        initial_state_digest=initial_state_digest,
        initial_state_payload=initial_state.model_dump(mode="json", exclude_none=False),
        initialization_transition_payload=(
            initialization_transition.model_dump(mode="json", exclude_none=False)
            if initialization_transition is not None
            else None
        ),
        materialization_digest=materialization_digest,
        interaction_contract_digest=interaction_contract.canonical_digest(),
        interaction_response_directives=directive_payload,
        interaction_response_directives_digest=sha256_digest(directive_payload),
        tool_catalog_digest=OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
        objective_catalog_digest=ATTACK_OBJECTIVE_CATALOG_DIGEST,
        oracle_contract_version=OFFICE_V2_ORACLE_CONTRACT_VERSION,
        oracle_evidence_version=OFFICE_V2_ORACLE_EVIDENCE_VERSION,
        model_identity=model_identity,
    )


__all__ = ["ScenarioCase", "build_v2_execution_envelope"]
