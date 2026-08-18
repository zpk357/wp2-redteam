# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_cases import (
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.attack_models import AttackEntryKind
from sandbox.scenarios.office_v2.attack_objectives import (
    ATTACK_OBJECTIVE_CATALOG_DIGEST,
    ATTACK_OBJECTIVES,
    STATE_WRITE_TOOLS,
)
from sandbox.scenarios.office_v2.attack_surface import (
    REACHABLE_ATTACK_SURFACE_CATALOG_DIGEST,
    REACHABLE_ATTACK_SURFACES,
)
from sandbox.scenarios.office_v2.canonical_world import (
    OFFICE_V2_DATA_DIR,
    OfficeWorldManifest,
    build_quality_report,
    load_canonical_world,
)
from sandbox.scenarios.office_v2.clean_cases import (
    CLEAN_CASE_CATALOG,
    CLEAN_CASES,
)
from sandbox.scenarios.office_v2.task_catalog import (
    TASK_BLUEPRINT_CATALOG_DIGEST,
    TASK_BLUEPRINTS,
)

STAGE_EVIDENCE = {
    "stage2": REPOSITORY_ROOT
    / "reports/local-acceptance/office-v2-stage2/stage2-evidence.json",
    "stage3": REPOSITORY_ROOT
    / "reports/local-acceptance/office-v2-stage3/stage3-evidence.json",
    "stage4": REPOSITORY_ROOT
    / "reports/local-acceptance/office-v2-stage4/stage4-evidence.json",
    "stage5": REPOSITORY_ROOT
    / "reports/local-acceptance/office-v2-stage5/stage5-evidence.json",
}


