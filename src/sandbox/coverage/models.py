"""Versioned contracts for behavior and risk coverage."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sandbox.protocol import TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.models import TestCase as ScenarioTestCase


class CoverageContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"


class OfficeToolExecutionEvidence(CoverageContract):
    call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=128)
    capability_id: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any]
    arguments_valid: bool = True
    allowed: bool
    authorized: bool | None = None
    outcome: str = Field(min_length=1, max_length=128)
    result: dict[str, Any]
    result_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    before_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    after_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    call_sequence: int = Field(ge=0)
    result_sequence: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_result_digest(self) -> OfficeToolExecutionEvidence:
        if self.result_digest != sha256_digest(self.result):
            raise ValueError("office tool evidence result_digest does not match result")
        if self.result_sequence <= self.call_sequence:
            raise ValueError("office tool result must follow its call")
        if self.result.get("allowed") is not self.allowed:
            raise ValueError("office tool evidence allowed fact does not match result")
        if self.result.get("outcome") != self.outcome:
            raise ValueError("office tool evidence outcome does not match result")
        rejection_kind = self.result.get("rejection_kind")
        if self.allowed:
            if self.authorized is None:
                raise ValueError("allowed office actions require an authorization fact")
            if self.result.get("authorized") is not self.authorized:
                raise ValueError("office tool evidence authorization does not match result")
            if rejection_kind is not None:
                raise ValueError("allowed office actions cannot carry a rejection kind")
        elif rejection_kind == "policy":
            if not self.arguments_valid or self.authorized is not False:
                raise ValueError(
                    "policy-blocked office actions require valid, unauthorized arguments"
                )
            if self.outcome != "blocked" or self.result.get("output") is not None:
                raise ValueError("policy-blocked office actions cannot produce a result")
            if self.before_state_digest != self.after_state_digest:
                raise ValueError("policy-blocked office actions cannot change business state")
        elif rejection_kind == "provenance":
            if not self.arguments_valid or self.authorized is None:
                raise ValueError(
                    "provenance-blocked office actions require valid arguments and authorization"
                )
            if self.result.get("authorized") is not self.authorized:
                raise ValueError(
                    "provenance-blocked office authorization does not match result"
                )
            if self.outcome != "blocked" or self.result.get("output") is not None:
                raise ValueError("provenance-blocked office actions cannot produce a result")
            if self.before_state_digest != self.after_state_digest:
                raise ValueError(
                    "provenance-blocked office actions cannot change business state"
                )
        elif self.authorized is not None:
            raise ValueError("non-policy rejections cannot assert an authorization result")
        return self


class OfficeBaselineActionEvidence(CoverageContract):
    action_sequence: int = Field(ge=1)
    tool_name: str = Field(min_length=1, max_length=128)
    capability_id: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any]
    authorized: bool
    outcome: str = Field(min_length=1, max_length=128)
    before_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    after_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class OfficeExecutionEvidence(CoverageContract):
    kind: Literal["office_episode"] = "office_episode"
    case_id: str = Field(min_length=1, max_length=128)
    scenario_id: str = Field(min_length=1, max_length=128)
    test_case: ScenarioTestCase
    test_case_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    initialization_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    scenario_initial_state: dict[str, Any]
    scenario_initial_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    episode_initial_state: dict[str, Any]
    episode_initial_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    final_state: dict[str, Any]
    final_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    baseline_actions: list[OfficeBaselineActionEvidence] = Field(default_factory=list)
    actions: list[OfficeToolExecutionEvidence] = Field(default_factory=list)
    baseline_action_count: int = Field(default=0, ge=0)
    total_action_count: int = Field(default=0, ge=0)
    unauthorized_action_count: int = Field(default=0, ge=0)
    normal_task_completed: bool
    attack_side_effect_observed: bool | None = None
    termination: Literal["succeeded", "timed_out", "cancelled", "error"]
    evidence_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_execution_evidence(self) -> OfficeExecutionEvidence:
        self.test_case.assert_integrity()
        if self.case_id != self.test_case.case_id:
            raise ValueError("office evidence case_id does not match frozen TestCase")
        if self.scenario_id != self.test_case.scenario.template_id:
            raise ValueError("office evidence scenario_id does not match frozen TestCase")
        if self.test_case_digest != self.test_case.content_digest:
            raise ValueError("office evidence test_case_digest does not match frozen TestCase")
        for state, digest, label in (
            (
                self.scenario_initial_state,
                self.scenario_initial_state_digest,
                "scenario_initial_state",
            ),
            (
                self.episode_initial_state,
                self.episode_initial_state_digest,
                "episode_initial_state",
            ),
            (self.final_state, self.final_state_digest, "final_state"),
        ):
            if sha256_digest(state) != digest:
                raise ValueError(f"office evidence {label}_digest does not match state")
        call_ids = [action.call_id for action in self.actions]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("office evidence action call_id values must be unique")
        sequences = [action.call_sequence for action in self.actions]
        if sequences != sorted(sequences):
            raise ValueError("office evidence actions must follow trace order")
        baseline_sequences = [action.action_sequence for action in self.baseline_actions]
        if baseline_sequences != list(range(1, len(self.baseline_actions) + 1)):
            raise ValueError("office evidence baseline actions must be contiguous")
        if self.baseline_action_count != len(self.baseline_actions):
            raise ValueError("office evidence baseline action count does not match summaries")
        if self.total_action_count < self.baseline_action_count:
            raise ValueError("office evidence total actions cannot precede the baseline")
        recorded_actions = sum(
            action.allowed
            or action.result.get("rejection_kind") in {"policy", "provenance"}
            for action in self.actions
        )
        if self.total_action_count != self.baseline_action_count + recorded_actions:
            raise ValueError("office evidence action counts do not match executed actions")
        if self.unauthorized_action_count > self.total_action_count:
            raise ValueError("office evidence unauthorized actions exceed total actions")
        observed_unauthorized = sum(
            not action.authorized for action in self.baseline_actions
        ) + sum(action.authorized is False for action in self.actions)
        if self.unauthorized_action_count != observed_unauthorized:
            raise ValueError("office evidence unauthorized action count does not match actions")

        expected_state_digest = self.scenario_initial_state_digest
        for action in self.baseline_actions:
            if action.before_state_digest != expected_state_digest:
                raise ValueError("office evidence baseline state chain is discontinuous")
            expected_state_digest = action.after_state_digest
        if expected_state_digest != self.episode_initial_state_digest:
            raise ValueError("office evidence baseline does not reach episode initial state")
        for action in self.actions:
            if action.before_state_digest != expected_state_digest:
                raise ValueError("office evidence trajectory state chain is discontinuous")
            expected_state_digest = action.after_state_digest
        if expected_state_digest != self.final_state_digest:
            raise ValueError("office evidence actions do not reach final state")
        expected = sha256_digest(
            self.model_dump(mode="json", exclude={"evidence_digest"})
        )
        if self.evidence_digest is not None and self.evidence_digest != expected:
            raise ValueError("office evidence digest does not match execution facts")
        self.evidence_digest = expected
        return self

    def assert_integrity(self) -> None:
        restored = type(self).model_validate(self.model_dump(mode="python"))
        if restored.evidence_digest != self.evidence_digest:
            raise ValueError("office execution evidence no longer matches its digest")


class CoverageInput(CoverageContract):
    trajectory_id: str = Field(min_length=1, max_length=256)
    execution_id: str = Field(min_length=1, max_length=256)
    source_kind: Literal[
        "week1",
        "recording",
        "strict_replay",
        "live_replay",
        "fork",
        "office_episode",
        "raw",
    ] = "raw"
    events: list[TraceEvent]
    prompt: str | None = None
    final_answer: str | None = None
    input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    manifest_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    scenario_evidence: OfficeExecutionEvidence | None = None

    @model_validator(mode="after")
    def validate_events(self) -> CoverageInput:
        if not self.events:
            raise ValueError("coverage input requires at least one event")
        for expected, event in enumerate(self.events):
            if event.execution_id != self.execution_id:
                raise ValueError("event execution_id does not match coverage input")
            if event.sequence != expected:
                raise ValueError("coverage input events must be contiguous")
        if self.events[-1].event_type not in {
            "execution_finished",
            "execution_timed_out",
            "execution_cancelled",
            "execution_error",
        }:
            raise ValueError("coverage input must end with a terminal execution event")
        if self.scenario_evidence is not None:
            self.scenario_evidence.assert_integrity()
        return self


class BehaviorFeatureKind(StrEnum):
    TOOL_UNIGRAM = "tool_unigram"
    TOOL_BIGRAM = "tool_bigram"
    TOOL_TRIGRAM = "tool_trigram"
    NODE_EDGE = "node_edge"
    TOOL_RESULT = "tool_result"
    PARAM_SHAPE = "param_shape"
    PARAM_SENSITIVITY = "param_sensitivity"
    AUTHORIZATION = "authorization"
    AUTHORIZATION_TRANSITION = "authorization_transition"
    STATE_CHANGE = "state_change"
    SECURITY_TRANSITION = "security_transition"
    TERMINATION = "termination"


class BehaviorFeature(CoverageContract):
    kind: BehaviorFeatureKind
    value: str = Field(min_length=1, max_length=512)
    source_sequences: list[int] = Field(default_factory=list)
    frequency: int = Field(default=1, ge=1)


class BehaviorProfile(CoverageContract):
    trajectory_id: str
    execution_id: str
    features: list[BehaviorFeature] = Field(default_factory=list)
    profile_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    feature_count: int = Field(default=0, ge=0)


class EvidenceRule(CoverageContract):
    tool_name: str | None = None
    argument_patterns: dict[str, str] = Field(default_factory=dict)
    security_risk_category: str | None = None
    result_risk_category: str | None = None
    allowed: bool | None = None
    outcomes: list[str] = Field(default_factory=list)
    termination: str | None = None


class RiskCategory(CoverageContract):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    description: str = ""
    report_weight: float | None = Field(default=None, ge=0.0)
    keywords: list[str] = Field(default_factory=list)
    evidence_rules: list[EvidenceRule] = Field(default_factory=list)
    impact_rules: list[EvidenceRule] = Field(default_factory=list)
    children: list[RiskCategory] = Field(default_factory=list)


class RiskTaxonomy(CoverageContract):
    taxonomy_version: str = Field(min_length=1, max_length=128)
    categories: list[RiskCategory]


class CampaignRiskWeights(CoverageContract):
    campaign_id: str
    taxonomy_version: str
    schedule_weights: dict[str, float] = Field(default_factory=dict)


class RiskReachability(CoverageContract):
    max_reachable_depth: int = Field(ge=1, le=3)
    rationale: str = ""


class CampaignRiskScope(CoverageContract):
    scope_version: str = Field(min_length=1, max_length=128)
    taxonomy_version: str = Field(min_length=1, max_length=128)
    categories: dict[str, RiskReachability] = Field(default_factory=dict)


class EvidenceReference(CoverageContract):
    source: Literal[
        "trace_event",
        "prompt",
        "final_answer",
        "manifest",
        "office_execution",
    ]
    event_sequence: int | None = Field(default=None, ge=0)
    artifact_digest: str | None = None
    excerpt_digest: str | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> EvidenceReference:
        if self.source == "trace_event" and self.event_sequence is None:
            raise ValueError("trace event evidence requires event_sequence")
        if self.source != "trace_event" and not (self.artifact_digest or self.excerpt_digest):
            raise ValueError("non-event evidence requires a digest")
        return self


class RiskStage(StrEnum):
    INTENT = "intent"
    ATTEMPTED = "attempted"
    BLOCKED = "blocked"
    REALIZED = "realized"


class RiskHit(CoverageContract):
    trajectory_id: str
    execution_id: str
    category_id: str
    depth: int = Field(ge=1, le=3)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    recognizer: Literal[
        "rule",
        "pattern",
        "keyword",
        "classifier",
        "impact",
        "office",
    ]
    stage: RiskStage | None = None
    mapping_version: str | None = None
    mapping_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    expectation: Literal["expected", "unexpected"] | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def validate_stage_depth(self) -> RiskHit:
        expected = {
            1: RiskStage.INTENT,
            2: RiskStage.ATTEMPTED,
            3: RiskStage.REALIZED,
        }
        if self.stage is None:
            self.stage = expected[self.depth]
        elif self.stage == RiskStage.INTENT and self.depth != 1:
            raise ValueError("risk intent must use depth 1")
        elif self.stage in {RiskStage.ATTEMPTED, RiskStage.BLOCKED} and self.depth != 2:
            raise ValueError("attempted or blocked risk must use depth 2")
        elif self.stage == RiskStage.REALIZED and self.depth != 3:
            raise ValueError("realized risk must use depth 3")
        if (self.mapping_version is None) != (self.mapping_digest is None):
            raise ValueError("risk mapping version and digest must be provided together")
        return self


class RiskDepthChange(CoverageContract):
    category_id: str
    previous_depth: int = Field(ge=0, le=3)
    current_depth: int = Field(ge=1, le=3)
    depth_gain: int = Field(ge=1, le=3)

    @model_validator(mode="after")
    def validate_gain(self) -> RiskDepthChange:
        if self.current_depth - self.previous_depth != self.depth_gain:
            raise ValueError("depth_gain must equal current_depth - previous_depth")
        return self


class BehaviorRiskLink(CoverageContract):
    relation: Literal["same_tool_window"] = "same_tool_window"
    tool_name: str
    tool_call_sequence: int = Field(ge=0)
    behavior_kind: BehaviorFeatureKind
    behavior_value: str
    behavior_source_sequences: list[int] = Field(default_factory=list)
    risk_category_id: str
    risk_depth: int = Field(ge=1, le=3)
    risk_recognizers: list[
        Literal["rule", "pattern", "keyword", "classifier", "impact", "office"]
    ] = Field(default_factory=list)
    risk_evidence_sequences: list[int] = Field(default_factory=list)
    behavior_new: bool = False
    risk_new: bool = False
    risk_depth_improved: bool = False
    novelty_class: Literal[
        "both_new",
        "behavior_new",
        "risk_new",
        "known_pair",
    ]


class CoverageResult(CoverageContract):
    trajectory_id: str
    execution_id: str
    input_digest: str
    behavior_profile_hash: str
    behavior_features_total: int = Field(default=0, ge=0)
    new_behavior_features: list[str] = Field(default_factory=list)
    new_behavior_count: int = Field(default=0, ge=0)
    cumulative_behavior_count: int = Field(default=0, ge=0)
    behavior_growth_rate: float = Field(default=0.0, ge=0.0)
    risk_hits: list[RiskHit] = Field(default_factory=list)
    execution_verified_risk_categories: list[str] = Field(default_factory=list)
    execution_verified_risk_depths: dict[str, int] = Field(default_factory=dict)
    execution_new_risk_categories: list[str] = Field(default_factory=list)
    execution_new_risk_count: int = Field(default=0, ge=0)
    execution_improved_risk_depths: dict[str, int] = Field(default_factory=dict)
    execution_risk_depth_changes: list[RiskDepthChange] = Field(default_factory=list)
    execution_risk_seed_delta: float = Field(default=0.0, ge=0.0, le=1.0)
    execution_combined_delta: float = Field(default=0.0, ge=0.0)
    new_risk_categories: list[str] = Field(default_factory=list)
    new_risk_count: int = Field(default=0, ge=0)
    improved_risk_depths: dict[str, int] = Field(default_factory=dict)
    risk_depth_changes: list[RiskDepthChange] = Field(default_factory=list)
    risk_progress_delta: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_seed_delta: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_scope_exceeded: list[str] = Field(default_factory=list)
    behavior_risk_links: list[BehaviorRiskLink] = Field(default_factory=list)
    cumulative_risk_count: int = Field(default=0, ge=0)
    intent_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    behavior_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    impact_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    behavior_delta: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_delta: float = Field(default=0.0, ge=0.0, le=1.0)
    combined_delta: float = Field(default=0.0, ge=0.0)
    already_evaluated: bool = False


class HeatmapCell(CoverageContract):
    behavior_cluster_id: str
    behavior_cluster_label: str
    risk_category_id: str
    trajectory_count: int = Field(default=0, ge=0)
    max_depth: int = Field(default=0, ge=0, le=3)
    new_coverage_count: int = Field(default=0, ge=0)
    confirmed_vulnerabilities: int = Field(default=0, ge=0)


class PrettyHeatmapRow(CoverageContract):
    behavior_cluster_id: str
    label: str
    trajectory_ids: list[str] = Field(default_factory=list)


class PrettyHeatmapColumn(CoverageContract):
    risk_category_id: str
    label: str


class PrettyHeatmapCell(CoverageContract):
    behavior_cluster_id: str
    behavior_cluster_label: str
    risk_category_id: str
    risk_category_label: str
    trajectory_count: int = Field(default=0, ge=0)
    max_depth: int = Field(default=0, ge=0, le=3)
    new_coverage_count: int = Field(default=0, ge=0)
    confirmed_vulnerabilities: int = Field(default=0, ge=0)


class PrettyHeatmapReport(CoverageContract):
    campaign_id: str
    taxonomy_version: str
    rows: list[PrettyHeatmapRow] = Field(default_factory=list)
    columns: list[PrettyHeatmapColumn] = Field(default_factory=list)
    cells: list[PrettyHeatmapCell] = Field(default_factory=list)


class PathRiskHeatmapCell(CoverageContract):
    behavior_kind: BehaviorFeatureKind
    behavior_path: str = Field(min_length=1, max_length=512)
    risk_category_id: str = Field(min_length=1, max_length=128)
    risk_category_label: str = Field(min_length=1, max_length=256)
    in_scope: bool
    trajectory_count: int = Field(default=0, ge=0)
    max_depth: int = Field(default=0, ge=0, le=3)
    stages: list[RiskStage] = Field(default_factory=list)
    depth_improvement_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_path_cell(self) -> PathRiskHeatmapCell:
        path_kinds = {
            BehaviorFeatureKind.TOOL_UNIGRAM,
            BehaviorFeatureKind.TOOL_BIGRAM,
            BehaviorFeatureKind.TOOL_TRIGRAM,
        }
        if self.behavior_kind not in path_kinds:
            raise ValueError("path-risk heatmap only accepts tool path features")
        if self.max_depth == 0 and (
            self.trajectory_count or self.stages or self.depth_improvement_count
        ):
            raise ValueError("empty path-risk cells cannot contain observed evidence")
        if self.max_depth > 0 and self.trajectory_count == 0:
            raise ValueError("observed path-risk cells require a trajectory")
        return self


class CoverageGrowthPoint(CoverageContract):
    observation_index: int = Field(ge=1)
    observation_unit: Literal["trajectory"] = "trajectory"
    trajectory_id: str = Field(min_length=1, max_length=256)
    new_behavior_count: int = Field(ge=0)
    cumulative_behavior_count: int = Field(ge=0)
    new_execution_risk_count: int = Field(ge=0)
    execution_risk_depth_gain: int = Field(ge=0)
    cumulative_execution_risk_count: int = Field(ge=0)
    cumulative_execution_risk_depth_sum: int = Field(ge=0)
    has_coverage_gain: bool


class CoverageSaturationSummary(CoverageContract):
    observation_unit: Literal["trajectory"] = "trajectory"
    observations: int = Field(ge=0)
    trailing_without_behavior_gain: int = Field(ge=0)
    max_without_behavior_gain: int = Field(ge=0)
    trailing_without_execution_risk_gain: int = Field(ge=0)
    max_without_execution_risk_gain: int = Field(ge=0)
    trailing_without_any_gain: int = Field(ge=0)
    max_without_any_gain: int = Field(ge=0)


class RiskCoverageGap(CoverageContract):
    risk_category_id: str = Field(min_length=1, max_length=128)
    risk_category_label: str = Field(min_length=1, max_length=256)
    observed_depth: int = Field(ge=0, le=3)
    observed_execution_depth: int = Field(ge=0, le=3)
    max_reachable_depth: int = Field(ge=1, le=3)
    next_execution_target_depth: int | None = Field(default=None, ge=2, le=3)
    observed_stages: list[RiskStage] = Field(default_factory=list)
    execution_stages: list[RiskStage] = Field(default_factory=list)
    scope_exceeded: bool = False

    @model_validator(mode="after")
    def validate_gap(self) -> RiskCoverageGap:
        expected_next = None
        if self.max_reachable_depth >= 2 and (
            self.observed_execution_depth < self.max_reachable_depth
        ):
            expected_next = max(2, self.observed_execution_depth + 1)
        if self.next_execution_target_depth != expected_next:
            raise ValueError(
                "risk coverage next execution target does not match observed depth"
            )
        if self.observed_execution_depth > self.observed_depth:
            raise ValueError("execution risk depth cannot exceed overall risk depth")
        if self.scope_exceeded != (self.observed_depth > self.max_reachable_depth):
            raise ValueError("risk coverage scope_exceeded does not match observed depth")
        return self


class CampaignCoverageFeedback(CoverageContract):
    campaign_id: str = Field(min_length=1, max_length=256)
    taxonomy_version: str = Field(min_length=1, max_length=128)
    taxonomy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    risk_mapping_version: str | None = None
    risk_mapping_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    risk_scope_version: str = Field(min_length=1, max_length=128)
    risk_scope_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    include_empty: bool
    observed_behavior_paths: int = Field(ge=0)
    path_risk_cells: list[PathRiskHeatmapCell] = Field(default_factory=list)
    risk_gaps: list[RiskCoverageGap] = Field(default_factory=list)
    growth: list[CoverageGrowthPoint] = Field(default_factory=list)
    saturation: CoverageSaturationSummary
    report_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_feedback(self) -> CampaignCoverageFeedback:
        if (self.risk_mapping_version is None) != (self.risk_mapping_digest is None):
            raise ValueError("risk mapping version and digest must be provided together")
        expected = sha256_digest(self.model_dump(mode="json", exclude={"report_digest"}))
        if self.report_digest is not None and self.report_digest != expected:
            raise ValueError("campaign coverage feedback digest does not match its contents")
        self.report_digest = expected
        return self


class CoverageSnapshot(CoverageContract):
    campaign_id: str
    taxonomy_version: str
    taxonomy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    risk_mapping_version: str | None = None
    risk_mapping_digest: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    total_trajectories: int = Field(default=0, ge=0)
    total_features: int = Field(default=0, ge=0)
    total_risk_categories: int = Field(default=0, ge=0)
    unique_behavior_profiles: int = Field(default=0, ge=0)
    intent_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    behavior_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    impact_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_depths: dict[str, int] = Field(default_factory=dict)
    execution_risk_depths: dict[str, int] = Field(default_factory=dict)
    risk_scope_version: str
    applicable_risk_categories: int = Field(default=0, ge=0)
    applicable_intent_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    applicable_behavior_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    applicable_impact_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    not_applicable_risk_categories: list[str] = Field(default_factory=list)
    uncovered_intent_categories: list[str] = Field(default_factory=list)
    uncovered_behavior_categories: list[str] = Field(default_factory=list)
    uncovered_impact_categories: list[str] = Field(default_factory=list)
    scope_exceeded_categories: dict[str, int] = Field(default_factory=dict)
    heatmap_data: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mapping_identity(self) -> CoverageSnapshot:
        if (self.risk_mapping_version is None) != (self.risk_mapping_digest is None):
            raise ValueError("risk mapping version and digest must be provided together")
        return self
