"""Deterministic adaptive scheduling contracts for office campaigns."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from sandbox.content_digests import decimalized_sha256_digest
from sandbox.scenarios.models import FrozenContract, Identifier

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
OFFICE_ADAPTIVE_SCHEDULER_POLICY_VERSION = "office-adaptive-interleave-v1"


def _scheduler_digest(value: object) -> str:
    return decimalized_sha256_digest(value, label="office adaptive scheduler digest")


class AdaptiveDirectionOutcome(StrEnum):
    SUBMITTED = "submitted"
    CANDIDATE_REJECTED = "candidate_rejected"
    PROVIDER_ERROR = "provider_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    CLEANUP_ERROR = "cleanup_error"
    SOAK_PROBE = "soak_probe"


class OfficeAdaptiveSchedulerPolicy(FrozenContract):
    policy_version: str = Field(
        default=OFFICE_ADAPTIVE_SCHEDULER_POLICY_VERSION,
        min_length=1,
        max_length=128,
    )
    batch_size: int = Field(default=2, ge=2, le=4)
    exploration_slots: int = Field(default=1, ge=1, le=3)
    max_consecutive_decisions: int = Field(default=2, ge=1, le=32)
    starvation_decisions: int = Field(default=2, ge=1, le=128)
    cooldown_after_no_gain: int = Field(default=2, ge=1, le=128)
    cooldown_observations: int = Field(default=2, ge=1, le=128)
    token_cost_reference: int = Field(default=4096, ge=1)
    risk_gap_weight: int = Field(default=8, ge=0)
    behavior_gap_weight: int = Field(default=4, ge=0)
    path_risk_novelty_weight: int = Field(default=5, ge=0)
    undersampling_weight: int = Field(default=5, ge=0)
    waiting_age_weight: int = Field(default=4, ge=0)
    repeat_penalty_weight: int = Field(default=4, ge=0)
    no_gain_penalty_weight: int = Field(default=5, ge=0)
    invalid_penalty_weight: int = Field(default=4, ge=0)
    cost_penalty_weight: int = Field(default=2, ge=0)
    virtual_runtime_penalty_weight: int = Field(default=1, ge=0)
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_policy(self) -> OfficeAdaptiveSchedulerPolicy:
        if self.exploration_slots >= self.batch_size:
            raise ValueError("exploration reserve must leave an exploitation slot")
        expected = _scheduler_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("adaptive scheduler policy digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class AdaptiveFrontierStats(FrozenContract):
    risk_category_id: Identifier
    selection_count: int = Field(default=0, ge=0)
    last_selected_decision_index: int | None = Field(default=None, ge=0)
    consecutive_selected_decisions: int = Field(default=0, ge=0)
    candidate_attempts: int = Field(default=0, ge=0)
    invalid_candidates: int = Field(default=0, ge=0)
    submitted_episodes: int = Field(default=0, ge=0)
    consecutive_no_gain: int = Field(default=0, ge=0)
    tokens_consumed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> AdaptiveFrontierStats:
        if self.invalid_candidates + self.submitted_episodes > self.candidate_attempts:
            raise ValueError("scheduler candidate outcomes exceed attempts")
        if self.last_selected_decision_index is None:
            if self.selection_count or self.consecutive_selected_decisions:
                raise ValueError("unselected frontier cannot carry selection history")
        elif not self.selection_count:
            raise ValueError("selected frontier requires selection history")
        if self.consecutive_selected_decisions > self.selection_count:
            raise ValueError("consecutive selections exceed total selections")
        return self


class AdaptiveFrontierInput(FrozenContract):
    risk_category_id: Identifier
    observed_execution_depth: int = Field(ge=0, le=3)
    max_reachable_depth: int = Field(ge=1, le=3)
    next_execution_target_depth: int | None = Field(default=None, ge=2, le=3)
    composition_ids: tuple[Identifier, ...]
    composition_objective_ids: tuple[Identifier, ...]
    parent_seed_ids: tuple[str, ...] = Field(default_factory=tuple)
    behavior_gap_ids: tuple[str, ...] = Field(default_factory=tuple)
    observed_path_risk_cells: int = Field(default=0, ge=0)
    total_path_risk_cells: int = Field(default=0, ge=0)
    virtual_runtime_millis: int = Field(default=0, ge=0)
    episode_limit: int = Field(default=0, ge=0)
    episodes_consumed: int = Field(default=0, ge=0)
    token_limit: int = Field(default=0, ge=0)
    tokens_consumed: int = Field(default=0, ge=0)
    recovery_status: str = Field(min_length=1, max_length=64)

    @field_validator("parent_seed_ids", "behavior_gap_ids")
    @classmethod
    def string_inputs_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("scheduler frontier strings must be non-empty and unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_frontier(self) -> AdaptiveFrontierInput:
        if len(self.composition_ids) != len(self.composition_objective_ids):
            raise ValueError("scheduler frontier requires aligned compositions")
        if tuple(sorted(self.composition_ids)) != self.composition_ids or len(
            self.composition_ids
        ) != len(set(self.composition_ids)):
            raise ValueError("scheduler frontier compositions must be unique and sorted")
        if self.recovery_status == "ready" and (
            not self.composition_ids or self.next_execution_target_depth is None
        ):
            raise ValueError("ready scheduler frontier requires a target composition")
        if self.recovery_status == "target_depth_reached" and (
            not self.composition_ids
            or self.next_execution_target_depth is not None
            or self.observed_execution_depth != self.max_reachable_depth
        ):
            raise ValueError(
                "target-depth scheduler frontier requires completed risk depth"
            )
        if self.observed_path_risk_cells > self.total_path_risk_cells:
            raise ValueError("observed path-risk cells exceed the frontier total")
        return self


class AdaptiveScoreComponents(FrozenContract):
    risk_gap: int = Field(ge=0)
    behavior_gap_novelty: int = Field(ge=0)
    path_risk_novelty: int = Field(ge=0)
    undersampling: int = Field(ge=0)
    waiting_age: int = Field(ge=0)
    repeat_penalty: int = Field(ge=0)
    no_gain_penalty: int = Field(ge=0)
    invalid_penalty: int = Field(ge=0)
    cost_penalty: int = Field(ge=0)
    virtual_runtime_penalty: int = Field(ge=0)
    total_score: int


class AdaptiveCandidateEvidence(FrozenContract):
    risk_category_id: Identifier
    frontier_digest: str = Field(pattern=_DIGEST_PATTERN)
    stats_digest: str = Field(pattern=_DIGEST_PATTERN)
    eligible: bool
    constraint_hits: tuple[str, ...] = Field(default_factory=tuple)
    waiting_decisions: int = Field(ge=0)
    score: AdaptiveScoreComponents
    tie_break_digest: str = Field(pattern=_DIGEST_PATTERN)

    @field_validator("constraint_hits")
    @classmethod
    def constraints_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))


class AdaptiveBatchDirection(FrozenContract):
    direction_id: str = Field(pattern=_DIGEST_PATTERN)
    ordinal: int = Field(ge=0, le=3)
    risk_category_id: Identifier
    objective_id: Identifier
    composition_id: Identifier
    parent_seed_id: str | None = None
    behavior_gap_id: str | None = None
    target_execution_depth: int = Field(ge=2, le=3)
    allocation_reason: str = Field(pattern=r"^(starvation|exploration|score)$")
    tie_break_digest: str = Field(pattern=_DIGEST_PATTERN)


class AdaptiveBatchDecision(FrozenContract):
    decision_id: str = Field(pattern=_DIGEST_PATTERN)
    campaign_id: str = Field(min_length=1, max_length=256)
    decision_index: int = Field(ge=0)
    policy_version: str = Field(min_length=1, max_length=128)
    policy_digest: str = Field(pattern=_DIGEST_PATTERN)
    feedback_digest: str = Field(pattern=_DIGEST_PATTERN)
    input_snapshot_digest: str = Field(pattern=_DIGEST_PATTERN)
    observed_behavior_paths: int = Field(ge=0)
    candidates: tuple[AdaptiveCandidateEvidence, ...]
    directions: tuple[AdaptiveBatchDirection, ...]
    result_digest: str = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_decision(self) -> AdaptiveBatchDecision:
        candidate_ids = [item.risk_category_id for item in self.candidates]
        if candidate_ids != sorted(candidate_ids) or len(candidate_ids) != len(
            set(candidate_ids)
        ):
            raise ValueError("scheduler candidates must be unique and sorted")
        ordinals = [item.ordinal for item in self.directions]
        risk_ids = [item.risk_category_id for item in self.directions]
        if ordinals != list(range(len(self.directions))) or len(risk_ids) != len(
            set(risk_ids)
        ):
            raise ValueError("scheduler directions must be contiguous and risk-unique")
        expected = _scheduler_digest(
            self.model_dump(mode="json", exclude={"decision_id", "result_digest"})
        )
        if self.decision_id != expected or self.result_digest != expected:
            raise ValueError("adaptive batch decision digest does not match")
        return self


class AdaptiveDirectionResult(FrozenContract):
    direction_id: str = Field(pattern=_DIGEST_PATTERN)
    outcome: AdaptiveDirectionOutcome
    token_cost: int = Field(default=0, ge=0)
    cost_microunits: int = Field(default=0, ge=0)
    elapsed_milliseconds: int = Field(default=0, ge=0)
    evidence_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    reason_code: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_result(self) -> AdaptiveDirectionResult:
        if (
            self.outcome == AdaptiveDirectionOutcome.SUBMITTED
            and self.evidence_digest is None
        ):
            raise ValueError("submitted direction requires evidence")
        return self


class AdaptiveBatchResult(FrozenContract):
    decision_id: str = Field(pattern=_DIGEST_PATTERN)
    direction_results: tuple[AdaptiveDirectionResult, ...]
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_result(self) -> AdaptiveBatchResult:
        ids = [item.direction_id for item in self.direction_results]
        if len(ids) != len(set(ids)):
            raise ValueError("adaptive direction results must be unique")
        canonical = tuple(
            sorted(self.direction_results, key=lambda item: item.direction_id)
        )
        object.__setattr__(self, "direction_results", canonical)
        expected = _scheduler_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("adaptive batch result digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class AdaptiveDecisionReference(FrozenContract):
    decision_id: str = Field(pattern=_DIGEST_PATTERN)
    decision_index: int = Field(ge=0)
    decision_digest: str = Field(pattern=_DIGEST_PATTERN)
    result_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)


class OfficeAdaptiveSchedulerSnapshot(FrozenContract):
    campaign_id: str = Field(min_length=1, max_length=256)
    policy_version: str = Field(min_length=1, max_length=128)
    policy_digest: str = Field(pattern=_DIGEST_PATTERN)
    next_decision_index: int = Field(default=0, ge=0)
    latest_feedback_digest: str = Field(pattern=_DIGEST_PATTERN)
    awaiting_feedback_after_digest: str | None = Field(
        default=None, pattern=_DIGEST_PATTERN
    )
    frontier_stats: tuple[AdaptiveFrontierStats, ...]
    active_decision: AdaptiveBatchDecision | None = None
    decisions: tuple[AdaptiveDecisionReference, ...] = Field(default_factory=tuple)
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_snapshot(self) -> OfficeAdaptiveSchedulerSnapshot:
        risk_ids = [item.risk_category_id for item in self.frontier_stats]
        if risk_ids != sorted(risk_ids) or len(risk_ids) != len(set(risk_ids)):
            raise ValueError("scheduler frontier stats must be unique and sorted")
        indices = [item.decision_index for item in self.decisions]
        if indices != list(range(len(indices))) or self.next_decision_index != len(
            indices
        ):
            raise ValueError("scheduler decision history is not contiguous")
        if self.active_decision is not None:
            if (
                not self.decisions
                or self.decisions[-1].decision_id
                != self.active_decision.decision_id
            ):
                raise ValueError("active scheduler decision is not the latest reference")
            if self.decisions[-1].result_digest is not None:
                raise ValueError("active scheduler decision cannot have a result")
            if self.active_decision.feedback_digest != self.latest_feedback_digest:
                raise ValueError("active scheduler decision uses stale feedback")
        if self.awaiting_feedback_after_digest is not None:
            if self.active_decision is not None:
                raise ValueError("scheduler cannot be active while awaiting feedback")
            if (
                not self.decisions
                or self.decisions[-1].result_digest is None
                or self.awaiting_feedback_after_digest != self.latest_feedback_digest
            ):
                raise ValueError("scheduler feedback wait has no completed boundary")
        if any(
            item.last_selected_decision_index is not None
            and item.last_selected_decision_index >= self.next_decision_index
            for item in self.frontier_stats
        ):
            raise ValueError("scheduler frontier stats reference a future decision")
        expected = _scheduler_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("adaptive scheduler snapshot digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


def schedule_adaptive_batch(
    *,
    campaign_id: str,
    random_seed: int,
    policy: OfficeAdaptiveSchedulerPolicy,
    decision_index: int,
    feedback_digest: str,
    input_snapshot_digest: str,
    observed_behavior_paths: int,
    frontiers: tuple[AdaptiveFrontierInput, ...],
    stats: tuple[AdaptiveFrontierStats, ...],
) -> AdaptiveBatchDecision:
    """Select a bounded batch with hard fairness constraints before soft scores."""

    stat_map = {item.risk_category_id: item for item in stats}
    candidates: list[AdaptiveCandidateEvidence] = []
    inputs = {item.risk_category_id: item for item in frontiers}
    if set(inputs) != set(stat_map):
        raise ValueError("scheduler frontier inputs and stats do not align")
    preliminarily_eligible: list[str] = []
    for frontier in frontiers:
        stat = stat_map[frontier.risk_category_id]
        budget_exhausted = (
            (frontier.episode_limit > 0 and frontier.episodes_consumed >= frontier.episode_limit)
            or (frontier.token_limit > 0 and frontier.tokens_consumed >= frontier.token_limit)
        )
        eligible = frontier.recovery_status in {
            "ready",
            "target_depth_reached",
        } and not budget_exhausted
        if eligible:
            preliminarily_eligible.append(frontier.risk_category_id)
    raw_consecutive_blocks = sorted(
        (
            risk_category_id
            for risk_category_id in preliminarily_eligible
            if stat_map[risk_category_id].last_selected_decision_index
            == decision_index - 1
            and stat_map[risk_category_id].consecutive_selected_decisions
            >= policy.max_consecutive_decisions
        ),
        key=lambda risk_category_id: (
            -stat_map[risk_category_id].consecutive_selected_decisions,
            risk_category_id,
        ),
    )
    enforceable_block_count = max(0, len(preliminarily_eligible) - 2)
    enforced_consecutive_blocks = set(
        raw_consecutive_blocks[:enforceable_block_count]
    )
    for frontier in sorted(frontiers, key=lambda item: item.risk_category_id):
        stat = stat_map[frontier.risk_category_id]
        waiting = (
            decision_index + 1
            if stat.last_selected_decision_index is None
            else max(0, decision_index - stat.last_selected_decision_index)
        )
        constraints: list[str] = []
        budget_exhausted = (
            (frontier.episode_limit > 0 and frontier.episodes_consumed >= frontier.episode_limit)
            or (frontier.token_limit > 0 and frontier.tokens_consumed >= frontier.token_limit)
        )
        eligible = frontier.recovery_status in {
            "ready",
            "target_depth_reached",
        } and not budget_exhausted
        if frontier.recovery_status not in {"ready", "target_depth_reached"}:
            constraints.append("frontier_not_ready")
        if frontier.recovery_status == "target_depth_reached":
            constraints.append("risk_target_depth_reached")
        if budget_exhausted:
            constraints.append("local_budget_exhausted")
        if frontier.risk_category_id in enforced_consecutive_blocks:
            eligible = False
            constraints.append("max_consecutive_share")
        elif frontier.risk_category_id in raw_consecutive_blocks:
            constraints.append("max_consecutive_share_infeasible")
        if waiting >= policy.starvation_decisions and eligible:
            constraints.append("starvation_due")
        score = _score(policy, frontier, stat, waiting)
        tie_break = _scheduler_digest(
            {
                "campaign_id": campaign_id,
                "random_seed": random_seed,
                "decision_index": decision_index,
                "input_snapshot_digest": input_snapshot_digest,
                "risk_category_id": frontier.risk_category_id,
            }
        )
        candidates.append(
            AdaptiveCandidateEvidence(
                risk_category_id=frontier.risk_category_id,
                frontier_digest=_scheduler_digest(frontier),
                stats_digest=_scheduler_digest(stat),
                eligible=eligible,
                constraint_hits=tuple(constraints),
                waiting_decisions=waiting,
                score=score,
                tie_break_digest=tie_break,
            )
        )
    eligible = [item for item in candidates if item.eligible]
    if not eligible:
        raise ValueError("adaptive scheduler requires an eligible frontier")
    limit = min(policy.batch_size, len(eligible))
    selected: list[tuple[AdaptiveCandidateEvidence, str]] = []
    starved = sorted(
        (item for item in eligible if "starvation_due" in item.constraint_hits),
        key=lambda item: (-item.waiting_decisions, item.tie_break_digest),
    )
    starvation_slots = max(0, limit - policy.exploration_slots)
    for item in starved[:starvation_slots]:
        selected.append((item, "starvation"))
    selected_ids = {item.risk_category_id for item, _reason in selected}
    remaining = [item for item in eligible if item.risk_category_id not in selected_ids]
    exploration_count = min(policy.exploration_slots, limit - len(selected))
    exploration = sorted(
        remaining,
        key=lambda item: (
            stat_map[item.risk_category_id].selection_count,
            item.waiting_decisions * -1,
            item.tie_break_digest,
        ),
    )
    for item in exploration[:exploration_count]:
        selected.append((item, "exploration"))
    selected_ids = {item.risk_category_id for item, _reason in selected}
    remaining = [item for item in eligible if item.risk_category_id not in selected_ids]
    ranked = sorted(
        remaining,
        key=lambda item: (-item.score.total_score, item.tie_break_digest),
    )
    for item in ranked[: limit - len(selected)]:
        selected.append((item, "score"))
    directions: list[AdaptiveBatchDirection] = []
    for ordinal, (candidate, reason) in enumerate(selected):
        frontier = inputs[candidate.risk_category_id]
        stat = stat_map[candidate.risk_category_id]
        target_execution_depth = (
            frontier.next_execution_target_depth or frontier.max_reachable_depth
        )
        choice = stat.selection_count % len(frontier.composition_ids)
        parent_seed_id = (
            frontier.parent_seed_ids[stat.selection_count % len(frontier.parent_seed_ids)]
            if frontier.parent_seed_ids
            else None
        )
        behavior_gap_id = (
            frontier.behavior_gap_ids[stat.selection_count % len(frontier.behavior_gap_ids)]
            if frontier.behavior_gap_ids
            else None
        )
        identity = {
            "campaign_id": campaign_id,
            "decision_index": decision_index,
            "ordinal": ordinal,
            "risk_category_id": frontier.risk_category_id,
            "composition_id": frontier.composition_ids[choice],
            "parent_seed_id": parent_seed_id,
            "behavior_gap_id": behavior_gap_id,
            "target_execution_depth": target_execution_depth,
            "allocation_reason": reason,
            "tie_break_digest": candidate.tie_break_digest,
        }
        directions.append(
            AdaptiveBatchDirection(
                direction_id=_scheduler_digest(identity),
                ordinal=ordinal,
                risk_category_id=frontier.risk_category_id,
                objective_id=frontier.composition_objective_ids[choice],
                composition_id=frontier.composition_ids[choice],
                parent_seed_id=parent_seed_id,
                behavior_gap_id=behavior_gap_id,
                target_execution_depth=target_execution_depth,
                allocation_reason=reason,
                tie_break_digest=candidate.tie_break_digest,
            )
        )
    decision_data = {
        "campaign_id": campaign_id,
        "decision_index": decision_index,
        "policy_version": policy.policy_version,
        "policy_digest": policy.content_digest,
        "feedback_digest": feedback_digest,
        "input_snapshot_digest": input_snapshot_digest,
        "observed_behavior_paths": observed_behavior_paths,
        "candidates": tuple(candidates),
        "directions": tuple(directions),
    }
    draft = AdaptiveBatchDecision.model_construct(
        decision_id="sha256:" + "0" * 64,
        result_digest="sha256:" + "0" * 64,
        **decision_data,
    )
    digest = _scheduler_digest(
        draft.model_dump(mode="json", exclude={"decision_id", "result_digest"})
    )
    return AdaptiveBatchDecision(
        decision_id=digest,
        result_digest=digest,
        **decision_data,
    )


def _score(
    policy: OfficeAdaptiveSchedulerPolicy,
    frontier: AdaptiveFrontierInput,
    stats: AdaptiveFrontierStats,
    waiting: int,
) -> AdaptiveScoreComponents:
    risk_gap = (
        1000
        * (frontier.max_reachable_depth - frontier.observed_execution_depth)
        // frontier.max_reachable_depth
    )
    behavior_gap = min(1000, len(frontier.behavior_gap_ids) * 250)
    path_risk = (
        1000
        if frontier.total_path_risk_cells == 0
        else 1000
        * (frontier.total_path_risk_cells - frontier.observed_path_risk_cells)
        // frontier.total_path_risk_cells
    )
    undersampling = 1000 // (1 + stats.selection_count)
    waiting_age = min(1000, 1000 * waiting // policy.starvation_decisions)
    repeat = 1000 if stats.last_selected_decision_index is not None and waiting <= 1 else 0
    no_gain = min(1000, stats.consecutive_no_gain * 500)
    invalid = 1000 * stats.invalid_candidates // max(1, stats.candidate_attempts)
    average_tokens = stats.tokens_consumed // max(1, stats.submitted_episodes)
    cost = min(1000, 1000 * average_tokens // policy.token_cost_reference)
    virtual_runtime = min(1000, frontier.virtual_runtime_millis)
    total = (
        policy.risk_gap_weight * risk_gap
        + policy.behavior_gap_weight * behavior_gap
        + policy.path_risk_novelty_weight * path_risk
        + policy.undersampling_weight * undersampling
        + policy.waiting_age_weight * waiting_age
        - policy.repeat_penalty_weight * repeat
        - policy.no_gain_penalty_weight * no_gain
        - policy.invalid_penalty_weight * invalid
        - policy.cost_penalty_weight * cost
        - policy.virtual_runtime_penalty_weight * virtual_runtime
    )
    return AdaptiveScoreComponents(
        risk_gap=risk_gap,
        behavior_gap_novelty=behavior_gap,
        path_risk_novelty=path_risk,
        undersampling=undersampling,
        waiting_age=waiting_age,
        repeat_penalty=repeat,
        no_gain_penalty=no_gain,
        invalid_penalty=invalid,
        cost_penalty=cost,
        virtual_runtime_penalty=virtual_runtime,
        total_score=total,
    )
