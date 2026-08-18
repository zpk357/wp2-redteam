"""Deterministic fair-baseline contracts for office campaigns."""

from __future__ import annotations

from collections import defaultdict, deque
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from sandbox.content_digests import decimalized_sha256_digest
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.candidate_generation import (
    CandidateGenerationStatus,
    CandidateSelection,
    OfficeCandidateCatalog,
    OfficeCandidateGenerator,
)
from sandbox.scenarios.catalogs import ScenarioCatalogManifest
from sandbox.scenarios.matrix import TestMatrix
from sandbox.scenarios.models import (
    AgentConfig,
    ExecutionBudget,
    FrozenContract,
    Identifier,
    TestCase,
)
from sandbox.scenarios.office_matrix import (
    OFFICE_V1_TEST_MATRIX,
    office_attack_expression,
)

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
OFFICE_BASELINE_POLICY_VERSION = "office-baseline-matrix-v1"


def _baseline_digest(value: object) -> str:
    return decimalized_sha256_digest(value, label="office baseline digest")


class OfficeBaselinePlanningError(ValueError):
    """Raised when a registered baseline sample cannot produce a legal candidate."""


class OfficeBaselineStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    COMMITTED = "committed"


class OfficeBaselineAttemptOutcome(StrEnum):
    COMMITTED = "committed"
    CASE_FAILURE = "case_failure"
    CANDIDATE_REJECTED = "candidate_rejected"
    PROVIDER_ERROR = "provider_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    CLEANUP_FAILURE = "cleanup_failure"
    SOAK_PROBE = "soak_probe"


class OfficeBaselineLease(FrozenContract):
    lease_token: str = Field(pattern=_DIGEST_PATTERN)
    baseline_item_id: Identifier
    worker_id: Identifier
    attempt_number: int = Field(ge=1)


class OfficeBaselineAttemptRecord(FrozenContract):
    lease_token: str = Field(pattern=_DIGEST_PATTERN)
    baseline_item_id: Identifier
    worker_id: Identifier
    attempt_number: int = Field(ge=1)
    outcome: OfficeBaselineAttemptOutcome
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    evidence_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)


class OfficeBaselineEpisodeReference(FrozenContract):
    trajectory_id: str = Field(min_length=1, max_length=256)
    execution_id: str = Field(min_length=1, max_length=256)
    reference_digest: str = Field(pattern=_DIGEST_PATTERN)


