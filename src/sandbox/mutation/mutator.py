"""Coverage-guided semantic mutation orchestration."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from sandbox.coverage.risk_scope import CampaignRiskScopeIndex
from sandbox.mutation.alignment import StaticSemanticVerifier
from sandbox.mutation.config import MutationConfig
from sandbox.mutation.diversity import DiversityGate
from sandbox.mutation.exceptions import (
    MutationProviderError,
    MutationProviderFailureKind,
    MutationTargetError,
)
from sandbox.mutation.models import (
    ForkMutationSpec,
    MutationBatch,
    MutationBatchStatus,
    MutationCandidate,
    MutationCandidateKind,
    MutationFeedback,
    MutationPlan,
    MutationProviderCall,
    MutationProviderCallStatus,
    MutationProviderKind,
    MutationRejectionReason,
    MutationSeed,
    RawMutationCandidate,
    RejectedMutation,
)
from sandbox.mutation.normalizer import (
    fork_dedupe_key,
    normalized_prompt_digest,
    prompt_dedupe_key,
    prompt_digest,
    stable_digest,
)
from sandbox.mutation.operators import MutationOperatorRegistryIndex
from sandbox.mutation.planner import MutationPlanner
from sandbox.mutation.priority import MutationPriorityCalculator
from sandbox.mutation.providers.base import MutationProvider
from sandbox.mutation.store import MutationStore


@dataclass
class _ProviderBatchOutcome:
    candidates: list[tuple[RawMutationCandidate, int]] = field(default_factory=list)
    failure_count: int = 0
    degraded_sub_batches: int = 0

    def extend(self, other: _ProviderBatchOutcome) -> None:
        self.candidates.extend(other.candidates)
        self.failure_count += other.failure_count
        self.degraded_sub_batches += other.degraded_sub_batches


class SemanticMutator:
    def __init__(
        self,
        config: MutationConfig,
        registry: MutationOperatorRegistryIndex,
        risk_scope: CampaignRiskScopeIndex,
        provider: MutationProvider,
        diversity_gate: DiversityGate,
        priority: MutationPriorityCalculator,
        store: MutationStore,
    ) -> None:
        self.config = config
        self.registry = registry
        self.risk_scope = risk_scope
        self.provider = provider
        self.diversity_gate = diversity_gate
        self.priority = priority
        self.store = store
        self.semantic_verifier = StaticSemanticVerifier(registry)
        self.planner = MutationPlanner(
            registry,
            risk_scope,
            oversample_factor=config.generation.oversample_factor,
        )

    async def mutate(
        self,
        seed: MutationSeed,
        feedback: MutationFeedback,
        count: int,
        *,
        random_seed: int,
        target_risk: str | None = None,
        operator_id: str | None = None,
    ) -> MutationBatch:
        if seed.mutation_depth >= self.config.generation.max_mutation_depth:
            raise MutationTargetError("seed reached maximum mutation depth")
        if count < 1 or count > self.config.generation.max_count:
            raise MutationTargetError("requested mutation count is outside configured bounds")
        request_digest = stable_digest(
            {
                "campaign_id": self.config.campaign_id,
                "seed_id": seed.seed_id,
                "feedback": feedback,
                "count": count,
                "provider": self.config.provider,
                "generation": self.config.generation,
                "registry_digest": self.registry.digest,
                "random_seed": random_seed,
                "target_risk": target_risk,
                "operator_id": operator_id,
            }
        )
        batch_id = stable_digest(
            {
                "campaign_id": self.config.campaign_id,
                "seed_id": seed.seed_id,
                "request_digest": request_digest,
            }
        )
        existing = self.store.get_batch(batch_id)
        if existing is not None:
            if existing.request_digest != request_digest:
                raise MutationTargetError("batch identity conflicts with request digest")
            return existing.model_copy(update={"already_generated": True})

        plan = self.planner.plan(
            seed,
            feedback,
            count,
            provider=self.provider.kind,
            target_risk=target_risk,
            operator_id=operator_id,
        )
        accepted: list[MutationCandidate] = []
        rejected: list[RejectedMutation] = []
        historical_dedupe_keys = self.store.dedupe_keys()
        generated_count = 0
        raw_budget = self.config.generation.max_raw_candidates_per_batch
        provider_failure_count = 0
        degraded_sub_batches = 0

        for attempt in range(self.config.generation.max_generation_attempts):
            if len(accepted) >= count or generated_count >= raw_budget:
                break
            attempt_seed = int(
                stable_digest(
                    {"random_seed": random_seed, "attempt": attempt, "batch_id": batch_id}
                ).removeprefix("sha256:")[:16],
                16,
            )
            raw_count = min(plan.oversample_count, raw_budget - generated_count)
            provider_outcome = await self._generate_provider_batches(
                batch_id=batch_id,
                seed=seed,
                plan=plan,
                feedback=feedback,
                attempt=attempt,
                attempt_seed=attempt_seed,
                requested_count=raw_count,
            )
            provider_failure_count += provider_outcome.failure_count
            degraded_sub_batches += provider_outcome.degraded_sub_batches
            for raw_index, (raw, candidate_seed) in enumerate(provider_outcome.candidates):
                if len(accepted) >= count or generated_count >= raw_budget:
                    break
                generated_count += 1
                attempt_id = stable_digest(
                    {
                        "batch_id": batch_id,
                        "attempt": attempt,
                        "raw_index": raw_index,
                        "raw": raw,
                    }
                )
                planned = self._planned_item(raw, plan)
                if planned is None:
                    rejected.append(
                        self._rejection(
                            attempt_id,
                            seed,
                            raw,
                            MutationRejectionReason.INCOMPATIBLE_OPERATOR,
                            "candidate operator and targets do not match the plan",
                        )
                    )
                    continue
                try:
                    candidate = self._materialize(
                        seed,
                        feedback,
                        raw,
                        planned.target_depths,
                        candidate_seed,
                    )
                except (ValueError, MutationTargetError) as exc:
                    rejected.append(
                        self._rejection(
                            attempt_id,
                            seed,
                            raw,
                            MutationRejectionReason.INVALID_SCHEMA,
                            str(exc),
                        )
                    )
                    continue
                historical_prompts = self.store.recent_prompts(
                    candidate.target_risks,
                    limit=self.config.diversity.similarity_history_limit,
                )
                diversity = await self.diversity_gate.check(
                    candidate,
                    parent_prompt=seed.case.prompt,
                    accepted=accepted,
                    historical_prompts=historical_prompts,
                    historical_dedupe_keys=historical_dedupe_keys,
                    requested_count=count,
                )
                if not diversity.accepted:
                    rejected.append(
                        self._rejection(
                            attempt_id,
                            seed,
                            raw,
                            diversity.reason or MutationRejectionReason.NEAR_DUPLICATE,
                            diversity.detail,
                            maximum_similarity=diversity.maximum_similarity,
                        )
                    )
                    continue
                components, score = self.priority.score(
                    seed=seed,
                    feedback=feedback,
                    target_risks=candidate.target_risks,
                    operator_id=candidate.operator_id,
                    similarity=diversity.maximum_similarity,
                )
                accepted.append(
                    candidate.model_copy(
                        update={
                            "priority_components": components,
                            "mutation_priority": score,
                        }
                    )
                )

        accepted.sort(key=lambda item: (-item.mutation_priority, item.mutation_id))
        if len(accepted) >= count:
            generation_status = (
                MutationBatchStatus.DEGRADED
                if provider_failure_count or degraded_sub_batches
                else MutationBatchStatus.COMPLETE
            )
        elif accepted:
            generation_status = MutationBatchStatus.PARTIAL
        else:
            generation_status = MutationBatchStatus.NO_PROGRESS
        batch = MutationBatch(
            batch_id=batch_id,
            campaign_id=self.config.campaign_id,
            request_digest=request_digest,
            feedback=feedback,
            plan=plan,
            requested_count=count,
            generated_count=generated_count,
            accepted=accepted[:count],
            rejected=rejected,
            exhausted=len(accepted) < count,
            generation_status=generation_status,
            provider_failure_count=provider_failure_count,
            degraded_sub_batches=degraded_sub_batches,
        )
        return self.store.commit_batch(batch)

    async def _generate_provider_batches(
        self,
        *,
        batch_id: str,
        seed: MutationSeed,
        plan: MutationPlan,
        feedback: MutationFeedback,
        attempt: int,
        attempt_seed: int,
        requested_count: int,
    ) -> _ProviderBatchOutcome:
        if self.provider.kind == MutationProviderKind.OLLAMA:
            maximum = self.config.generation.provider_batch_size
        else:
            maximum = requested_count
        sizes = []
        remaining = requested_count
        while remaining:
            current = min(maximum, remaining)
            sizes.append(current)
            remaining -= current

        outcome = _ProviderBatchOutcome()
        for index, size in enumerate(sizes):
            child = await self._generate_provider_sub_batch(
                batch_id=batch_id,
                seed=seed,
                plan=plan,
                feedback=feedback,
                attempt=attempt,
                attempt_seed=attempt_seed,
                sub_batch_path=str(index),
                requested_count=size,
            )
            outcome.extend(child)
        return outcome

    async def _generate_provider_sub_batch(
        self,
        *,
        batch_id: str,
        seed: MutationSeed,
        plan: MutationPlan,
        feedback: MutationFeedback,
        attempt: int,
        attempt_seed: int,
        sub_batch_path: str,
        requested_count: int,
    ) -> _ProviderBatchOutcome:
        outcome = _ProviderBatchOutcome()
        maximum_attempts = self.config.generation.provider_max_attempts
        for retry_index in range(maximum_attempts):
            sub_batch_seed = self._sub_batch_seed(
                batch_id=batch_id,
                attempt_seed=attempt_seed,
                attempt=attempt,
                sub_batch_path=sub_batch_path,
                retry_index=retry_index,
                requested_count=requested_count,
            )
            fallback_request_digest = stable_digest(
                {
                    "provider": self.provider.kind.value,
                    "provider_version": self.provider.version,
                    "model_name": self.provider.model_name,
                    "model_digest": self.provider.model_digest,
                    "seed": seed,
                    "plan": plan,
                    "count": requested_count,
                    "random_seed": sub_batch_seed,
                }
            )
            started = time.monotonic()
            try:
                provider_result = await self.provider.generate(
                    seed,
                    plan,
                    count=requested_count,
                    random_seed=sub_batch_seed,
                )
            except MutationProviderError as exc:
                latency_ms = max(0, round((time.monotonic() - started) * 1_000))
                self._record_provider_call(
                    batch_id=batch_id,
                    seed=seed,
                    plan=plan,
                    feedback=feedback,
                    attempt=attempt,
                    sub_batch_path=sub_batch_path,
                    retry_index=retry_index,
                    random_seed=sub_batch_seed,
                    requested_count=requested_count,
                    request_digest=exc.request_digest or fallback_request_digest,
                    response_digest=exc.response_digest,
                    response_bytes=exc.response_bytes,
                    latency_ms=latency_ms,
                    status=MutationProviderCallStatus.FAILED,
                    done_reason=exc.done_reason,
                    error=exc,
                )
                outcome.failure_count += 1
                if not exc.recoverable:
                    raise
                if self._should_shrink(exc, requested_count):
                    outcome.degraded_sub_batches += 1
                    left = requested_count // 2
                    right = requested_count - left
                    for suffix, size in (("0", left), ("1", right)):
                        child = await self._generate_provider_sub_batch(
                            batch_id=batch_id,
                            seed=seed,
                            plan=plan,
                            feedback=feedback,
                            attempt=attempt,
                            attempt_seed=attempt_seed,
                            sub_batch_path=f"{sub_batch_path}.{suffix}",
                            requested_count=size,
                        )
                        outcome.extend(child)
                    return outcome
                if retry_index + 1 < maximum_attempts:
                    delay = self.config.generation.provider_retry_backoff_seconds * 2**retry_index
                    if delay:
                        await asyncio.sleep(delay)
                    continue
                return outcome

            latency_ms = max(0, round((time.monotonic() - started) * 1_000))
            self._record_provider_call(
                batch_id=batch_id,
                seed=seed,
                plan=plan,
                feedback=feedback,
                attempt=attempt,
                sub_batch_path=sub_batch_path,
                retry_index=retry_index,
                random_seed=sub_batch_seed,
                requested_count=requested_count,
                request_digest=provider_result.request_digest,
                response_digest=provider_result.response_digest,
                response_bytes=provider_result.response_bytes,
                latency_ms=latency_ms,
                generated_count=len(provider_result.candidates),
                prompt_eval_count=provider_result.prompt_eval_count,
                eval_count=provider_result.eval_count,
                total_duration_ns=provider_result.total_duration_ns,
                load_duration_ns=provider_result.load_duration_ns,
                prompt_eval_duration_ns=provider_result.prompt_eval_duration_ns,
                eval_duration_ns=provider_result.eval_duration_ns,
                status=MutationProviderCallStatus.SUCCEEDED,
                done_reason=provider_result.done_reason,
            )
            if len(provider_result.candidates) < requested_count:
                outcome.degraded_sub_batches += 1
            outcome.candidates.extend(
                (candidate, sub_batch_seed) for candidate in provider_result.candidates
            )
            return outcome
        return outcome

    def _should_shrink(self, error: MutationProviderError, requested_count: int) -> bool:
        if requested_count <= self.config.generation.provider_min_batch_size:
            return False
        if error.kind in {
            MutationProviderFailureKind.TRUNCATED,
            MutationProviderFailureKind.INVALID_JSON,
            MutationProviderFailureKind.INVALID_SCHEMA,
            MutationProviderFailureKind.RESPONSE_TOO_LARGE,
        }:
            return True
        return error.kind == MutationProviderFailureKind.HTTP and error.http_status == 413

    @staticmethod
    def _sub_batch_seed(
        *,
        batch_id: str,
        attempt_seed: int,
        attempt: int,
        sub_batch_path: str,
        retry_index: int,
        requested_count: int,
    ) -> int:
        return int(
            stable_digest(
                {
                    "batch_id": batch_id,
                    "attempt_seed": attempt_seed,
                    "attempt": attempt,
                    "sub_batch_path": sub_batch_path,
                    "retry_index": retry_index,
                    "requested_count": requested_count,
                }
            ).removeprefix("sha256:")[:16],
            16,
        )

    @staticmethod
    def _planned_item(raw: RawMutationCandidate, plan: MutationPlan):
        targets = sorted(raw.target_risks)
        return next(
            (
                item
                for item in plan.items
                if item.operator_id == raw.operator_id and sorted(item.target_risks) == targets
            ),
            None,
        )

    def _materialize(
        self,
        seed: MutationSeed,
        feedback: MutationFeedback,
        raw: RawMutationCandidate,
        target_depths: dict[str, int],
        random_seed: int,
    ) -> MutationCandidate:
        operator = self.registry.get(raw.operator_id)
        target_risks = sorted(set(raw.target_risks))
        static_alignment = self.semantic_verifier.verify(raw)
        for category_id in target_risks:
            reachable = self.risk_scope.max_reachable_depth(category_id)
            if reachable is None:
                raise MutationTargetError(f"target is outside campaign scope: {category_id}")
            if target_depths[category_id] > reachable:
                raise MutationTargetError(f"target depth exceeds campaign scope: {category_id}")
        candidate_kind = operator.candidate_kinds[0]
        fork = None
        prompt = raw.prompt
        if candidate_kind == MutationCandidateKind.FORK:
            if not (seed.replay_id and seed.checkpoint_id):
                raise MutationTargetError("fork mutation requires replay and checkpoint")
            fork = ForkMutationSpec(
                parent_replay_id=seed.replay_id,
                checkpoint_id=seed.checkpoint_id,
                injection_type="prompt_append",
                content=raw.prompt,
            )
            prompt = None
            dedupe_key = fork_dedupe_key(
                parent_replay_id=seed.replay_id,
                checkpoint_id=seed.checkpoint_id,
                injection_type=fork.injection_type,
                content=fork.content,
            )
        else:
            dedupe_key = prompt_dedupe_key(raw.prompt)
        feedback_digest = stable_digest(feedback)
        identity = {
            "parent_seed_id": seed.seed_id,
            "parent_mutation_id": seed.parent_mutation_id,
            "mutation_depth": seed.mutation_depth + 1,
            "operator_id": operator.operator_id,
            "operator_version": operator.version,
            "target_risks": target_risks,
            "target_depths": target_depths,
            "candidate_kind": candidate_kind.value,
            "fork": fork,
            "normalized_prompt_sha256": normalized_prompt_digest(raw.prompt),
            "dedupe_key": dedupe_key,
            "provider": self.provider.kind.value,
            "provider_version": self.provider.version,
            "model_digest": self.provider.model_digest,
            "generation_prompt_version": self.provider.generation_prompt_version,
            "random_seed": random_seed,
            "feedback_digest": feedback_digest,
            "static_alignment": static_alignment,
        }
        path_signature = self.priority.path_signature(
            seed,
            operator.operator_id,
            target_risks,
        )
        return MutationCandidate(
            mutation_id=stable_digest(identity),
            candidate_kind=candidate_kind,
            parent_seed_id=seed.seed_id,
            parent_mutation_id=seed.parent_mutation_id,
            mutation_depth=seed.mutation_depth + 1,
            operator_id=operator.operator_id,
            operator_version=operator.version,
            target_risks=target_risks,
            provider_claimed_operator_id=raw.operator_id,
            provider_claimed_target_risks=sorted(set(raw.target_risks)),
            static_alignment=static_alignment,
            target_depths=target_depths,
            prompt=prompt,
            fork=fork,
            prompt_sha256=prompt_digest(raw.prompt),
            normalized_prompt_sha256=normalized_prompt_digest(raw.prompt),
            dedupe_key=dedupe_key,
            provider=self.provider.kind,
            provider_version=self.provider.version,
            model_name=self.provider.model_name,
            model_digest=self.provider.model_digest,
            generation_prompt_version=self.provider.generation_prompt_version,
            random_seed=random_seed,
            expected_novelty=raw.expected_novelty,
            constraints_preserved=raw.constraints_preserved,
            path_signature=path_signature,
            mutation_priority=0.0,
            feedback_digest=feedback_digest,
        )

    def _record_provider_call(
        self,
        *,
        batch_id: str,
        seed: MutationSeed,
        plan: MutationPlan,
        feedback: MutationFeedback,
        attempt: int,
        sub_batch_path: str,
        retry_index: int,
        random_seed: int,
        requested_count: int,
        request_digest: str,
        latency_ms: int,
        status: MutationProviderCallStatus,
        response_digest: str | None = None,
        response_bytes: int | None = None,
        generated_count: int = 0,
        prompt_eval_count: int | None = None,
        eval_count: int | None = None,
        total_duration_ns: int | None = None,
        load_duration_ns: int | None = None,
        prompt_eval_duration_ns: int | None = None,
        eval_duration_ns: int | None = None,
        done_reason: str | None = None,
        error: Exception | None = None,
    ) -> None:
        provider_error = error if isinstance(error, MutationProviderError) else None
        identity = {
            "batch_id": batch_id,
            "attempt": attempt,
            "sub_batch_path": sub_batch_path,
            "retry_index": retry_index,
            "request_digest": request_digest,
            "response_digest": response_digest,
            "status": status.value,
            "error_kind": provider_error.kind.value if provider_error else None,
            "http_status": provider_error.http_status if provider_error else None,
            "done_reason": done_reason,
            "latency_ms": latency_ms,
        }
        self.store.record_provider_call(
            MutationProviderCall(
                call_id=stable_digest(identity),
                batch_id=batch_id,
                parent_seed_id=seed.seed_id,
                plan_id=plan.plan_id,
                feedback_digest=stable_digest(feedback),
                attempt_index=attempt,
                sub_batch_path=sub_batch_path,
                retry_index=retry_index,
                random_seed=random_seed,
                requested_count=requested_count,
                provider=self.provider.kind,
                provider_version=self.provider.version,
                model_name=self.provider.model_name,
                model_digest=self.provider.model_digest,
                request_digest=request_digest,
                response_digest=response_digest,
                response_bytes=response_bytes,
                latency_ms=latency_ms,
                generated_count=generated_count,
                prompt_eval_count=prompt_eval_count,
                eval_count=eval_count,
                total_duration_ns=total_duration_ns,
                load_duration_ns=load_duration_ns,
                prompt_eval_duration_ns=prompt_eval_duration_ns,
                eval_duration_ns=eval_duration_ns,
                status=status,
                error_code=type(error).__name__ if error else None,
                error_kind=provider_error.kind if provider_error else None,
                retryable=provider_error.recoverable if provider_error else False,
                http_status=provider_error.http_status if provider_error else None,
                done_reason=done_reason,
                response_summary=provider_error.response_summary if provider_error else "",
                error_detail=str(error)[:500] if error else "",
            )
        )

    @staticmethod
    def _rejection(
        attempt_id: str,
        seed: MutationSeed,
        raw: RawMutationCandidate,
        reason: MutationRejectionReason,
        detail: str,
        *,
        maximum_similarity: float | None = None,
    ) -> RejectedMutation:
        return RejectedMutation(
            attempt_id=attempt_id,
            parent_seed_id=seed.seed_id,
            operator_id=raw.operator_id,
            target_risks=sorted(set(raw.target_risks)),
            prompt_sha256=prompt_digest(raw.prompt),
            normalized_prompt_sha256=normalized_prompt_digest(raw.prompt),
            maximum_similarity=maximum_similarity,
            reason=reason,
            detail=detail[:500],
        )
