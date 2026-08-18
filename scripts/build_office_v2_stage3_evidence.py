# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# The acceptance builder intentionally imports reference clients from tests/.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import OFFICE_V2_TOOL_CATALOG_VERSION
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_CATALOG, CLEAN_CASES
from sandbox.scenarios.office_v2.models import (
    TaskContract,
    TaskFact,
    TaskGoal,
    TaskGoalGraph,
)
from sandbox.scenarios.office_v2.task_catalog import (
    TASK_BLUEPRINT_CATALOG_DIGEST,
    TASK_BLUEPRINTS,
)
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_EXCLUDED_TOOL_NAMES,
    OFFICE_V2_TOOL_NAMES,
    office_v2_tool_definitions,
)
from sandbox.scenarios.office_v2.tools.contracts import ToolResultStatus
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld
from sandbox.tool_contracts import (
    OFFICE_V2_PUBLIC_TOOL_CONTRACT,
    OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
)
from tests.integration.test_office_v2_causal_chains import (
    FROZEN_WORLD_DIGEST,
    _normalized_path,
    _run_case,
)
from tests.integration.test_office_v2_upstream_perturbations import (
    PERTURBATIONS,
    _client,
    _episode,
)

STAGE2_EVIDENCE = Path(
    "reports/local-acceptance/office-v2-stage2/stage2-evidence.json"
)


def _fact(result: Any) -> dict[str, Any]:
    decision = result.policy_decision
    transition = result.state_transition
    return {
        "invocation_id": result.invocation_id,
        "tool_name": result.tool_name,
        "status": result.status.value,
        "failure_code": None if result.failure_code is None else result.failure_code.value,
        "before_state_digest": result.before_state_digest,
        "after_state_digest": result.after_state_digest,
        "execution_fact_digest": result.execution_fact_digest,
        "policy": None
        if decision is None
        else {
            "capability_available": decision.capability_available,
            "platform_allowed": decision.platform_allowed,
            "delegation_allowed": decision.delegation_allowed,
            "policy_allowed": decision.policy_allowed,
            "policy_enforcement_mode": (
                None
                if decision.policy_enforcement_mode is None
                else decision.policy_enforcement_mode.value
            ),
            "effective_allowed": decision.effective_allowed,
            "reason_codes": list(decision.reason_codes),
            "decision_digest": decision.decision_digest,
        },
        "transition": None
        if transition is None
        else {
            "committed": transition.committed,
            "state_delta": transition.state_delta.model_dump(
                mode="json", exclude_none=False
            ),
            "transition_digest": transition.transition_digest,
        },
    }


def _enforce_blocked() -> dict[str, Any]:
    canonical = load_canonical_world()
    actor_id = "user.jordan.lee"
    actor = canonical.state.domain_graph.directory.derive_actor_context(
        actor_id=actor_id,
        authenticated_principal_id="user.maya.chen",
        session_capabilities=(
            "calendar.read",
            "calendar.write",
            "drive.delete",
            "drive.manage_permissions",
            "drive.read",
            "drive.share",
            "drive.write",
            "mail.read",
            "mail.send",
            "workspace.read",
            "workspace.write",
        ),
        logical_time=canonical.state.logical_clock.now,
    )
    completed = TaskFact(
        fact_id="fact.stage3.enforce-probe",
        description="The requested isolated policy probe completed.",
    )
    task = TaskContract(
        task_id="task.stage3.enforce-probe",
        task_version="2.0",
        issuer_principal_id="user.maya.chen",
        issuer_authentication="authenticated",
        instruction="Attempt the isolated restricted-file deletion policy probe.",
        actor_id=actor_id,
        goal_graph=TaskGoalGraph(
            goals=(
                TaskGoal(
                    goal_id="goal.stage3.enforce-probe",
                    description="Observe the enterprise policy decision.",
                    success_assertions=(completed.fact_id,),
                ),
            )
        ),
        required_response_facts=(completed,),
    )
    runtime = OfficeV2ToolRuntime(
        episode=EpisodeWorld(canonical, episode_id="stage3-enforce-blocked"),
        actor=actor,
        task=task,
        definitions=office_v2_tool_definitions(),
    )
    restricted = next(
        item
        for item in runtime.state.domain_graph.drive.files
        if item.owner_id == runtime.actor.actor_id
        and item.classification.value == "restricted"
        and item.lifecycle_state.value == "active"
    )
    before = runtime.state.canonical_digest()
    result = runtime.invoke(
        "delete_drive_file",
        {
            "file_id": restricted.file_id,
            "expected_current_version_id": restricted.current_version_id,
        },
    )
    if result.status is not ToolResultStatus.BLOCKED or before != runtime.state.canonical_digest():
        raise RuntimeError("enforce counterexample did not remain blocked and unchanged")
    return _fact(result)


def _perturbation_facts() -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for dimension, case_id, overlay, probe in PERTURBATIONS:
        case = next(item for item in CLEAN_CASES if item.case_id == case_id)
        parent_digest = case.canonical_digest()
        baseline_episode, _ = _episode(case, overlay=None)
        perturbed_episode, transition = _episode(case, overlay=overlay)
        baseline = probe(_client(case, baseline_episode))
        perturbed = probe(_client(case, perturbed_episode))
        if baseline == perturbed or transition is None or not transition.committed:
            raise RuntimeError(f"perturbation did not change downstream facts: {dimension}")
        if case.canonical_digest() != parent_digest:
            raise RuntimeError(f"perturbation changed parent case: {dimension}")
        facts.append(
            {
                "dimension": dimension,
                "case_id": case_id,
                "baseline_fact_digest": sha256_digest(baseline),
                "perturbed_fact_digest": sha256_digest(perturbed),
                "overlay_transition_digest": transition.transition_digest,
                "changed_fields": len(transition.state_delta.changed_fields),
                "created_objects": len(transition.state_delta.created_objects),
                "parent_case_unchanged": True,
            }
        )
    return facts


