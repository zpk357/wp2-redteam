from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.coverage.models import CoverageResult, CoverageSnapshot
from sandbox.coverage.risk_scope import CampaignRiskScopeLoader
from sandbox.coverage.taxonomy import RiskTaxonomyLoader
from sandbox.engine.case_source import TemplateCaseSource
from sandbox.mutation.config import MutationConfig, MutationGenerationConfig
from sandbox.mutation.diversity import DiversityGate
from sandbox.mutation.exceptions import (
    MutationProviderError,
    MutationProviderFailureKind,
    MutationTargetError,
)
from sandbox.mutation.feedback import MutationFeedbackBuilder
from sandbox.mutation.models import (
    MutationBatch,
    MutationBatchStatus,
    MutationHistorySnapshot,
    MutationProviderCall,
    MutationProviderCallStatus,
    MutationProviderKind,
    MutationProviderResult,
    MutationSeed,
    RawMutationCandidate,
    to_test_case,
)
from sandbox.mutation.mutator import SemanticMutator
from sandbox.mutation.normalizer import normalize_prompt, prompt_digest, stable_digest
from sandbox.mutation.operators import MutationOperatorRegistryLoader
from sandbox.mutation.priority import MutationPriorityCalculator
from sandbox.mutation.providers.rule_based import RuleBasedMutationProvider
from sandbox.mutation.similarity import CharacterShingleSimilarity
from sandbox.mutation.store import MutationStore


def _components(
    tmp_path: Path,
    *,
    generation: MutationGenerationConfig | None = None,
):
    taxonomy = RiskTaxonomyLoader(Path("config/risk-taxonomy.yaml")).load()
    scope = CampaignRiskScopeLoader(Path("config/risk-scope-week3.yaml"), taxonomy).load()
    registry = MutationOperatorRegistryLoader(
        Path("config/mutation-operators.yaml"), taxonomy
    ).load()
    config = MutationConfig(
        store_root=tmp_path,
        generation=generation or MutationGenerationConfig(),
    )
    store = MutationStore(
        config.store_root,
        config.campaign_id,
        metadata={
            "taxonomy_version": taxonomy.taxonomy_version,
            "risk_scope_version": scope.scope_version,
            "risk_scope_digest": scope.digest,
            "operator_registry_version": registry.registry_version,
            "operator_registry_digest": registry.digest,
            "normalization_version": config.diversity.normalization_version,
            "similarity_version": config.diversity.similarity_version,
            "priority_formula_version": config.priority.formula_version,
        },
    )
    return taxonomy, scope, registry, config, store


def _snapshot(taxonomy, scope, *, include_depths: bool = True) -> CoverageSnapshot:
    depths = dict.fromkeys(taxonomy.leaf_ids, 0) if include_depths else {}
    return CoverageSnapshot(
        campaign_id="week4-baseline",
        taxonomy_version=taxonomy.taxonomy_version,
        taxonomy_digest=taxonomy.digest,
        risk_scope_version=scope.scope_version,
        risk_depths=depths,
    )


def _seed() -> MutationSeed:
    case = TemplateCaseSource().generate("path-absolute-001", seed=42)
    return MutationSeed(
        seed_id=case.case_id,
        case=case,
        prompt_sha256=prompt_digest(case.prompt),
    )


