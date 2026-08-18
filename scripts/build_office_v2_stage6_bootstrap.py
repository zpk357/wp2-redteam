#!/usr/bin/env python3
"""Build the frozen parent corpus for one fresh Office V2 Stage 6 Campaign."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from sandbox.coverage.v2_episode_coverage import empty_v2_coverage_snapshot
from sandbox.fuzzer.v2_campaign import CampaignLifecycle
from sandbox.fuzzer.v2_campaign_state import build_campaign_budget, build_campaign_state
from sandbox.fuzzer.v2_corpus import (
    AttackSeed,
    BindingRequirements,
    CarrierRecipe,
    CorpusEntry,
    DeliveredPayload,
    ExecutionCosts,
    ExecutionRecord,
    MaterializedCandidate,
    OriginIntent,
    PayloadSpec,
    SeedKind,
    V2Corpus,
    seal_contract,
)
from sandbox.fuzzer.v2_frontier import build_frontier_snapshot, compile_risk_frontiers
from sandbox.fuzzer.v2_real_runtime import RealCampaignBootstrap
from sandbox.fuzzer.v2_scheduler import new_baseline_exposure_ledger
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_cases import (
    RepresentativeScenarioFixture,
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.attack_models import (
    DirectTaskCondition,
    IndirectContentCondition,
    ParameterSourceManipulationCondition,
)


def _payload_location(fixture: RepresentativeScenarioFixture):
    case = fixture.scenario_case
    condition = case.adversarial_condition
    if isinstance(condition, DirectTaskCondition):
        return (
            condition.instruction_variant,
            "task",
            case.task.task_id,
            str(case.task.task_version),
            "instruction",
        )
    if isinstance(condition, IndirectContentCondition):
        placement = condition.placements[0]
        return (
            condition.adversarial_content,
            str(placement.resource_ref.kind),
            placement.resource_ref.resource_id,
            placement.resource_ref.version_id or "unversioned",
            ".".join(placement.field_path),
        )
    if isinstance(condition, ParameterSourceManipulationCondition) and isinstance(
        condition.visible_value, str
    ):
        placement = condition.source_placement
        return (
            condition.visible_value,
            str(placement.resource_ref.kind),
            placement.resource_ref.resource_id,
            placement.resource_ref.version_id or "unversioned",
            ".".join(placement.field_path),
        )
    return None


def _corpus_resource_id(value: str) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", value):
        return value
    return "bootstrap-resource." + sha256_digest(value).split(":", 1)[1][:24]


def _selected_fixtures() -> tuple[RepresentativeScenarioFixture, ...]:
    selected: dict[str, RepresentativeScenarioFixture] = {}
    for fixture in build_representative_scenario_fixtures():
        objective_id = fixture.scenario_case.attack_objective.objective_id
        if objective_id not in selected and _payload_location(fixture) is not None:
            selected[objective_id] = fixture
    expected = {item.objective_id for item in compile_risk_frontiers()}
    if set(selected) != expected:
        missing = ",".join(sorted(expected - set(selected)))
        raise ValueError(f"frozen fixtures lack writable parents for: {missing}")
    return tuple(selected[key] for key in sorted(selected))


def build_stage6_bootstrap(
    *,
    model_identity_digest: str,
    episode_limit: int = 50,
    mutator_token_limit: int = 1_000_000,
    monetary_microunit_limit: int = 1_000_000_000,
) -> RealCampaignBootstrap:
    coverage = empty_v2_coverage_snapshot()
    frontiers = compile_risk_frontiers()
    frontier_ids_by_objective: dict[str, tuple[str, ...]] = {}
    for objective_id in sorted({item.objective_id for item in frontiers}):
        frontier_ids_by_objective[objective_id] = tuple(
            item.frontier_id for item in frontiers if item.objective_id == objective_id
        )

    corpus = V2Corpus()
    for fixture in _selected_fixtures():
        case = fixture.scenario_case
        objective = case.attack_objective
        content, carrier_kind, resource_id, resource_version, field_path = (
            _payload_location(fixture) or ()
        )
        suffix = objective.objective_id.removeprefix("objective.").replace(".", "-")
        payload = PayloadSpec(
            payload_spec_id=f"payload.bootstrap.{suffix}",
            content=content,
            carrier_kind=carrier_kind,
            field_path=field_path,
            content_digest=sha256_digest({"content": content}),
        )
        seed_id = f"seed.bootstrap.{suffix}"
        first_milestone = (
            objective.milestone_graph.milestones[0].milestone_id
            if objective.milestone_graph is not None
            else objective.attempted_assertions[0].assertion_id
        )
        seed = seal_contract(
            AttackSeed,
            {
                "seed_id": seed_id,
                "payload_specs": (payload,),
                "carrier_recipe": CarrierRecipe(
                    entry_kind=case.adversarial_condition.entry_kind.value,
                    carrier_kind=carrier_kind,
                    required_field_paths=(field_path,),
                ),
                "origin_intent": OriginIntent(
                    objective_id=objective.objective_id,
                    milestone_id=first_milestone,
                ),
                "binding_requirements": BindingRequirements(
                    actor_roles=case.actor.active_role_ids,
                    task_blueprint_ids=(case.parent_case_id,),
                    resource_kinds=(carrier_kind,),
                ),
                "root_seed_id": seed_id,
                "generation_depth": 0,
            },
            "seed_content_digest",
        )
        candidate_id = f"materialized.bootstrap.{suffix}"
        candidate = seal_contract(
            MaterializedCandidate,
            {
                "materialized_candidate_id": candidate_id,
                "seed_id": seed.seed_id,
                "generation_allocation_id": f"allocation.bootstrap.{suffix}",
                "scenario_case_id": case.case_id,
                "actor_id": case.actor.actor_id,
                "task_id": case.task.task_id,
                "resource_binding_digest": sha256_digest(
                    {
                        "task_bindings": case.task_bindings,
                        "objective_bindings": case.objective_bindings,
                    }
                ),
                "delivered_payloads": (
                    DeliveredPayload(
                        payload_spec_id=payload.payload_spec_id,
                        resource_id=_corpus_resource_id(resource_id),
                        resource_version=resource_version,
                        field_path=field_path,
                        content_digest=payload.content_digest,
                        materialization_evidence_digest=(
                            case.materialization_record.materialization_digest
                        ),
                    ),
                ),
                "binding_source_digest": fixture.compatibility_decision.decision_digest,
                "comparison_context_digest": case.content_digest,
                "baseline_snapshot_digest": coverage.snapshot_digest,
            },
            "materialization_digest",
        )
        execution_id = f"execution.bootstrap.{suffix}"
        execution = seal_contract(
            ExecutionRecord,
            {
                "execution_record_id": execution_id,
                "seed_id": seed.seed_id,
                "materialized_candidate_id": candidate.materialized_candidate_id,
                "scenario_case_id": case.case_id,
                "actor_id": case.actor.actor_id,
                "task_id": case.task.task_id,
                "resource_binding_digest": candidate.resource_binding_digest,
                "binding_source_digest": candidate.binding_source_digest,
                "comparison_context_digest": candidate.comparison_context_digest,
                "episode_digest": sha256_digest(
                    {"kind": "frozen-materialization-parent", "case": case.content_digest}
                ),
                "manifest_digest": sha256_digest(
                    {"kind": "no-agent-episode", "fixture": fixture.fixture_id}
                ),
                "oracle_fact_digest": sha256_digest(
                    {"kind": "no-oracle-facts", "fixture": fixture.fixture_id}
                ),
                "coverage_facts_digest": sha256_digest(
                    {"kind": "no-coverage-facts", "fixture": fixture.fixture_id}
                ),
                "coverage_delta_digest": sha256_digest(
                    {"kind": "no-coverage-delta", "fixture": fixture.fixture_id}
                ),
                "exposure_stages": ("planned", "delivered"),
                "utility_disposition": "bootstrap-parent-only",
                "normal_task_completed": False,
                "submitted": False,
                "termination_reason": "frozen-materialization-parent",
                "cleanup_confirmed": True,
                "attempt_receipt_ids": (f"attempt.bootstrap.{suffix}",),
                "costs": ExecutionCosts(),
            },
            "record_digest",
        )
        entry = seal_contract(
            CorpusEntry,
            {
                "corpus_entry_id": f"corpus-entry.bootstrap.{suffix}",
                "seed_id": seed.seed_id,
                "seed_kind": SeedKind.RISK,
                "promotion_reasons": ("frozen-compatible-bootstrap-parent",),
                "execution_record_ids": (execution.execution_record_id,),
                "risk_contribution_keys": (objective.content_digest,),
                "frontier_ids": frontier_ids_by_objective[objective.objective_id],
                "carrier_kinds": (carrier_kind,),
                "compatibility_digests": (candidate.binding_source_digest,),
            },
            "entry_digest",
        )
        corpus.add_seed(seed)
        corpus.add_candidate(candidate)
        corpus.add_execution(execution)
        corpus.add_entry(entry)

    state = build_campaign_state(
        coverage=coverage,
        corpus=corpus.snapshot(),
        frontiers=build_frontier_snapshot(risk_frontiers=frontiers),
        exposure_ledger=new_baseline_exposure_ledger(),
        budget=build_campaign_budget(
            episode_limit=episode_limit,
            mutator_token_limit=mutator_token_limit,
            monetary_microunit_limit=monetary_microunit_limit,
        ),
        lifecycle=CampaignLifecycle(),
    )
    return RealCampaignBootstrap(
        initial_state=state,
        model_identity_digest=model_identity_digest,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-identity-digest", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--episode-limit", type=int, default=50)
    args = parser.parse_args()
    bootstrap = build_stage6_bootstrap(
        model_identity_digest=args.model_identity_digest,
        episode_limit=args.episode_limit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(bootstrap.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(bootstrap.initial_state.state_digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
