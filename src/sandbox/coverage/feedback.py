"""Deterministic path-risk, growth, and saturation feedback for a coverage campaign."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sandbox.coverage.evidence import is_execution_evidenced_hit
from sandbox.coverage.exceptions import CoverageIntegrityError
from sandbox.coverage.models import (
    BehaviorFeatureKind,
    BehaviorProfile,
    CampaignCoverageFeedback,
    CoverageGrowthPoint,
    CoverageResult,
    CoverageSaturationSummary,
    CoverageSnapshot,
    PathRiskHeatmapCell,
    RiskCoverageGap,
    RiskHit,
    RiskStage,
)
from sandbox.coverage.risk_scope import CampaignRiskScopeIndex
from sandbox.coverage.taxonomy import RiskTaxonomyIndex

_PATH_KINDS = frozenset(
    {
        BehaviorFeatureKind.TOOL_UNIGRAM,
        BehaviorFeatureKind.TOOL_BIGRAM,
        BehaviorFeatureKind.TOOL_TRIGRAM,
    }
)
_STAGE_ORDER = {
    RiskStage.INTENT: 1,
    RiskStage.ATTEMPTED: 2,
    RiskStage.BLOCKED: 3,
    RiskStage.REALIZED: 4,
}


@dataclass
class _PathRiskAggregate:
    trajectories: set[str] = field(default_factory=set)
    max_depth: int = 0
    stages: set[RiskStage] = field(default_factory=set)
    improvement_trajectories: set[str] = field(default_factory=set)


class CampaignCoverageFeedbackBuilder:
    def __init__(
        self,
        taxonomy: RiskTaxonomyIndex,
        risk_scope: CampaignRiskScopeIndex,
    ) -> None:
        self.taxonomy = taxonomy
        self.risk_scope = risk_scope

    def build(
        self,
        snapshot: CoverageSnapshot,
        profiles: list[BehaviorProfile],
        results: list[CoverageResult],
        hits: list[RiskHit],
        *,
        include_empty: bool,
    ) -> CampaignCoverageFeedback:
        self._validate_inputs(snapshot, profiles, results, hits)
        growth = self._growth(snapshot, results)
        path_cells, observed_paths = self._path_risk_cells(
            profiles,
            results,
            hits,
            include_empty=include_empty,
        )
        return CampaignCoverageFeedback(
            campaign_id=snapshot.campaign_id,
            taxonomy_version=snapshot.taxonomy_version,
            taxonomy_digest=snapshot.taxonomy_digest,
            risk_mapping_version=snapshot.risk_mapping_version,
            risk_mapping_digest=snapshot.risk_mapping_digest,
            risk_scope_version=snapshot.risk_scope_version,
            risk_scope_digest=self.risk_scope.digest,
            include_empty=include_empty,
            observed_behavior_paths=len(observed_paths),
            path_risk_cells=path_cells,
            risk_gaps=self._risk_gaps(snapshot, hits),
            growth=growth,
            saturation=self._saturation(growth),
        )

    def _validate_inputs(
        self,
        snapshot: CoverageSnapshot,
        profiles: list[BehaviorProfile],
        results: list[CoverageResult],
        hits: list[RiskHit],
    ) -> None:
        if (
            snapshot.taxonomy_version != self.taxonomy.taxonomy_version
            or snapshot.taxonomy_digest != self.taxonomy.digest
        ):
            raise CoverageIntegrityError("coverage feedback taxonomy identity mismatch")
        if snapshot.risk_scope_version != self.risk_scope.scope_version:
            raise CoverageIntegrityError("coverage feedback risk scope identity mismatch")
        leaf_ids = set(self.taxonomy.leaf_ids)
        if set(snapshot.risk_depths) != leaf_ids:
            raise CoverageIntegrityError(
                "coverage feedback requires an overall depth for every taxonomy leaf"
            )
        if set(snapshot.execution_risk_depths) != leaf_ids:
            raise CoverageIntegrityError(
                "coverage feedback requires an execution depth for every taxonomy leaf"
            )

        result_ids = [result.trajectory_id for result in results]
        profile_ids = [profile.trajectory_id for profile in profiles]
        result_id_set = set(result_ids)
        if len(result_ids) != len(result_id_set) or len(profile_ids) != len(set(profile_ids)):
            raise CoverageIntegrityError("coverage feedback trajectory identities are duplicated")
        if result_id_set != set(profile_ids):
            raise CoverageIntegrityError(
                "coverage feedback results and behavior profiles do not align"
            )
        if len(results) != snapshot.total_trajectories:
            raise CoverageIntegrityError(
                "coverage feedback result count does not match the cumulative snapshot"
            )
        if any(hit.trajectory_id not in result_id_set for hit in hits):
            raise CoverageIntegrityError("coverage feedback contains an orphan risk hit")

        profiles_by_id = {profile.trajectory_id: profile for profile in profiles}
        hits_by_trajectory: dict[str, list[RiskHit]] = defaultdict(list)
        for hit in hits:
            hits_by_trajectory[hit.trajectory_id].append(hit)
        for result in results:
            profile = profiles_by_id[result.trajectory_id]
            if profile.execution_id != result.execution_id:
                raise CoverageIntegrityError(
                    "coverage feedback profile and result execution identities do not align"
                )
            if (
                result.behavior_profile_hash != profile.profile_hash
                or result.behavior_features_total != profile.feature_count
            ):
                raise CoverageIntegrityError(
                    "coverage feedback result does not match its behavior profile"
                )
            trajectory_hits = hits_by_trajectory[result.trajectory_id]
            if any(hit.execution_id != result.execution_id for hit in trajectory_hits):
                raise CoverageIntegrityError(
                    "coverage feedback risk hit execution identity mismatch"
                )
            if trajectory_hits != result.risk_hits:
                raise CoverageIntegrityError(
                    "coverage feedback persisted risk hits do not match the result"
                )

        expected_mapping = (
            (snapshot.risk_mapping_version, snapshot.risk_mapping_digest)
            if snapshot.risk_mapping_version is not None
            else None
        )
        for hit in hits:
            observed_mapping = (
                (hit.mapping_version, hit.mapping_digest)
                if hit.mapping_version is not None
                else None
            )
            if observed_mapping != expected_mapping:
                raise CoverageIntegrityError(
                    "coverage feedback risk hit mapping identity mismatch"
                )

    def _growth(
        self,
        snapshot: CoverageSnapshot,
        results: list[CoverageResult],
    ) -> list[CoverageGrowthPoint]:
        cumulative_behavior = 0
        execution_depths: dict[str, int] = {}
        points: list[CoverageGrowthPoint] = []

        for index, result in enumerate(results, start=1):
            if result.already_evaluated:
                raise CoverageIntegrityError(
                    "persisted coverage result cannot be marked already_evaluated"
                )
            cumulative_behavior += result.new_behavior_count
            if result.cumulative_behavior_count != cumulative_behavior:
                raise CoverageIntegrityError(
                    "coverage behavior growth is not contiguous in created order"
                )

            change_ids = [
                change.category_id for change in result.execution_risk_depth_changes
            ]
            if len(change_ids) != len(set(change_ids)):
                raise CoverageIntegrityError(
                    "coverage execution risk growth repeats a category"
                )
            expected_improvements = {
                change.category_id: change.current_depth
                for change in result.execution_risk_depth_changes
            }
            if expected_improvements != result.execution_improved_risk_depths:
                raise CoverageIntegrityError(
                    "coverage execution risk improvements conflict with depth changes"
                )
            expected_new_count = sum(
                change.previous_depth == 0
                for change in result.execution_risk_depth_changes
            )
            if result.execution_new_risk_count != expected_new_count:
                raise CoverageIntegrityError(
                    "coverage new execution risk count conflicts with depth changes"
                )

            depth_gain = 0
            for change in result.execution_risk_depth_changes:
                previous = execution_depths.get(change.category_id, 0)
                if change.previous_depth != previous:
                    raise CoverageIntegrityError(
                        "coverage execution risk growth is not contiguous in created order"
                    )
                execution_depths[change.category_id] = change.current_depth
                depth_gain += change.depth_gain

            points.append(
                CoverageGrowthPoint(
                    observation_index=index,
                    trajectory_id=result.trajectory_id,
                    new_behavior_count=result.new_behavior_count,
                    cumulative_behavior_count=result.cumulative_behavior_count,
                    new_execution_risk_count=result.execution_new_risk_count,
                    execution_risk_depth_gain=depth_gain,
                    cumulative_execution_risk_count=len(execution_depths),
                    cumulative_execution_risk_depth_sum=sum(execution_depths.values()),
                    has_coverage_gain=(result.new_behavior_count > 0 or depth_gain > 0),
                )
            )

        expected_execution_depths = {
            category_id: depth
            for category_id, depth in snapshot.execution_risk_depths.items()
            if depth > 0
        }
        if execution_depths != expected_execution_depths:
            raise CoverageIntegrityError(
                "coverage growth does not reconstruct cumulative execution risk depths"
            )
        if cumulative_behavior != snapshot.total_features:
            raise CoverageIntegrityError(
                "coverage growth does not reconstruct cumulative behavior features"
            )
        return points

    def _path_risk_cells(
        self,
        profiles: list[BehaviorProfile],
        results: list[CoverageResult],
        hits: list[RiskHit],
        *,
        include_empty: bool,
    ) -> tuple[list[PathRiskHeatmapCell], set[tuple[BehaviorFeatureKind, str]]]:
        observed_paths = {
            (feature.kind, feature.value)
            for profile in profiles
            for feature in profile.features
            if feature.kind in _PATH_KINDS
        }
        paths_by_trajectory = {
            profile.trajectory_id: {
                (feature.kind, feature.value)
                for feature in profile.features
                if feature.kind in _PATH_KINDS
            }
            for profile in profiles
        }
        hits_by_trajectory_category: dict[tuple[str, str], list[RiskHit]] = defaultdict(list)
        for hit in hits:
            hits_by_trajectory_category[(hit.trajectory_id, hit.category_id)].append(hit)

        aggregates: dict[
            tuple[BehaviorFeatureKind, str, str], _PathRiskAggregate
        ] = defaultdict(_PathRiskAggregate)
        for result in results:
            verified_categories = set(result.execution_verified_risk_categories)
            for link in result.behavior_risk_links:
                if link.risk_category_id not in verified_categories:
                    raise CoverageIntegrityError(
                        "path-risk link is not backed by an execution-verified risk"
                    )
                if link.behavior_kind not in _PATH_KINDS:
                    continue
                path = (link.behavior_kind, link.behavior_value)
                if path not in paths_by_trajectory[result.trajectory_id]:
                    raise CoverageIntegrityError(
                        "path-risk link references a behavior path from another trajectory"
                    )
                stages = self._link_stages(
                    link.risk_evidence_sequences,
                    hits_by_trajectory_category[
                        (result.trajectory_id, link.risk_category_id)
                    ],
                )
                if not stages:
                    raise CoverageIntegrityError(
                        "path-risk link cannot be reconstructed from risk evidence"
                    )
                key = (*path, link.risk_category_id)
                aggregate = aggregates[key]
                aggregate.trajectories.add(result.trajectory_id)
                aggregate.max_depth = max(aggregate.max_depth, link.risk_depth)
                aggregate.stages.update(stages)
                if link.risk_depth_improved:
                    aggregate.improvement_trajectories.add(result.trajectory_id)

        linked_categories = {key[2] for key in aggregates}
        category_ids = set(self.risk_scope.category_ids) | linked_categories
        keys = (
            {
                (*path, category_id)
                for path in observed_paths
                for category_id in category_ids
            }
            if include_empty
            else set(aggregates)
        )
        cells: list[PathRiskHeatmapCell] = []
        scope_ids = set(self.risk_scope.category_ids)
        for kind, path, category_id in sorted(
            keys,
            key=lambda item: (item[0].value, item[1], item[2]),
        ):
            category = self.taxonomy.get(category_id)
            aggregate = aggregates.get((kind, path, category_id), _PathRiskAggregate())
            cells.append(
                PathRiskHeatmapCell(
                    behavior_kind=kind,
                    behavior_path=path,
                    risk_category_id=category_id,
                    risk_category_label=category.label,
                    in_scope=category_id in scope_ids,
                    trajectory_count=len(aggregate.trajectories),
                    max_depth=aggregate.max_depth,
                    stages=self._sorted_stages(aggregate.stages),
                    depth_improvement_count=len(aggregate.improvement_trajectories),
                )
            )
        return cells, observed_paths

    @staticmethod
    def _link_stages(sequences: list[int], hits: list[RiskHit]) -> set[RiskStage]:
        link_sequences = set(sequences)
        stages: set[RiskStage] = set()
        for hit in hits:
            hit_sequences = {
                reference.event_sequence
                for reference in hit.evidence
                if reference.source == "trace_event"
                and reference.event_sequence is not None
            }
            if link_sequences & hit_sequences and hit.stage is not None:
                stages.add(hit.stage)
        return stages

    def _risk_gaps(
        self,
        snapshot: CoverageSnapshot,
        hits: list[RiskHit],
    ) -> list[RiskCoverageGap]:
        observed_stages: dict[str, set[RiskStage]] = defaultdict(set)
        execution_stages: dict[str, set[RiskStage]] = defaultdict(set)
        for hit in hits:
            if hit.stage is None:
                continue
            observed_stages[hit.category_id].add(hit.stage)
            if is_execution_evidenced_hit(hit):
                execution_stages[hit.category_id].add(hit.stage)

        gaps: list[RiskCoverageGap] = []
        for category_id in self.risk_scope.category_ids:
            max_reachable = self.risk_scope.max_reachable_depth(category_id)
            assert max_reachable is not None
            observed = snapshot.risk_depths[category_id]
            execution_observed = snapshot.execution_risk_depths[category_id]
            next_execution = None
            if max_reachable >= 2 and execution_observed < max_reachable:
                next_execution = max(2, execution_observed + 1)
            gaps.append(
                RiskCoverageGap(
                    risk_category_id=category_id,
                    risk_category_label=self.taxonomy.get(category_id).label,
                    observed_depth=observed,
                    observed_execution_depth=execution_observed,
                    max_reachable_depth=max_reachable,
                    next_execution_target_depth=next_execution,
                    observed_stages=self._sorted_stages(observed_stages[category_id]),
                    execution_stages=self._sorted_stages(execution_stages[category_id]),
                    scope_exceeded=observed > max_reachable,
                )
            )
        return gaps

    @staticmethod
    def _saturation(growth: list[CoverageGrowthPoint]) -> CoverageSaturationSummary:
        def streaks(gains: list[bool]) -> tuple[int, int]:
            current = 0
            maximum = 0
            for gained in gains:
                current = 0 if gained else current + 1
                maximum = max(maximum, current)
            return current, maximum

        behavior = streaks([point.new_behavior_count > 0 for point in growth])
        risk = streaks([point.execution_risk_depth_gain > 0 for point in growth])
        combined = streaks([point.has_coverage_gain for point in growth])
        return CoverageSaturationSummary(
            observations=len(growth),
            trailing_without_behavior_gain=behavior[0],
            max_without_behavior_gain=behavior[1],
            trailing_without_execution_risk_gain=risk[0],
            max_without_execution_risk_gain=risk[1],
            trailing_without_any_gain=combined[0],
            max_without_any_gain=combined[1],
        )

    @staticmethod
    def _sorted_stages(stages: set[RiskStage]) -> list[RiskStage]:
        return sorted(stages, key=_STAGE_ORDER.__getitem__)
