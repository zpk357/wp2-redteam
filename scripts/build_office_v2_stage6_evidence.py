# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_ORACLE_CONTRACT_VERSION,
    OFFICE_V2_ORACLE_EVIDENCE_VERSION,
)
from sandbox.scenarios.office_v2.attack_models import AttackEntryKind
from sandbox.scenarios.office_v2.attack_objectives import ATTACK_OBJECTIVES
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASES
from sandbox.scenarios.office_v2.models import DecisionMode
from sandbox.scenarios.office_v2.oracle import evaluate_scenario_oracle
from sandbox.scenarios.office_v2.oracle_evidence import (
    OracleEvidenceIntegrityError,
    build_oracle_evidence_bundle,
)
from sandbox.scenarios.office_v2.oracle_models import MilestoneOutcome, ViolationKind
from sandbox.scenarios.office_v2.oracle_trace import (
    build_oracle_evidence_from_trace,
    rebuild_oracle_evidence_bundle,
)
from sandbox.scenarios.office_v2.security_oracle import (
    evaluate_exposure,
    evaluate_planned_objective,
    evaluate_policy_violations,
)
from sandbox.scenarios.office_v2.tools.contracts import ToolFailureCode, ToolResultStatus
from tests.integration.test_office_v2_stage6_acceptance import (
    _evaluate_clean_reference_case,
)
from tests.unit.test_office_v2_exposure_evaluator import (
    _bundle as _exposure_bundle,
)
from tests.unit.test_office_v2_exposure_evaluator import (
    _materialization as _exposure_materialization,
)
from tests.unit.test_office_v2_exposure_evaluator import (
    _placement as _exposure_placement,
)
from tests.unit.test_office_v2_milestone_evaluator import (
    _blocked_bundle,
    _bundle_for_steps,
    _ordered_steps,
)
from tests.unit.test_office_v2_oracle_trace import _recording
from tests.unit.test_office_v2_policy_violations import (
    _decision,
    _single_exchange_bundle,
    _with_decision,
)
from tests.unit.test_office_v2_utility_evaluator import _t10_bundle

STAGE5_EVIDENCE = (
    REPOSITORY_ROOT / "reports" / "local-acceptance" / "office-v2-stage5" / "stage5-evidence.json"
)
FROZEN_STAGE5_EVIDENCE_DIGEST = (
    "sha256:b44931cdd4e7e2173771f2b356860dc937fb253ff4a0fce8cf9a2f77bb50fe04"
)


def _load_stage5_digest() -> str:
    payload = json.loads(STAGE5_EVIDENCE.read_text(encoding="utf-8"))
    digest = payload.pop("evidence_digest")
    if digest != FROZEN_STAGE5_EVIDENCE_DIGEST or sha256_digest(payload) != digest:
        raise RuntimeError("stage 5 evidence identity is invalid")
    return digest


def _clean_case_facts() -> list[dict[str, Any]]:
    facts = []
    for clean_case in CLEAN_CASES:
        bundle, result = _evaluate_clean_reference_case(clean_case)
        active = tuple(
            item
            for item in result.utility.goal_facts
            if item.required and item.status.value != "inactive"
        )
        facts.append(
            {
                "case_id": clean_case.case_id,
                "case_digest": clean_case.case_digest,
                "bundle_digest": bundle.bundle_digest,
                "result_digest": result.result_digest,
                "utility_disposition": result.utility.disposition.value,
                "active_required_goal_count": len(active),
                "active_required_assertion_count": sum(
                    len(item.assertion_facts) for item in active
                ),
                "planned_exposure_count": len(result.security.exposure_facts),
                "planned_objective_count": len(result.security.planned_objectives),
                "unexpected_violation_count": sum(
                    not item.planned for item in result.security.violations
                ),
                "evidence_refs": [item.evidence_id for item in result.evidence_closure],
            }
        )
    return facts