class TruncatingPartialProvider:
    kind = MutationProviderKind.OLLAMA
    version = "test-ollama-v1"
    model_name = "test-model"
    model_digest = "sha256:" + "a" * 64
    generation_prompt_version = "test-prompt-v1"

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def generate(self, seed, plan, *, count: int, random_seed: int):
        self.calls.append((count, random_seed))
        request_digest = stable_digest(
            {"count": count, "random_seed": random_seed, "call": len(self.calls)}
        )
        if len(self.calls) == 1:
            raise MutationProviderError(
                "truncated test response",
                kind=MutationProviderFailureKind.TRUNCATED,
                recoverable=True,
                request_digest=request_digest,
                response_digest=stable_digest("truncated"),
                response_bytes=123,
                response_summary='{"candidates":[',
                done_reason="length",
            )
        if len(self.calls) > 2:
            raise MutationProviderError(
                "temporary transport failure",
                kind=MutationProviderFailureKind.TRANSPORT,
                recoverable=True,
                request_digest=request_digest,
            )
        candidates = []
        for index in range(count):
            item = plan.items[index % len(plan.items)]
            candidates.append(
                RawMutationCandidate(
                    prompt=f"isolated mutation {random_seed}-{index}",
                    operator_id=item.operator_id,
                    target_risks=item.target_risks,
                )
            )
        return MutationProviderResult(
            candidates=candidates,
            request_digest=request_digest,
            response_digest=stable_digest(candidates),
            response_bytes=256,
            done_reason="stop",
        )


class RecoverableFailureProvider(TruncatingPartialProvider):
    async def generate(self, seed, plan, *, count: int, random_seed: int):
        self.calls.append((count, random_seed))
        raise MutationProviderError(
            "temporary transport failure",
            kind=MutationProviderFailureKind.TRANSPORT,
            recoverable=True,
            request_digest=stable_digest(
                {"count": count, "random_seed": random_seed, "call": len(self.calls)}
            ),
        )


class PermanentFailureProvider(TruncatingPartialProvider):
    async def generate(self, seed, plan, *, count: int, random_seed: int):
        self.calls.append((count, random_seed))
        raise MutationProviderError(
            "invalid provider configuration",
            kind=MutationProviderFailureKind.HTTP,
            recoverable=False,
            request_digest=stable_digest({"count": count, "random_seed": random_seed}),
            http_status=400,
        )


def test_legacy_mutation_records_remain_readable() -> None:
    batch = MutationBatch(
        batch_id="sha256:" + "1" * 64,
        campaign_id="legacy",
        request_digest="sha256:" + "2" * 64,
        requested_count=1,
        generated_count=0,
        exhausted=True,
    )
    legacy_batch = batch.model_dump(
        exclude={"generation_status", "provider_failure_count", "degraded_sub_batches"}
    )
    restored_batch = MutationBatch.model_validate(legacy_batch)
    assert restored_batch.generation_status is None
    assert restored_batch.provider_failure_count == 0

    call = MutationProviderCall(
        call_id="sha256:" + "3" * 64,
        batch_id=batch.batch_id,
        parent_seed_id="legacy-seed",
        plan_id="sha256:" + "4" * 64,
        feedback_digest="sha256:" + "5" * 64,
        attempt_index=0,
        random_seed=42,
        requested_count=12,
        provider=MutationProviderKind.OLLAMA,
        provider_version="ollama-mutator-v1",
        model_name="qwen3:8b",
        model_digest="sha256:" + "6" * 64,
        request_digest="sha256:" + "7" * 64,
        latency_ms=1,
        generated_count=0,
        status=MutationProviderCallStatus.FAILED,
    )
    legacy_call = call.model_dump(
        exclude={
            "sub_batch_path",
            "retry_index",
            "error_kind",
            "retryable",
            "http_status",
            "done_reason",
            "response_summary",
        }
    )
    restored_call = MutationProviderCall.model_validate(legacy_call)
    assert restored_call.sub_batch_path == "0"
    assert restored_call.retry_index == 0
    assert restored_call.error_kind is None


def test_prompt_normalization_is_stable() -> None:
    assert normalize_prompt("Ａ  B\r\n\r\n\r\n C  ") == "A B\n\n C"


def test_feedback_requires_explicit_depth_for_every_leaf(tmp_path: Path) -> None:
    taxonomy, scope, _registry, _config, store = _components(tmp_path)
    try:
        builder = MutationFeedbackBuilder(taxonomy, scope)
        with pytest.raises(MutationTargetError, match="explicit risk_depths"):
            builder.build(_seed(), _snapshot(taxonomy, scope, include_depths=False))
    finally:
        store.close()


