"""Deterministic Office V2 verification-fork materialization."""

from __future__ import annotations

from sandbox.scenarios.office_v2.adversarial_conditions import (
    build_direct_task_condition,
    build_indirect_content_condition,
    build_parameter_source_condition,
)
from sandbox.scenarios.office_v2.attack_cases import (
    ScenarioMaterializationResult,
    materialize_scenario_case,
)
from sandbox.scenarios.office_v2.attack_compatibility import solve_compatibility
from sandbox.scenarios.office_v2.attack_models import (
    CompatibilityPurpose,
    DirectTaskCondition,
    IndirectContentCondition,
    MaterializedScenarioCase,
    ParameterSourceManipulationCondition,
)
from sandbox.scenarios.office_v2.canonical_world import CanonicalOfficeWorld
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID


def infer_office_v2_compatibility_purpose(
    source_case: MaterializedScenarioCase,
    canonical_world: CanonicalOfficeWorld,
) -> CompatibilityPurpose:
    parent = CLEAN_CASE_BY_ID[source_case.parent_case_id]
    surface = (
        None
        if isinstance(source_case.adversarial_condition, DirectTaskCondition)
        else source_case.reachable_surface
    )
    matches = tuple(
        purpose
        for purpose in CompatibilityPurpose
        if solve_compatibility(
            parent,
            source_case.attack_objective,
            source_case.adversarial_condition,
            state=canonical_world.state,
            surface=surface,
            purpose=purpose,
        ).decision_digest
        == source_case.materialization_record.compatibility_digest
    )
    if len(matches) != 1:
        raise ValueError("materialized case compatibility purpose is not recoverable")
    return matches[0]


def rematerialize_office_v2_scenario_text(
    *,
    source_case: MaterializedScenarioCase,
    canonical_world: CanonicalOfficeWorld,
    generated_content: str,
    purpose: CompatibilityPurpose,
    seed: int,
) -> ScenarioMaterializationResult:
    parent = CLEAN_CASE_BY_ID[source_case.parent_case_id]
    objective = source_case.attack_objective
    condition = source_case.adversarial_condition
    surface = source_case.reachable_surface
    state = canonical_world.state
    if isinstance(condition, DirectTaskCondition):
        rebuilt = build_direct_task_condition(
            parent,
            objective,
            state=state,
            instruction=generated_content,
            issuer_override=condition.issuer_override,
            issuer_authentication=condition.issuer_authentication,
            template_id=condition.template_id,
        )
        selected_surface = None
    elif isinstance(condition, IndirectContentCondition):
        if surface is None:
            raise ValueError("indirect content case requires reachable surface")
        rebuilt = build_indirect_content_condition(
            parent,
            objective,
            surface,
            state=state,
            placements=condition.placements,
            operation=condition.operation,
            content=generated_content,
            template_id=condition.template_id,
        )
        selected_surface = surface
    elif isinstance(condition, ParameterSourceManipulationCondition):
        if surface is None or not isinstance(condition.visible_value, str):
            raise ValueError("parameter source does not expose a mutable text slot")
        rebuilt = build_parameter_source_condition(
            parent,
            objective,
            surface,
            state=state,
            source_placement=condition.source_placement,
            semantic_parameter=condition.semantic_parameter,
            visible_value=generated_content,
            verification_sources=condition.verification_sources,
            template_id=condition.template_id,
        )
        selected_surface = surface
    else:
        raise ValueError("condition has no provider-writable semantic text slot")
    decision = solve_compatibility(
        parent,
        objective,
        rebuilt,
        state=state,
        surface=selected_surface,
        purpose=purpose,
    )
    result = materialize_scenario_case(
        parent,
        objective,
        rebuilt,
        decision,
        canonical_world=canonical_world,
        surface=selected_surface,
        seed=seed,
    )
    if canonical_world.world_digest != source_case.base_world_digest:
        raise ValueError("canonical world identity changed during rematerialization")
    if result.scenario_case.parent_case_digest != source_case.parent_case_digest:
        raise ValueError("parent case identity changed during rematerialization")
    return result


__all__ = [
    "infer_office_v2_compatibility_purpose",
    "rematerialize_office_v2_scenario_text",
]