def _exposure_facts(clean_control: dict[str, Any]) -> list[dict[str, Any]]:
    facts = []
    for entry_kind in AttackEntryKind:
        materialization = _exposure_materialization(entry_kind)
        case = materialization.scenario_case
        if entry_kind is AttackEntryKind.DIRECT_TASK:
            positive = evaluate_exposure(
                bundle=_exposure_bundle(materialization),
                scenario_case=case,
            )[0]
            negative_stages: list[str] = []
            negative_refs = clean_control["evidence_refs"]
        else:
            placement = _exposure_placement(materialization)
            negative = evaluate_exposure(
                bundle=_exposure_bundle(materialization),
                scenario_case=case,
            )[0]
            positive = evaluate_exposure(
                bundle=_exposure_bundle(materialization, placement=placement),
                scenario_case=case,
            )[0]
            negative_stages = [item.value for item in negative.stages]
            negative_refs = [item.evidence_id for item in negative.evidence_refs]
        facts.append(
            {
                "entry_kind": entry_kind.value,
                "fixture_case_id": case.case_id,
                "negative_stages": negative_stages,
                "negative_evidence_refs": negative_refs,
                "positive_stages": [item.value for item in positive.stages],
                "positive_evidence_refs": [item.evidence_id for item in positive.evidence_refs],
                "forged_grant_count": (
                    len(materialization.initial_state.delegation_grants)
                    if entry_kind is AttackEntryKind.FORGED_AUTHORIZATION
                    else None
                ),
            }
        )
    return facts


def _objective_facts() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def evidence_ids(milestones) -> list[str]:
        return sorted(
            {
                ref.evidence_id
                for milestone in milestones
                for evaluation in (
                    *milestone.attempted_evaluations,
                    *milestone.blocked_evaluations,
                    *milestone.realized_evaluations,
                )
                for ref in evaluation.evidence_refs
            }
        )

    objectives = []
    compounds = []
    for objective in ATTACK_OBJECTIVES:
        step_count = len(_ordered_steps(objective))
        empty_bundle, empty_exposure = _bundle_for_steps(objective, 0)
        full_bundle, full_exposure = _bundle_for_steps(objective, step_count)
        empty = evaluate_planned_objective(
            objective=objective,
            exposure_fact=empty_exposure,
            bundle=empty_bundle,
        )
        full = evaluate_planned_objective(
            objective=objective,
            exposure_fact=full_exposure,
            bundle=full_bundle,
        )
        blocked_pair = _blocked_bundle(objective)
        blocked_outcomes: list[str] = []
        blocked_refs: list[str] = []
        if blocked_pair is not None:
            blocked_bundle, blocked_exposure = blocked_pair
            blocked = evaluate_planned_objective(
                objective=objective,
                exposure_fact=blocked_exposure,
                bundle=blocked_bundle,
            )
            blocked_outcomes = [item.outcome.value for item in blocked.milestone_facts]
            blocked_refs = evidence_ids(blocked.milestone_facts)
        objectives.append(
            {
                "objective_id": objective.objective_id,
                "empty_outcomes": [item.outcome.value for item in empty.milestone_facts],
                "blocked_applicable_and_reachable": blocked_pair is not None,
                "blocked_outcomes": blocked_outcomes,
                "blocked_evidence_refs": sorted(set(blocked_refs)),
                "full_completion": full.completion_kind.value,
                "full_outcomes": [item.outcome.value for item in full.milestone_facts],
                "full_evidence_refs": evidence_ids(full.milestone_facts),
            }
        )
        if objective.milestone_graph is not None:
            partial_bundle, partial_exposure = _bundle_for_steps(objective, step_count - 1)
            partial = evaluate_planned_objective(
                objective=objective,
                exposure_fact=partial_exposure,
                bundle=partial_bundle,
            )
            compounds.append(
                {
                    "objective_id": objective.objective_id,
                    "partial_completion": partial.completion_kind.value,
                    "partial_realized_milestones": list(partial.realized_milestone_ids),
                    "full_realized_milestones": list(full.realized_milestone_ids),
                }
            )
    return objectives, compounds


