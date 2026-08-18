"""Risk and behavior frontier contracts for Office V2 scheduling."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.coverage.v2_behavior import V2BehaviorFeatureKind
from sandbox.coverage.v2_contracts import V2RiskFamily
from sandbox.coverage.v2_risk_catalog import V2_RISK_CATALOG
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest


class FrontierKind(StrEnum):
    RISK = "risk"
    BEHAVIOR = "behavior"


class MilestoneState(StrEnum):
    UNSEEN = "unseen"
    ATTEMPTED = "attempted"
    REALIZED = "realized"


class FrontierSchedulingState(StrEnum):
    READY = "ready"
    ACTIVE = "active"
    COOLING = "cooling"
    LOCALLY_SATURATED = "locally_saturated"
    LOCAL_BUDGET_EXHAUSTED = "local_budget_exhausted"
    AWAITING_PARENT = "awaiting_parent"
    AWAITING_OPERATOR = "awaiting_operator"
    UNREACHABLE = "unreachable"


class MilestoneOutcomeLedger(OfficeV2Contract):
    attempted_seen: bool = False
    blocked_seen: bool = False
    realized_seen: bool = False

    @model_validator(mode="after")
    def implications_hold(self) -> Self:
        if (self.blocked_seen or self.realized_seen) and not self.attempted_seen:
            raise ValueError("blocked/realized milestone requires attempted")
        return self

    @property
    def milestone_state(self) -> MilestoneState:
        if self.realized_seen:
            return MilestoneState.REALIZED
        if self.attempted_seen:
            return MilestoneState.ATTEMPTED
        return MilestoneState.UNSEEN

    def merge(self, other: MilestoneOutcomeLedger) -> MilestoneOutcomeLedger:
        return MilestoneOutcomeLedger(
            attempted_seen=self.attempted_seen or other.attempted_seen,
            blocked_seen=self.blocked_seen or other.blocked_seen,
            realized_seen=self.realized_seen or other.realized_seen,
        )


class RiskFrontier(OfficeV2Contract):
    frontier_id: Identifier
    scenario_id: Identifier
    primary_scheduling_family: V2RiskFamily
    objective_id: Identifier
    target_milestone_id: Identifier
    outcome_ledger: MilestoneOutcomeLedger = Field(default_factory=MilestoneOutcomeLedger)
    context_gap_digests: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    scheduling_state: FrontierSchedulingState = FrontierSchedulingState.READY
    state_reason_codes: tuple[Identifier, ...] = Field(default_factory=tuple)
    locally_committed_episodes: int = Field(default=0, ge=0)
    consecutive_no_gain: int = Field(default=0, ge=0)
    local_budget_used: int = Field(default=0, ge=0)
    frontier_digest: Sha256Digest

    @field_validator("context_gap_digests", "state_reason_codes")
    @classmethod
    def tuples_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("frontier values must be unique")
        return tuple(sorted(value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"frontier_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def state_and_digest_match(self) -> Self:
        if (
            self.scheduling_state is FrontierSchedulingState.UNREACHABLE
            and not self.state_reason_codes
        ):
            raise ValueError("unreachable frontier requires stable reason")
        if self.frontier_digest != sha256_digest(self.digest_payload()):
            raise ValueError("risk frontier digest does not match")
        return self


class BehaviorFrontier(OfficeV2Contract):
    frontier_id: Identifier
    scenario_id: Identifier
    behavior_gap_kind: Identifier
    feature_family: V2BehaviorFeatureKind
    behavior_anchor_digest: Sha256Digest
    gap_descriptor_digest: Sha256Digest
    related_objective_id: Identifier | None = None
    scheduling_state: FrontierSchedulingState = FrontierSchedulingState.READY
    state_reason_codes: tuple[Identifier, ...] = Field(default_factory=tuple)
    locally_committed_episodes: int = Field(default=0, ge=0)
    consecutive_no_gain: int = Field(default=0, ge=0)
    local_budget_used: int = Field(default=0, ge=0)
    frontier_digest: Sha256Digest

    @model_validator(mode="after")
    def primary_and_digest_match(self) -> Self:
        if self.feature_family in {
            V2BehaviorFeatureKind.INVOCATION_COUNT,
            V2BehaviorFeatureKind.EQUIVALENT_RESOURCE,
            V2BehaviorFeatureKind.EXPRESSION_VARIATION,
            V2BehaviorFeatureKind.PATH_LENGTH,
            V2BehaviorFeatureKind.EQUIVALENT_OBJECT_STATE,
        }:
            raise ValueError("secondary diversity cannot create BehaviorFrontier")
        if (
            self.scheduling_state is FrontierSchedulingState.UNREACHABLE
            and not self.state_reason_codes
        ):
            raise ValueError("unreachable frontier requires stable reason")
        if self.frontier_digest != sha256_digest(self.digest_payload()):
            raise ValueError("behavior frontier digest does not match")
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"frontier_digest"}, exclude_none=False)


class MutationCapabilityManifest(OfficeV2Contract):
    manifest_id: Identifier
    operator_family: Identifier
    required_seed_properties: tuple[Identifier, ...] = Field(default_factory=tuple)
    allowed_changed_dimensions: tuple[Identifier, ...] = Field(min_length=1)
    preserved_dimensions: tuple[Identifier, ...] = Field(min_length=1)
    supported_frontier_kinds: tuple[FrontierKind, ...] = Field(min_length=1)
    supported_objective_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    supported_behavior_kinds: tuple[V2BehaviorFeatureKind, ...] = Field(default_factory=tuple)
    manifest_digest: Sha256Digest

    @field_validator(
        "required_seed_properties",
        "allowed_changed_dimensions",
        "preserved_dimensions",
        "supported_objective_ids",
    )
    @classmethod
    def identifiers_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("mutation capability values must be unique")
        return tuple(sorted(value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"manifest_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def dimensions_and_digest_match(self) -> Self:
        if set(self.allowed_changed_dimensions).intersection(self.preserved_dimensions):
            raise ValueError("changed and preserved dimensions must not overlap")
        if self.manifest_digest != sha256_digest(self.digest_payload()):
            raise ValueError("mutation capability digest does not match")
        return self


class V2FrontierSnapshot(OfficeV2Contract):
    risk_frontiers: tuple[RiskFrontier, ...] = Field(default_factory=tuple)
    behavior_frontiers: tuple[BehaviorFrontier, ...] = Field(default_factory=tuple)
    snapshot_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"snapshot_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def canonical_and_digest_match(self) -> Self:
        for values, label in (
            (self.risk_frontiers, "risk"),
            (self.behavior_frontiers, "behavior"),
        ):
            ids = tuple(item.frontier_id for item in values)
            if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
                raise ValueError(f"{label} frontier snapshot must be canonical")
        if self.snapshot_digest != sha256_digest(self.digest_payload()):
            raise ValueError("frontier snapshot digest does not match")
        return self


def build_frontier_snapshot(
    *,
    risk_frontiers: tuple[RiskFrontier, ...],
    behavior_frontiers: tuple[BehaviorFrontier, ...] = (),
) -> V2FrontierSnapshot:
    return _seal(
        V2FrontierSnapshot,
        {
            "risk_frontiers": tuple(
                sorted(risk_frontiers, key=lambda item: item.frontier_id)
            ),
            "behavior_frontiers": tuple(
                sorted(behavior_frontiers, key=lambda item: item.frontier_id)
            ),
        },
        "snapshot_digest",
    )


def _seal(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(
        **payload, **{digest_field: "sha256:" + "0" * 64}
    )
    return model_type(
        **payload, **{digest_field: sha256_digest(draft.digest_payload())}
    )


def compile_risk_frontiers(
    *, scenario_id: str = "office-workspace-v2"
) -> tuple[RiskFrontier, ...]:
    frontiers = []
    for objective in V2_RISK_CATALOG.objectives:
        for milestone in objective.milestones:
            key = {
                "scenario_id": scenario_id,
                "primary_scheduling_family": objective.classification.primary_scheduling_family,
                "objective_id": objective.objective_id,
                "target_milestone_id": milestone.milestone_id,
            }
            frontier_id = "risk-frontier-" + sha256_digest(key).split(":", 1)[1][:20]
            frontiers.append(
                _seal(
                    RiskFrontier,
                    {"frontier_id": frontier_id, **key},
                    "frontier_digest",
                )
            )
    return tuple(sorted(frontiers, key=lambda item: item.frontier_id))


def build_behavior_frontier(
    *,
    scenario_id: str,
    behavior_gap_kind: str,
    feature_family: V2BehaviorFeatureKind,
    behavior_anchor_digest: str,
    gap_descriptor_digest: str,
    related_objective_id: str | None = None,
) -> BehaviorFrontier:
    key = {
        "scenario_id": scenario_id,
        "behavior_gap_kind": behavior_gap_kind,
        "feature_family": feature_family,
        "behavior_anchor_digest": behavior_anchor_digest,
        "gap_descriptor_digest": gap_descriptor_digest,
        "related_objective_id": related_objective_id,
    }
    frontier_id = "behavior-frontier-" + sha256_digest(key).split(":", 1)[1][:20]
    return _seal(
        BehaviorFrontier,
        {"frontier_id": frontier_id, **key},
        "frontier_digest",
    )


def resolve_frontier_readiness(
    *,
    has_compatible_parent: bool,
    has_compatible_operator: bool,
    stable_unreachable_reason_codes: tuple[str, ...] = (),
) -> FrontierSchedulingState:
    if stable_unreachable_reason_codes:
        return FrontierSchedulingState.UNREACHABLE
    if not has_compatible_operator:
        return FrontierSchedulingState.AWAITING_OPERATOR
    if not has_compatible_parent:
        return FrontierSchedulingState.AWAITING_PARENT
    return FrontierSchedulingState.READY


__all__ = [
    "BehaviorFrontier",
    "FrontierKind",
    "FrontierSchedulingState",
    "MilestoneOutcomeLedger",
    "MilestoneState",
    "MutationCapabilityManifest",
    "RiskFrontier",
    "V2FrontierSnapshot",
    "build_behavior_frontier",
    "build_frontier_snapshot",
    "compile_risk_frontiers",
    "resolve_frontier_readiness",
]
