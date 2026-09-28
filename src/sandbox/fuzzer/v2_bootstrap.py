"""Build the initial Office V2 corpus for an exploratory Campaign."""

from __future__ import annotations

import re

from sandbox.coverage.v2_episode_coverage import empty_v2_coverage_snapshot
from sandbox.fuzzer.v2_campaign import CampaignLifecycle
from sandbox.fuzzer.v2_campaign_state import build_campaign_budget, build_campaign_state
from sandbox.fuzzer.v2_corpus import (
    CorpusEntry,
    DeliveredPayload,
    ExecutableSeedSupport,
    FrozenRootSupportRecord,
    MaterializedCandidate,
    PayloadSpec,
    SeedKind,
    V2Corpus,
    seal_contract,
)
from sandbox.fuzzer.v2_real_runtime import ExploratoryCampaignBootstrap
from sandbox.fuzzer.v2_seed_pools import build_initial_seed_catalog
from sandbox.fuzzer.v2_target_oracle import (
    build_formal_target_scenario_supports,
    validate_target_oracle_catalog,
)
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_models import DirectTaskCondition


def _payload_location(case):
    condition = case.adversarial_condition
    if not isinstance(condition, DirectTaskCondition):
        raise ValueError("root support must use a direct task condition")
    return (
        condition.instruction_variant,
        "task",
        case.task.task_id,
        str(case.task.task_version),
        "instruction",
    )


def _corpus_resource_id(value: str) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", value):
        return value
    return "bootstrap-resource." + sha256_digest(value).split(":", 1)[1][:24]


def build_exploratory_bootstrap(
    *,
    model_name: str = "qwen3.5:27b-q4_K_M",
    episode_limit: int = 10,
    mutator_token_limit: int = 1_000_000,
    monetary_microunit_limit: int = 1_000_000_000,
) -> ExploratoryCampaignBootstrap:
    coverage = empty_v2_coverage_snapshot()
    seed_catalog = build_initial_seed_catalog()
    root_seeds = tuple(seed for pool in seed_catalog.pools for seed in pool.root_seeds)
    validate_target_oracle_catalog(root_seeds)
    supports = build_formal_target_scenario_supports()
    corpus = V2Corpus()
    for support in supports:
        formal_seed = support.seed
        case = support.scenario_case
        _, carrier_kind, resource_id, resource_version, field_path = _payload_location(case)
        suffix = formal_seed.seed_id.replace(".", "-")
        content = formal_seed.base_text
        payload = PayloadSpec(
            payload_spec_id=f"payload.bootstrap.{suffix}",
            content=content,
            carrier_kind=carrier_kind,
            field_path=field_path,
            content_digest=sha256_digest({"content": content}),
        )
        seed = seal_contract(
            ExecutableSeedSupport,
            {
                "seed_id": formal_seed.seed_id,
                "payload_specs": (payload,),
                "root_seed_id": formal_seed.seed_id,
                "generation_depth": 0,
            },
            "seed_content_digest",
        )
        candidate = seal_contract(
            MaterializedCandidate,
            {
                "materialized_candidate_id": f"materialized.bootstrap.{suffix}",
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
                        materialization_evidence_digest=case.materialization_record.materialization_digest,
                    ),
                ),
                "binding_source_digest": support.compatibility_decision.decision_digest,
                "comparison_context_digest": case.content_digest,
                "baseline_snapshot_digest": coverage.snapshot_digest,
            },
            "materialization_digest",
        )
        root_support = seal_contract(
            FrozenRootSupportRecord,
            {
                "support_record_id": f"support.bootstrap.{suffix}",
                "seed_id": seed.seed_id,
                "materialized_candidate_id": candidate.materialized_candidate_id,
                "scenario_case_id": case.case_id,
                "actor_id": case.actor.actor_id,
                "task_id": case.task.task_id,
                "resource_binding_digest": candidate.resource_binding_digest,
                "binding_source_digest": candidate.binding_source_digest,
                "comparison_context_digest": candidate.comparison_context_digest,
                "baseline_snapshot_digest": candidate.baseline_snapshot_digest,
            },
            "support_digest",
        )
        entry = seal_contract(
            CorpusEntry,
            {
                "corpus_entry_id": f"corpus-entry.bootstrap.{suffix}",
                "seed_id": seed.seed_id,
                "seed_kind": SeedKind.RISK,
                "promotion_reasons": ("exploratory-bootstrap-parent",),
                "supporting_root_record_ids": (root_support.support_record_id,),
                "risk_contribution_keys": (support.target_oracle.oracle_digest,),
                "compatibility_digests": (candidate.binding_source_digest,),
            },
            "entry_digest",
        )
        corpus.add_execution_support(seed)
        corpus.add_candidate(candidate)
        corpus.add_root_support(root_support)
        corpus.add_entry(entry)

    state = build_campaign_state(
        coverage=coverage,
        corpus=corpus.snapshot(),
        seed_catalog=seed_catalog,
        new_seed_priority_queue=(),
        budget=build_campaign_budget(
            episode_limit=episode_limit,
            mutator_token_limit=mutator_token_limit,
            monetary_microunit_limit=monetary_microunit_limit,
        ),
        lifecycle=CampaignLifecycle(),
    )
    return ExploratoryCampaignBootstrap(initial_state=state, model_name=model_name)


__all__ = ["build_exploratory_bootstrap"]
