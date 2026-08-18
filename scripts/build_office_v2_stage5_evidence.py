# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

# Stage 5 feasibility drivers intentionally remain in tests/ and never enter Agent code.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_ATTACK_CASE_CATALOG_VERSION,
    OFFICE_V2_ATTACK_CONTRACT_VERSION,
    OFFICE_V2_ATTACK_FIELD_REGISTRY_VERSION,
    OFFICE_V2_ATTACK_MATERIALIZER_VERSION,
    OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION,
    OFFICE_V2_REACHABILITY_VERSION,
)
from sandbox.scenarios.office_v2.adversarial_conditions import field_value
from sandbox.scenarios.office_v2.attack_cases import (
    RepresentativeScenarioFixture,
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.attack_compatibility import solve_compatibility
from sandbox.scenarios.office_v2.attack_models import (
    AttackEntryKind,
    CompatibilityPurpose,
    ParameterSourceManipulationCondition,
)
from sandbox.scenarios.office_v2.attack_objectives import (
    ATTACK_OBJECTIVE_CATALOG,
    ATTACK_OBJECTIVE_CATALOG_DIGEST,
    ATTACK_OBJECTIVES,
    STATE_WRITE_TOOLS,
)
from sandbox.scenarios.office_v2.attack_surface import (
    ATTACKABLE_FIELD_CATALOG,
    ATTACKABLE_FIELD_CATALOG_DIGEST,
    ATTACKABLE_FIELDS,
    REACHABLE_ATTACK_SURFACE_CATALOG,
    REACHABLE_ATTACK_SURFACE_CATALOG_DIGEST,
    REACHABLE_ATTACK_SURFACES,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from tests.integration.test_office_v2_stage5_representatives import (
    _observed_resource,
    _run_witness,
)

STAGE4_EVIDENCE = (
    REPOSITORY_ROOT / "reports" / "local-acceptance" / "office-v2-stage4" / "stage4-evidence.json"
)
FROZEN_WORLD_DIGEST = "sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106"
FROZEN_STAGE4_EVIDENCE_DIGEST = (
    "sha256:022763e692d764ff0e9045da242bf1272074142e9f498afd5917e83a68788077"
)


def _load_stage4() -> tuple[dict[str, Any], str]:
    payload = json.loads(STAGE4_EVIDENCE.read_text(encoding="utf-8"))
    digest = payload.pop("evidence_digest")
    if sha256_digest(payload) != digest or digest != FROZEN_STAGE4_EVIDENCE_DIGEST:
        raise RuntimeError("stage 4 evidence identity is invalid")
    return payload, digest


def _fixture_fact(fixture: RepresentativeScenarioFixture) -> dict[str, Any]:
    case = fixture.scenario_case
    transition = fixture.materialization.initialization_transition
    return {
        "fixture_id": fixture.fixture_id,
        "case_id": case.case_id,
        "case_digest": case.content_digest,
        "parent_case_id": case.parent_case_id,
        "parent_case_digest": case.parent_case_digest,
        "lineage": list(case.lineage),
        "actor_id": case.actor.actor_id,
        "actor_role_ids": list(case.actor.active_role_ids),
        "task_digest": case.task.canonical_digest(),
        "objective_id": case.attack_objective.objective_id,
        "objective_digest": case.attack_objective.content_digest,
        "entry_kind": case.adversarial_condition.entry_kind.value,
        "condition_digest": case.adversarial_condition.content_digest,
        "purpose": fixture.purpose.value,
        "authority_contrast": fixture.authority_contrast,
        "calibration_tags": list(fixture.calibration_tags),
        "structure_key": asdict(fixture.structure_key),
        "task_bindings": [
            item.model_dump(mode="json", exclude_none=False) for item in case.task_bindings
        ],
        "objective_bindings": [
            item.model_dump(mode="json", exclude_none=False) for item in case.objective_bindings
        ],
        "compatibility": fixture.compatibility_decision.model_dump(mode="json", exclude_none=False),
        "materialization": case.materialization_record.model_dump(mode="json", exclude_none=False),
        "initialization_transition": (
            None if transition is None else transition.model_dump(mode="json", exclude_none=False)
        ),
    }


def _counterfactuals(
    fixtures: tuple[RepresentativeScenarioFixture, ...],
) -> list[dict[str, Any]]:
    canonical = load_canonical_world()
    selected = {
        item.scenario_case.adversarial_condition.entry_kind: item
        for item in fixtures
        if item.scenario_case.attack_objective.objective_id.startswith("objective.a01.")
    }
    facts = []
    for kind in AttackEntryKind:
        fixture = selected[kind]
        case = fixture.scenario_case
        parent = CLEAN_CASE_BY_ID[case.parent_case_id]
        if kind is AttackEntryKind.DIRECT_TASK:
            facts.append(
                {
                    "entry_kind": kind.value,
                    "fixture_id": fixture.fixture_id,
                    "clean_task_digest": parent.task.canonical_digest(),
                    "attack_task_digest": case.task.canonical_digest(),
                    "clean_world_digest": canonical.state.canonical_digest(),
                    "attack_world_digest": case.initial_world_digest,
                    "task_changed": parent.task != case.task,
                    "world_changed": False,
                    "changed_fields": [],
                }
            )
            continue
        changes = case.materialization_record.changed_content_fields
        observations = []
        for change in changes:
            ref = change.placement.resource_ref
            clean = _observed_resource(canonical.state, case.actor, ref)
            attack = _observed_resource(fixture.materialization.initial_state, case.actor, ref)
            observations.append(
                {
                    "placement": change.placement.model_dump(mode="json"),
                    "clean_observation_digest": clean.canonical_digest(),
                    "attack_observation_digest": attack.canonical_digest(),
                    "agent_visible_changed": clean != attack,
                }
            )
        facts.append(
            {
                "entry_kind": kind.value,
                "fixture_id": fixture.fixture_id,
                "clean_task_digest": parent.task.canonical_digest(),
                "attack_task_digest": case.task.canonical_digest(),
                "clean_world_digest": canonical.state.canonical_digest(),
                "attack_world_digest": case.initial_world_digest,
                "task_changed": parent.task != case.task,
                "world_changed": True,
                "changed_fields": observations,
                "authority_assertions": list(
                    case.materialization_record.authoritative_state_assertions
                ),
            }
        )
    return facts


def _compatibility_rejections(
    fixtures: tuple[RepresentativeScenarioFixture, ...],
) -> list[dict[str, Any]]:
    state = load_canonical_world().state
    selections = (
        next(
            item
            for item in fixtures
            if item.scenario_case.attack_objective.objective_id.startswith("objective.a01.")
            and item.scenario_case.adversarial_condition.entry_kind is AttackEntryKind.DIRECT_TASK
        ),
        next(
            item
            for item in fixtures
            if item.scenario_case.attack_objective.objective_id.startswith("objective.a09.")
        ),
    )
    purposes = (
        CompatibilityPurpose.REALIZED_WITNESS,
        CompatibilityPurpose.BLOCKED_CALIBRATION,
    )
    facts = []
    for fixture, purpose in zip(selections, purposes, strict=True):
        case = fixture.scenario_case
        parent = CLEAN_CASE_BY_ID[case.parent_case_id]
        decision = solve_compatibility(
            parent,
            case.attack_objective,
            case.adversarial_condition,
            state=state,
            surface=case.reachable_surface,
            purpose=purpose,
        )
        facts.append(decision.model_dump(mode="json", exclude_none=False))
    return facts


def _authorization_contrast(
    fixtures: tuple[RepresentativeScenarioFixture, ...], stage4: dict[str, Any]
) -> dict[str, Any]:
    forged = next(
        item
        for item in fixtures
        if item.scenario_case.attack_objective.objective_id.startswith("objective.a01.")
        and item.scenario_case.adversarial_condition.entry_kind
        is AttackEntryKind.FORGED_AUTHORIZATION
    )
    real = next(
        item for item in stage4["interactions"] if item["example_id"] == "authorization-apollo"
    )
    untrusted = next(
        item
        for item in stage4["interactions"]
        if item["example_id"] == "untrusted-business-content"
    )
    before_grants = len(load_canonical_world().state.delegation_grants)
    after_grants = len(forged.materialization.initial_state.delegation_grants)
    return {
        "forged_fixture_id": forged.fixture_id,
        "forged_condition": forged.scenario_case.adversarial_condition.model_dump(
            mode="json", exclude_none=False
        ),
        "forged_grant_count_before": before_grants,
        "forged_grant_count_after": after_grants,
        "forged_created_grant": after_grants != before_grants,
        "authenticated_reply": {
            "example_id": real["example_id"],
            "status": real["trusted_outcome"]["status"],
            "before_state_digest": real["trusted_outcome"]["before_state_digest"],
            "after_state_digest": real["trusted_outcome"]["after_state_digest"],
        },
        "business_content_reply": {
            "example_id": untrusted["example_id"],
            "status": untrusted["trusted_outcome"]["status"],
            "before_state_digest": untrusted["trusted_outcome"]["before_state_digest"],
            "after_state_digest": untrusted["trusted_outcome"]["after_state_digest"],
        },
    }


def _parameter_contrast(
    fixtures: tuple[RepresentativeScenarioFixture, ...],
) -> dict[str, Any]:
    fixture = next(
        item
        for item in fixtures
        if item.scenario_case.attack_objective.objective_id.startswith("objective.a01.")
        and item.scenario_case.adversarial_condition.entry_kind
        is AttackEntryKind.PARAMETER_SOURCE_MANIPULATION
    )
    condition = fixture.scenario_case.adversarial_condition
    if not isinstance(condition, ParameterSourceManipulationCondition):
        raise RuntimeError("parameter representative has the wrong condition type")
    canonical = load_canonical_world().state
    verification = [
        {
            "placement": item.model_dump(mode="json"),
            "value_digest": sha256_digest(field_value(canonical, item)),
        }
        for item in condition.verification_sources
    ]
    return {
        "fixture_id": fixture.fixture_id,
        "semantic_parameter": condition.semantic_parameter.value,
        "source_placement": condition.source_placement.model_dump(mode="json"),
        "original_value_digest": condition.original_value_digest,
        "visible_value_digest": condition.visible_value_digest,
        "verification_sources": verification,
        "verification_independent": condition.source_placement
        not in condition.verification_sources,
        "overlay_frozen_before_execution": True,
    }


def _structural_gates(
    fixtures: tuple[RepresentativeScenarioFixture, ...],
    full_witnesses: list[dict[str, Any]],
    partial_witnesses: list[dict[str, Any]],
) -> dict[str, Any]:
    entry_counts = Counter(
        item.scenario_case.adversarial_condition.entry_kind.value for item in fixtures
    )
    indirect_domains = Counter(
        change.placement.resource_ref.kind.value
        for item in fixtures
        if item.scenario_case.adversarial_condition.entry_kind is AttackEntryKind.INDIRECT_CONTENT
        for change in item.scenario_case.materialization_record.changed_content_fields
    )
    parameter_kinds = sorted(
        item.scenario_case.adversarial_condition.semantic_parameter.value
        for item in fixtures
        if isinstance(
            item.scenario_case.adversarial_condition,
            ParameterSourceManipulationCondition,
        )
    )
    return {
        "objective_count": len(ATTACK_OBJECTIVES),
        "compound_objective_count": sum(
            item.milestone_graph is not None for item in ATTACK_OBJECTIVES
        ),
        "write_tool_count": len(STATE_WRITE_TOOLS),
        "write_tool_names": sorted(STATE_WRITE_TOOLS),
        "field_count": len(ATTACKABLE_FIELDS),
        "surface_count": len(REACHABLE_ATTACK_SURFACES),
        "reachable_field_count": sum(len(item.fields) for item in REACHABLE_ATTACK_SURFACES),
        "representative_count": len(fixtures),
        "structure_key_count": len({item.structure_key for item in fixtures}),
        "entry_counts": dict(sorted(entry_counts.items())),
        "indirect_domain_counts": dict(sorted(indirect_domains.items())),
        "parameter_kinds": parameter_kinds,
        "multi_position_count": sum(
            len(item.scenario_case.materialization_record.changed_content_fields) > 1
            for item in fixtures
        ),
        "clarification_count": sum(
            bool(
                CLEAN_CASE_BY_ID[
                    item.scenario_case.parent_case_id
                ].task.user_response_script.requests
            )
            for item in fixtures
        ),
        "actor_count": len({item.scenario_case.actor.actor_id for item in fixtures}),
        "authority_contrasts": sorted(
            {item.authority_contrast for item in fixtures if item.authority_contrast}
        ),
        "full_witness_count": len(full_witnesses),
        "partial_witness_count": len(partial_witnesses),
        "initialization_transition_count": sum(
            item.materialization.initialization_transition is not None for item in fixtures
        ),
        "canonical_unchanged": load_canonical_world().world_digest == FROZEN_WORLD_DIGEST,
        "parents_unchanged": all(
            CLEAN_CASE_BY_ID[item.scenario_case.parent_case_id].case_digest
            == item.scenario_case.parent_case_digest
            for item in fixtures
        ),
        "siblings_unique": len({item.scenario_case.case_id for item in fixtures}) == len(fixtures),
    }


def build_stage5_evidence() -> dict[str, Any]:
    stage4, stage4_digest = _load_stage4()
    canonical = load_canonical_world()
    canonical_before = canonical.world_digest
    fixtures = build_representative_scenario_fixtures()
    full_witnesses = [asdict(_run_witness(item, partial=False)) for item in ATTACK_OBJECTIVES]
    compound = tuple(item for item in ATTACK_OBJECTIVES if item.milestone_graph is not None)
    partial_witnesses = [asdict(_run_witness(item, partial=True)) for item in compound]
    payload: dict[str, Any] = {
        "schema_version": "office-v2-stage5-evidence-v1",
        "evidence_class": "local_deterministic_scenario_and_toolruntime_witness",
        "limitations": {
            "real_model_used": False,
            "docker_used": False,
            "scripted_driver_proves_model_understanding": False,
            "witness_proves_toolruntime_feasibility_only": True,
            "stage6_oracle_used": False,
            "coverage_or_mutation_used": False,
            "representatives_are_production_search_space": False,
        },
        "identity": {
            "world_digest": canonical.world_digest,
            "stage4_evidence_digest": stage4_digest,
            "attack_contract_version": OFFICE_V2_ATTACK_CONTRACT_VERSION,
            "objective_catalog_version": OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION,
            "objective_catalog_digest": ATTACK_OBJECTIVE_CATALOG_DIGEST,
            "field_registry_version": OFFICE_V2_ATTACK_FIELD_REGISTRY_VERSION,
            "field_registry_digest": ATTACKABLE_FIELD_CATALOG_DIGEST,
            "reachability_version": OFFICE_V2_REACHABILITY_VERSION,
            "surface_catalog_digest": REACHABLE_ATTACK_SURFACE_CATALOG_DIGEST,
            "case_catalog_version": OFFICE_V2_ATTACK_CASE_CATALOG_VERSION,
            "materializer_version": OFFICE_V2_ATTACK_MATERIALIZER_VERSION,
        },
        "objective_catalog": ATTACK_OBJECTIVE_CATALOG.model_dump(mode="json", exclude_none=False),
        "field_registry": ATTACKABLE_FIELD_CATALOG.model_dump(mode="json", exclude_none=False),
        "reachable_surfaces": REACHABLE_ATTACK_SURFACE_CATALOG.model_dump(
            mode="json", exclude_none=False
        ),
        "representative_fixtures": [_fixture_fact(item) for item in fixtures],
        "compatibility_rejections": _compatibility_rejections(fixtures),
        "entry_counterfactuals": _counterfactuals(fixtures),
        "authorization_contrast": _authorization_contrast(fixtures, stage4),
        "parameter_contrast": _parameter_contrast(fixtures),
        "full_witnesses": full_witnesses,
        "partial_witnesses": partial_witnesses,
        "structural_gates": _structural_gates(
            fixtures,
            full_witnesses,
            partial_witnesses,
        ),
        "invariance": {
            "canonical_digest_before": canonical_before,
            "canonical_digest_after": load_canonical_world().world_digest,
            "parent_case_digests": {
                item.scenario_case.parent_case_id: CLEAN_CASE_BY_ID[
                    item.scenario_case.parent_case_id
                ].case_digest
                for item in fixtures
            },
        },
    }
    validate_stage5_evidence(payload)
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def validate_stage5_evidence(payload: dict[str, Any]) -> None:
    identity = payload["identity"]
    gates = payload["structural_gates"]
    if identity != {
        "world_digest": FROZEN_WORLD_DIGEST,
        "stage4_evidence_digest": FROZEN_STAGE4_EVIDENCE_DIGEST,
        "attack_contract_version": OFFICE_V2_ATTACK_CONTRACT_VERSION,
        "objective_catalog_version": OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION,
        "objective_catalog_digest": ATTACK_OBJECTIVE_CATALOG_DIGEST,
        "field_registry_version": OFFICE_V2_ATTACK_FIELD_REGISTRY_VERSION,
        "field_registry_digest": ATTACKABLE_FIELD_CATALOG_DIGEST,
        "reachability_version": OFFICE_V2_REACHABILITY_VERSION,
        "surface_catalog_digest": REACHABLE_ATTACK_SURFACE_CATALOG_DIGEST,
        "case_catalog_version": OFFICE_V2_ATTACK_CASE_CATALOG_VERSION,
        "materializer_version": OFFICE_V2_ATTACK_MATERIALIZER_VERSION,
    }:
        raise ValueError("Stage 5 identity gate failed")
    if (
        gates["objective_count"] != 12
        or gates["compound_objective_count"] != 6
        or gates["write_tool_count"] != 9
        or gates["field_count"] != 19
        or gates["surface_count"] != 24
        or gates["reachable_field_count"] != 570
        or gates["representative_count"] != 24
        or gates["structure_key_count"] != 24
        or gates["full_witness_count"] != 12
        or gates["partial_witness_count"] != 6
    ):
        raise ValueError("Stage 5 quantity gate failed")
    if set(gates["write_tool_names"]) != STATE_WRITE_TOOLS:
        raise ValueError("nine-write-tool gate failed")
    if any(count < 4 for count in gates["entry_counts"].values()):
        raise ValueError("four-entry representative gate failed")
    if set(gates["indirect_domain_counts"]) != {
        "mail_message",
        "drive_file",
        "calendar_event",
        "workspace_file",
    } or any(count < 2 for count in gates["indirect_domain_counts"].values()):
        raise ValueError("four-domain indirect placement gate failed")
    if set(gates["parameter_kinds"]) != {
        "recipient",
        "participant_set",
        "start_time",
        "end_time",
        "resource_reference",
    }:
        raise ValueError("parameter semantic gate failed")
    if (
        gates["multi_position_count"] < 2
        or gates["clarification_count"] < 4
        or gates["actor_count"] < 4
        or set(gates["authority_contrasts"]) != {"acl", "task-delegation", "active-grant"}
        or gates["canonical_unchanged"] is not True
        or gates["parents_unchanged"] is not True
        or gates["siblings_unique"] is not True
    ):
        raise ValueError("representative diversity or invariance gate failed")
    counterfactuals = payload["entry_counterfactuals"]
    if {item["entry_kind"] for item in counterfactuals} != {item.value for item in AttackEntryKind}:
        raise ValueError("four-entry counterfactual gate failed")
    direct = next(item for item in counterfactuals if item["entry_kind"] == "direct_task")
    if not direct["task_changed"] or direct["world_changed"]:
        raise ValueError("direct-task counterfactual is invalid")
    if any(
        not all(change["agent_visible_changed"] for change in item["changed_fields"])
        for item in counterfactuals
        if item["entry_kind"] != "direct_task"
    ):
        raise ValueError("content-entry observation counterfactual is invalid")
    authorization = payload["authorization_contrast"]
    if (
        authorization["forged_created_grant"] is not False
        or authorization["authenticated_reply"]["status"] != "grant_created"
        or authorization["business_content_reply"]["status"] != "rejected"
        or authorization["business_content_reply"]["before_state_digest"]
        != authorization["business_content_reply"]["after_state_digest"]
    ):
        raise ValueError("forged/real authorization contrast is invalid")
    parameter = payload["parameter_contrast"]
    if (
        parameter["original_value_digest"] == parameter["visible_value_digest"]
        or parameter["verification_independent"] is not True
        or parameter["overlay_frozen_before_execution"] is not True
    ):
        raise ValueError("parameter source contrast is invalid")
    if any(
        item["before_state_digest"] == item["after_state_digest"]
        for item in (*payload["full_witnesses"], *payload["partial_witnesses"])
    ):
        raise ValueError("ToolRuntime witness did not change state")
    full_by_id = {item["objective_id"]: item for item in payload["full_witnesses"]}
    if set(full_by_id) != {item.objective_id for item in ATTACK_OBJECTIVES}:
        raise ValueError("12-objective witness gate failed")
    if any(
        len(item["completed_milestone_ids"])
        >= len(full_by_id[item["objective_id"]]["completed_milestone_ids"])
        for item in payload["partial_witnesses"]
    ):
        raise ValueError("compound partial milestone gate failed")
    if any(item["status"] == "compatible" for item in payload["compatibility_rejections"]):
        raise ValueError("compatibility rejection evidence is invalid")
    if (
        payload["invariance"]["canonical_digest_before"]
        != payload["invariance"]["canonical_digest_after"]
    ):
        raise ValueError("canonical world changed during Stage 5 evidence")
    if payload["limitations"] != {
        "real_model_used": False,
        "docker_used": False,
        "scripted_driver_proves_model_understanding": False,
        "witness_proves_toolruntime_feasibility_only": True,
        "stage6_oracle_used": False,
        "coverage_or_mutation_used": False,
        "representatives_are_production_search_space": False,
    }:
        raise ValueError("Stage 5 limitations are incomplete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check is not None:
        payload = json.loads(args.check.read_text(encoding="utf-8"))
        digest = payload.pop("evidence_digest")
        validate_stage5_evidence(payload)
        if sha256_digest(payload) != digest:
            raise ValueError("stage 5 evidence digest is invalid")
        print(digest)
        return 0
    evidence = build_stage5_evidence()
    rendered = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
