"""Auditable completion and budget semantics for office campaigns."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from sandbox.content_digests import decimalized_sha256_digest
from sandbox.scenarios.models import FrozenContract, Identifier

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
OFFICE_CAMPAIGN_COMPLETION_POLICY_VERSION = "office-campaign-completion-v1"


def _completion_digest(value: object) -> str:
    return decimalized_sha256_digest(value, label="office campaign completion digest")


class OfficeCampaignCompletionStatus(StrEnum):
    BASELINE_INCOMPLETE = "baseline_incomplete"
    BASELINE_COMPLETE = "baseline_complete"
    SATURATED = "saturated"
    BUDGET_EXHAUSTED_INCOMPLETE = "budget_exhausted_incomplete"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class OfficeCampaignControlAction(StrEnum):
    PAUSE = "pause"
    CANCEL = "cancel"


class OfficeCampaignCompletionPolicy(FrozenContract):
    policy_version: str = Field(
        default=OFFICE_CAMPAIGN_COMPLETION_POLICY_VERSION,
        min_length=1,
        max_length=128,
    )
    minimum_global_no_gain_submitted_episodes: int = Field(default=4, ge=1)
    minimum_frontier_no_gain_submitted_episodes: int = Field(default=2, ge=1)
    max_submitted_episodes: int | None = Field(default=500, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_cost_microunits: int | None = Field(default=None, ge=1)
    max_elapsed_milliseconds: int | None = Field(default=None, ge=1)
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_policy(self) -> OfficeCampaignCompletionPolicy:
        if all(
            limit is None
            for limit in (
                self.max_submitted_episodes,
                self.max_tokens,
                self.max_cost_microunits,
                self.max_elapsed_milliseconds,
            )
        ):
            raise ValueError("office campaign requires at least one finite budget")
        expected = _completion_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("office completion policy digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class OfficeCompletionObservation(FrozenContract):
    ordinal: int = Field(ge=0)
    previous_feedback_digest: str = Field(pattern=_DIGEST_PATTERN)
    feedback_digest: str = Field(pattern=_DIGEST_PATTERN)
    submitted_episode_count: int = Field(ge=1, le=4)
    behavior_gain: bool
    execution_risk_depth_gain: bool
    path_risk_gain: bool
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_observation(self) -> OfficeCompletionObservation:
        if self.previous_feedback_digest == self.feedback_digest:
            raise ValueError("completion observation requires new feedback")
        expected = _completion_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("completion observation digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self

    @property
    def has_coverage_gain(self) -> bool:
        return (
            self.behavior_gain
            or self.execution_risk_depth_gain
            or self.path_risk_gain
        )


class OfficeCampaignControlRecord(FrozenContract):
    ordinal: int = Field(ge=0)
    action: OfficeCampaignControlAction
    reason_code: str = Field(min_length=1, max_length=128)
    evidence_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_record(self) -> OfficeCampaignControlRecord:
        expected = _completion_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("campaign control record digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class OfficeCampaignCompletionEvaluation(FrozenContract):
    status: OfficeCampaignCompletionStatus
    settled_reachable_frontier_ids: tuple[Identifier, ...]
    budget_exhaustion_reason_codes: tuple[str, ...]
    reason_codes: tuple[str, ...]

    @field_validator(
        "settled_reachable_frontier_ids",
        "budget_exhaustion_reason_codes",
        "reason_codes",
    )
    @classmethod
    def strings_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("completion evaluation strings must be unique")
        return tuple(sorted(value))


class OfficeCampaignCompletionState(FrozenContract):
    campaign_id: str = Field(min_length=1, max_length=256)
    policy_version: str = Field(min_length=1, max_length=128)
    policy_digest: str = Field(pattern=_DIGEST_PATTERN)
    status: OfficeCampaignCompletionStatus
    baseline_complete: bool
    baseline_required_count: int = Field(ge=0)
    baseline_committed_count: int = Field(ge=0)
    reachable_frontier_ids: tuple[Identifier, ...]
    settled_reachable_frontier_ids: tuple[Identifier, ...]
    submitted_episode_count: int = Field(ge=0)
    tokens_consumed: int = Field(ge=0)
    cost_microunits_consumed: int = Field(ge=0)
    elapsed_milliseconds_consumed: int = Field(ge=0)
    consecutive_submitted_without_any_gain: int = Field(ge=0)
    latest_qualifying_feedback_digest: str | None = Field(
        default=None, pattern=_DIGEST_PATTERN
    )
    budget_exhaustion_reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    observations: tuple[OfficeCompletionObservation, ...] = Field(default_factory=tuple)
    controls: tuple[OfficeCampaignControlRecord, ...] = Field(default_factory=tuple)
    revision: int = Field(default=0, ge=0)
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @field_validator(
        "reachable_frontier_ids",
        "settled_reachable_frontier_ids",
        "budget_exhaustion_reason_codes",
        "reason_codes",
    )
    @classmethod
    def strings_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("completion state strings must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_state(self) -> OfficeCampaignCompletionState:
        if self.baseline_committed_count > self.baseline_required_count:
            raise ValueError("completion baseline count exceeds its plan")
        expected_baseline_complete = self.baseline_required_count > 0 and (
            self.baseline_committed_count == self.baseline_required_count
        )
        if self.baseline_complete != expected_baseline_complete:
            raise ValueError("completion baseline milestone does not match its counts")
        if not set(self.settled_reachable_frontier_ids).issubset(
            self.reachable_frontier_ids
        ):
            raise ValueError("settled completion frontiers are not reachable")
        observation_ordinals = [item.ordinal for item in self.observations]
        if observation_ordinals != list(range(len(observation_ordinals))):
            raise ValueError("completion observations are not contiguous")
        control_ordinals = [item.ordinal for item in self.controls]
        if control_ordinals != list(range(len(control_ordinals))):
            raise ValueError("campaign controls are not contiguous")
        expected_latest = (
            self.observations[-1].feedback_digest if self.observations else None
        )
        if self.latest_qualifying_feedback_digest != expected_latest:
            raise ValueError("completion latest qualifying feedback does not match")
        expected_no_gain = consecutive_no_gain_submissions(self.observations)
        if self.consecutive_submitted_without_any_gain != expected_no_gain:
            raise ValueError("completion global no-gain window does not match")
        if self.status == OfficeCampaignCompletionStatus.BASELINE_INCOMPLETE:
            if self.baseline_complete:
                raise ValueError("baseline-incomplete status cannot be complete")
        elif self.status == OfficeCampaignCompletionStatus.BASELINE_COMPLETE:
            if not self.baseline_complete:
                raise ValueError("baseline-complete status requires its milestone")
        elif self.status == OfficeCampaignCompletionStatus.SATURATED:
            if not self.baseline_complete or set(
                self.settled_reachable_frontier_ids
            ) != set(self.reachable_frontier_ids):
                raise ValueError("saturated status requires all reachable frontiers")
        elif self.status == OfficeCampaignCompletionStatus.BUDGET_EXHAUSTED_INCOMPLETE:
            if not self.budget_exhaustion_reason_codes:
                raise ValueError("budget exhaustion requires stable reasons")
        elif not self.controls:
            raise ValueError("paused or cancelled status requires a control record")
        elif (
            self.status == OfficeCampaignCompletionStatus.PAUSED
            and self.controls[-1].action != OfficeCampaignControlAction.PAUSE
        ) or (
            self.status == OfficeCampaignCompletionStatus.CANCELLED
            and self.controls[-1].action != OfficeCampaignControlAction.CANCEL
        ):
            raise ValueError("campaign control status does not match its latest action")
        expected = _completion_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("office campaign completion state digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OfficeCampaignCompletionStatus.SATURATED,
            OfficeCampaignCompletionStatus.BUDGET_EXHAUSTED_INCOMPLETE,
            OfficeCampaignCompletionStatus.CANCELLED,
        }


def consecutive_no_gain_submissions(
    observations: tuple[OfficeCompletionObservation, ...],
) -> int:
    count = 0
    for observation in observations:
        if observation.has_coverage_gain:
            count = 0
        else:
            count += observation.submitted_episode_count
    return count


def evaluate_office_campaign_completion(
    *,
    policy: OfficeCampaignCompletionPolicy,
    baseline_complete: bool,
    reachable_frontier_ids: tuple[str, ...],
    target_depth_reached_ids: tuple[str, ...],
    frontier_no_gain_counts: dict[str, int],
    consecutive_submitted_without_any_gain: int,
    submitted_episode_count: int,
    tokens_consumed: int,
    cost_microunits_consumed: int,
    elapsed_milliseconds_consumed: int,
    has_pending_work: bool,
) -> OfficeCampaignCompletionEvaluation:
    reachable = tuple(sorted(reachable_frontier_ids))
    settled = tuple(
        risk_category_id
        for risk_category_id in reachable
        if risk_category_id in target_depth_reached_ids
        or frontier_no_gain_counts.get(risk_category_id, 0)
        >= policy.minimum_frontier_no_gain_submitted_episodes
    )
    budget_reasons = []
    budget_values = (
        (
            "submitted_episode_budget_exhausted",
            submitted_episode_count,
            policy.max_submitted_episodes,
        ),
        ("token_budget_exhausted", tokens_consumed, policy.max_tokens),
        (
            "cost_budget_exhausted",
            cost_microunits_consumed,
            policy.max_cost_microunits,
        ),
        (
            "elapsed_budget_exhausted",
            elapsed_milliseconds_consumed,
            policy.max_elapsed_milliseconds,
        ),
    )
    for reason_code, consumed, limit in budget_values:
        if limit is not None and consumed >= limit:
            budget_reasons.append(reason_code)
    saturated = (
        baseline_complete
        and not has_pending_work
        and set(settled) == set(reachable)
        and (
            not reachable
            or consecutive_submitted_without_any_gain
            >= policy.minimum_global_no_gain_submitted_episodes
        )
    )
    if saturated:
        status = OfficeCampaignCompletionStatus.SATURATED
        reasons = ("all_reachable_frontiers_saturated",)
    elif budget_reasons and not has_pending_work:
        status = OfficeCampaignCompletionStatus.BUDGET_EXHAUSTED_INCOMPLETE
        reasons = tuple(budget_reasons)
    elif baseline_complete:
        status = OfficeCampaignCompletionStatus.BASELINE_COMPLETE
        reasons = ("fair_baseline_complete",)
    else:
        status = OfficeCampaignCompletionStatus.BASELINE_INCOMPLETE
        reasons = ("fair_baseline_incomplete",)
    return OfficeCampaignCompletionEvaluation(
        status=status,
        settled_reachable_frontier_ids=settled,
        budget_exhaustion_reason_codes=tuple(budget_reasons),
        reason_codes=reasons,
    )