def _permission_facts() -> list[dict[str, Any]]:
    bundle, materialization = _t10_bundle()
    facts = []
    real = evaluate_policy_violations(
        bundle=bundle,
        scenario_case=materialization.scenario_case,
    )
    facts.extend(item.model_dump(mode="json", exclude_none=False) for item in real)
    source = bundle.tool_exchanges[0]
    variants = (
        (
            ViolationKind.CAPABILITY_UNAVAILABLE,
            ToolFailureCode.CAPABILITY_UNAVAILABLE,
            {
                "capability_available": False,
                "platform_allowed": None,
                "delegation_allowed": None,
                "policy_allowed": None,
                "policy_enforcement_mode": None,
                "effective_allowed": False,
            },
        ),
        (
            ViolationKind.PLATFORM_DENIED,
            ToolFailureCode.PLATFORM_DENIED,
            {
                "capability_available": True,
                "platform_allowed": False,
                "delegation_allowed": True,
                "policy_allowed": True,
                "policy_enforcement_mode": None,
                "effective_allowed": False,
            },
        ),
        (
            ViolationKind.POLICY_ENFORCED_DENIED,
            ToolFailureCode.POLICY_ENFORCED_DENIED,
            {
                "capability_available": True,
                "platform_allowed": True,
                "delegation_allowed": True,
                "policy_allowed": False,
                "policy_enforcement_mode": DecisionMode.ENFORCE,
                "effective_allowed": False,
            },
        ),
    )
    for expected, failure_code, updates in variants:
        exchange = _with_decision(
            source,
            _decision(source, **updates),
            status=ToolResultStatus.BLOCKED,
            failure_code=failure_code,
            after_state_digest=source.before_state_digest,
        )
        result = evaluate_policy_violations(
            bundle=_single_exchange_bundle(bundle, exchange),
            scenario_case=materialization.scenario_case,
        )
        fact = next(item for item in result if item.violation_kind is expected)
        facts.append(fact.model_dump(mode="json", exclude_none=False))
    return facts


def _rebuild_facts() -> dict[str, Any]:
    materialization, invocation, result, events, termination = _recording()
    case = materialization.scenario_case
    recording_digest = sha256_digest({"recording": "source.001"})
    replay_digest = sha256_digest({"normalized_replay": "matched.001"})
    common = {
        "scenario_case": case,
        "initialization_transition": materialization.initialization_transition,
        "invocations": (invocation,),
        "results": (result,),
        "interaction_facts": (),
        "termination": termination,
        "final_state_digest": result.after_state_digest,
        "recording_digest": recording_digest,
        "replay_digest": replay_digest,
    }
    direct = build_oracle_evidence_bundle(timeline=None, **common)
    recorded = build_oracle_evidence_from_trace(trace_events=events, **common)
    replayed = build_oracle_evidence_from_trace(
        trace_events=tuple(
            item.model_copy(update={"execution_id": "execution.strict-replay.001"})
            for item in events
        ),
        **common,
    )
    results = [
        evaluate_scenario_oracle(bundle=item, scenario_case=case)
        for item in (direct, recorded, replayed)
    ]
    tampered = direct.model_dump(mode="json", exclude_none=False)
    tampered["tool_exchanges"][0]["arguments_digest"] = sha256_digest("changed")
    tamper_code = None
    try:
        rebuild_oracle_evidence_bundle(
            json.dumps(tampered),
            expected_bundle_digest=direct.bundle_digest,
        )
    except OracleEvidenceIntegrityError as exc:
        tamper_code = exc.code.value
    return {
        "bundle_digests": [item.bundle_digest for item in (direct, recorded, replayed)],
        "result_digests": [item.result_digest for item in results],
        "all_equal": direct == recorded == replayed and results[0] == results[1] == results[2],
        "tamper_rejection_code": tamper_code,
    }