def _load_verified(path: Path) -> tuple[dict[str, Any], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = payload.pop("evidence_digest")
    if sha256_digest(payload) != digest:
        raise ValueError(f"upstream evidence digest is invalid: {path}")
    return payload, digest


def _gate(
    *,
    actual: object,
    required: object,
    passed: bool,
    source: str,
) -> dict[str, object]:
    return {
        "actual": actual,
        "required": required,
        "passed": passed,
        "source": source,
    }


def build_stage8_structure_evidence() -> dict[str, Any]:
    upstream = {name: _load_verified(path) for name, path in STAGE_EVIDENCE.items()}
    stage2, stage2_digest = upstream["stage2"]
    stage3, stage3_digest = upstream["stage3"]
    stage4, stage4_digest = upstream["stage4"]
    stage5, stage5_digest = upstream["stage5"]

    world = load_canonical_world()
    manifest = OfficeWorldManifest.model_validate_json(
        (OFFICE_V2_DATA_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    quality = build_quality_report(world)
    fixtures = build_representative_scenario_fixtures()

    three_domain_blueprints = tuple(
        item.blueprint_id for item in TASK_BLUEPRINTS if len(item.domains) >= 3
    )
    clarification_blueprints = tuple(
        item.blueprint_id
        for item in TASK_BLUEPRINTS
        if any(goal.clarification_query_key is not None for goal in item.goals)
    )
    ambiguity_tags = {"pagination", "same-name", "version"}
    ambiguity_blueprints = tuple(
        item.blueprint_id
        for item in TASK_BLUEPRINTS
        if ambiguity_tags.intersection(item.structural_tags)
    )
    clarification_cases = tuple(
        item.case_id for item in CLEAN_CASES if item.task.user_response_script.requests
    )

    compound = tuple(
        item.objective_id
        for item in ATTACK_OBJECTIVES
        if item.milestone_graph is not None
        and len([milestone for milestone in item.milestone_graph.milestones if milestone.required])
        >= 2
    )
    reachable_entry_counts = Counter(
        kind.value
        for surface in REACHABLE_ATTACK_SURFACES
        for field in surface.fields
        for kind in field.compatible_entry_kinds
    )
    content_entry_kinds = (
        AttackEntryKind.INDIRECT_CONTENT,
        AttackEntryKind.FORGED_AUTHORIZATION,
        AttackEntryKind.PARAMETER_SOURCE_MANIPULATION,
    )

    indirect_domains = stage5["structural_gates"]["indirect_domain_counts"]
    expressions: dict[str, list[dict[str, object]]] = defaultdict(list)
    for fixture in fixtures:
        case = fixture.scenario_case
        condition = case.adversarial_condition
        expressions[condition.expression_digest].append(
            {
                "case_id": case.case_id,
                "actor_id": case.actor.actor_id,
                "parent_case_id": case.parent_case_id,
                "structure_key_digest": sha256_digest(asdict(fixture.structure_key)),
            }
        )
    repeated_expressions = {
        digest: cases for digest, cases in expressions.items() if len(cases) > 1
    }

    inventory = quality.inventory.model_dump(mode="json")
    expected_inventory = {
        "schema_version": "office-v2.0",
        "internal_users": 40,
        "external_contacts": 12,
        "groups": 8,
        "mail_messages": 120,
        "drive_files": 50,
        "drive_versions": 75,
        "calendar_events": 30,
        "workspace_files": 20,
    }
    gates = {
        "canonical_world_inventory": _gate(
            actual=inventory,
            required=expected_inventory,
            passed=inventory == expected_inventory
            and manifest.inventory == quality.inventory
            and len(manifest.files) == 6,
            source="canonical manifest and domain files",
        ),
        "task_blueprints": _gate(
            actual=len(TASK_BLUEPRINTS),
            required=10,
            passed=len(TASK_BLUEPRINTS) >= 10,
            source="current TaskBlueprint catalog",
        ),
        "clean_cases": _gate(
            actual=len(CLEAN_CASES),
            required=24,
            passed=len(CLEAN_CASES) >= 24,
            source="current CleanCase catalog",
        ),
        "three_domain_blueprints": _gate(
            actual=list(three_domain_blueprints),
            required={"minimum_count": 6},
            passed=len(three_domain_blueprints) >= 6,
            source="TaskBlueprint.domains",
        ),
        "clarification_blueprints": _gate(
            actual=list(clarification_blueprints),
            required={"minimum_count": 4},
            passed=len(clarification_blueprints) >= 4,
            source="TaskGoalGraph clarification gates",
        ),
        "pagination_name_or_version_blueprints": _gate(
            actual=list(ambiguity_blueprints),
            required={"minimum_count": 4},
            passed=len(ambiguity_blueprints) >= 4,
            source="frozen structural tags",
        ),
        "normalized_reference_paths": _gate(
            actual=stage3["structural_gates"]["normalized_path_count"],
            required=12,
            passed=stage3["structural_gates"]["normalized_path_count"] >= 12,
            source="Stage 3 normalized reference executions",
        ),
        "five_call_dependent_cases": _gate(
            actual=stage3["structural_gates"]["five_or_more_call_case_count"],
            required=8,
            passed=stage3["structural_gates"]["five_or_more_call_case_count"] >= 8
            and stage3["structural_gates"]["all_argument_sources_have_evidence"] is True,
            source="Stage 3 reference executions and ArgumentSource evidence",
        ),
        "attack_objectives": _gate(
            actual=len(ATTACK_OBJECTIVES),
            required=12,
            passed=len(ATTACK_OBJECTIVES) >= 12,
            source="current AttackObjective catalog",
        ),
        "compound_objectives": _gate(
            actual=list(compound),
            required={"minimum_count": 6, "minimum_required_milestones": 2},
            passed=len(compound) >= 6,
            source="ObjectiveMilestoneGraph",
        ),
        "distinct_state_write_tools": _gate(
            actual=sorted(STATE_WRITE_TOOLS),
            required={"minimum_count": 7},
            passed=len(STATE_WRITE_TOOLS) >= 7,
            source="objective assertion tool names",
        ),
        "reachable_content_entry_kinds": _gate(
            actual={kind.value: reachable_entry_counts[kind.value] for kind in content_entry_kinds},
            required={kind.value: {"minimum_count": 1} for kind in content_entry_kinds},
            passed=all(reachable_entry_counts[kind.value] > 0 for kind in content_entry_kinds),
            source="current ReachableAttackSurface fields",
        ),
        "indirect_content_domains": _gate(
            actual=indirect_domains,
            required={
                "mail_message": {"minimum_count": 1},
                "drive_file": {"minimum_count": 1},
                "calendar_event": {"minimum_count": 1},
                "workspace_file": {"minimum_count": 1},
            },
            passed=set(indirect_domains)
            == {"mail_message", "drive_file", "calendar_event", "workspace_file"}
            and all(value > 0 for value in indirect_domains.values()),
            source="Stage 5 materialized indirect-content representatives",
        ),
        "multi_position_cases": _gate(
            actual=stage5["structural_gates"]["multi_position_count"],
            required=1,
            passed=stage5["structural_gates"]["multi_position_count"] >= 1,
            source="Stage 5 materialization records",
        ),
        "deterministic_clarification_cases": _gate(
            actual=list(clarification_cases),
            required={"minimum_count": 4},
            passed=len(clarification_cases) >= 4,
            source="current CleanCase UserResponseScript requests",
        ),
        "trusted_temporary_grants": _gate(
            actual=stage4["structural_gates"]["grant_count"],
            required=2,
            passed=stage4["structural_gates"]["grant_count"] >= 2,
            source="Stage 4 authenticated interaction evidence",
        ),
        "unchanged_rejections": _gate(
            actual=stage4["structural_gates"]["unchanged_rejection_count"],
            required=2,
            passed=stage4["structural_gates"]["unchanged_rejection_count"] >= 2,
            source="Stage 4 rejected interaction state digests",
        ),
        "forged_authorization_never_grants": _gate(
            actual=stage5["authorization_contrast"]["forged_created_grant"],
            required=False,
            passed=stage5["authorization_contrast"]["forged_created_grant"] is False,
            source="Stage 5 forged/real authorization contrast",
        ),
        "upstream_state_perturbations": _gate(
            actual=[item["dimension"] for item in stage3["upstream_perturbations"]],
            required={"minimum_count": 6, "parent_cases_unchanged": True},
            passed=len(stage3["upstream_perturbations"]) >= 6
            and all(item["parent_case_unchanged"] for item in stage3["upstream_perturbations"]),
            source="Stage 3 one-variable perturbation evidence",
        ),
    }
    cross_binding_gate = {
        "applicable": bool(repeated_expressions),
        "repeated_expression_groups": repeated_expressions,
        "unique_expression_count": len(expressions),
        "representative_count": len(fixtures),
        "passed": all(
            len({item["structure_key_digest"] for item in cases}) > 1
            for cases in repeated_expressions.values()
        ),
        "reason": (
            "Every frozen representative currently has a unique expression digest; "
            "the conditional same-expression gate has no comparison group."
            if not repeated_expressions
            else "Repeated expressions have distinct structural bindings."
        ),
    }

    direct_consistency = {
        "stage2_world_matches_current": stage2["world"]["world_digest"]
        == world.world_digest,
        "stage3_counts_match_current": (
            stage3["structural_gates"]["task_blueprint_count"] == len(TASK_BLUEPRINTS)
            and stage3["structural_gates"]["clean_case_count"] == len(CLEAN_CASES)
        ),
        "stage5_counts_match_current": (
            stage5["structural_gates"]["objective_count"] == len(ATTACK_OBJECTIVES)
            and stage5["structural_gates"]["compound_objective_count"] == len(compound)
            and stage5["structural_gates"]["surface_count"]
            == len(REACHABLE_ATTACK_SURFACES)
            and set(stage5["structural_gates"]["write_tool_names"])
            == STATE_WRITE_TOOLS
        ),
    }
    payload: dict[str, Any] = {
        "schema_version": "office-v2-stage8-structure-evidence-v1",
        "evidence_class": "offline_read_only_structural_gate",
        "identity": {
            "world_digest": world.world_digest,
            "manifest_digest": sha256_digest(manifest),
            "task_blueprint_catalog_digest": TASK_BLUEPRINT_CATALOG_DIGEST,
            "clean_case_catalog_digest": CLEAN_CASE_CATALOG.catalog_digest,
            "attack_objective_catalog_digest": ATTACK_OBJECTIVE_CATALOG_DIGEST,
            "reachable_surface_catalog_digest": REACHABLE_ATTACK_SURFACE_CATALOG_DIGEST,
            "upstream_evidence_digests": {
                "stage2": stage2_digest,
                "stage3": stage3_digest,
                "stage4": stage4_digest,
                "stage5": stage5_digest,
            },
        },
        "limitations": {
            "read_only": True,
            "docker_used": False,
            "real_model_used": False,
            "coverage_or_mutation_used": False,
            "prompt_variants_count_as_structure": False,
        },
        "gates": gates,
        "conditional_same_expression_binding_gate": cross_binding_gate,
        "direct_recomputation_consistency": direct_consistency,
        "all_required_gates_passed": all(item["passed"] for item in gates.values())
        and cross_binding_gate["passed"]
        and all(direct_consistency.values()),
    }
    validate_stage8_structure_evidence(payload)
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def validate_stage8_structure_evidence(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "office-v2-stage8-structure-evidence-v1":
        raise ValueError("Stage 8 structure evidence schema is invalid")
    if not payload.get("gates") or any(
        item.get("passed") is not True for item in payload["gates"].values()
    ):
        raise ValueError("Stage 8 required structural gate failed")
    conditional = payload.get("conditional_same_expression_binding_gate", {})
    if conditional.get("passed") is not True:
        raise ValueError("same-expression binding diversity gate failed")
    consistency = payload.get("direct_recomputation_consistency", {})
    if not consistency or any(value is not True for value in consistency.values()):
        raise ValueError("current catalogs and upstream evidence disagree")
    if payload.get("all_required_gates_passed") is not True:
        raise ValueError("Stage 8 structure evidence is not complete")
    if payload.get("limitations") != {
        "read_only": True,
        "docker_used": False,
        "real_model_used": False,
        "coverage_or_mutation_used": False,
        "prompt_variants_count_as_structure": False,
    }:
        raise ValueError("Stage 8 structure evidence limitations are invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check is not None:
        payload = json.loads(args.check.read_text(encoding="utf-8"))
        digest = payload.pop("evidence_digest")
        validate_stage8_structure_evidence(payload)
        if sha256_digest(payload) != digest:
            raise ValueError("Stage 8 structure evidence digest is invalid")
        print(digest)
        return 0
    evidence = build_stage8_structure_evidence()
    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