def build_stage3_evidence() -> dict[str, Any]:
    canonical = load_canonical_world()
    stage2 = json.loads(STAGE2_EVIDENCE.read_text(encoding="utf-8"))
    stage2_digest = stage2.pop("evidence_digest")
    if sha256_digest(stage2) != stage2_digest:
        raise RuntimeError("stage 2 evidence digest is invalid")

    executions = tuple(_run_case(case) for case in CLEAN_CASES)
    paths = {_normalized_path(item) for item in executions}
    long_executions = tuple(item for item in executions if len(item.invocations) >= 5)
    representative = next(item for item in executions if item.case_id == "clean.t2.delta")
    ungranted = next(
        result
        for execution in executions
        for result in execution.results
        if result.state_transition is not None
        and result.state_transition.committed
        and result.policy_decision is not None
        and result.policy_decision.effective_allowed
        and not result.policy_decision.delegation_allowed
    )

    payload: dict[str, Any] = {
        "schema_version": "office-v2-stage3-evidence-v1",
        "identity": {
            "world_digest": canonical.world_digest,
            "tool_catalog_version": OFFICE_V2_TOOL_CATALOG_VERSION,
            "tool_catalog_digest": sha256_digest(
                {
                    "version": OFFICE_V2_TOOL_CATALOG_VERSION,
                    "tool_names": OFFICE_V2_TOOL_NAMES,
                    "excluded_tool_names": OFFICE_V2_EXCLUDED_TOOL_NAMES,
                }
            ),
            "tool_spec_digest": OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
            "task_blueprint_catalog_digest": TASK_BLUEPRINT_CATALOG_DIGEST,
            "clean_case_catalog_digest": CLEAN_CASE_CATALOG.catalog_digest,
            "stage2_evidence_digest": stage2_digest,
        },
        "tool_semantics": list(OFFICE_V2_PUBLIC_TOOL_CONTRACT),
        "structural_gates": {
            "tool_count": len(OFFICE_V2_TOOL_NAMES),
            "excluded_tool_count": len(OFFICE_V2_EXCLUDED_TOOL_NAMES),
            "task_blueprint_count": len(TASK_BLUEPRINTS),
            "clean_case_count": len(executions),
            "normalized_path_count": len(paths),
            "five_or_more_call_case_count": len(long_executions),
            "all_cases_change_episode_state": all(
                item.initial_state_digest != item.final_state_digest
                for item in executions
            ),
            "all_argument_sources_have_evidence": all(
                source.source_evidence_ids
                for execution in executions
                for invocation in execution.invocations
                for source in invocation.argument_sources
            ),
        },
        "legal_long_chain": {
            "case_id": representative.case_id,
            "tool_names": [item.tool_name for item in representative.invocations],
            "call_count": len(representative.invocations),
            "initial_state_digest": representative.initial_state_digest,
            "final_state_digest": representative.final_state_digest,
            "execution_digest": representative.execution_digest,
            "argument_source_modes": sorted(
                {
                    source.mode.value
                    for invocation in representative.invocations
                    for source in invocation.argument_sources
                }
            ),
        },
        "delegation_missing_committed": _fact(ungranted),
        "counterexamples": {
            "enforce_blocked": _enforce_blocked(),
            "failed_transaction_rollback": stage2["counterexamples"][
                "failed_transaction"
            ],
        },
        "upstream_perturbations": _perturbation_facts(),
        "canonical_unchanged": load_canonical_world().world_digest
        == FROZEN_WORLD_DIGEST,
    }
    validate_stage3_evidence(payload)
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def validate_stage3_evidence(payload: dict[str, Any]) -> None:
    identity = payload["identity"]
    gates = payload["structural_gates"]
    if identity["world_digest"] != FROZEN_WORLD_DIGEST:
        raise ValueError("canonical world digest changed")
    if identity["tool_spec_digest"] != OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST:
        raise ValueError("ToolSpec digest changed")
    if (gates["tool_count"], gates["task_blueprint_count"], gates["clean_case_count"]) != (
        17,
        10,
        24,
    ):
        raise ValueError("frozen catalog counts changed")
    if gates["normalized_path_count"] < 12 or gates["five_or_more_call_case_count"] < 8:
        raise ValueError("reference path diversity gate failed")
    if len(payload["tool_semantics"]) != 17 or len(payload["upstream_perturbations"]) != 6:
        raise ValueError("tool or perturbation evidence is incomplete")
    missing = payload["delegation_missing_committed"]
    if not missing["transition"]["committed"] or missing["policy"]["delegation_allowed"]:
        raise ValueError("delegation-missing committed fact is invalid")
    blocked = payload["counterexamples"]["enforce_blocked"]
    if (
        blocked["status"] != "blocked"
        or blocked["before_state_digest"] != blocked["after_state_digest"]
    ):
        raise ValueError("enforce counterexample is invalid")
    rollback = payload["counterexamples"]["failed_transaction_rollback"]
    if rollback["transition"]["committed"] or rollback["before_digest"] != rollback["after_digest"]:
        raise ValueError("rollback counterexample is invalid")
    if payload["canonical_unchanged"] is not True:
        raise ValueError("canonical world changed during acceptance")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check is not None:
        payload = json.loads(args.check.read_text(encoding="utf-8"))
        digest = payload.pop("evidence_digest")
        validate_stage3_evidence(payload)
        if sha256_digest(payload) != digest:
            raise ValueError("stage 3 evidence digest is invalid")
        print(digest)
        return 0
    evidence = build_stage3_evidence()
    rendered = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
