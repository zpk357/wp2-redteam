# ruff: noqa: E402
"""Build self-validating local evidence for Office V2 coverage step 2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sandbox.coverage.v2_behavior import (
    V2BehaviorDimension,
    V2BehaviorFeatureKind,
    V2BehaviorFeatureTier,
    V2BehaviorValueRole,
    build_v2_behavior_feature,
    build_v2_behavior_profile,
    normalize_v2_behavior_value,
)
from sandbox.coverage.v2_contracts import (
    V2_COVERAGE_CONTRACT_IDENTITY,
    V2MilestoneOutcomeBits,
    build_v2_candidate_batch_baseline,
)
from sandbox.coverage.v2_episode_behavior import extract_v2_behavior_profile
from sandbox.coverage.v2_episode_coverage import (
    V2CandidateEpisode,
    build_v2_episode_coverage_facts,
    empty_v2_coverage_snapshot,
    evaluate_v2_candidate_batch,
)
from sandbox.coverage.v2_input import (
    v2_coverage_input_from_direct,
    v2_coverage_input_from_recording,
    v2_coverage_input_from_strict_replay,
)
from sandbox.coverage.v2_risk_catalog import V2_RISK_CATALOG
from sandbox.coverage.v2_unexpected_risk import map_v2_unexpected_risks
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.oracle import evaluate_scenario_oracle
from sandbox.scenarios.office_v2.oracle_models import MilestoneOutcome, ObjectiveCompletionKind
from sandbox.scenarios.office_v2.security_oracle import (
    evaluate_compound_objective,
    evaluate_planned_objective,
)
from tests.unit.test_office_v2_behavior_contracts import _evidence
from tests.unit.test_office_v2_coverage_input import (
    _artifact,
    _bundles,
    _manifest,
    _matched_replay,
    _recording_state_payload,
)
from tests.unit.test_office_v2_episode_behavior import _clean_artifact
from tests.unit.test_office_v2_milestone_evaluator import (
    _blocked_bundle,
    _bundle_for_steps,
    _objective,
    _ordered_steps,
)
from tests.unit.test_office_v2_utility_evaluator import _t10_bundle

SCHEMA_VERSION = "office-v2-coverage-step2-evidence-v1"
DEFAULT_OUTPUT = Path(
    "reports/local-acceptance/office-v2-coverage-step2/step2-evidence.json"
)


def _comparison(check_id: str, passed: bool, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"check_id": check_id, "passed": passed, "evidence": evidence}


def _acquisition_inputs():
    direct_bundle, recording_bundle, replay_bundle = _bundles()
    recording_execution_id = "execution.step2.recording.001"
    recording_state_payload = _recording_state_payload(
        recording_execution_id,
        recording_bundle,
    )
    manifest = _manifest(
        recording_bundle,
        recording_state_payload=recording_state_payload,
    )
    return (
        v2_coverage_input_from_direct(
            _artifact("execution.step2.direct.001", direct_bundle),
            container_removed=True,
        ),
        v2_coverage_input_from_recording(
            manifest,
            _artifact(recording_execution_id, recording_bundle),
            recording_state_payload=recording_state_payload,
            container_removed=True,
        ),
        v2_coverage_input_from_strict_replay(
            manifest,
            _matched_replay(manifest, replay_bundle),
            _artifact("execution.step2.replay.001", replay_bundle),
            source_recording_state_payload=recording_state_payload,
        ),
    )


def _unexpected_input():
    bundle, materialization = _t10_bundle()
    result = evaluate_scenario_oracle(
        bundle=bundle,
        scenario_case=materialization.scenario_case,
    )
    artifact = SimpleNamespace(
        execution_id="execution.step2.unexpected.001",
        artifact_digest=sha256_digest("step2-unexpected-artifact"),
        evidence_bundle=bundle,
        oracle_result=result,
    )
    return v2_coverage_input_from_direct(artifact, container_removed=True)


def build_step2_evidence() -> dict[str, Any]:
    direct, recording, replay = _acquisition_inputs()
    acquisition_facts = tuple(
        build_v2_episode_coverage_facts(item)
        for item in (direct, recording, replay)
    )
    canonical_digests = {item.canonical_fact_digest for item in acquisition_facts}
    profile_digests = {item.behavior.profile_digest for item in acquisition_facts}
    planned_digests = {item.planned_risk.planned_risk_digest for item in acquisition_facts}

    resource_a = normalize_v2_behavior_value(
        "drive.apollo.plan", role=V2BehaviorValueRole.INSTANCE_ID
    )
    resource_b = normalize_v2_behavior_value(
        "drive.borealis.pack", role=V2BehaviorValueRole.INSTANCE_ID
    )

    allowed = build_v2_behavior_feature(
        tier=V2BehaviorFeatureTier.PRIMARY,
        kind=V2BehaviorFeatureKind.PERMISSION_BRANCH,
        dimensions=(V2BehaviorDimension(name="platform", value="allowed"),),
        evidence_refs=(_evidence("permission-allowed"),),
    )
    denied = build_v2_behavior_feature(
        tier=V2BehaviorFeatureTier.PRIMARY,
        kind=V2BehaviorFeatureKind.PERMISSION_BRANCH,
        dimensions=(V2BehaviorDimension(name="platform", value="denied"),),
        evidence_refs=(_evidence("permission-denied"),),
    )

    clean_input = v2_coverage_input_from_direct(
        _clean_artifact("clean.t1.apollo"), container_removed=True
    )
    clean_profile = extract_v2_behavior_profile(clean_input)
    overlay_profile = extract_v2_behavior_profile(direct)
    state_kinds = {
        V2BehaviorFeatureKind.STATE_OBJECT_CHANGE,
        V2BehaviorFeatureKind.STATE_FIELD_CHANGE,
        V2BehaviorFeatureKind.STATE_RELATION_CHANGE,
        V2BehaviorFeatureKind.STATE_CROSS_DOMAIN,
    }
    clean_state = {
        item.feature_key_digest
        for item in clean_profile.primary_features
        if item.kind in state_kinds
    }
    overlay_state = {
        item.feature_key_digest
        for item in overlay_profile.primary_features
        if item.kind in state_kinds
    }
    same_path_without_state = build_v2_behavior_profile(
        canonical_fact_digest=sha256_digest("step2-same-path-without-state"),
        primary_features=tuple(
            item for item in clean_profile.primary_features if item.kind not in state_kinds
        ),
        secondary_diversity=clean_profile.secondary_diversity,
        normalized_path=clean_profile.normalized_path,
    )

    attempted = V2MilestoneOutcomeBits.from_episode_outcome(MilestoneOutcome.ATTEMPTED)
    blocked = V2MilestoneOutcomeBits.from_episode_outcome(MilestoneOutcome.BLOCKED)
    realized = V2MilestoneOutcomeBits.from_episode_outcome(MilestoneOutcome.REALIZED)

    atomic = _objective("objective.a02.")
    blocked_pair = _blocked_bundle(atomic)
    if blocked_pair is None:
        raise RuntimeError("A02 must retain a blocked calibration witness")
    blocked_bundle, blocked_exposure = blocked_pair
    blocked_result = evaluate_planned_objective(
        objective=atomic,
        exposure_fact=blocked_exposure,
        bundle=blocked_bundle,
    )
    realized_bundle, realized_exposure = _bundle_for_steps(atomic, 1)
    realized_result = evaluate_planned_objective(
        objective=atomic,
        exposure_fact=realized_exposure,
        bundle=realized_bundle,
    )

    a01 = _objective("objective.a01.")
    partial_bundle, partial_exposure = _bundle_for_steps(a01, 1)
    full_bundle, full_exposure = _bundle_for_steps(a01, len(_ordered_steps(a01)))
    partial = evaluate_compound_objective(
        objective=a01, exposure_fact=partial_exposure, bundle=partial_bundle
    )
    full = evaluate_compound_objective(
        objective=a01, exposure_fact=full_exposure, bundle=full_bundle
    )

    unexpected_input = _unexpected_input()
    unexpected = map_v2_unexpected_risks(unexpected_input)
    unexpected_episode = build_v2_episode_coverage_facts(unexpected_input)
    planned_count = len(direct.oracle_facts.security.planned_objectives)

    empty = empty_v2_coverage_snapshot()
    facts = acquisition_facts[0]
    candidates = (
        V2CandidateEpisode(candidate_id="candidate.a", episode_facts=facts),
        V2CandidateEpisode(candidate_id="candidate.b", episode_facts=facts),
    )
    baseline = build_v2_candidate_batch_baseline(
        campaign_id="campaign.step2.001",
        candidate_set_id="candidate-set.step2.001",
        candidate_set_digest=sha256_digest("candidate-set.step2.001"),
        candidate_ids=("candidate.a", "candidate.b"),
        baseline_snapshot_digest=empty.snapshot_digest,
    )
    forward = evaluate_v2_candidate_batch(
        batch_baseline=baseline,
        baseline_snapshot=empty,
        candidates=candidates,
    )
    reverse = evaluate_v2_candidate_batch(
        batch_baseline=baseline,
        baseline_snapshot=empty,
        candidates=tuple(reversed(candidates)),
    )
    incomplete_eligibility = facts.eligibility

    comparisons = [
        _comparison(
            "direct-recording-replay-fact-equivalence",
            len(canonical_digests) == len(profile_digests) == len(planned_digests) == 1,
            {
                "canonical_fact_digest": next(iter(canonical_digests)),
                "behavior_profile_digest": next(iter(profile_digests)),
                "planned_risk_digest": next(iter(planned_digests)),
            },
        ),
        _comparison(
            "equivalent-resource-id-normalization",
            resource_a == resource_b == "identifier",
            {"normalized_value": resource_a},
        ),
        _comparison(
            "permission-branch-distinction",
            allowed.feature_key_digest != denied.feature_key_digest,
            {"allowed": allowed.feature_key_digest, "denied": denied.feature_key_digest},
        ),
        _comparison(
            "state-delta-distinction",
            bool(clean_state)
            and clean_profile.normalized_path == same_path_without_state.normalized_path
            and clean_profile.profile_digest != same_path_without_state.profile_digest,
            {
                "normalized_path_digest": clean_profile.normalized_path.path_digest,
                "committed_state_feature_count": len(clean_state),
                "profile_with_state": clean_profile.profile_digest,
                "profile_without_state": same_path_without_state.profile_digest,
            },
        ),
        _comparison(
            "atomic-outcome-branch-mapping",
            attempted.attempted_seen
            and blocked.attempted_seen
            and blocked.blocked_seen
            and realized.attempted_seen
            and realized.realized_seen
            and blocked_result.milestone_facts[0].outcome is MilestoneOutcome.BLOCKED
            and realized_result.milestone_facts[0].outcome is MilestoneOutcome.REALIZED,
            {
                "objective_id": atomic.objective_id,
                "attempted": attempted.model_dump(mode="json"),
                "blocked_oracle_outcome": blocked_result.milestone_facts[0].outcome.value,
                "realized_oracle_outcome": realized_result.milestone_facts[0].outcome.value,
            },
        ),
        _comparison(
            "compound-objective-partial-full",
            partial.completion_kind is ObjectiveCompletionKind.PARTIAL
            and full.completion_kind is ObjectiveCompletionKind.FULL,
            {
                "objective_id": a01.objective_id,
                "partial_realized": len(partial.realized_milestone_ids),
                "full_realized": len(full.realized_milestone_ids),
            },
        ),
        _comparison(
            "planned-unexpected-separation",
            planned_count > 0
            and bool(unexpected.violations)
            and all(item.matched_objective_id is None for item in unexpected.violations)
            and any(
                item.planned_or_unexpected == "unexpected"
                for item in unexpected_episode.risk_context_cells
            )
            and any(
                item.risk_fact_key_digest
                in {
                    violation.unexpected_risk_digest
                    for violation in unexpected.violations
                }
                for item in unexpected_episode.behavior_risk_links
            ),
            {
                "planned_objective_count": planned_count,
                "unexpected_violation_count": len(unexpected.violations),
                "unexpected_context_count": sum(
                    item.planned_or_unexpected == "unexpected"
                    for item in unexpected_episode.risk_context_cells
                ),
                "unexpected_behavior_link_count": sum(
                    item.risk_fact_key_digest
                    in {
                        violation.unexpected_risk_digest
                        for violation in unexpected.violations
                    }
                    for item in unexpected_episode.behavior_risk_links
                ),
            },
        ),
        _comparison(
            "initialization-agent-state-separation",
            direct.behavior_source_facts.initialization_materialization_digest
            not in direct.behavior_source_facts.agent_transition_digests
            and not overlay_state,
            {
                "materialization_digest": (
                    direct.behavior_source_facts.initialization_materialization_digest
                ),
                "agent_transition_count": len(
                    direct.behavior_source_facts.agent_transition_digests
                ),
            },
        ),
        _comparison(
            "candidate-batch-order-independence",
            forward == reverse
            and forward.deltas[0].new_primary_behavior_features
            == forward.deltas[1].new_primary_behavior_features,
            {
                "batch_result_digest": forward.batch_result_digest,
                "next_snapshot_digest": forward.next_snapshot.snapshot_digest,
            },
        ),
        _comparison(
            "coverage-with-incomplete-utility-companion",
            bool(forward.deltas[0].new_primary_behavior_features)
            and not incomplete_eligibility.normal_task_completed
            and incomplete_eligibility.submitted,
            {
                "utility_disposition": incomplete_eligibility.utility_disposition.value,
                "normal_task_completed": incomplete_eligibility.normal_task_completed,
                "submitted": incomplete_eligibility.submitted,
            },
        ),
    ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if all(item["passed"] for item in comparisons) else "failed",
        "identity": {
            "coverage_contract_digest": V2_COVERAGE_CONTRACT_IDENTITY.identity_digest,
            "risk_catalog_digest": V2_RISK_CATALOG.catalog_digest,
            "risk_taxonomy_digest": V2_RISK_CATALOG.taxonomy_digest,
            "risk_mapping_digest": V2_RISK_CATALOG.mapping_digest,
            "risk_scope_digest": V2_RISK_CATALOG.scope_digest,
            "family_count": len(V2_RISK_CATALOG.families),
            "objective_count": len(V2_RISK_CATALOG.objectives),
            "milestone_count": V2_RISK_CATALOG.milestone_count,
        },
        "comparisons": comparisons,
        "limitations": {
            "docker_run_performed": False,
            "real_qwen_run_performed": False,
            "judge_run_performed": False,
            "corpus_or_mutation_implemented": False,
            "frozen_stage2_to_stage8_evidence_rebuilt": False,
        },
    }
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def validate_step2_evidence(payload: dict[str, Any]) -> None:
    digest = payload.get("evidence_digest")
    unsigned = {key: value for key, value in payload.items() if key != "evidence_digest"}
    if digest != sha256_digest(unsigned):
        raise ValueError("step2 evidence digest mismatch")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("step2 evidence schema mismatch")
    comparisons = payload.get("comparisons", [])
    if len(comparisons) != 10 or len({item["check_id"] for item in comparisons}) != 10:
        raise ValueError("step2 evidence requires exactly ten comparisons")
    if payload.get("status") != "passed" or not all(item.get("passed") for item in comparisons):
        raise ValueError("step2 evidence contains a failed comparison")
    identity = payload.get("identity", {})
    if (
        identity.get("coverage_contract_digest")
        != V2_COVERAGE_CONTRACT_IDENTITY.identity_digest
        or identity.get("risk_catalog_digest") != V2_RISK_CATALOG.catalog_digest
        or identity.get("family_count") != 4
        or identity.get("objective_count") != 12
        or identity.get("milestone_count") != 23
    ):
        raise ValueError("step2 evidence identity mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = build_step2_evidence()
    validate_step2_evidence(payload)
    if args.check:
        stored = json.loads(args.output.read_text(encoding="utf-8"))
        validate_step2_evidence(stored)
        if stored != payload:
            raise ValueError("stored step2 evidence differs from current facts")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