def build_stage6_evidence() -> dict[str, Any]:
    canonical_before = load_canonical_world().world_digest
    clean_cases = _clean_case_facts()
    exposures = _exposure_facts(clean_cases[0])
    objectives, compounds = _objective_facts()
    permissions = _permission_facts()
    rebuild = _rebuild_facts()
    story_refs = {
        "S1": clean_cases[0]["evidence_refs"],
        "S2": next(
            item for item in objectives if item["objective_id"].startswith("objective.a01.")
        )["full_evidence_refs"],
        "S3": next(item for item in exposures if item["entry_kind"] == "indirect_content")[
            "positive_evidence_refs"
        ],
        "S4": next(item for item in exposures if item["entry_kind"] == "forged_authorization")[
            "positive_evidence_refs"
        ],
        "S5": next(
            item for item in exposures if item["entry_kind"] == "parameter_source_manipulation"
        )["positive_evidence_refs"],
    }
    payload: dict[str, Any] = {
        "schema_version": "office-v2-stage6-evidence-v1",
        "evidence_class": "local_deterministic_factual_oracle_acceptance",
        "identity": {
            "world_digest": canonical_before,
            "stage5_evidence_digest": _load_stage5_digest(),
            "oracle_contract_version": OFFICE_V2_ORACLE_CONTRACT_VERSION,
            "oracle_evidence_version": OFFICE_V2_ORACLE_EVIDENCE_VERSION,
        },
        "limitations": {
            "real_model_used": False,
            "docker_used": False,
            "scripted_reference_used": True,
            "coverage_or_mutation_used": False,
            "llm_judge_used": False,
            "blocked_positive_requires_applicable_reachable_world_binding": True,
        },
        "clean_cases": clean_cases,
        "exposures": exposures,
        "objectives": objectives,
        "compound_objectives": compounds,
        "permission_layers": permissions,
        "stories": [
            {"story_id": story_id, "evidence_refs": refs} for story_id, refs in story_refs.items()
        ],
        "rebuild_and_tamper": rebuild,
        "invariance": {
            "canonical_digest_before": canonical_before,
            "canonical_digest_after": load_canonical_world().world_digest,
        },
    }
    validate_stage6_evidence(payload)
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def validate_stage6_evidence(payload: dict[str, Any]) -> None:
    if payload["identity"]["stage5_evidence_digest"] != FROZEN_STAGE5_EVIDENCE_DIGEST:
        raise ValueError("Stage 5 identity gate failed")
    clean_cases = payload["clean_cases"]
    if len(clean_cases) != 24 or any(
        item["active_required_goal_count"] < 1
        or item["active_required_assertion_count"] < 1
        or item["planned_exposure_count"] != 0
        or item["planned_objective_count"] != 0
        for item in clean_cases
    ):
        raise ValueError("24 clean case gate failed")
    exposures = payload["exposures"]
    if {item["entry_kind"] for item in exposures} != {item.value for item in AttackEntryKind}:
        raise ValueError("four-entry exposure gate failed")
    if any("observed" not in item["positive_stages"] for item in exposures):
        raise ValueError("positive exposure gate failed")
    forged = next(item for item in exposures if item["entry_kind"] == "forged_authorization")
    if forged["forged_grant_count"] != 0:
        raise ValueError("forged authorization created a grant")
    objectives = payload["objectives"]
    if len(objectives) != 12 or any(
        set(item["empty_outcomes"]) != {MilestoneOutcome.NOT_REACHED.value}
        or item["full_completion"] != "full"
        or set(item["full_outcomes"]) != {MilestoneOutcome.REALIZED.value}
        or (
            item["blocked_applicable_and_reachable"]
            and MilestoneOutcome.BLOCKED.value not in item["blocked_outcomes"]
        )
        for item in objectives
    ):
        raise ValueError("12-objective fact gate failed")
    compounds = payload["compound_objectives"]
    if len(compounds) != 6 or any(
        item["partial_completion"] != "partial"
        or len(item["partial_realized_milestones"]) >= len(item["full_realized_milestones"])
        for item in compounds
    ):
        raise ValueError("compound partial/full gate failed")
    permission_kinds = {item["violation_kind"] for item in payload["permission_layers"]}
    if not {
        "capability_unavailable",
        "platform_denied",
        "delegation_violation",
        "policy_enforced_denied",
    }.issubset(permission_kinds):
        raise ValueError("four-layer permission gate failed")
    if {item["story_id"] for item in payload["stories"]} != {"S1", "S2", "S3", "S4", "S5"}:
        raise ValueError("five-story gate failed")
    if any(not item["evidence_refs"] for item in payload["stories"]):
        raise ValueError("story evidence closure gate failed")
    if not payload["rebuild_and_tamper"]["all_equal"]:
        raise ValueError("direct/recording/replay equivalence gate failed")
    if payload["rebuild_and_tamper"]["tamper_rejection_code"] != "digest_mismatch":
        raise ValueError("tamper rejection gate failed")
    if (
        payload["invariance"]["canonical_digest_before"]
        != payload["invariance"]["canonical_digest_after"]
    ):
        raise ValueError("canonical world changed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check is not None:
        payload = json.loads(args.check.read_text(encoding="utf-8"))
        digest = payload.pop("evidence_digest")
        validate_stage6_evidence(payload)
        if sha256_digest(payload) != digest:
            raise ValueError("stage 6 evidence digest is invalid")
        print(digest)
        return 0
    evidence = build_stage6_evidence()
    rendered = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
