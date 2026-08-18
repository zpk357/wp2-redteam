"""Deterministic baseline, parent selection, and single-candidate scheduling."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from sandbox.coverage.v2_risk_catalog import V2_RISK_CATALOG
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_corpus import AttackSeed, CorpusEntry, CorpusEntryState, ExecutionRecord
from .v2_frontier import FrontierKind, FrontierSchedulingState
from .v2_identity import V2_SCHEDULER_POLICY_DIGEST, V2_SCHEDULER_POLICY_VERSION
from .v2_mutation_identity import V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST


class BaselineStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    UNREACHABLE = "unreachable"


class AllocationLane(StrEnum):
    BASELINE = "baseline"
    RISK = "risk"
    EXPLORATION = "exploration"
    STARVATION = "starvation"


class BaselineExposureItem(OfficeV2Contract):
    objective_id: Identifier
    status: BaselineStatus = BaselineStatus.PENDING
    execution_record_id: Identifier | None = None
    unreachable_reason_codes: tuple[Identifier, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def terminal_evidence_matches(self) -> Self:
        if self.status is BaselineStatus.SUBMITTED and self.execution_record_id is None:
            raise ValueError("submitted baseline item requires execution record")
        if self.status is BaselineStatus.UNREACHABLE and not self.unreachable_reason_codes:
            raise ValueError("unreachable baseline item requires stable reason")
        if self.status is BaselineStatus.PENDING and (
            self.execution_record_id is not None or self.unreachable_reason_codes
        ):
            raise ValueError("pending baseline item cannot carry terminal evidence")
        return self


class BaselineExposureLedger(OfficeV2Contract):
    items: tuple[BaselineExposureItem, ...] = Field(min_length=12, max_length=12)
    ledger_digest: Sha256Digest

    @field_validator("items")
    @classmethod
    def items_are_complete(
        cls, value: tuple[BaselineExposureItem, ...]
    ) -> tuple[BaselineExposureItem, ...]:
        expected = {item.objective_id for item in V2_RISK_CATALOG.objectives}
        actual = {item.objective_id for item in value}
        if actual != expected or len(value) != len(expected):
            raise ValueError("baseline ledger requires every objective exactly once")
        return tuple(sorted(value, key=lambda item: item.objective_id))

    @property
    def baseline_complete(self) -> bool:
        return all(item.status is not BaselineStatus.PENDING for item in self.items)

    def next_pending(self) -> BaselineExposureItem | None:
        return next((item for item in self.items if item.status is BaselineStatus.PENDING), None)

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"ledger_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.ledger_digest != sha256_digest(self.digest_payload()):
            raise ValueError("baseline ledger digest does not match")
        return self


def _seal(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(
        **payload, **{digest_field: "sha256:" + "0" * 64}
    )
    return model_type(
        **payload, **{digest_field: sha256_digest(draft.digest_payload())}
    )


def new_baseline_exposure_ledger() -> BaselineExposureLedger:
    return _seal(
        BaselineExposureLedger,
        {
            "items": tuple(
                BaselineExposureItem(objective_id=item.objective_id)
                for item in V2_RISK_CATALOG.objectives
            )
        },
        "ledger_digest",
    )


def update_baseline_item(
    ledger: BaselineExposureLedger,
    *,
    objective_id: str,
    execution_record_id: str | None = None,
    unreachable_reason_codes: tuple[str, ...] = (),
) -> BaselineExposureLedger:
    if (execution_record_id is None) == (not unreachable_reason_codes):
        raise ValueError("baseline update requires exactly one terminal proof")
    replacement = BaselineExposureItem(
        objective_id=objective_id,
        status=(
            BaselineStatus.SUBMITTED
            if execution_record_id is not None
            else BaselineStatus.UNREACHABLE
        ),
        execution_record_id=execution_record_id,
        unreachable_reason_codes=unreachable_reason_codes,
    )
    found = False
    items = []
    for item in ledger.items:
        if item.objective_id != objective_id:
            items.append(item)
            continue
        found = True
        if item.status is not BaselineStatus.PENDING and item != replacement:
            raise ValueError("baseline terminal item is immutable")
        items.append(replacement)
    if not found:
        raise ValueError("baseline objective is not in frozen catalog")
    return _seal(BaselineExposureLedger, {"items": tuple(items)}, "ledger_digest")


class ParentSelectionCandidate(OfficeV2Contract):
    corpus_entry: CorpusEntry
    seed: AttackSeed
    supporting_execution: ExecutionRecord
    compatible_frontier_ids: tuple[Identifier, ...]
    required_seed_properties: tuple[Identifier, ...] = Field(default_factory=tuple)
    available_seed_properties: tuple[Identifier, ...] = Field(default_factory=tuple)
    risk_proximity: int = Field(default=0, ge=0)
    primary_novelty: int = Field(default=0, ge=0)
    wait_decisions: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def references_close(self) -> Self:
        if self.corpus_entry.seed_id != self.seed.seed_id:
            raise ValueError("parent candidate seed does not close")
        if self.supporting_execution.execution_record_id not in (
            self.corpus_entry.execution_record_ids
        ):
            raise ValueError("parent candidate supporting execution is not indexed")
        if self.supporting_execution.seed_id != self.seed.seed_id:
            raise ValueError("supporting execution refers to a different seed")
        return self


class ParentSelection(OfficeV2Contract):
    corpus_entry_id: Identifier
    parent_seed_id: Identifier
    supporting_execution_record_id: Identifier
    binding_source_digest: Sha256Digest
    hard_filter_reason_codes: tuple[Identifier, ...]
    soft_score_components: tuple[tuple[Identifier, int], ...]
    selection_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"selection_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.selection_digest != sha256_digest(self.digest_payload()):
            raise ValueError("parent selection digest does not match")
        return self


def select_parent(
    *, frontier_id: str, candidates: tuple[ParentSelectionCandidate, ...]
) -> ParentSelection | None:
    eligible = []
    for item in candidates:
        if item.corpus_entry.state is not CorpusEntryState.ACTIVE:
            continue
        if frontier_id not in item.compatible_frontier_ids:
            continue
        if not set(item.required_seed_properties).issubset(item.available_seed_properties):
            continue
        utility_bonus = 1 if item.supporting_execution.normal_task_completed else 0
        productive = item.corpus_entry.statistics.productive_child_count
        no_gain = item.corpus_entry.statistics.consecutive_no_gain
        cost_penalty = item.supporting_execution.costs.monetary_microunits
        score = (
            item.risk_proximity * 100
            + item.primary_novelty * 20
            + utility_bonus * 10
            + productive * 5
            + item.wait_decisions
            - no_gain * 15
            - cost_penalty
        )
        eligible.append((score, item.seed.seed_content_digest, item))
    if not eligible:
        return None
    _, _, chosen = max(eligible, key=lambda value: (value[0], value[1]))
    components = (
        ("risk-proximity", chosen.risk_proximity),
        ("primary-novelty", chosen.primary_novelty),
        ("wait-decisions", chosen.wait_decisions),
    )
    payload = {
        "corpus_entry_id": chosen.corpus_entry.corpus_entry_id,
        "parent_seed_id": chosen.seed.seed_id,
        "supporting_execution_record_id": chosen.supporting_execution.execution_record_id,
        "binding_source_digest": chosen.supporting_execution.binding_source_digest,
        "hard_filter_reason_codes": ("active", "compatible", "properties-satisfied"),
        "soft_score_components": components,
    }
    return _seal(ParentSelection, payload, "selection_digest")


class ComparisonContext(OfficeV2Contract):
    actor_id: Identifier
    task_id: Identifier
    resource_binding_digest: Sha256Digest
    allocation_target_digest: Sha256Digest
    authorization_branch: Identifier
    baseline_snapshot_digest: Sha256Digest
    comparison_context_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"comparison_context_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.comparison_context_digest != sha256_digest(self.digest_payload()):
            raise ValueError("comparison context digest does not match")
        return self


class RebindAllocation(OfficeV2Contract):
    rebind_allocation_id: Identifier
    previous_comparison_context_digest: Sha256Digest
    next_context: ComparisonContext
    changed_dimensions: tuple[Identifier, ...] = Field(min_length=1)
    rebind_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"rebind_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def changed_and_digest_match(self) -> Self:
        if self.previous_comparison_context_digest == self.next_context.comparison_context_digest:
            raise ValueError("rebind allocation must change comparison context")
        if not set(self.changed_dimensions).issubset({"actor", "task", "resource"}):
            raise ValueError("rebind allocation can only change actor, task, or resource")
        if self.rebind_digest != sha256_digest(self.digest_payload()):
            raise ValueError("rebind allocation digest does not match")
        return self


class RetargetAllocation(OfficeV2Contract):
    retarget_allocation_id: Identifier
    source_objective_id: Identifier
    destination_objective_id: Identifier
    destination_milestone_id: Identifier
    destination_target_digest: Sha256Digest
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    previous_comparison_context_digest: Sha256Digest
    next_context: ComparisonContext
    retarget_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"retarget_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def target_and_digest_match(self) -> Self:
        if self.source_objective_id == self.destination_objective_id:
            raise ValueError("retarget allocation must change objective")
        if self.previous_comparison_context_digest == self.next_context.comparison_context_digest:
            raise ValueError("retarget allocation must change comparison context")
        if self.next_context.allocation_target_digest != self.destination_target_digest:
            raise ValueError("retarget destination does not match next comparison context")
        if self.retarget_digest != sha256_digest(self.digest_payload()):
            raise ValueError("retarget allocation digest does not match")
        return self


class AuthorizationBranchAllocation(OfficeV2Contract):
    authorization_allocation_id: Identifier
    source_authorization_branch: Identifier
    destination_authorization_branch: Identifier
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    previous_comparison_context_digest: Sha256Digest
    next_context: ComparisonContext
    authorization_allocation_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"authorization_allocation_digest"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def branch_and_digest_match(self) -> Self:
        if self.source_authorization_branch == self.destination_authorization_branch:
            raise ValueError("authorization allocation must change branch")
        if self.next_context.authorization_branch != self.destination_authorization_branch:
            raise ValueError("authorization destination does not match next context")
        if self.previous_comparison_context_digest == self.next_context.comparison_context_digest:
            raise ValueError("authorization allocation must change comparison context")
        if self.authorization_allocation_digest != sha256_digest(self.digest_payload()):
            raise ValueError("authorization allocation digest does not match")
        return self


class OperatorAllocation(OfficeV2Contract):
    operator_allocation_id: Identifier
    frontier_id: Identifier
    supporting_execution_record_id: Identifier
    feedback_digest: Sha256Digest
    selected_operator_families: tuple[Identifier, ...] = Field(min_length=1)
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    policy_digest: Sha256Digest
    operator_allocation_digest: Sha256Digest

    @field_validator("selected_operator_families")
    @classmethod
    def operator_families_are_unique(
        cls, value: tuple[Identifier, ...]
    ) -> tuple[Identifier, ...]:
        if len(set(value)) != len(value):
            raise ValueError("operator allocation cannot repeat an operator family")
        return value

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"operator_allocation_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.operator_allocation_digest != sha256_digest(self.digest_payload()):
            raise ValueError("operator allocation digest does not match")
        return self


class FrontierOption(OfficeV2Contract):
    frontier_kind: FrontierKind
    frontier_id: Identifier
    objective_id: Identifier | None = None
    target_milestone_id: Identifier | None = None
    behavior_gap_kind: Identifier | None = None
    feature_family: Identifier | None = None
    scheduling_state: FrontierSchedulingState
    baseline_pending: bool = False
    wait_decisions: int = Field(default=0, ge=0)
    local_budget_remaining: int = Field(default=0, ge=0)
    risk_gap_score: int = Field(default=0, ge=0)
    behavior_rarity_score: int = Field(default=0, ge=0)
    consecutive_family_share: int = Field(default=0, ge=0)


class SchedulerPolicy(OfficeV2Contract):
    policy_version: Literal["office-v2-scheduler-policy-v1"] = V2_SCHEDULER_POLICY_VERSION
    policy_digest: Sha256Digest = V2_SCHEDULER_POLICY_DIGEST
    starvation_wait_decisions: int = Field(default=4, ge=1)
    behavior_reserve_interval: int = Field(default=4, ge=1)
    max_consecutive_family_share: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def identity_matches(self) -> Self:
        if self.policy_digest != V2_SCHEDULER_POLICY_DIGEST:
            raise ValueError("scheduler policy digest does not match frozen identity")
        return self


class GenerationAllocation(OfficeV2Contract):
    generation_allocation_id: Identifier
    generation_index: int = Field(ge=0)
    frontier_kind: FrontierKind
    frontier_id: Identifier
    allocation_target_digest: Sha256Digest
    parent_seed_id: Identifier
    supporting_execution_record_id: Identifier
    binding_source_digest: Sha256Digest
    candidate_count: Literal[1] = 1
    allocation_lane: AllocationLane
    reason_codes: tuple[Identifier, ...] = Field(min_length=1)
    score_components: tuple[tuple[Identifier, int], ...] = Field(default_factory=tuple)
    coverage_snapshot_digest: Sha256Digest
    corpus_digest: Sha256Digest
    frontier_digest: Sha256Digest
    policy_digest: Sha256Digest = V2_SCHEDULER_POLICY_DIGEST
    allocation_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"allocation_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def identity_and_digest_match(self) -> Self:
        if self.policy_digest != V2_SCHEDULER_POLICY_DIGEST:
            raise ValueError("allocation uses wrong scheduler policy")
        if self.allocation_digest != sha256_digest(self.digest_payload()):
            raise ValueError("generation allocation digest does not match")
        return self


class MutationGenerationAllocation(OfficeV2Contract):
    mutation_generation_allocation_id: Identifier
    base_allocation: GenerationAllocation
    initial_context: ComparisonContext
    operator_allocation: OperatorAllocation
    rebind_allocation: RebindAllocation | None = None
    retarget_allocation: RetargetAllocation | None = None
    authorization_branch_allocation: AuthorizationBranchAllocation | None = None
    final_context: ComparisonContext
    mutation_allocation_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"mutation_allocation_digest"}, exclude_none=False
        )

    @staticmethod
    def _unchanged(
        before: ComparisonContext,
        after: ComparisonContext,
        fields: tuple[str, ...],
    ) -> bool:
        return all(getattr(before, field) == getattr(after, field) for field in fields)

    @model_validator(mode="after")
    def ownership_chain_and_digest_match(self) -> Self:
        base = self.base_allocation
        operator = self.operator_allocation
        if operator.frontier_id != base.frontier_id:
            raise ValueError("operator allocation targets a different frontier")
        if operator.supporting_execution_record_id != base.supporting_execution_record_id:
            raise ValueError("operator allocation uses a different supporting execution")
        if operator.policy_digest != V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST:
            raise ValueError("operator allocation uses a different feedback policy")

        current = self.initial_context
        if self.rebind_allocation is not None:
            item = self.rebind_allocation
            if item.previous_comparison_context_digest != current.comparison_context_digest:
                raise ValueError("rebind allocation does not continue comparison context")
            after = item.next_context
            if not self._unchanged(
                current,
                after,
                ("allocation_target_digest", "authorization_branch", "baseline_snapshot_digest"),
            ):
                raise ValueError("rebind allocation changed target, authorization, or baseline")
            changed = {
                name
                for name, field in (
                    ("actor", "actor_id"),
                    ("task", "task_id"),
                    ("resource", "resource_binding_digest"),
                )
                if getattr(current, field) != getattr(after, field)
            }
            if changed != set(item.changed_dimensions):
                raise ValueError("rebind changed dimensions do not match context diff")
            current = after

        if self.retarget_allocation is not None:
            item = self.retarget_allocation
            if item.previous_comparison_context_digest != current.comparison_context_digest:
                raise ValueError("retarget allocation does not continue comparison context")
            after = item.next_context
            if not self._unchanged(
                current,
                after,
                (
                    "actor_id",
                    "task_id",
                    "resource_binding_digest",
                    "authorization_branch",
                    "baseline_snapshot_digest",
                ),
            ):
                raise ValueError("retarget allocation changed non-target context")
            if item.destination_target_digest != base.allocation_target_digest:
                raise ValueError("retarget destination does not match scheduled target")
            current = after

        if self.authorization_branch_allocation is not None:
            item = self.authorization_branch_allocation
            if item.previous_comparison_context_digest != current.comparison_context_digest:
                raise ValueError("authorization allocation does not continue comparison context")
            after = item.next_context
            if not self._unchanged(
                current,
                after,
                (
                    "actor_id",
                    "task_id",
                    "resource_binding_digest",
                    "allocation_target_digest",
                    "baseline_snapshot_digest",
                ),
            ):
                raise ValueError("authorization allocation changed non-authorization context")
            current = after

        if current != self.final_context:
            raise ValueError("final comparison context does not match allocation chain")
        if self.final_context.allocation_target_digest != base.allocation_target_digest:
            raise ValueError("final comparison context does not match scheduled target")
        if self.mutation_allocation_digest != sha256_digest(self.digest_payload()):
            raise ValueError("mutation generation allocation digest does not match")
        return self


def choose_frontier(
    *,
    options: tuple[FrontierOption, ...],
    generation_index: int,
    policy: SchedulerPolicy,
) -> tuple[FrontierOption, AllocationLane, tuple[str, ...]]:
    ready = tuple(
        item
        for item in options
        if item.scheduling_state is FrontierSchedulingState.READY
        and item.local_budget_remaining > 0
    )
    if not ready:
        raise ValueError("constraint-infeasible-no-ready-frontier")
    baseline = tuple(item for item in ready if item.baseline_pending)
    if baseline:
        return min(baseline, key=lambda item: item.frontier_id), AllocationLane.BASELINE, (
            "baseline-debt",
        )
    starved = tuple(
        item for item in ready if item.wait_decisions >= policy.starvation_wait_decisions
    )
    if starved:
        return max(starved, key=lambda item: (item.wait_decisions, item.frontier_id)), (
            AllocationLane.STARVATION
        ), ("starvation-protection",)
    behaviors = tuple(item for item in ready if item.frontier_kind is FrontierKind.BEHAVIOR)
    if behaviors and generation_index % policy.behavior_reserve_interval == 0:
        return max(
            behaviors, key=lambda item: (item.behavior_rarity_score, item.frontier_id)
        ), AllocationLane.EXPLORATION, ("behavior-reserve",)
    eligible = tuple(
        item
        for item in ready
        if item.consecutive_family_share < policy.max_consecutive_family_share
    ) or ready
    chosen = max(
        eligible,
        key=lambda item: (
            item.risk_gap_score + item.behavior_rarity_score + item.wait_decisions,
            item.frontier_id,
        ),
    )
    lane = (
        AllocationLane.RISK
        if chosen.frontier_kind is FrontierKind.RISK
        else AllocationLane.EXPLORATION
    )
    return chosen, lane, ("soft-ranking",)


__all__ = [
    "AllocationLane",
    "AuthorizationBranchAllocation",
    "BaselineExposureItem",
    "BaselineExposureLedger",
    "BaselineStatus",
    "ComparisonContext",
    "FrontierOption",
    "GenerationAllocation",
    "MutationGenerationAllocation",
    "OperatorAllocation",
    "ParentSelection",
    "ParentSelectionCandidate",
    "RebindAllocation",
    "RetargetAllocation",
    "SchedulerPolicy",
    "choose_frontier",
    "new_baseline_exposure_ledger",
    "select_parent",
    "update_baseline_item",
]