def test_feedback_digests_non_integral_coverage_values(tmp_path: Path) -> None:
    taxonomy, scope, _registry, _config, store = _components(tmp_path)
    try:
        snapshot = _snapshot(taxonomy, scope).model_copy(
            update={
                "intent_coverage": 1 / 3,
                "applicable_behavior_coverage": 2 / 3,
            }
        )
        coverage_result = CoverageResult(
            trajectory_id="trajectory-with-floats",
            execution_id="execution-with-floats",
            input_digest="sha256:" + "1" * 64,
            behavior_profile_hash="sha256:" + "2" * 64,
            behavior_growth_rate=1 / 3,
            risk_progress_delta=2 / 3,
            combined_delta=0.5,
        )
        seed = _seed().model_copy(update={"coverage_result": coverage_result})
        builder = MutationFeedbackBuilder(taxonomy, scope)

        first = builder.build(seed, snapshot)
        second = builder.build(seed, snapshot)

        assert first.coverage_snapshot_digest == second.coverage_snapshot_digest
        assert first.parent_coverage_digest == second.parent_coverage_digest
        assert first.coverage_snapshot_digest.startswith("sha256:")
        assert first.parent_coverage_digest is not None
        assert first.parent_coverage_digest.startswith("sha256:")
    finally:
        store.close()


async def test_rule_based_mutation_is_diverse_persistent_and_idempotent(
    tmp_path: Path,
) -> None:
    taxonomy, scope, registry, config, store = _components(tmp_path)
    try:
        seed = _seed()
        feedback = MutationFeedbackBuilder(taxonomy, scope).build(
            seed,
            _snapshot(taxonomy, scope),
            history=MutationHistorySnapshot(campaign_id=config.campaign_id),
        )
        assert len(feedback.risk_gaps) == len(scope.category_ids)
        assert all(gap.gap_ratio > 0 for gap in feedback.risk_gaps)
        similarity = CharacterShingleSimilarity()
        mutator = SemanticMutator(
            config,
            registry,
            scope,
            RuleBasedMutationProvider(),
            DiversityGate(similarity, config.diversity),
            MutationPriorityCalculator(config.priority),
            store,
        )
        batch = await mutator.mutate(seed, feedback, 6, random_seed=42)
        assert batch.feedback == feedback
        assert batch.plan is not None
        assert batch.plan.feedback_digest.startswith("sha256:")
        assert len(store.provider_calls()) >= 1
        assert all(call.status.value == "succeeded" for call in store.provider_calls())
        assert len(batch.accepted) >= 4
        assert len({candidate.operator_id for candidate in batch.accepted}) >= 3
        assert len({candidate.dedupe_key for candidate in batch.accepted}) == len(batch.accepted)
        assert all(candidate.mutation_priority >= 0 for candidate in batch.accepted)
        assert all(candidate.priority_components for candidate in batch.accepted)
        assert all(candidate.path_signature for candidate in batch.accepted)
        case = to_test_case(batch.accepted[0])
        assert case.metadata["mutation_id"] == batch.accepted[0].mutation_id

        repeated = await mutator.mutate(seed, feedback, 6, random_seed=42)
        assert repeated.already_generated is True
        assert [item.mutation_id for item in repeated.accepted] == [
            item.mutation_id for item in batch.accepted
        ]
        snapshot = store.snapshot()
        assert snapshot.total_batches == 1
        assert snapshot.total_accepted == len(batch.accepted)
        first = batch.accepted[0]
        assert snapshot.path_counts[first.path_signature] >= 1

        updated_feedback = MutationFeedbackBuilder(taxonomy, scope).build(
            seed,
            _snapshot(taxonomy, scope),
            history=snapshot,
        )
        components, _score = MutationPriorityCalculator(config.priority).score(
            seed=seed,
            feedback=updated_feedback,
            target_risks=first.target_risks,
            operator_id=first.operator_id,
            similarity=0.0,
        )
        assert components["path_frequency"] > 0
    finally:
        store.close()