class OfficeBaselineItem(FrozenContract):
    baseline_item_id: Identifier
    ordinal: int = Field(ge=0)
    selection: CandidateSelection
    candidate: TestCase
    status: OfficeBaselineStatus = OfficeBaselineStatus.QUEUED
    attempt_count: int = Field(default=0, ge=0)
    attempt_history: tuple[OfficeBaselineAttemptRecord, ...] = Field(
        default_factory=tuple
    )
    active_lease: OfficeBaselineLease | None = None
    committed_episode: OfficeBaselineEpisodeReference | None = None
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @field_validator("attempt_history")
    @classmethod
    def attempts_are_canonical(
        cls, value: tuple[OfficeBaselineAttemptRecord, ...]
    ) -> tuple[OfficeBaselineAttemptRecord, ...]:
        numbers = [item.attempt_number for item in value]
        tokens = [item.lease_token for item in value]
        if numbers != list(range(1, len(value) + 1)) or len(tokens) != len(
            set(tokens)
        ):
            raise ValueError("baseline attempt history must be contiguous and unique")
        return value

    @model_validator(mode="after")
    def validate_item(self) -> OfficeBaselineItem:
        self.selection.assert_integrity()
        self.candidate.assert_integrity()
        attack = self.candidate.attack
        if attack is None:
            raise ValueError("baseline item requires an attacked candidate")
        if (
            self.selection.task_id != self.candidate.benign_task.task_id
            or self.selection.objective_id != attack.objective.objective_id
            or self.selection.carrier_id != attack.carrier.carrier_id
        ):
            raise ValueError("baseline selection does not match its candidate")
        if any(
            item.baseline_item_id != self.baseline_item_id
            for item in self.attempt_history
        ):
            raise ValueError("baseline attempt belongs to another item")
        completed_attempts = len(self.attempt_history)
        expected_attempts = completed_attempts + int(
            self.status == OfficeBaselineStatus.LEASED
        )
        if self.attempt_count != expected_attempts:
            raise ValueError("baseline attempt count does not match its state")
        if self.status == OfficeBaselineStatus.QUEUED:
            if self.active_lease is not None or self.committed_episode is not None:
                raise ValueError("queued baseline item cannot retain a lease or commit")
        elif self.status == OfficeBaselineStatus.LEASED:
            if self.active_lease is None or self.committed_episode is not None:
                raise ValueError("leased baseline item requires only an active lease")
            if (
                self.active_lease.baseline_item_id != self.baseline_item_id
                or self.active_lease.attempt_number != self.attempt_count
            ):
                raise ValueError("baseline active lease does not match its item")
        else:
            if self.active_lease is not None or self.committed_episode is None:
                raise ValueError("committed baseline item requires only episode evidence")
            if (
                not self.attempt_history
                or self.attempt_history[-1].outcome
                != OfficeBaselineAttemptOutcome.COMMITTED
                or self.attempt_history[-1].evidence_digest
                != self.committed_episode.reference_digest
            ):
                raise ValueError("baseline commit does not match its final attempt")
        expected = _baseline_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("baseline item digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class OfficeBaselinePlan(FrozenContract):
    policy_version: str = Field(min_length=1, max_length=128)
    source_matrix_digest: str = Field(pattern=_DIGEST_PATTERN)
    items: tuple[OfficeBaselineItem, ...]
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_plan(self) -> OfficeBaselinePlan:
        ids = [item.baseline_item_id for item in self.items]
        ordinals = [item.ordinal for item in self.items]
        if len(ids) != len(set(ids)) or ordinals != list(range(len(self.items))):
            raise ValueError("baseline plan items must be unique and contiguous")
        if any(
            item.status != OfficeBaselineStatus.QUEUED or item.attempt_count != 0
            for item in self.items
        ):
            raise ValueError("baseline plan must contain pristine queued items")
        expected = _baseline_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("baseline plan digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class OfficeBaselineScanSnapshot(FrozenContract):
    policy_version: str = Field(min_length=1, max_length=128)
    plan_digest: str = Field(pattern=_DIGEST_PATTERN)
    items: tuple[OfficeBaselineItem, ...]
    queued_item_ids: tuple[Identifier, ...]
    committed_item_ids: tuple[Identifier, ...]
    active_item_id: Identifier | None = None
    next_item_id: Identifier | None = None
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_snapshot(self) -> OfficeBaselineScanSnapshot:
        ids = [item.baseline_item_id for item in self.items]
        ordinals = [item.ordinal for item in self.items]
        if len(ids) != len(set(ids)) or ordinals != list(range(len(self.items))):
            raise ValueError("baseline snapshot items must be unique and contiguous")
        queued = tuple(
            item.baseline_item_id
            for item in self.items
            if item.status == OfficeBaselineStatus.QUEUED
        )
        committed = tuple(
            item.baseline_item_id
            for item in self.items
            if item.status == OfficeBaselineStatus.COMMITTED
        )
        leased = tuple(
            item.baseline_item_id
            for item in self.items
            if item.status == OfficeBaselineStatus.LEASED
        )
        if self.queued_item_ids != queued or self.committed_item_ids != committed:
            raise ValueError("baseline snapshot summaries do not match items")
        expected_active = leased[0] if leased else None
        if len(leased) > 1 or self.active_item_id != expected_active:
            raise ValueError("baseline snapshot must contain at most one active lease")
        eligible = [
            item for item in self.items if item.status == OfficeBaselineStatus.QUEUED
        ]
        expected_next = (
            min(eligible, key=lambda item: (item.attempt_count, item.ordinal)).baseline_item_id
            if eligible and not leased
            else None
        )
        if self.next_item_id != expected_next:
            raise ValueError("baseline snapshot next item is not deterministic")
        expected = _baseline_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("baseline snapshot digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class OfficeBaselineWorkLease(FrozenContract):
    baseline_item_id: Identifier
    ordinal: int = Field(ge=0)
    lease: OfficeBaselineLease
    selection: CandidateSelection
    candidate: TestCase


class OfficeBaselinePlanner:
    """Build the frozen matrix sample and rotate objectives before repeat coverage."""

    def __init__(
        self,
        *,
        campaign_id: str,
        manifest: ScenarioCatalogManifest,
        random_seed: int,
        agent: AgentConfig,
        budget: ExecutionBudget,
        catalog: OfficeCandidateCatalog,
        matrix: TestMatrix = OFFICE_V1_TEST_MATRIX,
    ) -> None:
        self.campaign_id = campaign_id
        self.random_seed = random_seed
        self.agent = agent
        self.budget = budget
        self.catalog = catalog
        self.matrix = matrix
        self.generator = OfficeCandidateGenerator(manifest, catalog)

    def plan(self) -> OfficeBaselinePlan:
        selected: dict[str, tuple[CandidateSelection, TestCase]] = {}
        for case in self.matrix.attack_cases:
            resolved = self._selection_for_matrix_case(case)
            if resolved is not None:
                selection, candidate = resolved
                if selection.content_digest in selected:
                    raise OfficeBaselinePlanningError(
                        "baseline matrix resolved to a duplicate campaign selection"
                    )
                selected[selection.content_digest] = (selection, candidate)

        represented = {item[0].objective_id for item in selected.values()}
        for objective in sorted(
            self.catalog.attack_objectives, key=lambda item: item.objective_id
        ):
            if objective.objective_id in represented:
                continue
            fallback = self._first_compatible_selection(objective.objective_id)
            if fallback is not None:
                selection, candidate = fallback
                selected[selection.content_digest] = (selection, candidate)

        by_objective: dict[str, deque[tuple[CandidateSelection, TestCase]]] = (
            defaultdict(deque)
        )
        for selection, candidate in selected.values():
            by_objective[selection.objective_id].append((selection, candidate))
        for values in by_objective.values():
            ordered = sorted(values, key=lambda item: item[0].content_digest)
            values.clear()
            values.extend(ordered)

        rotated: list[tuple[CandidateSelection, TestCase]] = []
        objective_ids = sorted(by_objective)
        while any(by_objective.values()):
            for objective_id in objective_ids:
                if by_objective[objective_id]:
                    rotated.append(by_objective[objective_id].popleft())

        items = tuple(
            self._item(ordinal, selection, candidate)
            for ordinal, (selection, candidate) in enumerate(rotated)
        )
        return OfficeBaselinePlan(
            policy_version=OFFICE_BASELINE_POLICY_VERSION,
            source_matrix_digest=self.matrix.content_digest,
            items=items,
        )

    def _selection_for_matrix_case(
        self, case: TestCase
    ) -> tuple[CandidateSelection, TestCase] | None:
        attack = case.attack
        if attack is None:
            return None
        task = next(
            (
                item
                for item in self.catalog.benign_tasks
                if item.task_id == case.benign_task.task_id and item == case.benign_task
            ),
            None,
        )
        objective = next(
            (
                item
                for item in self.catalog.attack_objectives
                if item.objective_id == attack.objective.objective_id
                and item == attack.objective
            ),
            None,
        )
        carrier = next(
            (
                item
                for item in self.catalog.injection_carriers
                if item.carrier_id == attack.carrier.carrier_id
                and item == attack.carrier
            ),
            None,
        )
        if task is None or objective is None or carrier is None:
            return None
        expression_ids = tuple(
            expression_id
            for expression_id in self.catalog.expression_ids
            if office_attack_expression(objective, expression_id) == attack.payload  # type: ignore[arg-type]
        )
        if len(expression_ids) != 1:
            return None
        result = self._generate(
            task.task_id,
            objective.objective_id,
            carrier.carrier_id,
            expression_ids[0],
        )
        if result is None:
            raise OfficeBaselinePlanningError(
                f"registered baseline case is not executable: {case.case_id}"
            )
        return result

    def _first_compatible_selection(
        self, objective_id: str
    ) -> tuple[CandidateSelection, TestCase] | None:
        for task in sorted(self.catalog.benign_tasks, key=lambda item: item.task_id):
            for carrier in sorted(
                self.catalog.injection_carriers, key=lambda item: item.carrier_id
            ):
                for expression_id in self.catalog.expression_ids:
                    result = self._generate(
                        task.task_id,
                        objective_id,
                        carrier.carrier_id,
                        expression_id,
                    )
                    if result is not None:
                        return result
        return None

    def _generate(
        self,
        task_id: str,
        objective_id: str,
        carrier_id: str,
        expression_id: str,
    ) -> tuple[CandidateSelection, TestCase] | None:
        identity = sha256_digest(
            {
                "campaign_id": self.campaign_id,
                "task_id": task_id,
                "objective_id": objective_id,
                "carrier_id": carrier_id,
                "expression_id": expression_id,
            }
        )
        selection = CandidateSelection(
            selection_id="baseline-selection-"
            + identity.removeprefix("sha256:")[:24],
            task_id=task_id,
            objective_id=objective_id,
            carrier_id=carrier_id,
            expression_id=expression_id,
            agent=self.agent,
            budget=self.budget.model_dump(mode="python"),
            seed=self.random_seed,
        )
        result = self.generator.generate(selection)
        if result.status != CandidateGenerationStatus.ACCEPTED:
            return None
        assert result.candidate is not None
        return selection, result.candidate

    @staticmethod
    def _item(
        ordinal: int, selection: CandidateSelection, candidate: TestCase
    ) -> OfficeBaselineItem:
        identity = sha256_digest(
            {
                "selection_digest": selection.content_digest,
                "candidate_digest": candidate.content_digest,
            }
        )
        return OfficeBaselineItem(
            baseline_item_id="office-baseline-"
            + identity.removeprefix("sha256:")[:24],
            ordinal=ordinal,
            selection=selection,
            candidate=candidate,
        )
