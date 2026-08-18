"""Persistent objective exposure and risk frontier state for office campaigns."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field, field_validator, model_validator

from sandbox.content_digests import decimalized_sha256_digest
from sandbox.coverage.models import CampaignCoverageFeedback, CoverageInput, CoverageResult
from sandbox.coverage.office_risk import (
    OFFICE_RISK_MAPPING_DIGEST,
    OFFICE_RISK_MAPPING_VERSION,
)
from sandbox.coverage.risk_scope import CampaignRiskScopeIndex
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.candidate_generation import (
    OFFICE_V1_CANDIDATE_CATALOG,
    CandidateGenerationStatus,
    CandidateSelection,
    OfficeCandidateCatalog,
    OfficeCandidateGenerator,
)
from sandbox.scenarios.models import (
    AgentConfig,
    ExecutionBudget,
    FrozenContract,
    Identifier,
)
from sandbox.scenarios.office_campaign_baseline import (
    OFFICE_BASELINE_POLICY_VERSION,
    OfficeBaselineAttemptOutcome,
    OfficeBaselineAttemptRecord,
    OfficeBaselineEpisodeReference,
    OfficeBaselineItem,
    OfficeBaselineLease,
    OfficeBaselinePlanner,
    OfficeBaselineScanSnapshot,
    OfficeBaselineStatus,
    OfficeBaselineWorkLease,
)
from sandbox.scenarios.office_campaign_completion import (
    OfficeCampaignCompletionPolicy,
    OfficeCampaignCompletionState,
    OfficeCampaignCompletionStatus,
    OfficeCampaignControlAction,
    OfficeCampaignControlRecord,
    OfficeCompletionObservation,
    consecutive_no_gain_submissions,
    evaluate_office_campaign_completion,
)
from sandbox.scenarios.office_campaign_scheduler import (
    AdaptiveBatchDecision,
    AdaptiveBatchResult,
    AdaptiveDecisionReference,
    AdaptiveDirectionOutcome,
    AdaptiveFrontierInput,
    AdaptiveFrontierStats,
    OfficeAdaptiveSchedulerPolicy,
    OfficeAdaptiveSchedulerSnapshot,
    schedule_adaptive_batch,
)

if TYPE_CHECKING:
    from sandbox.fuzzer.models import ScenarioCampaignManifest

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
OFFICE_CAMPAIGN_STATE_SCHEMA_VERSION = "office-campaign-state-v4"


def _state_digest(value: object) -> str:
    return decimalized_sha256_digest(value, label="office campaign state digest")


class OfficeCampaignStateError(ValueError):
    """Raised when office campaign exploration state cannot be trusted."""


class ObjectiveExposureStatus(StrEnum):
    UNSEEN = "unseen"
    EXECUTED = "executed"
    UNREACHABLE_OR_INCOMPATIBLE = "unreachable_or_incompatible"


class RiskFrontierRecoveryStatus(StrEnum):
    READY = "ready"
    COOLED = "cooled"
    TARGET_DEPTH_REACHED = "target_depth_reached"
    UNREACHABLE_OR_INCOMPATIBLE = "unreachable_or_incompatible"


class CompatibleComposition(FrozenContract):
    composition_id: Identifier
    task_id: Identifier
    objective_id: Identifier
    carrier_id: Identifier
    expression_id: Identifier
    test_case_id: Identifier
    test_case_digest: str = Field(pattern=_DIGEST_PATTERN)


class CommittedEpisodeReference(FrozenContract):
    trajectory_id: str = Field(min_length=1, max_length=256)
    execution_id: str = Field(min_length=1, max_length=256)
    source_kind: str = Field(min_length=1, max_length=64)
    case_id: Identifier
    test_case_digest: str = Field(pattern=_DIGEST_PATTERN)
    objective_id: Identifier
    input_digest: str = Field(pattern=_DIGEST_PATTERN)
    coverage_result_digest: str = Field(pattern=_DIGEST_PATTERN)
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_digest(self) -> CommittedEpisodeReference:
        expected = _state_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("committed episode reference digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class FeedbackApplicationReference(FrozenContract):
    feedback_digest: str = Field(pattern=_DIGEST_PATTERN)
    application_digest: str = Field(pattern=_DIGEST_PATTERN)


class ObjectiveExposureEntry(FrozenContract):
    scenario_template_id: Identifier
    objective_id: Identifier
    objective_digest: str = Field(pattern=_DIGEST_PATTERN)
    risk_category_ids: tuple[Identifier, ...]
    compatible_compositions: tuple[CompatibleComposition, ...]
    status: ObjectiveExposureStatus
    committed_episodes: tuple[CommittedEpisodeReference, ...] = Field(
        default_factory=tuple
    )
    unreachable_reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    revision: int = Field(default=0, ge=0)
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @field_validator("risk_category_ids", "unreachable_reason_codes")
    @classmethod
    def strings_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("objective exposure string collections must be unique")
        return tuple(sorted(value))

    @field_validator("compatible_compositions")
    @classmethod
    def compositions_are_canonical(
        cls, value: tuple[CompatibleComposition, ...]
    ) -> tuple[CompatibleComposition, ...]:
        ids = [item.composition_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("objective compatible compositions must be unique")
        return tuple(sorted(value, key=lambda item: item.composition_id))

    @field_validator("committed_episodes")
    @classmethod
    def episodes_are_canonical(
        cls, value: tuple[CommittedEpisodeReference, ...]
    ) -> tuple[CommittedEpisodeReference, ...]:
        trajectory_ids = [item.trajectory_id for item in value]
        execution_ids = [item.execution_id for item in value]
        if len(trajectory_ids) != len(set(trajectory_ids)) or len(execution_ids) != len(
            set(execution_ids)
        ):
            raise ValueError("objective committed episodes must be unique")
        return tuple(sorted(value, key=lambda item: item.trajectory_id))

    @model_validator(mode="after")
    def validate_state(self) -> ObjectiveExposureEntry:
        if any(
            item.objective_id != self.objective_id
            for item in self.compatible_compositions
        ):
            raise ValueError("compatible composition objective does not match ledger entry")
        if any(
            item.objective_id != self.objective_id for item in self.committed_episodes
        ):
            raise ValueError("committed episode objective does not match ledger entry")
        if self.status == ObjectiveExposureStatus.UNSEEN:
            if not self.compatible_compositions or self.committed_episodes:
                raise ValueError("unseen objective requires compatible, unexecuted state")
            if self.unreachable_reason_codes:
                raise ValueError("unseen objective cannot carry unreachable reasons")
        elif self.status == ObjectiveExposureStatus.EXECUTED:
            if not self.compatible_compositions or not self.committed_episodes:
                raise ValueError("executed objective requires compatibility and an episode")
            if self.unreachable_reason_codes:
                raise ValueError("executed objective cannot carry unreachable reasons")
        elif self.compatible_compositions or self.committed_episodes:
            raise ValueError("unreachable objective cannot carry compositions or episodes")
        elif not self.unreachable_reason_codes:
            raise ValueError("unreachable objective requires stable reason codes")
        expected = _state_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("objective exposure entry digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class ObjectiveExposureLedger(FrozenContract):
    campaign_id: str = Field(min_length=1, max_length=256)
    scenario_template_id: Identifier
    entries: tuple[ObjectiveExposureEntry, ...]
    unseen_objective_ids: tuple[Identifier, ...]
    executed_objective_ids: tuple[Identifier, ...]
    unreachable_objective_ids: tuple[Identifier, ...]
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_ledger(self) -> ObjectiveExposureLedger:
        objective_ids = [entry.objective_id for entry in self.entries]
        if objective_ids != sorted(objective_ids) or len(objective_ids) != len(
            set(objective_ids)
        ):
            raise ValueError("objective exposure ledger entries must be unique and sorted")
        if any(
            entry.scenario_template_id != self.scenario_template_id
            for entry in self.entries
        ):
            raise ValueError("objective exposure ledger contains another scenario")
        expected_groups = {
            "unseen_objective_ids": tuple(
                entry.objective_id
                for entry in self.entries
                if entry.status == ObjectiveExposureStatus.UNSEEN
            ),
            "executed_objective_ids": tuple(
                entry.objective_id
                for entry in self.entries
                if entry.status == ObjectiveExposureStatus.EXECUTED
            ),
            "unreachable_objective_ids": tuple(
                entry.objective_id
                for entry in self.entries
                if entry.status
                == ObjectiveExposureStatus.UNREACHABLE_OR_INCOMPATIBLE
            ),
        }
        for field_name, expected in expected_groups.items():
            if getattr(self, field_name) != expected:
                raise ValueError(
                    f"objective exposure ledger {field_name} does not match entries"
                )
        expected_digest = _state_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected_digest:
            raise ValueError("objective exposure ledger digest does not match")
        object.__setattr__(self, "content_digest", expected_digest)
        return self


class FrontierLocalBudget(FrozenContract):
    episode_limit: int = Field(default=0, ge=0)
    token_limit: int = Field(default=0, ge=0)
    episodes_consumed: int = Field(default=0, ge=0)
    tokens_consumed: int = Field(default=0, ge=0)

class RiskFrontierHints(FrozenContract):
    risk_category_id: Identifier
    parent_seed_ids: tuple[str, ...] = Field(default_factory=tuple)
    behavior_gap_ids: tuple[str, ...] = Field(default_factory=tuple)
    local_budget: FrontierLocalBudget = Field(default_factory=FrontierLocalBudget)
    cooldown_until_observation: int | None = Field(default=None, ge=0)
    virtual_runtime: float = Field(default=0.0, ge=0.0)

    @field_validator("parent_seed_ids", "behavior_gap_ids")
    @classmethod
    def hints_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("frontier hint collections must be non-empty and unique")
        return tuple(sorted(value))


class RiskFrontierEntry(FrozenContract):
    scenario_template_id: Identifier
    risk_category_id: Identifier
    risk_category_label: str = Field(min_length=1, max_length=256)
    max_reachable_depth: int = Field(ge=1, le=3)
    observed_execution_depth: int = Field(ge=0, le=3)
    next_execution_target_depth: int | None = Field(default=None, ge=2, le=3)
    objective_ids: tuple[Identifier, ...]
    compatible_compositions: tuple[CompatibleComposition, ...]
    parent_seed_ids: tuple[str, ...] = Field(default_factory=tuple)
    behavior_gap_ids: tuple[str, ...] = Field(default_factory=tuple)
    local_budget: FrontierLocalBudget = Field(default_factory=FrontierLocalBudget)
    cooldown_until_observation: int | None = Field(default=None, ge=0)
    virtual_runtime: float = Field(default=0.0, ge=0.0)
    recovery_status: RiskFrontierRecoveryStatus
    unreachable_reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    last_feedback_digest: str = Field(pattern=_DIGEST_PATTERN)
    revision: int = Field(default=0, ge=0)
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @field_validator(
        "objective_ids",
        "parent_seed_ids",
        "behavior_gap_ids",
        "unreachable_reason_codes",
    )
    @classmethod
    def collections_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("risk frontier collections must be non-empty and unique")
        return tuple(sorted(value))

    @field_validator("compatible_compositions")
    @classmethod
    def compositions_are_canonical(
        cls, value: tuple[CompatibleComposition, ...]
    ) -> tuple[CompatibleComposition, ...]:
        ids = [item.composition_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("risk frontier compatible compositions must be unique")
        return tuple(sorted(value, key=lambda item: item.composition_id))

    @model_validator(mode="after")
    def validate_frontier(self) -> RiskFrontierEntry:
        if self.observed_execution_depth > self.max_reachable_depth:
            raise ValueError("frontier execution depth exceeds its reachable scope")
        expected_next = None
        if self.observed_execution_depth < self.max_reachable_depth:
            expected_next = max(2, self.observed_execution_depth + 1)
        if self.next_execution_target_depth != expected_next:
            raise ValueError("frontier next depth does not match observed execution depth")
        if any(
            item.objective_id not in self.objective_ids
            for item in self.compatible_compositions
        ):
            raise ValueError("frontier composition is not linked to a frontier objective")
        if not self.compatible_compositions:
            if self.objective_ids:
                raise ValueError("unreachable frontier cannot retain objective identities")
            if not self.unreachable_reason_codes:
                raise ValueError("unreachable frontier requires stable reason codes")
            if self.recovery_status != RiskFrontierRecoveryStatus.UNREACHABLE_OR_INCOMPATIBLE:
                raise ValueError("frontier without compatible components must be unreachable")
        elif self.recovery_status == RiskFrontierRecoveryStatus.UNREACHABLE_OR_INCOMPATIBLE:
            raise ValueError("compatible frontier cannot be marked unreachable")
        elif self.unreachable_reason_codes:
            raise ValueError("compatible frontier cannot carry unreachable reasons")
        elif self.next_execution_target_depth is None:
            if self.recovery_status != RiskFrontierRecoveryStatus.TARGET_DEPTH_REACHED:
                raise ValueError("frontier at maximum depth must be marked reached")
        elif self.recovery_status == RiskFrontierRecoveryStatus.COOLED:
            if self.cooldown_until_observation is None:
                raise ValueError("cooled frontier requires a cooldown boundary")
        elif self.recovery_status != RiskFrontierRecoveryStatus.READY:
            raise ValueError("open frontier must be ready or cooled")
        expected = _state_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("risk frontier entry digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self


class OfficeCampaignStateSnapshot(FrozenContract):
    campaign_id: str = Field(min_length=1, max_length=256)
    scenario_template_id: Identifier
    catalog_manifest_digest: str = Field(pattern=_DIGEST_PATTERN)
    taxonomy_version: str = Field(min_length=1, max_length=128)
    taxonomy_digest: str = Field(pattern=_DIGEST_PATTERN)
    risk_mapping_version: str = Field(min_length=1, max_length=128)
    risk_mapping_digest: str = Field(pattern=_DIGEST_PATTERN)
    risk_scope_version: str = Field(min_length=1, max_length=128)
    risk_scope_digest: str = Field(pattern=_DIGEST_PATTERN)
    current_feedback_digest: str = Field(pattern=_DIGEST_PATTERN)
    revision: int = Field(ge=0)
    objective_ledger: ObjectiveExposureLedger
    risk_frontiers: tuple[RiskFrontierEntry, ...]
    baseline_scan: OfficeBaselineScanSnapshot
    adaptive_scheduler: OfficeAdaptiveSchedulerSnapshot
    completion: OfficeCampaignCompletionState
    feedback_applications: tuple[FeedbackApplicationReference, ...]
    content_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_snapshot(self) -> OfficeCampaignStateSnapshot:
        risk_ids = [entry.risk_category_id for entry in self.risk_frontiers]
        if risk_ids != sorted(risk_ids) or len(risk_ids) != len(set(risk_ids)):
            raise ValueError("snapshot risk frontiers must be unique and sorted")
        if (
            self.objective_ledger.campaign_id != self.campaign_id
            or self.objective_ledger.scenario_template_id
            != self.scenario_template_id
        ):
            raise ValueError("snapshot objective ledger identity does not match")
        if any(
            entry.scenario_template_id != self.scenario_template_id
            for entry in self.risk_frontiers
        ):
            raise ValueError("snapshot entries do not match the locked scenario")
        if (
            self.adaptive_scheduler.campaign_id != self.campaign_id
            or self.adaptive_scheduler.latest_feedback_digest
            != self.current_feedback_digest
        ):
            raise ValueError("snapshot adaptive scheduler identity does not match")
        if (
            self.completion.campaign_id != self.campaign_id
            or self.completion.baseline_required_count
            != len(self.baseline_scan.items)
            or self.completion.baseline_committed_count
            != len(self.baseline_scan.committed_item_ids)
            or self.completion.baseline_complete
            != (
                bool(self.baseline_scan.items)
                and len(self.baseline_scan.committed_item_ids)
                == len(self.baseline_scan.items)
            )
        ):
            raise ValueError("snapshot completion baseline projection does not match")
        reachable_risk_ids = tuple(
            entry.risk_category_id
            for entry in self.risk_frontiers
            if entry.compatible_compositions
        )
        if self.completion.reachable_frontier_ids != reachable_risk_ids:
            raise ValueError("snapshot completion frontier projection does not match")
        if {
            entry.risk_category_id for entry in self.adaptive_scheduler.frontier_stats
        } != set(risk_ids):
            raise ValueError("snapshot adaptive scheduler frontiers do not match")
        application_ids = [
            entry.feedback_digest for entry in self.feedback_applications
        ]
        if application_ids != sorted(application_ids) or len(application_ids) != len(
            set(application_ids)
        ):
            raise ValueError("snapshot feedback applications must be unique and sorted")
        if self.current_feedback_digest not in application_ids:
            raise ValueError("snapshot current feedback application is missing")
        if any(
            entry.last_feedback_digest != self.current_feedback_digest
            for entry in self.risk_frontiers
        ):
            raise ValueError("snapshot frontiers do not match current feedback")
        ledger_episodes = {
            episode.trajectory_id: episode
            for objective in self.objective_ledger.entries
            for episode in objective.committed_episodes
        }
        for item in self.baseline_scan.items:
            committed = item.committed_episode
            if committed is None:
                continue
            ledger_episode = ledger_episodes.get(committed.trajectory_id)
            if (
                ledger_episode is None
                or ledger_episode.execution_id != committed.execution_id
                or ledger_episode.content_digest != committed.reference_digest
            ):
                raise ValueError("baseline commit does not exist in objective ledger")
        baseline_objective_ids = {
            item.selection.objective_id for item in self.baseline_scan.items
        }
        if any(
            frontier.compatible_compositions
            and not baseline_objective_ids.intersection(frontier.objective_ids)
            for frontier in self.risk_frontiers
        ):
            raise ValueError("reachable risk frontier has no legal baseline seed")
        expected = _state_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("office campaign state snapshot digest does not match")
        object.__setattr__(self, "content_digest", expected)
        return self

    @property
    def objective_exposures(self) -> tuple[ObjectiveExposureEntry, ...]:
        return self.objective_ledger.entries


class OfficeCampaignStateStore:
    """Idempotent SQLite state for office objective exposure and risk frontiers."""

    def __init__(
        self,
        root: Path,
        manifest: ScenarioCampaignManifest,
        initial_feedback: CampaignCoverageFeedback,
        *,
        agent: AgentConfig,
        risk_scope: CampaignRiskScopeIndex,
        budget: ExecutionBudget | None = None,
        scheduler_policy: OfficeAdaptiveSchedulerPolicy | None = None,
        completion_policy: OfficeCampaignCompletionPolicy | None = None,
        catalog: OfficeCandidateCatalog = OFFICE_V1_CANDIDATE_CATALOG,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if (
            not manifest.campaign_id
            or manifest.campaign_id in {".", ".."}
            or any(character in manifest.campaign_id for character in "/\\:")
        ):
            raise OfficeCampaignStateError("invalid office campaign_id")
        self.manifest = manifest
        self.catalog = catalog
        self.generator = OfficeCandidateGenerator(manifest.scenario_catalogs, catalog)
        self.agent = agent
        self.risk_scope = risk_scope
        self.budget = budget or ExecutionBudget()
        self.scheduler_policy = scheduler_policy or OfficeAdaptiveSchedulerPolicy(
            policy_version=manifest.scheduler_policy_version
        )
        if self.scheduler_policy.policy_version != manifest.scheduler_policy_version:
            raise OfficeCampaignStateError(
                "adaptive scheduler policy version does not match campaign manifest"
            )
        self.completion_policy = completion_policy or OfficeCampaignCompletionPolicy()
        self.baseline_plan = OfficeBaselinePlanner(
            campaign_id=manifest.campaign_id,
            manifest=manifest.scenario_catalogs,
            random_seed=manifest.random_seed,
            agent=agent,
            budget=self.budget,
            catalog=catalog,
        ).plan()
        self._validate_feedback(initial_feedback)
        self.initial_feedback = initial_feedback
        base = root.resolve()
        base.mkdir(parents=True, exist_ok=True)
        self.root = (base / manifest.campaign_id).resolve()
        if base not in self.root.parents:
            raise OfficeCampaignStateError("office campaign state path escapes root")
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "office-campaign-state.db"
        self._connection = sqlite3.connect(
            self.database_path,
            timeout=busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute("PRAGMA foreign_keys = ON")
        try:
            self._create_schema()
            self._validate_metadata()
            self._initialize_or_validate()
        except BaseException:
            self._connection.close()
            raise

    def __enter__(self) -> OfficeCampaignStateStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    def snapshot(self) -> OfficeCampaignStateSnapshot:
        snapshot = self._snapshot_from_rows(self._connection)
        row = self._connection.execute(
            "SELECT snapshot_digest, snapshot_json FROM snapshots WHERE revision = ?",
            (snapshot.revision,),
        ).fetchone()
        if row is None:
            raise OfficeCampaignStateError("office campaign snapshot is missing")
        persisted = OfficeCampaignStateSnapshot.model_validate_json(row["snapshot_json"])
        if row["snapshot_digest"] != persisted.content_digest or persisted != snapshot:
            raise OfficeCampaignStateError(
                "office campaign rows do not match the persisted snapshot"
            )
        return snapshot

    def commit_episode(
        self,
        coverage_input: CoverageInput,
        coverage_result: CoverageResult,
    ) -> OfficeCampaignStateSnapshot:
        reference = self._episode_reference(coverage_input, coverage_result)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT trajectory_id FROM committed_episodes WHERE trajectory_id = ?",
                (reference.trajectory_id,),
            ).fetchone()
            if existing is None and self._load_completion(connection).is_terminal:
                raise OfficeCampaignStateError(
                    "new episode cannot replace a terminal completion state"
                )
            if not self._commit_episode_reference(connection, reference):
                return self.snapshot()
            self._recompute_completion(connection)
            revision, feedback_digest = self._advance_revision(connection)
            self._persist_snapshot(connection, revision, feedback_digest)
        return self.snapshot()

    def pause_campaign(
        self, reason_code: str, *, evidence_digest: str | None = None
    ) -> OfficeCampaignStateSnapshot:
        return self._apply_control(
            OfficeCampaignControlAction.PAUSE,
            reason_code=reason_code,
            evidence_digest=evidence_digest,
        )

    def cancel_campaign(
        self, reason_code: str, *, evidence_digest: str | None = None
    ) -> OfficeCampaignStateSnapshot:
        return self._apply_control(
            OfficeCampaignControlAction.CANCEL,
            reason_code=reason_code,
            evidence_digest=evidence_digest,
        )

    def lease_next_baseline_item(
        self, worker_id: str
    ) -> OfficeBaselineWorkLease | None:
        with self.transaction() as connection:
            completion = self._load_completion(connection)
            if completion.status not in {
                OfficeCampaignCompletionStatus.BASELINE_INCOMPLETE,
                OfficeCampaignCompletionStatus.BASELINE_COMPLETE,
            }:
                raise OfficeCampaignStateError(
                    "baseline leasing is blocked by campaign completion status: "
                    f"{completion.status.value}"
                )
            items = self._load_baseline_items(connection)
            active = tuple(
                item for item in items if item.status == OfficeBaselineStatus.LEASED
            )
            if active:
                item = active[0]
                assert item.active_lease is not None
                if item.active_lease.worker_id != worker_id:
                    raise OfficeCampaignStateError(
                        "baseline lease is owned by another worker"
                    )
                return self._baseline_work(item)
            queued = tuple(
                item for item in items if item.status == OfficeBaselineStatus.QUEUED
            )
            if not queued:
                return None
            if completion.status != OfficeCampaignCompletionStatus.BASELINE_INCOMPLETE:
                raise OfficeCampaignStateError(
                    "baseline leasing is blocked by campaign completion status: "
                    f"{completion.status.value}"
                )
            item = min(queued, key=lambda entry: (entry.attempt_count, entry.ordinal))
            attempt_number = item.attempt_count + 1
            lease = OfficeBaselineLease(
                lease_token=_state_digest(
                    {
                        "campaign_id": self.manifest.campaign_id,
                        "baseline_item_id": item.baseline_item_id,
                        "worker_id": worker_id,
                        "attempt_number": attempt_number,
                    }
                ),
                baseline_item_id=item.baseline_item_id,
                worker_id=worker_id,
                attempt_number=attempt_number,
            )
            updated = OfficeBaselineItem.model_validate(
                {
                    **item.model_dump(mode="python", exclude={"content_digest"}),
                    "status": OfficeBaselineStatus.LEASED,
                    "attempt_count": attempt_number,
                    "active_lease": lease,
                }
            )
            self._replace_baseline_item(connection, updated)
            self._recompute_completion(connection)
            revision, feedback_digest = self._advance_revision(connection)
            self._persist_snapshot(connection, revision, feedback_digest)
        return self._baseline_work(updated)

    def release_baseline_item(
        self,
        lease_token: str,
        *,
        outcome: OfficeBaselineAttemptOutcome,
        reason_code: str,
        evidence_digest: str | None = None,
    ) -> OfficeCampaignStateSnapshot:
        if outcome == OfficeBaselineAttemptOutcome.COMMITTED:
            raise OfficeCampaignStateError(
                "baseline commit outcome requires episode evidence"
            )
        with self.transaction() as connection:
            items = self._load_baseline_items(connection)
            item = self._active_baseline_item(items, lease_token)
            if item is None:
                existing = self._baseline_attempt(items, lease_token)
                if existing is None:
                    raise OfficeCampaignStateError("baseline lease token is not active")
                expected = OfficeBaselineAttemptRecord(
                    lease_token=lease_token,
                    baseline_item_id=existing.baseline_item_id,
                    worker_id=existing.worker_id,
                    attempt_number=existing.attempt_number,
                    outcome=outcome,
                    reason_code=reason_code,
                    evidence_digest=evidence_digest,
                )
                if existing != expected:
                    raise OfficeCampaignStateError(
                        "baseline lease was already released with different evidence"
                    )
                return self.snapshot()
            assert item.active_lease is not None
            record = OfficeBaselineAttemptRecord(
                lease_token=lease_token,
                baseline_item_id=item.baseline_item_id,
                worker_id=item.active_lease.worker_id,
                attempt_number=item.active_lease.attempt_number,
                outcome=outcome,
                reason_code=reason_code,
                evidence_digest=evidence_digest,
            )
            updated = OfficeBaselineItem.model_validate(
                {
                    **item.model_dump(mode="python", exclude={"content_digest"}),
                    "status": OfficeBaselineStatus.QUEUED,
                    "attempt_history": (*item.attempt_history, record),
                    "active_lease": None,
                }
            )
            self._replace_baseline_item(connection, updated)
            self._recompute_completion(connection)
            revision, feedback_digest = self._advance_revision(connection)
            self._persist_snapshot(connection, revision, feedback_digest)
        return self.snapshot()

    def commit_baseline_episode(
        self,
        lease_token: str,
        coverage_input: CoverageInput,
        coverage_result: CoverageResult,
    ) -> OfficeCampaignStateSnapshot:
        reference = self._episode_reference(coverage_input, coverage_result)
        with self.transaction() as connection:
            items = self._load_baseline_items(connection)
            item = self._active_baseline_item(items, lease_token)
            if item is None:
                committed_item = next(
                    (
                        candidate
                        for candidate in items
                        if candidate.status == OfficeBaselineStatus.COMMITTED
                        and candidate.attempt_history
                        and candidate.attempt_history[-1].lease_token == lease_token
                    ),
                    None,
                )
                if committed_item is None:
                    raise OfficeCampaignStateError("baseline lease token is not active")
                committed = committed_item.committed_episode
                assert committed is not None
                if (
                    committed.trajectory_id != reference.trajectory_id
                    or committed.execution_id != reference.execution_id
                    or committed.reference_digest != reference.content_digest
                ):
                    raise OfficeCampaignStateError(
                        "baseline lease was already committed with different evidence"
                    )
                return self.snapshot()
            if (
                reference.case_id != item.candidate.case_id
                or reference.test_case_digest != item.candidate.content_digest
                or reference.objective_id != item.selection.objective_id
            ):
                raise OfficeCampaignStateError(
                    "submitted episode does not match the leased baseline candidate"
                )
            self._commit_episode_reference(connection, reference)
            assert item.active_lease is not None
            record = OfficeBaselineAttemptRecord(
                lease_token=lease_token,
                baseline_item_id=item.baseline_item_id,
                worker_id=item.active_lease.worker_id,
                attempt_number=item.active_lease.attempt_number,
                outcome=OfficeBaselineAttemptOutcome.COMMITTED,
                reason_code="valid_submitted_episode",
                evidence_digest=reference.content_digest,
            )
            updated = OfficeBaselineItem.model_validate(
                {
                    **item.model_dump(mode="python", exclude={"content_digest"}),
                    "status": OfficeBaselineStatus.COMMITTED,
                    "attempt_history": (*item.attempt_history, record),
                    "active_lease": None,
                    "committed_episode": OfficeBaselineEpisodeReference(
                        trajectory_id=reference.trajectory_id,
                        execution_id=reference.execution_id,
                        reference_digest=reference.content_digest,
                    ),
                }
            )
            self._replace_baseline_item(connection, updated)
            self._recompute_completion(connection)
            revision, feedback_digest = self._advance_revision(connection)
            self._persist_snapshot(connection, revision, feedback_digest)
        return self.snapshot()

    def schedule_next_adaptive_batch(self) -> AdaptiveBatchDecision:
        """Persist or recover the next deterministic post-baseline batch."""

        with self.transaction() as connection:
            completion = self._load_completion(connection)
            if completion.status in {
                OfficeCampaignCompletionStatus.PAUSED,
                OfficeCampaignCompletionStatus.CANCELLED,
                OfficeCampaignCompletionStatus.SATURATED,
                OfficeCampaignCompletionStatus.BUDGET_EXHAUSTED_INCOMPLETE,
            }:
                raise OfficeCampaignStateError(
                    "adaptive scheduling is blocked by campaign completion status: "
                    f"{completion.status.value}"
                )
            scheduler = self._load_scheduler(connection)
            if scheduler.active_decision is not None:
                return scheduler.active_decision
            if scheduler.awaiting_feedback_after_digest is not None:
                raise OfficeCampaignStateError(
                    "adaptive scheduler requires fresh coverage feedback"
                )
            baseline_items = self._load_baseline_items(connection)
            if not baseline_items or any(
                item.status != OfficeBaselineStatus.COMMITTED
                for item in baseline_items
            ):
                raise OfficeCampaignStateError(
                    "adaptive scheduling requires a completed fair baseline"
                )
            if completion.status != OfficeCampaignCompletionStatus.BASELINE_COMPLETE:
                raise OfficeCampaignStateError(
                    "adaptive scheduling requires a completed fair baseline"
                )
            feedback = self._load_current_feedback(connection)
            if feedback.saturation.observations < len(baseline_items):
                raise OfficeCampaignStateError(
                    "adaptive scheduling requires coverage feedback that includes "
                    "the completed baseline"
                )
            snapshot = self._snapshot_from_rows(connection)
            stats = {item.risk_category_id: item for item in scheduler.frontier_stats}
            frontier_inputs = self._scheduler_frontier_inputs(
                snapshot.risk_frontiers, feedback
            )
            try:
                decision = schedule_adaptive_batch(
                    campaign_id=self.manifest.campaign_id,
                    random_seed=self.manifest.random_seed,
                    policy=self.scheduler_policy,
                    decision_index=scheduler.next_decision_index,
                    feedback_digest=feedback.report_digest,
                    input_snapshot_digest=snapshot.content_digest,
                    observed_behavior_paths=feedback.observed_behavior_paths,
                    frontiers=frontier_inputs,
                    stats=scheduler.frontier_stats,
                )
            except ValueError as error:
                raise OfficeCampaignStateError(str(error)) from error
            selected_ids = {item.risk_category_id for item in decision.directions}
            updated_stats = []
            for risk_category_id in sorted(stats):
                entry = stats[risk_category_id]
                if risk_category_id in selected_ids:
                    consecutive = (
                        entry.consecutive_selected_decisions + 1
                        if entry.last_selected_decision_index
                        == decision.decision_index - 1
                        else 1
                    )
                    entry = AdaptiveFrontierStats.model_validate(
                        {
                            **entry.model_dump(mode="python"),
                            "selection_count": entry.selection_count + 1,
                            "last_selected_decision_index": decision.decision_index,
                            "consecutive_selected_decisions": consecutive,
                        }
                    )
                    frontier = self._load_frontier(connection, risk_category_id)
                    updated_frontier = RiskFrontierEntry.model_validate(
                        {
                            **frontier.model_dump(
                                mode="python", exclude={"content_digest"}
                            ),
                            "virtual_runtime": frontier.virtual_runtime + 1.0,
                            "revision": frontier.revision + 1,
                        }
                    )
                    self._replace_frontier(connection, updated_frontier)
                elif entry.last_selected_decision_index is not None:
                    entry = AdaptiveFrontierStats.model_validate(
                        {
                            **entry.model_dump(mode="python"),
                            "consecutive_selected_decisions": 0,
                        }
                    )
                updated_stats.append(entry)
            reference = AdaptiveDecisionReference(
                decision_id=decision.decision_id,
                decision_index=decision.decision_index,
                decision_digest=decision.result_digest,
            )
            updated_scheduler = OfficeAdaptiveSchedulerSnapshot(
                campaign_id=scheduler.campaign_id,
                policy_version=scheduler.policy_version,
                policy_digest=scheduler.policy_digest,
                next_decision_index=scheduler.next_decision_index + 1,
                latest_feedback_digest=scheduler.latest_feedback_digest,
                awaiting_feedback_after_digest=None,
                frontier_stats=tuple(updated_stats),
                active_decision=decision,
                decisions=(*scheduler.decisions, reference),
            )
            connection.execute(
                "INSERT INTO adaptive_decisions("
                "decision_id, decision_index, decision_digest, decision_json"
                ") VALUES (?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.decision_index,
                    decision.result_digest,
                    decision.model_dump_json(),
                ),
            )
            self._replace_scheduler(connection, updated_scheduler)
            self._recompute_completion(connection)
            revision, feedback_digest = self._advance_revision(connection)
            self._persist_snapshot(connection, revision, feedback_digest)
        return decision

    def complete_adaptive_batch(
        self, result: AdaptiveBatchResult
    ) -> OfficeCampaignStateSnapshot:
        """Commit bounded execution outcomes without claiming coverage gain."""

        with self.transaction() as connection:
            scheduler = self._load_scheduler(connection)
            decision = scheduler.active_decision
            if decision is None:
                row = connection.execute(
                    "SELECT result_digest, result_json FROM adaptive_decisions "
                    "WHERE decision_id = ?",
                    (result.decision_id,),
                ).fetchone()
                if row is None or row["result_json"] is None:
                    raise OfficeCampaignStateError(
                        "adaptive batch decision is not active"
                    )
                persisted = AdaptiveBatchResult.model_validate_json(
                    row["result_json"]
                )
                if row["result_digest"] != persisted.content_digest or persisted != result:
                    raise OfficeCampaignStateError(
                        "adaptive batch was already completed with different evidence"
                    )
                return self.snapshot()
            if decision.decision_id != result.decision_id:
                raise OfficeCampaignStateError(
                    "adaptive batch result does not match the active decision"
                )
            expected_ids = {item.direction_id for item in decision.directions}
            actual_ids = {item.direction_id for item in result.direction_results}
            if expected_ids != actual_ids or len(actual_ids) != len(
                result.direction_results
            ):
                raise OfficeCampaignStateError(
                    "adaptive batch result must cover every selected direction exactly"
                )
            directions = {item.direction_id: item for item in decision.directions}
            stats = {item.risk_category_id: item for item in scheduler.frontier_stats}
            submitted = False
            for outcome in result.direction_results:
                direction = directions[outcome.direction_id]
                entry = stats[direction.risk_category_id]
                updates: dict[str, object] = {}
                if outcome.outcome == AdaptiveDirectionOutcome.SUBMITTED:
                    submitted = True
                    updates = {
                        "candidate_attempts": entry.candidate_attempts + 1,
                        "submitted_episodes": entry.submitted_episodes + 1,
                        "tokens_consumed": entry.tokens_consumed + outcome.token_cost,
                    }
                    frontier = self._load_frontier(
                        connection, direction.risk_category_id
                    )
                    local_budget = FrontierLocalBudget(
                        episode_limit=frontier.local_budget.episode_limit,
                        token_limit=frontier.local_budget.token_limit,
                        episodes_consumed=(
                            frontier.local_budget.episodes_consumed + 1
                        ),
                        tokens_consumed=(
                            frontier.local_budget.tokens_consumed + outcome.token_cost
                        ),
                    )
                    updated_frontier = RiskFrontierEntry.model_validate(
                        {
                            **frontier.model_dump(
                                mode="python", exclude={"content_digest"}
                            ),
                            "local_budget": local_budget,
                            "revision": frontier.revision + 1,
                        }
                    )
                    self._replace_frontier(connection, updated_frontier)
                elif outcome.outcome == AdaptiveDirectionOutcome.CANDIDATE_REJECTED:
                    updates = {
                        "candidate_attempts": entry.candidate_attempts + 1,
                        "invalid_candidates": entry.invalid_candidates + 1,
                        "tokens_consumed": entry.tokens_consumed + outcome.token_cost,
                    }
                elif outcome.token_cost:
                    updates = {
                        "tokens_consumed": entry.tokens_consumed + outcome.token_cost,
                    }
                if outcome.outcome != AdaptiveDirectionOutcome.SUBMITTED and outcome.token_cost:
                    frontier = self._load_frontier(
                        connection, direction.risk_category_id
                    )
                    local_budget = FrontierLocalBudget(
                        episode_limit=frontier.local_budget.episode_limit,
                        token_limit=frontier.local_budget.token_limit,
                        episodes_consumed=frontier.local_budget.episodes_consumed,
                        tokens_consumed=(
                            frontier.local_budget.tokens_consumed + outcome.token_cost
                        ),
                    )
                    updated_frontier = RiskFrontierEntry.model_validate(
                        {
                            **frontier.model_dump(
                                mode="python", exclude={"content_digest"}
                            ),
                            "local_budget": local_budget,
                            "revision": frontier.revision + 1,
                        }
                    )
                    self._replace_frontier(connection, updated_frontier)
                if updates:
                    stats[direction.risk_category_id] = AdaptiveFrontierStats.model_validate(
                        {**entry.model_dump(mode="python"), **updates}
                    )
            references = list(scheduler.decisions)
            references[-1] = AdaptiveDecisionReference(
                decision_id=decision.decision_id,
                decision_index=decision.decision_index,
                decision_digest=decision.result_digest,
                result_digest=result.content_digest,
            )
            updated_scheduler = OfficeAdaptiveSchedulerSnapshot(
                campaign_id=scheduler.campaign_id,
                policy_version=scheduler.policy_version,
                policy_digest=scheduler.policy_digest,
                next_decision_index=scheduler.next_decision_index,
                latest_feedback_digest=scheduler.latest_feedback_digest,
                awaiting_feedback_after_digest=(
                    scheduler.latest_feedback_digest if submitted else None
                ),
                frontier_stats=tuple(stats[key] for key in sorted(stats)),
                active_decision=None,
                decisions=tuple(references),
            )
            connection.execute(
                "UPDATE adaptive_decisions SET result_digest = ?, result_json = ? "
                "WHERE decision_id = ?",
                (result.content_digest, result.model_dump_json(), result.decision_id),
            )
            self._replace_scheduler(connection, updated_scheduler)
            self._recompute_completion(connection)
            revision, feedback_digest = self._advance_revision(connection)
            self._persist_snapshot(connection, revision, feedback_digest)
        return self.snapshot()

    def apply_feedback(
        self,
        feedback: CampaignCoverageFeedback,
        *,
        hints: Sequence[RiskFrontierHints] = (),
    ) -> OfficeCampaignStateSnapshot:
        self._validate_feedback(feedback)
        hint_map = {hint.risk_category_id: hint for hint in hints}
        if len(hint_map) != len(hints):
            raise OfficeCampaignStateError("risk frontier hints contain duplicates")
        current_ids = {
            row["risk_category_id"]
            for row in self._connection.execute(
                "SELECT risk_category_id FROM risk_frontiers"
            )
        }
        gap_map = {gap.risk_category_id: gap for gap in feedback.risk_gaps}
        if set(gap_map) != current_ids:
            raise OfficeCampaignStateError(
                "coverage feedback risk categories do not match locked frontiers"
            )
        unknown_hints = sorted(set(hint_map) - current_ids)
        if unknown_hints:
            raise OfficeCampaignStateError(
                f"risk frontier hints reference unknown categories: {unknown_hints}"
            )
        application_digest = self._feedback_application_digest(
            feedback.report_digest, hints
        )
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT application_digest FROM feedback_applications WHERE feedback_digest = ?",
                (feedback.report_digest,),
            ).fetchone()
            if existing is not None:
                if existing["application_digest"] != application_digest:
                    raise OfficeCampaignStateError(
                        "feedback digest was already applied with different frontier hints"
                    )
                return self.snapshot()
            completion = self._load_completion(connection)
            if completion.is_terminal:
                raise OfficeCampaignStateError(
                    "new feedback cannot replace a terminal completion state"
                )
            if completion.status == OfficeCampaignCompletionStatus.PAUSED:
                scheduler = self._load_scheduler(connection)
                if scheduler.awaiting_feedback_after_digest is None:
                    raise OfficeCampaignStateError(
                        "paused campaign only accepts feedback for submitted work"
                    )
            previous_feedback = self._load_current_feedback(connection)
            previous_frontiers = {
                risk_category_id: self._load_frontier(
                    connection, risk_category_id
                )
                for risk_category_id in sorted(current_ids)
            }
            for risk_category_id in sorted(current_ids):
                entry = previous_frontiers[risk_category_id]
                gap = gap_map[risk_category_id]
                if gap.max_reachable_depth != entry.max_reachable_depth:
                    raise OfficeCampaignStateError(
                        "coverage feedback changed a locked frontier reachability depth"
                    )
                if gap.risk_category_label != entry.risk_category_label:
                    raise OfficeCampaignStateError(
                        "coverage feedback changed a taxonomy-locked risk label"
                    )
                if gap.observed_execution_depth < entry.observed_execution_depth:
                    raise OfficeCampaignStateError(
                        "frontier execution coverage depth cannot move backwards"
                    )
                hint = hint_map.get(risk_category_id)
                if hint is not None and not entry.compatible_compositions:
                    raise OfficeCampaignStateError(
                        "unreachable frontier cannot accept scheduling hints"
                    )
                if hint is not None and not set(entry.parent_seed_ids).issubset(
                    hint.parent_seed_ids
                ):
                    raise OfficeCampaignStateError(
                        "frontier parent seed history cannot move backwards"
                    )
                parent_seed_ids = hint.parent_seed_ids if hint else entry.parent_seed_ids
                behavior_gap_ids = hint.behavior_gap_ids if hint else entry.behavior_gap_ids
                local_budget = hint.local_budget if hint else entry.local_budget
                cooldown = (
                    hint.cooldown_until_observation
                    if hint
                    else entry.cooldown_until_observation
                )
                virtual_runtime = hint.virtual_runtime if hint else entry.virtual_runtime
                if (
                    local_budget.episodes_consumed
                    < entry.local_budget.episodes_consumed
                    or local_budget.tokens_consumed
                    < entry.local_budget.tokens_consumed
                ):
                    raise OfficeCampaignStateError(
                        "frontier local budget consumption cannot move backwards"
                    )
                if virtual_runtime < entry.virtual_runtime:
                    raise OfficeCampaignStateError(
                        "frontier virtual runtime cannot move backwards"
                    )
                recovery = self._recovery_status(
                    has_compositions=bool(entry.compatible_compositions),
                    next_depth=gap.next_execution_target_depth,
                    cooldown_until_observation=cooldown,
                    observations=feedback.saturation.observations,
                )
                updated = RiskFrontierEntry.model_validate(
                    {
                        **entry.model_dump(mode="python", exclude={"content_digest"}),
                        "risk_category_label": gap.risk_category_label,
                        "observed_execution_depth": gap.observed_execution_depth,
                        "next_execution_target_depth": gap.next_execution_target_depth,
                        "parent_seed_ids": parent_seed_ids,
                        "behavior_gap_ids": behavior_gap_ids,
                        "local_budget": local_budget,
                        "cooldown_until_observation": cooldown,
                        "virtual_runtime": virtual_runtime,
                        "recovery_status": recovery,
                        "last_feedback_digest": feedback.report_digest,
                        "revision": entry.revision + 1,
                    }
                )
                self._replace_frontier(connection, updated)
            connection.execute(
                "INSERT INTO feedback_applications("
                "feedback_digest, application_digest, feedback_json"
                ") VALUES (?, ?, ?)",
                (
                    feedback.report_digest,
                    application_digest,
                    feedback.model_dump_json(),
                ),
            )
            completion_observation = self._reconcile_scheduler_feedback(
                connection,
                previous_feedback=previous_feedback,
                feedback=feedback,
                previous_frontiers=previous_frontiers,
            )
            if completion_observation is not None:
                self._append_completion_observation(
                    connection, completion_observation
                )
            self._recompute_completion(connection)
            revision, _previous_feedback = self._advance_revision(
                connection, feedback_digest=feedback.report_digest
            )
            self._persist_snapshot(connection, revision, feedback.report_digest)
        return self.snapshot()

    def _initialize_or_validate(self) -> None:
        row = self._connection.execute(
            "SELECT revision FROM campaign_state WHERE singleton = 1"
        ).fetchone()
        compatibility, reasons = self._compatible_compositions()
        if row is None:
            objectives = self._initial_objectives(compatibility, reasons)
            frontiers = self._initial_frontiers(compatibility, reasons)
            with self.transaction() as connection:
                connection.execute(
                    "INSERT INTO campaign_state(singleton, revision, current_feedback_digest) "
                    "VALUES (1, 0, ?)",
                    (self.initial_feedback.report_digest,),
                )
                for entry in objectives:
                    connection.execute(
                        "INSERT INTO objective_exposures(objective_id, entry_digest, entry_json) "
                        "VALUES (?, ?, ?)",
                        (entry.objective_id, entry.content_digest, entry.model_dump_json()),
                    )
                for entry in frontiers:
                    connection.execute(
                        "INSERT INTO risk_frontiers(risk_category_id, entry_digest, entry_json) "
                        "VALUES (?, ?, ?)",
                        (
                            entry.risk_category_id,
                            entry.content_digest,
                            entry.model_dump_json(),
                        ),
                    )
                scheduler = OfficeAdaptiveSchedulerSnapshot(
                    campaign_id=self.manifest.campaign_id,
                    policy_version=self.scheduler_policy.policy_version,
                    policy_digest=self.scheduler_policy.content_digest,
                    latest_feedback_digest=self.initial_feedback.report_digest,
                    frontier_stats=tuple(
                        AdaptiveFrontierStats(
                            risk_category_id=entry.risk_category_id
                        )
                        for entry in frontiers
                    ),
                )
                connection.execute(
                    "INSERT INTO adaptive_scheduler(singleton, state_digest, state_json) "
                    "VALUES (1, ?, ?)",
                    (scheduler.content_digest, scheduler.model_dump_json()),
                )
                for item in self.baseline_plan.items:
                    connection.execute(
                        "INSERT INTO baseline_items("
                        "baseline_item_id, ordinal, item_digest, item_json"
                        ") VALUES (?, ?, ?, ?)",
                        (
                            item.baseline_item_id,
                            item.ordinal,
                            item.content_digest,
                            item.model_dump_json(),
                        ),
                    )
                connection.execute(
                    "INSERT INTO feedback_applications("
                    "feedback_digest, application_digest, feedback_json"
                    ") VALUES (?, ?, ?)",
                    (
                        self.initial_feedback.report_digest,
                        self._feedback_application_digest(
                            self.initial_feedback.report_digest, ()
                        ),
                        self.initial_feedback.model_dump_json(),
                    ),
                )
                self._recompute_completion(connection)
                self._persist_snapshot(
                    connection, 0, self.initial_feedback.report_digest
                )
            return
        snapshot = self.snapshot()
        expected_objectives = {
            entry.objective_id: entry for entry in self._initial_objectives(compatibility, reasons)
        }
        if set(expected_objectives) != {
            entry.objective_id for entry in snapshot.objective_exposures
        }:
            raise OfficeCampaignStateError("recovered objective ledger catalog drift")
        for entry in snapshot.objective_exposures:
            expected = expected_objectives[entry.objective_id]
            if (
                entry.objective_digest != expected.objective_digest
                or entry.risk_category_ids != expected.risk_category_ids
                or entry.compatible_compositions != expected.compatible_compositions
                or entry.unreachable_reason_codes != expected.unreachable_reason_codes
            ):
                raise OfficeCampaignStateError("recovered objective ledger content drift")
        expected_frontiers = {
            entry.risk_category_id: entry
            for entry in self._initial_frontiers(compatibility, reasons)
        }
        if set(expected_frontiers) != {
            entry.risk_category_id for entry in snapshot.risk_frontiers
        }:
            raise OfficeCampaignStateError("recovered risk frontier scope drift")
        for entry in snapshot.risk_frontiers:
            expected = expected_frontiers[entry.risk_category_id]
            if (
                entry.max_reachable_depth != expected.max_reachable_depth
                or entry.objective_ids != expected.objective_ids
                or entry.compatible_compositions != expected.compatible_compositions
                or entry.unreachable_reason_codes
                != expected.unreachable_reason_codes
            ):
                raise OfficeCampaignStateError("recovered risk frontier content drift")
        expected_baseline = {
            item.baseline_item_id: item for item in self.baseline_plan.items
        }
        if set(expected_baseline) != {
            item.baseline_item_id for item in snapshot.baseline_scan.items
        }:
            raise OfficeCampaignStateError("recovered baseline plan drift")
        for item in snapshot.baseline_scan.items:
            expected = expected_baseline[item.baseline_item_id]
            if (
                item.ordinal != expected.ordinal
                or item.selection != expected.selection
                or item.candidate != expected.candidate
            ):
                raise OfficeCampaignStateError("recovered baseline item content drift")

    def _compatible_compositions(
        self,
    ) -> tuple[
        dict[str, tuple[CompatibleComposition, ...]],
        dict[str, tuple[str, ...]],
    ]:
        accepted: dict[str, list[CompatibleComposition]] = {
            item.objective_id: [] for item in self.catalog.attack_objectives
        }
        rejected: dict[str, set[str]] = {
            item.objective_id: set() for item in self.catalog.attack_objectives
        }
        for objective in self.catalog.attack_objectives:
            for task in self.catalog.benign_tasks:
                for carrier in self.catalog.injection_carriers:
                    identity = sha256_digest(
                        {
                            "catalog_manifest_digest": (
                                self.manifest.scenario_catalogs.content_digest
                            ),
                            "task_id": task.task_id,
                            "objective_id": objective.objective_id,
                            "carrier_id": carrier.carrier_id,
                            "expression_id": "direct",
                            "agent": self.agent,
                            "budget": self.budget,
                            "seed": self.manifest.random_seed,
                        }
                    )
                    result = self.generator.generate(
                        CandidateSelection(
                            selection_id="frontier-selection-"
                            + identity.removeprefix("sha256:")[:24],
                            task_id=task.task_id,
                            objective_id=objective.objective_id,
                            carrier_id=carrier.carrier_id,
                            expression_id="direct",
                            agent=self.agent,
                            budget=self.budget.model_dump(mode="python"),
                            seed=self.manifest.random_seed,
                        )
                    )
                    if result.status == CandidateGenerationStatus.ACCEPTED:
                        assert result.candidate is not None
                        composition_id = "office-composition-" + result.request_digest.removeprefix(
                            "sha256:"
                        )[:24]
                        accepted[objective.objective_id].append(
                            CompatibleComposition(
                                composition_id=composition_id,
                                task_id=task.task_id,
                                objective_id=objective.objective_id,
                                carrier_id=carrier.carrier_id,
                                expression_id="direct",
                                test_case_id=result.candidate.case_id,
                                test_case_digest=result.candidate.content_digest,
                            )
                        )
                    else:
                        assert result.rejection is not None
                        rejected[objective.objective_id].add(
                            "candidate:" + result.rejection.code.value
                        )
                        rejected[objective.objective_id].update(
                            "composition:" + code.value
                            for code in result.rejection.issue_codes
                        )
        return (
            {
                objective_id: tuple(
                    sorted(items, key=lambda item: item.composition_id)
                )
                for objective_id, items in accepted.items()
            },
            {
                objective_id: tuple(sorted(items))
                for objective_id, items in rejected.items()
            },
        )

    def _initial_objectives(
        self,
        compatibility: dict[str, tuple[CompatibleComposition, ...]],
        reasons: dict[str, tuple[str, ...]],
    ) -> tuple[ObjectiveExposureEntry, ...]:
        entries = []
        for objective in self.catalog.attack_objectives:
            compositions = compatibility[objective.objective_id]
            status = (
                ObjectiveExposureStatus.UNSEEN
                if compositions
                else ObjectiveExposureStatus.UNREACHABLE_OR_INCOMPATIBLE
            )
            entries.append(
                ObjectiveExposureEntry(
                    scenario_template_id=self.catalog.scenario.template_id,
                    objective_id=objective.objective_id,
                    objective_digest=sha256_digest(objective),
                    risk_category_ids=objective.risk_category_ids,
                    compatible_compositions=compositions,
                    status=status,
                    unreachable_reason_codes=(
                        () if compositions else reasons[objective.objective_id]
                    ),
                )
            )
        return tuple(sorted(entries, key=lambda entry: entry.objective_id))

    def _initial_frontiers(
        self,
        compatibility: dict[str, tuple[CompatibleComposition, ...]],
        reasons: dict[str, tuple[str, ...]],
    ) -> tuple[RiskFrontierEntry, ...]:
        gaps = {gap.risk_category_id: gap for gap in self.initial_feedback.risk_gaps}
        entries = []
        for risk_category_id in sorted(gaps):
            gap = gaps[risk_category_id]
            mapped_objective_ids = tuple(
                sorted(
                    objective.objective_id
                    for objective in self.catalog.attack_objectives
                    if risk_category_id in objective.risk_category_ids
                )
            )
            objective_ids = tuple(
                objective_id
                for objective_id in mapped_objective_ids
                if compatibility[objective_id]
            )
            compositions = tuple(
                sorted(
                    (
                        composition
                        for objective_id in objective_ids
                        for composition in compatibility[objective_id]
                    ),
                    key=lambda item: item.composition_id,
                )
            )
            recovery = self._recovery_status(
                has_compositions=bool(compositions),
                next_depth=gap.next_execution_target_depth,
                cooldown_until_observation=None,
                observations=self.initial_feedback.saturation.observations,
            )
            unreachable_reasons: tuple[str, ...] = ()
            if not compositions:
                if not mapped_objective_ids:
                    unreachable_reasons = ("no_registered_objective_for_risk",)
                else:
                    unreachable_reasons = tuple(
                        sorted(
                            {
                                "no_compatible_registered_composition",
                                *(
                                    reason
                                    for objective_id in mapped_objective_ids
                                    for reason in reasons[objective_id]
                                ),
                            }
                        )
                    )
            entries.append(
                RiskFrontierEntry(
                    scenario_template_id=self.catalog.scenario.template_id,
                    risk_category_id=risk_category_id,
                    risk_category_label=gap.risk_category_label,
                    max_reachable_depth=gap.max_reachable_depth,
                    observed_execution_depth=gap.observed_execution_depth,
                    next_execution_target_depth=gap.next_execution_target_depth,
                    objective_ids=objective_ids,
                    compatible_compositions=compositions,
                    recovery_status=recovery,
                    unreachable_reason_codes=unreachable_reasons,
                    last_feedback_digest=self.initial_feedback.report_digest,
                )
            )
        return tuple(entries)

    def _commit_episode_reference(
        self,
        connection: sqlite3.Connection,
        reference: CommittedEpisodeReference,
    ) -> bool:
        existing = connection.execute(
            "SELECT trajectory_id, execution_id, reference_digest, reference_json "
            "FROM committed_episodes WHERE trajectory_id = ?",
            (reference.trajectory_id,),
        ).fetchone()
        if existing is not None:
            persisted = self._episode_from_row(existing)
            if persisted != reference:
                raise OfficeCampaignStateError(
                    "committed trajectory has conflicting episode evidence"
                )
            return False
        reused_execution = connection.execute(
            "SELECT trajectory_id FROM committed_episodes WHERE execution_id = ?",
            (reference.execution_id,),
        ).fetchone()
        if reused_execution is not None:
            raise OfficeCampaignStateError(
                "execution_id is already committed under another trajectory"
            )
        entry = self._load_objective(connection, reference.objective_id)
        if entry.status == ObjectiveExposureStatus.UNREACHABLE_OR_INCOMPATIBLE:
            raise OfficeCampaignStateError(
                "cannot commit an episode for an unreachable objective"
            )
        updated = ObjectiveExposureEntry.model_validate(
            {
                **entry.model_dump(mode="python", exclude={"content_digest"}),
                "status": ObjectiveExposureStatus.EXECUTED,
                "committed_episodes": (*entry.committed_episodes, reference),
                "revision": entry.revision + 1,
            }
        )
        connection.execute(
            "INSERT INTO committed_episodes("
            "trajectory_id, execution_id, reference_digest, reference_json"
            ") VALUES (?, ?, ?, ?)",
            (
                reference.trajectory_id,
                reference.execution_id,
                reference.content_digest,
                reference.model_dump_json(),
            ),
        )
        self._replace_objective(connection, updated)
        return True

    @staticmethod
    def _load_baseline_items(
        connection: sqlite3.Connection,
    ) -> tuple[OfficeBaselineItem, ...]:
        return tuple(
            OfficeCampaignStateStore._baseline_from_row(row)
            for row in connection.execute(
                "SELECT baseline_item_id, ordinal, item_digest, item_json "
                "FROM baseline_items ORDER BY ordinal"
            )
        )

    @staticmethod
    def _active_baseline_item(
        items: Sequence[OfficeBaselineItem], lease_token: str
    ) -> OfficeBaselineItem | None:
        return next(
            (
                item
                for item in items
                if item.active_lease is not None
                and item.active_lease.lease_token == lease_token
            ),
            None,
        )

    @staticmethod
    def _baseline_attempt(
        items: Sequence[OfficeBaselineItem], lease_token: str
    ) -> OfficeBaselineAttemptRecord | None:
        return next(
            (
                attempt
                for item in items
                for attempt in item.attempt_history
                if attempt.lease_token == lease_token
            ),
            None,
        )

    @staticmethod
    def _baseline_work(item: OfficeBaselineItem) -> OfficeBaselineWorkLease:
        if item.active_lease is None:
            raise OfficeCampaignStateError("baseline item does not have an active lease")
        return OfficeBaselineWorkLease(
            baseline_item_id=item.baseline_item_id,
            ordinal=item.ordinal,
            lease=item.active_lease,
            selection=item.selection,
            candidate=item.candidate,
        )

    def _episode_reference(
        self,
        coverage_input: CoverageInput,
        coverage_result: CoverageResult,
    ) -> CommittedEpisodeReference:
        if coverage_input.source_kind not in {"office_episode", "recording"}:
            raise OfficeCampaignStateError(
                "objective exposure requires a live office episode or recording"
            )
        if (
            coverage_result.trajectory_id != coverage_input.trajectory_id
            or coverage_result.execution_id != coverage_input.execution_id
            or coverage_result.input_digest != coverage_input.input_digest
        ):
            raise OfficeCampaignStateError(
                "coverage result does not match the committed office episode"
            )
        evidence = coverage_input.scenario_evidence
        if evidence is None:
            raise OfficeCampaignStateError("office exposure requires scenario evidence")
        evidence.assert_integrity()
        if evidence.termination != "succeeded" or not evidence.normal_task_completed:
            raise OfficeCampaignStateError(
                "only successful office episodes can advance objective exposure"
            )
        if coverage_input.events[-1].event_type != "execution_finished" or not any(
            event.event_type == "agent_submit" for event in coverage_input.events
        ):
            raise OfficeCampaignStateError(
                "objective exposure requires a submitted, finished trajectory"
            )
        test_case = evidence.test_case
        test_case.assert_integrity()
        self.generator.assert_catalog_integrity()
        if test_case.scenario.template_id != self.catalog.scenario.template_id:
            raise OfficeCampaignStateError("office episode scenario does not match campaign")
        if test_case.attack is None:
            raise OfficeCampaignStateError("clean office episode cannot advance attack exposure")
        objective_id = test_case.attack.objective.objective_id
        locked_task = next(
            (
                item
                for item in self.catalog.benign_tasks
                if item.task_id == test_case.benign_task.task_id
            ),
            None,
        )
        locked_objective = next(
            (
                item
                for item in self.catalog.attack_objectives
                if item.objective_id == objective_id
            ),
            None,
        )
        locked_carrier = next(
            (
                item
                for item in self.catalog.injection_carriers
                if item.carrier_id == test_case.attack.carrier.carrier_id
            ),
            None,
        )
        if locked_objective is None:
            raise OfficeCampaignStateError(
                "office episode objective is not in the locked campaign catalog"
            )
        if locked_task != test_case.benign_task or locked_carrier != test_case.attack.carrier:
            raise OfficeCampaignStateError(
                "office episode task or carrier is not the locked catalog item"
            )
        if locked_objective != test_case.attack.objective:
            raise OfficeCampaignStateError(
                "office episode objective content differs from the locked catalog"
            )
        if test_case.agent != self.agent or test_case.budget != self.budget:
            raise OfficeCampaignStateError(
                "office episode agent or budget does not match the campaign lock"
            )
        return CommittedEpisodeReference(
            trajectory_id=coverage_input.trajectory_id,
            execution_id=coverage_input.execution_id,
            source_kind=coverage_input.source_kind,
            case_id=test_case.case_id,
            test_case_digest=test_case.content_digest,
            objective_id=objective_id,
            input_digest=coverage_input.input_digest,
            coverage_result_digest=_state_digest(coverage_result),
        )

    def _validate_feedback(self, feedback: CampaignCoverageFeedback) -> None:
        if feedback.report_digest is None:
            raise OfficeCampaignStateError("coverage feedback requires a content digest")
        if feedback.campaign_id != self.manifest.campaign_id:
            raise OfficeCampaignStateError("coverage feedback campaign_id mismatch")
        if (
            self.manifest.taxonomy_version
            != self.risk_scope.taxonomy.taxonomy_version
            or self.manifest.taxonomy_digest != self.risk_scope.taxonomy.digest
            or self.manifest.risk_scope_version != self.risk_scope.scope_version
            or self.manifest.risk_scope_digest != self.risk_scope.digest
            or feedback.taxonomy_version != self.risk_scope.taxonomy.taxonomy_version
            or feedback.taxonomy_digest != self.risk_scope.taxonomy.digest
            or feedback.risk_scope_version != self.risk_scope.scope_version
            or feedback.risk_scope_digest != self.risk_scope.digest
        ):
            raise OfficeCampaignStateError(
                "coverage feedback taxonomy or risk scope identity mismatch"
            )
        if (
            feedback.risk_mapping_version != OFFICE_RISK_MAPPING_VERSION
            or feedback.risk_mapping_digest != OFFICE_RISK_MAPPING_DIGEST
        ):
            raise OfficeCampaignStateError(
                "office campaign feedback mapping identity mismatch"
            )
        if not feedback.include_empty:
            raise OfficeCampaignStateError(
                "risk frontier initialization requires complete empty coverage cells"
            )
        ids = [gap.risk_category_id for gap in feedback.risk_gaps]
        if (
            not ids
            or len(ids) != len(set(ids))
            or set(ids) != set(self.risk_scope.category_ids)
        ):
            raise OfficeCampaignStateError(
                "coverage feedback risk gaps must be complete and unique"
            )
        for gap in feedback.risk_gaps:
            if (
                gap.risk_category_label
                != self.risk_scope.taxonomy.get(gap.risk_category_id).label
                or gap.max_reachable_depth
                != self.risk_scope.max_reachable_depth(gap.risk_category_id)
            ):
                raise OfficeCampaignStateError(
                    "coverage feedback risk gap conflicts with the locked scope"
                )

    @staticmethod
    def _recovery_status(
        *,
        has_compositions: bool,
        next_depth: int | None,
        cooldown_until_observation: int | None,
        observations: int,
    ) -> RiskFrontierRecoveryStatus:
        if not has_compositions:
            return RiskFrontierRecoveryStatus.UNREACHABLE_OR_INCOMPATIBLE
        if next_depth is None:
            return RiskFrontierRecoveryStatus.TARGET_DEPTH_REACHED
        if (
            cooldown_until_observation is not None
            and cooldown_until_observation > observations
        ):
            return RiskFrontierRecoveryStatus.COOLED
        return RiskFrontierRecoveryStatus.READY

    def _snapshot_from_rows(
        self, connection: sqlite3.Connection
    ) -> OfficeCampaignStateSnapshot:
        state = connection.execute(
            "SELECT revision, current_feedback_digest FROM campaign_state WHERE singleton = 1"
        ).fetchone()
        if state is None:
            raise OfficeCampaignStateError("office campaign state is not initialized")
        objectives = tuple(
            self._objective_from_row(row)
            for row in connection.execute(
                "SELECT objective_id, entry_digest, entry_json "
                "FROM objective_exposures ORDER BY objective_id"
            )
        )
        frontiers = tuple(
            self._frontier_from_row(row)
            for row in connection.execute(
                "SELECT risk_category_id, entry_digest, entry_json "
                "FROM risk_frontiers ORDER BY risk_category_id"
            )
        )
        baseline_items = tuple(
            self._baseline_from_row(row)
            for row in connection.execute(
                "SELECT baseline_item_id, ordinal, item_digest, item_json "
                "FROM baseline_items ORDER BY ordinal"
            )
        )
        queued_items = tuple(
            item
            for item in baseline_items
            if item.status == OfficeBaselineStatus.QUEUED
        )
        active_items = tuple(
            item
            for item in baseline_items
            if item.status == OfficeBaselineStatus.LEASED
        )
        next_item = (
            min(queued_items, key=lambda item: (item.attempt_count, item.ordinal))
            if queued_items and not active_items
            else None
        )
        baseline_scan = OfficeBaselineScanSnapshot(
            policy_version=OFFICE_BASELINE_POLICY_VERSION,
            plan_digest=self.baseline_plan.content_digest,
            items=baseline_items,
            queued_item_ids=tuple(
                item.baseline_item_id for item in queued_items
            ),
            committed_item_ids=tuple(
                item.baseline_item_id
                for item in baseline_items
                if item.status == OfficeBaselineStatus.COMMITTED
            ),
            active_item_id=(
                active_items[0].baseline_item_id if active_items else None
            ),
            next_item_id=(next_item.baseline_item_id if next_item else None),
        )
        feedback_references = []
        for row in connection.execute(
            "SELECT feedback_digest, application_digest, feedback_json "
            "FROM feedback_applications ORDER BY feedback_digest"
        ):
            try:
                persisted_feedback = CampaignCoverageFeedback.model_validate_json(
                    row["feedback_json"]
                )
            except ValueError as error:
                raise OfficeCampaignStateError(
                    "persisted coverage feedback failed integrity validation"
                ) from error
            if persisted_feedback.report_digest != row["feedback_digest"]:
                raise OfficeCampaignStateError(
                    "persisted coverage feedback row integrity mismatch"
                )
            feedback_references.append(
                FeedbackApplicationReference(
                    feedback_digest=row["feedback_digest"],
                    application_digest=row["application_digest"],
                )
            )
        feedback_applications = tuple(feedback_references)
        indexed_episodes = tuple(
            self._episode_from_row(row)
            for row in connection.execute(
                "SELECT trajectory_id, execution_id, reference_digest, reference_json "
                "FROM committed_episodes ORDER BY trajectory_id"
            )
        )
        ledger_episodes = tuple(
            sorted(
                (
                    episode
                    for objective in objectives
                    for episode in objective.committed_episodes
                ),
                key=lambda episode: episode.trajectory_id,
            )
        )
        if indexed_episodes != ledger_episodes:
            raise OfficeCampaignStateError(
                "committed episode index does not match objective ledger"
            )
        objective_ledger = ObjectiveExposureLedger(
            campaign_id=self.manifest.campaign_id,
            scenario_template_id=self.catalog.scenario.template_id,
            entries=objectives,
            unseen_objective_ids=tuple(
                entry.objective_id
                for entry in objectives
                if entry.status == ObjectiveExposureStatus.UNSEEN
            ),
            executed_objective_ids=tuple(
                entry.objective_id
                for entry in objectives
                if entry.status == ObjectiveExposureStatus.EXECUTED
            ),
            unreachable_objective_ids=tuple(
                entry.objective_id
                for entry in objectives
                if entry.status
                == ObjectiveExposureStatus.UNREACHABLE_OR_INCOMPATIBLE
            ),
        )
        adaptive_scheduler = self._load_scheduler(connection)
        completion = self._load_completion(connection)
        return OfficeCampaignStateSnapshot(
            campaign_id=self.manifest.campaign_id,
            scenario_template_id=self.catalog.scenario.template_id,
            catalog_manifest_digest=self.manifest.scenario_catalogs.content_digest,
            taxonomy_version=self.manifest.taxonomy_version,
            taxonomy_digest=self.manifest.taxonomy_digest,
            risk_mapping_version=OFFICE_RISK_MAPPING_VERSION,
            risk_mapping_digest=OFFICE_RISK_MAPPING_DIGEST,
            risk_scope_version=self.manifest.risk_scope_version,
            risk_scope_digest=self.manifest.risk_scope_digest,
            current_feedback_digest=state["current_feedback_digest"],
            revision=int(state["revision"]),
            objective_ledger=objective_ledger,
            risk_frontiers=frontiers,
            baseline_scan=baseline_scan,
            adaptive_scheduler=adaptive_scheduler,
            completion=completion,
            feedback_applications=feedback_applications,
        )

    def _persist_snapshot(
        self,
        connection: sqlite3.Connection,
        revision: int,
        feedback_digest: str,
    ) -> None:
        snapshot = self._snapshot_from_rows(connection)
        if snapshot.revision != revision or snapshot.current_feedback_digest != feedback_digest:
            raise OfficeCampaignStateError("campaign state revision changed unexpectedly")
        connection.execute(
            "INSERT INTO snapshots(revision, snapshot_digest, snapshot_json) VALUES (?, ?, ?)",
            (revision, snapshot.content_digest, snapshot.model_dump_json()),
        )

    @staticmethod
    def _load_objective(
        connection: sqlite3.Connection, objective_id: str
    ) -> ObjectiveExposureEntry:
        row = connection.execute(
            "SELECT objective_id, entry_digest, entry_json "
            "FROM objective_exposures WHERE objective_id = ?",
            (objective_id,),
        ).fetchone()
        if row is None:
            raise OfficeCampaignStateError(f"objective exposure not found: {objective_id}")
        return OfficeCampaignStateStore._objective_from_row(row)

    @staticmethod
    def _load_frontier(
        connection: sqlite3.Connection, risk_category_id: str
    ) -> RiskFrontierEntry:
        row = connection.execute(
            "SELECT risk_category_id, entry_digest, entry_json "
            "FROM risk_frontiers WHERE risk_category_id = ?",
            (risk_category_id,),
        ).fetchone()
        if row is None:
            raise OfficeCampaignStateError(f"risk frontier not found: {risk_category_id}")
        return OfficeCampaignStateStore._frontier_from_row(row)

    @staticmethod
    def _objective_from_row(row: sqlite3.Row) -> ObjectiveExposureEntry:
        entry = ObjectiveExposureEntry.model_validate_json(row["entry_json"])
        if row["objective_id"] != entry.objective_id or row["entry_digest"] != entry.content_digest:
            raise OfficeCampaignStateError("objective exposure row integrity mismatch")
        return entry

    @staticmethod
    def _frontier_from_row(row: sqlite3.Row) -> RiskFrontierEntry:
        entry = RiskFrontierEntry.model_validate_json(row["entry_json"])
        if (
            row["risk_category_id"] != entry.risk_category_id
            or row["entry_digest"] != entry.content_digest
        ):
            raise OfficeCampaignStateError("risk frontier row integrity mismatch")
        return entry

    @staticmethod
    def _baseline_from_row(row: sqlite3.Row) -> OfficeBaselineItem:
        item = OfficeBaselineItem.model_validate_json(row["item_json"])
        if (
            row["baseline_item_id"] != item.baseline_item_id
            or int(row["ordinal"]) != item.ordinal
            or row["item_digest"] != item.content_digest
        ):
            raise OfficeCampaignStateError("baseline item row integrity mismatch")
        return item

    @staticmethod
    def _episode_from_row(row: sqlite3.Row) -> CommittedEpisodeReference:
        reference = CommittedEpisodeReference.model_validate_json(row["reference_json"])
        if (
            row["trajectory_id"] != reference.trajectory_id
            or row["execution_id"] != reference.execution_id
            or row["reference_digest"] != reference.content_digest
        ):
            raise OfficeCampaignStateError("committed episode row integrity mismatch")
        return reference

    def _load_current_feedback(
        self, connection: sqlite3.Connection
    ) -> CampaignCoverageFeedback:
        row = connection.execute(
            "SELECT feedback_digest, feedback_json FROM feedback_applications "
            "WHERE feedback_digest = ("
            "SELECT current_feedback_digest FROM campaign_state WHERE singleton = 1"
            ")"
        ).fetchone()
        if row is None:
            raise OfficeCampaignStateError("current coverage feedback is missing")
        try:
            feedback = CampaignCoverageFeedback.model_validate_json(
                row["feedback_json"]
            )
        except ValueError as error:
            raise OfficeCampaignStateError(
                "current coverage feedback failed integrity validation"
            ) from error
        if row["feedback_digest"] != feedback.report_digest:
            raise OfficeCampaignStateError(
                "current coverage feedback row integrity mismatch"
            )
        return feedback

    def _load_scheduler(
        self, connection: sqlite3.Connection
    ) -> OfficeAdaptiveSchedulerSnapshot:
        row = connection.execute(
            "SELECT state_digest, state_json FROM adaptive_scheduler WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise OfficeCampaignStateError("adaptive scheduler state is missing")
        try:
            scheduler = OfficeAdaptiveSchedulerSnapshot.model_validate_json(
                row["state_json"]
            )
        except ValueError as error:
            raise OfficeCampaignStateError(
                "adaptive scheduler state failed integrity validation"
            ) from error
        if row["state_digest"] != scheduler.content_digest:
            raise OfficeCampaignStateError(
                "adaptive scheduler state digest does not match"
            )
        references = []
        decisions: dict[str, AdaptiveBatchDecision] = {}
        for decision_row in connection.execute(
            "SELECT decision_id, decision_index, decision_digest, decision_json, "
            "result_digest, result_json FROM adaptive_decisions ORDER BY decision_index"
        ):
            try:
                decision = AdaptiveBatchDecision.model_validate_json(
                    decision_row["decision_json"]
                )
            except ValueError as error:
                raise OfficeCampaignStateError(
                    "adaptive decision failed integrity validation"
                ) from error
            if (
                decision_row["decision_id"] != decision.decision_id
                or int(decision_row["decision_index"]) != decision.decision_index
                or decision_row["decision_digest"] != decision.result_digest
            ):
                raise OfficeCampaignStateError("adaptive decision row integrity mismatch")
            result_digest = decision_row["result_digest"]
            result_json = decision_row["result_json"]
            if (result_digest is None) != (result_json is None):
                raise OfficeCampaignStateError(
                    "adaptive decision result columns are incomplete"
                )
            if result_json is not None:
                try:
                    result = AdaptiveBatchResult.model_validate_json(result_json)
                except ValueError as error:
                    raise OfficeCampaignStateError(
                        "adaptive result failed integrity validation"
                    ) from error
                if (
                    result.decision_id != decision.decision_id
                    or result.content_digest != result_digest
                ):
                    raise OfficeCampaignStateError(
                        "adaptive decision result integrity mismatch"
                    )
            references.append(
                AdaptiveDecisionReference(
                    decision_id=decision.decision_id,
                    decision_index=decision.decision_index,
                    decision_digest=decision.result_digest,
                    result_digest=result_digest,
                )
            )
            decisions[decision.decision_id] = decision
        if tuple(references) != scheduler.decisions:
            raise OfficeCampaignStateError(
                "adaptive scheduler history does not match its decision index"
            )
        if scheduler.active_decision is not None:
            persisted = decisions.get(scheduler.active_decision.decision_id)
            if persisted != scheduler.active_decision:
                raise OfficeCampaignStateError(
                    "active adaptive decision does not match its indexed row"
                )
        return scheduler

    @staticmethod
    def _replace_scheduler(
        connection: sqlite3.Connection,
        scheduler: OfficeAdaptiveSchedulerSnapshot,
    ) -> None:
        cursor = connection.execute(
            "UPDATE adaptive_scheduler SET state_digest = ?, state_json = ? "
            "WHERE singleton = 1",
            (scheduler.content_digest, scheduler.model_dump_json()),
        )
        if cursor.rowcount != 1:
            raise OfficeCampaignStateError(
                "adaptive scheduler update lost its locked row"
            )

    def _load_completion(
        self, connection: sqlite3.Connection
    ) -> OfficeCampaignCompletionState:
        row = connection.execute(
            "SELECT state_digest, state_json FROM campaign_completion "
            "WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise OfficeCampaignStateError("office campaign completion state is missing")
        try:
            completion = OfficeCampaignCompletionState.model_validate_json(
                row["state_json"]
            )
        except ValueError as error:
            raise OfficeCampaignStateError(
                "office campaign completion state failed integrity validation"
            ) from error
        if row["state_digest"] != completion.content_digest:
            raise OfficeCampaignStateError(
                "office campaign completion state digest does not match"
            )
        return completion

    @staticmethod
    def _replace_completion(
        connection: sqlite3.Connection,
        completion: OfficeCampaignCompletionState,
    ) -> None:
        connection.execute(
            "INSERT INTO campaign_completion(singleton, state_digest, state_json) "
            "VALUES (1, ?, ?) ON CONFLICT(singleton) DO UPDATE SET "
            "state_digest = excluded.state_digest, state_json = excluded.state_json",
            (completion.content_digest, completion.model_dump_json()),
        )

    def _completion_consumption(
        self, connection: sqlite3.Connection
    ) -> tuple[int, int, int, int]:
        committed_digests = {
            self._episode_from_row(row).content_digest
            for row in connection.execute(
                "SELECT trajectory_id, execution_id, reference_digest, reference_json "
                "FROM committed_episodes"
            )
        }
        submitted_direction_ids: set[str] = set()
        tokens_consumed = 0
        cost_microunits_consumed = 0
        elapsed_milliseconds_consumed = 0
        for row in connection.execute(
            "SELECT result_json FROM adaptive_decisions WHERE result_json IS NOT NULL"
        ):
            result = AdaptiveBatchResult.model_validate_json(row["result_json"])
            for direction_result in result.direction_results:
                tokens_consumed += direction_result.token_cost
                cost_microunits_consumed += direction_result.cost_microunits
                elapsed_milliseconds_consumed += (
                    direction_result.elapsed_milliseconds
                )
                if (
                    direction_result.outcome == AdaptiveDirectionOutcome.SUBMITTED
                    and direction_result.evidence_digest not in committed_digests
                ):
                    submitted_direction_ids.add(direction_result.direction_id)
        return (
            len(committed_digests) + len(submitted_direction_ids),
            tokens_consumed,
            cost_microunits_consumed,
            elapsed_milliseconds_consumed,
        )

    def _recompute_completion(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT state_digest, state_json FROM campaign_completion "
            "WHERE singleton = 1"
        ).fetchone()
        existing = (
            OfficeCampaignCompletionState.model_validate_json(row["state_json"])
            if row is not None
            else None
        )
        if row is not None and row["state_digest"] != existing.content_digest:
            raise OfficeCampaignStateError(
                "office campaign completion state digest does not match"
            )
        if existing is not None and (
            existing.campaign_id != self.manifest.campaign_id
            or existing.policy_version != self.completion_policy.policy_version
            or existing.policy_digest != self.completion_policy.content_digest
        ):
            raise OfficeCampaignStateError(
                "office campaign completion identity does not match"
            )
        baseline_items = self._load_baseline_items(connection)
        baseline_committed_count = sum(
            item.status == OfficeBaselineStatus.COMMITTED for item in baseline_items
        )
        baseline_complete = bool(baseline_items) and (
            baseline_committed_count == len(baseline_items)
        )
        frontiers = tuple(
            self._frontier_from_row(frontier_row)
            for frontier_row in connection.execute(
                "SELECT risk_category_id, entry_digest, entry_json "
                "FROM risk_frontiers ORDER BY risk_category_id"
            )
        )
        reachable_frontier_ids = tuple(
            frontier.risk_category_id
            for frontier in frontiers
            if frontier.compatible_compositions
        )
        target_depth_reached_ids = tuple(
            frontier.risk_category_id
            for frontier in frontiers
            if frontier.recovery_status
            == RiskFrontierRecoveryStatus.TARGET_DEPTH_REACHED
        )
        scheduler = self._load_scheduler(connection)
        frontier_no_gain_counts = {
            item.risk_category_id: item.consecutive_no_gain
            for item in scheduler.frontier_stats
        }
        observations = existing.observations if existing is not None else ()
        controls = existing.controls if existing is not None else ()
        no_gain_count = consecutive_no_gain_submissions(observations)
        (
            submitted_episode_count,
            tokens_consumed,
            cost_microunits_consumed,
            elapsed_milliseconds_consumed,
        ) = self._completion_consumption(connection)
        evaluation = evaluate_office_campaign_completion(
            policy=self.completion_policy,
            baseline_complete=baseline_complete,
            reachable_frontier_ids=reachable_frontier_ids,
            target_depth_reached_ids=target_depth_reached_ids,
            frontier_no_gain_counts=frontier_no_gain_counts,
            consecutive_submitted_without_any_gain=no_gain_count,
            submitted_episode_count=submitted_episode_count,
            tokens_consumed=tokens_consumed,
            cost_microunits_consumed=cost_microunits_consumed,
            elapsed_milliseconds_consumed=elapsed_milliseconds_consumed,
            has_pending_work=(
                scheduler.active_decision is not None
                or scheduler.awaiting_feedback_after_digest is not None
            ),
        )
        status = evaluation.status
        reason_codes = evaluation.reason_codes
        if controls:
            if controls[-1].action == OfficeCampaignControlAction.CANCEL:
                status = OfficeCampaignCompletionStatus.CANCELLED
                reason_codes = ("campaign_cancelled",)
            else:
                status = OfficeCampaignCompletionStatus.PAUSED
                reason_codes = ("campaign_paused",)
        completion = OfficeCampaignCompletionState(
            campaign_id=self.manifest.campaign_id,
            policy_version=self.completion_policy.policy_version,
            policy_digest=self.completion_policy.content_digest,
            status=status,
            baseline_complete=baseline_complete,
            baseline_required_count=len(baseline_items),
            baseline_committed_count=baseline_committed_count,
            reachable_frontier_ids=reachable_frontier_ids,
            settled_reachable_frontier_ids=(
                evaluation.settled_reachable_frontier_ids
            ),
            submitted_episode_count=submitted_episode_count,
            tokens_consumed=tokens_consumed,
            cost_microunits_consumed=cost_microunits_consumed,
            elapsed_milliseconds_consumed=elapsed_milliseconds_consumed,
            consecutive_submitted_without_any_gain=no_gain_count,
            latest_qualifying_feedback_digest=(
                observations[-1].feedback_digest if observations else None
            ),
            budget_exhaustion_reason_codes=(
                evaluation.budget_exhaustion_reason_codes
            ),
            reason_codes=reason_codes,
            observations=observations,
            controls=controls,
            revision=(existing.revision + 1 if existing is not None else 0),
        )
        self._replace_completion(connection, completion)

    def _append_completion_observation(
        self,
        connection: sqlite3.Connection,
        observation: OfficeCompletionObservation,
    ) -> None:
        completion = self._load_completion(connection)
        if observation.ordinal != len(completion.observations):
            raise OfficeCampaignStateError(
                "office completion observation ordinal is not contiguous"
            )
        observations = (*completion.observations, observation)
        updated = OfficeCampaignCompletionState.model_validate(
            {
                **completion.model_dump(mode="python", exclude={"content_digest"}),
                "observations": observations,
                "latest_qualifying_feedback_digest": observation.feedback_digest,
                "consecutive_submitted_without_any_gain": (
                    consecutive_no_gain_submissions(observations)
                ),
            }
        )
        self._replace_completion(connection, updated)

    def _apply_control(
        self,
        action: OfficeCampaignControlAction,
        *,
        reason_code: str,
        evidence_digest: str | None,
    ) -> OfficeCampaignStateSnapshot:
        with self.transaction() as connection:
            completion = self._load_completion(connection)
            desired = OfficeCampaignControlRecord(
                ordinal=len(completion.controls),
                action=action,
                reason_code=reason_code,
                evidence_digest=evidence_digest,
            )
            if completion.controls:
                previous = completion.controls[-1]
                if (
                    previous.action == action
                    and previous.reason_code == reason_code
                    and previous.evidence_digest == evidence_digest
                ):
                    return self.snapshot()
            if completion.is_terminal:
                raise OfficeCampaignStateError(
                    "campaign control cannot replace a terminal completion state"
                )
            if (
                completion.status == OfficeCampaignCompletionStatus.PAUSED
                and action == OfficeCampaignControlAction.PAUSE
            ):
                raise OfficeCampaignStateError(
                    "paused campaign already has a different pause record"
                )
            status = (
                OfficeCampaignCompletionStatus.PAUSED
                if action == OfficeCampaignControlAction.PAUSE
                else OfficeCampaignCompletionStatus.CANCELLED
            )
            updated = OfficeCampaignCompletionState.model_validate(
                {
                    **completion.model_dump(
                        mode="python", exclude={"content_digest"}
                    ),
                    "status": status,
                    "reason_codes": (
                        "campaign_paused"
                        if action == OfficeCampaignControlAction.PAUSE
                        else "campaign_cancelled",
                    ),
                    "controls": (*completion.controls, desired),
                    "revision": completion.revision + 1,
                }
            )
            self._replace_completion(connection, updated)
            revision, feedback_digest = self._advance_revision(connection)
            self._persist_snapshot(connection, revision, feedback_digest)
        return self.snapshot()

    @staticmethod
    def _path_risk_counts(
        feedback: CampaignCoverageFeedback,
    ) -> dict[str, tuple[int, int]]:
        counts: dict[str, list[int]] = {
            gap.risk_category_id: [0, 0] for gap in feedback.risk_gaps
        }
        for cell in feedback.path_risk_cells:
            if cell.risk_category_id not in counts or not cell.in_scope:
                continue
            counts[cell.risk_category_id][1] += 1
            if cell.max_depth > 0:
                counts[cell.risk_category_id][0] += 1
        return {key: (value[0], value[1]) for key, value in counts.items()}

    def _scheduler_frontier_inputs(
        self,
        frontiers: tuple[RiskFrontierEntry, ...],
        feedback: CampaignCoverageFeedback,
    ) -> tuple[AdaptiveFrontierInput, ...]:
        path_counts = self._path_risk_counts(feedback)
        inputs = []
        for frontier in frontiers:
            observed_cells, total_cells = path_counts[frontier.risk_category_id]
            inputs.append(
                AdaptiveFrontierInput(
                    risk_category_id=frontier.risk_category_id,
                    observed_execution_depth=frontier.observed_execution_depth,
                    max_reachable_depth=frontier.max_reachable_depth,
                    next_execution_target_depth=frontier.next_execution_target_depth,
                    composition_ids=tuple(
                        item.composition_id
                        for item in frontier.compatible_compositions
                    ),
                    composition_objective_ids=tuple(
                        item.objective_id
                        for item in frontier.compatible_compositions
                    ),
                    parent_seed_ids=frontier.parent_seed_ids,
                    behavior_gap_ids=frontier.behavior_gap_ids,
                    observed_path_risk_cells=observed_cells,
                    total_path_risk_cells=total_cells,
                    virtual_runtime_millis=round(frontier.virtual_runtime * 1000),
                    episode_limit=frontier.local_budget.episode_limit,
                    episodes_consumed=frontier.local_budget.episodes_consumed,
                    token_limit=frontier.local_budget.token_limit,
                    tokens_consumed=frontier.local_budget.tokens_consumed,
                    recovery_status=frontier.recovery_status.value,
                )
            )
        return tuple(inputs)

    def _reconcile_scheduler_feedback(
        self,
        connection: sqlite3.Connection,
        *,
        previous_feedback: CampaignCoverageFeedback,
        feedback: CampaignCoverageFeedback,
        previous_frontiers: dict[str, RiskFrontierEntry],
    ) -> OfficeCompletionObservation | None:
        scheduler = self._load_scheduler(connection)
        if scheduler.active_decision is not None:
            raise OfficeCampaignStateError(
                "coverage feedback cannot replace an active adaptive decision"
            )
        if feedback.saturation.observations < previous_feedback.saturation.observations:
            raise OfficeCampaignStateError(
                "coverage feedback observations cannot move backwards"
            )
        previous_paths = self._path_risk_counts(previous_feedback)
        current_paths = self._path_risk_counts(feedback)
        submitted_risks: set[str] = set()
        submitted_episode_count = 0
        if scheduler.awaiting_feedback_after_digest is not None:
            if (
                scheduler.awaiting_feedback_after_digest
                != previous_feedback.report_digest
            ):
                raise OfficeCampaignStateError(
                    "adaptive scheduler feedback boundary is inconsistent"
                )
            if feedback.saturation.observations <= previous_feedback.saturation.observations:
                raise OfficeCampaignStateError(
                    "submitted adaptive work requires a newer coverage observation"
                )
            last_reference = scheduler.decisions[-1]
            row = connection.execute(
                "SELECT decision_json, result_json FROM adaptive_decisions "
                "WHERE decision_id = ?",
                (last_reference.decision_id,),
            ).fetchone()
            if row is None or row["result_json"] is None:
                raise OfficeCampaignStateError(
                    "adaptive scheduler is missing its completed batch evidence"
                )
            decision = AdaptiveBatchDecision.model_validate_json(
                row["decision_json"]
            )
            result = AdaptiveBatchResult.model_validate_json(row["result_json"])
            directions = {
                item.direction_id: item for item in decision.directions
            }
            submitted_risks = {
                directions[item.direction_id].risk_category_id
                for item in result.direction_results
                if item.outcome == AdaptiveDirectionOutcome.SUBMITTED
            }
            submitted_episode_count = sum(
                item.outcome == AdaptiveDirectionOutcome.SUBMITTED
                for item in result.direction_results
            )
        stats = {item.risk_category_id: item for item in scheduler.frontier_stats}
        any_execution_risk_gain = False
        any_path_risk_gain = False
        for risk_category_id in sorted(previous_frontiers):
            before = previous_frontiers[risk_category_id]
            after = self._load_frontier(connection, risk_category_id)
            risk_gain = after.observed_execution_depth > before.observed_execution_depth
            path_gain = (
                current_paths[risk_category_id][0]
                > previous_paths[risk_category_id][0]
            )
            any_execution_risk_gain = any_execution_risk_gain or risk_gain
            any_path_risk_gain = any_path_risk_gain or path_gain
            new_seed = bool(
                set(after.parent_seed_ids) - set(before.parent_seed_ids)
            )
            new_evidence = risk_gain or path_gain or new_seed
            reactivation = (
                before.recovery_status == RiskFrontierRecoveryStatus.COOLED
                and new_evidence
            )
            stat = stats[risk_category_id]
            no_gain = stat.consecutive_no_gain
            if risk_category_id in submitted_risks:
                no_gain = 0 if new_evidence else no_gain + 1
            elif new_evidence:
                no_gain = 0
            cooldown = after.cooldown_until_observation
            if reactivation or (
                risk_category_id in submitted_risks and new_evidence
            ):
                cooldown = None
            elif (
                risk_category_id in submitted_risks
                and no_gain >= self.scheduler_policy.cooldown_after_no_gain
            ):
                cooldown = (
                    feedback.saturation.observations
                    + self.scheduler_policy.cooldown_observations
                )
            recovery = self._recovery_status(
                has_compositions=bool(after.compatible_compositions),
                next_depth=after.next_execution_target_depth,
                cooldown_until_observation=cooldown,
                observations=feedback.saturation.observations,
            )
            if (
                no_gain != stat.consecutive_no_gain
                or cooldown != after.cooldown_until_observation
                or recovery != after.recovery_status
            ):
                stats[risk_category_id] = AdaptiveFrontierStats.model_validate(
                    {
                        **stat.model_dump(mode="python"),
                        "consecutive_no_gain": no_gain,
                    }
                )
                updated_frontier = RiskFrontierEntry.model_validate(
                    {
                        **after.model_dump(mode="python", exclude={"content_digest"}),
                        "cooldown_until_observation": cooldown,
                        "recovery_status": recovery,
                        "revision": after.revision + 1,
                    }
                )
                self._replace_frontier(connection, updated_frontier)
        updated_scheduler = OfficeAdaptiveSchedulerSnapshot(
            campaign_id=scheduler.campaign_id,
            policy_version=scheduler.policy_version,
            policy_digest=scheduler.policy_digest,
            next_decision_index=scheduler.next_decision_index,
            latest_feedback_digest=feedback.report_digest,
            awaiting_feedback_after_digest=None,
            frontier_stats=tuple(stats[key] for key in sorted(stats)),
            active_decision=scheduler.active_decision,
            decisions=scheduler.decisions,
        )
        self._replace_scheduler(connection, updated_scheduler)
        if not submitted_episode_count:
            return None
        completion = self._load_completion(connection)
        return OfficeCompletionObservation(
            ordinal=len(completion.observations),
            previous_feedback_digest=previous_feedback.report_digest,
            feedback_digest=feedback.report_digest,
            submitted_episode_count=submitted_episode_count,
            behavior_gain=(
                feedback.observed_behavior_paths
                > previous_feedback.observed_behavior_paths
            ),
            execution_risk_depth_gain=any_execution_risk_gain,
            path_risk_gain=any_path_risk_gain,
        )

    @staticmethod
    def _feedback_application_digest(
        feedback_digest: str, hints: Sequence[RiskFrontierHints]
    ) -> str:
        canonical_hints = sorted(hints, key=lambda hint: hint.risk_category_id)
        return _state_digest(
            {
                "feedback_digest": feedback_digest,
                "hints": [
                    hint.model_dump(mode="python") for hint in canonical_hints
                ],
            }
        )

    @staticmethod
    def _replace_objective(
        connection: sqlite3.Connection, entry: ObjectiveExposureEntry
    ) -> None:
        connection.execute(
            "UPDATE objective_exposures SET entry_digest = ?, entry_json = ? "
            "WHERE objective_id = ?",
            (entry.content_digest, entry.model_dump_json(), entry.objective_id),
        )

    @staticmethod
    def _replace_frontier(
        connection: sqlite3.Connection, entry: RiskFrontierEntry
    ) -> None:
        connection.execute(
            "UPDATE risk_frontiers SET entry_digest = ?, entry_json = ? WHERE risk_category_id = ?",
            (entry.content_digest, entry.model_dump_json(), entry.risk_category_id),
        )

    @staticmethod
    def _replace_baseline_item(
        connection: sqlite3.Connection, item: OfficeBaselineItem
    ) -> None:
        cursor = connection.execute(
            "UPDATE baseline_items SET item_digest = ?, item_json = ? "
            "WHERE baseline_item_id = ? AND ordinal = ?",
            (
                item.content_digest,
                item.model_dump_json(),
                item.baseline_item_id,
                item.ordinal,
            ),
        )
        if cursor.rowcount != 1:
            raise OfficeCampaignStateError("baseline item update lost its locked row")

    @staticmethod
    def _advance_revision(
        connection: sqlite3.Connection, *, feedback_digest: str | None = None
    ) -> tuple[int, str]:
        state = connection.execute(
            "SELECT revision, current_feedback_digest FROM campaign_state WHERE singleton = 1"
        ).fetchone()
        if state is None:
            raise OfficeCampaignStateError("office campaign state is not initialized")
        revision = int(state["revision"]) + 1
        selected_feedback = feedback_digest or str(state["current_feedback_digest"])
        connection.execute(
            "UPDATE campaign_state SET revision = ?, current_feedback_digest = ? "
            "WHERE singleton = 1",
            (revision, selected_feedback),
        )
        return revision, selected_feedback

    def _validate_metadata(self) -> None:
        metadata = {
            "schema_version": OFFICE_CAMPAIGN_STATE_SCHEMA_VERSION,
            "campaign_id": self.manifest.campaign_id,
            "manifest_digest": sha256_digest(self.manifest),
            "catalog_manifest_digest": self.manifest.scenario_catalogs.content_digest,
            "taxonomy_version": self.manifest.taxonomy_version,
            "taxonomy_digest": self.manifest.taxonomy_digest,
            "risk_mapping_version": OFFICE_RISK_MAPPING_VERSION,
            "risk_mapping_digest": OFFICE_RISK_MAPPING_DIGEST,
            "risk_scope_version": self.manifest.risk_scope_version,
            "risk_scope_digest": self.manifest.risk_scope_digest,
            "agent_digest": sha256_digest(self.agent),
            "budget_digest": sha256_digest(self.budget),
            "baseline_policy_version": OFFICE_BASELINE_POLICY_VERSION,
            "baseline_plan_digest": self.baseline_plan.content_digest,
            "scheduler_policy_version": self.scheduler_policy.policy_version,
            "scheduler_policy_digest": self.scheduler_policy.content_digest,
            "completion_policy_version": self.completion_policy.policy_version,
            "completion_policy_digest": self.completion_policy.content_digest,
        }
        with self.transaction() as connection:
            for key, value in metadata.items():
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key = ?", (key,)
                ).fetchone()
                if row is not None and row["value"] != value:
                    raise OfficeCampaignStateError(
                        f"office campaign state metadata mismatch: {key}"
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
                    (key, value),
                )

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS campaign_state(
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                revision INTEGER NOT NULL,
                current_feedback_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS objective_exposures(
                objective_id TEXT PRIMARY KEY,
                entry_digest TEXT NOT NULL,
                entry_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS risk_frontiers(
                risk_category_id TEXT PRIMARY KEY,
                entry_digest TEXT NOT NULL,
                entry_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS baseline_items(
                baseline_item_id TEXT PRIMARY KEY,
                ordinal INTEGER NOT NULL UNIQUE,
                item_digest TEXT NOT NULL,
                item_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS committed_episodes(
                trajectory_id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL UNIQUE,
                reference_digest TEXT NOT NULL,
                reference_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback_applications(
                feedback_digest TEXT PRIMARY KEY,
                application_digest TEXT NOT NULL,
                feedback_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS adaptive_scheduler(
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                state_digest TEXT NOT NULL,
                state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS campaign_completion(
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                state_digest TEXT NOT NULL,
                state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS adaptive_decisions(
                decision_id TEXT PRIMARY KEY,
                decision_index INTEGER NOT NULL UNIQUE,
                decision_digest TEXT NOT NULL UNIQUE,
                decision_json TEXT NOT NULL,
                result_digest TEXT UNIQUE,
                result_json TEXT
            );
            CREATE TABLE IF NOT EXISTS snapshots(
                revision INTEGER PRIMARY KEY,
                snapshot_digest TEXT NOT NULL UNIQUE,
                snapshot_json TEXT NOT NULL
            );
            """
        )