async def test_ollama_batches_shrink_and_preserve_partial_success(tmp_path: Path) -> None:
    generation = MutationGenerationConfig(
        oversample_factor=1,
        max_generation_attempts=1,
        provider_batch_size=4,
        provider_min_batch_size=2,
        provider_max_attempts=2,
        provider_retry_backoff_seconds=0,
    )
    taxonomy, scope, registry, config, store = _components(
        tmp_path,
        generation=generation,
    )
    provider = TruncatingPartialProvider()
    try:
        seed = _seed()
        feedback = MutationFeedbackBuilder(taxonomy, scope).build(
            seed,
            _snapshot(taxonomy, scope),
        )
        mutator = SemanticMutator(
            config,
            registry,
            scope,
            provider,
            DiversityGate(CharacterShingleSimilarity(), config.diversity),
            MutationPriorityCalculator(config.priority),
            store,
        )

        batch = await mutator.mutate(seed, feedback, 4, random_seed=42)

        assert [count for count, _seed_value in provider.calls] == [4, 2, 2, 2]
        assert len({seed_value for _count, seed_value in provider.calls}) == 4
        assert batch.generation_status == MutationBatchStatus.PARTIAL
        assert batch.generated_count == 2
        assert batch.accepted
        assert batch.provider_failure_count == 3
        assert batch.degraded_sub_batches == 1
        calls = store.provider_calls()
        assert [call.sub_batch_path for call in calls] == ["0", "0.0", "0.1", "0.1"]
        assert calls[0].error_kind == MutationProviderFailureKind.TRUNCATED
        assert calls[0].response_bytes == 123
        assert calls[0].done_reason == "length"
        assert calls[0].response_summary == '{"candidates":['
        assert calls[-1].retry_index == 1
    finally:
        store.close()


async def test_recoverable_provider_exhaustion_becomes_no_progress(tmp_path: Path) -> None:
    generation = MutationGenerationConfig(
        oversample_factor=1,
        max_generation_attempts=1,
        provider_max_attempts=2,
        provider_retry_backoff_seconds=0,
    )
    taxonomy, scope, registry, config, store = _components(
        tmp_path,
        generation=generation,
    )
    provider = RecoverableFailureProvider()
    try:
        seed = _seed()
        feedback = MutationFeedbackBuilder(taxonomy, scope).build(
            seed,
            _snapshot(taxonomy, scope),
        )
        mutator = SemanticMutator(
            config,
            registry,
            scope,
            provider,
            DiversityGate(CharacterShingleSimilarity(), config.diversity),
            MutationPriorityCalculator(config.priority),
            store,
        )

        batch = await mutator.mutate(seed, feedback, 4, random_seed=42)

        assert batch.generation_status == MutationBatchStatus.NO_PROGRESS
        assert batch.accepted == []
        assert batch.provider_failure_count == 2
        assert len(store.provider_calls()) == 2
    finally:
        store.close()


async def test_permanent_provider_failure_is_not_downgraded(tmp_path: Path) -> None:
    generation = MutationGenerationConfig(
        oversample_factor=1,
        max_generation_attempts=1,
        provider_max_attempts=2,
        provider_retry_backoff_seconds=0,
    )
    taxonomy, scope, registry, config, store = _components(
        tmp_path,
        generation=generation,
    )
    provider = PermanentFailureProvider()
    try:
        seed = _seed()
        feedback = MutationFeedbackBuilder(taxonomy, scope).build(
            seed,
            _snapshot(taxonomy, scope),
        )
        mutator = SemanticMutator(
            config,
            registry,
            scope,
            provider,
            DiversityGate(CharacterShingleSimilarity(), config.diversity),
            MutationPriorityCalculator(config.priority),
            store,
        )

        with pytest.raises(MutationProviderError, match="invalid provider configuration"):
            await mutator.mutate(seed, feedback, 4, random_seed=42)

        calls = store.provider_calls()
        assert len(calls) == 1
        assert calls[0].retryable is False
        assert calls[0].http_status == 400
    finally:
        store.close()
