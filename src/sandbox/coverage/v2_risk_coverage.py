"""Extract planned Office V2 risk coverage from trusted Oracle facts."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_models import AttackEntryKind
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.oracle_models import (
    EvidenceRef,
    ExposureStage,
    MilestoneOutcome,
    ObjectiveCompletionKind,
)

from .v2_contracts import (
    V2_COVERAGE_CONTRACT_IDENTITY,
    V2EpisodeEligibilityFacts,
    V2ExposureProgress,
    V2MilestoneOutcomeBits,
    V2ObjectiveRiskClassification,
    build_v2_episode_eligibility_facts,
)
from .v2_input import V2CoverageInput
from .v2_risk_catalog import V2_RISK_CATALOG


class V2RiskCoverageExtractionError(ValueError):
    """Trusted Oracle facts do not match the frozen V2 risk denominator."""


def _canonical_evidence(refs: list[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    by_id: dict[str, EvidenceRef] = {}
    for ref in refs:
        existing = by_id.setdefault(ref.evidence_id, ref)
        if existing != ref:
            raise V2RiskCoverageExtractionError("conflicting risk evidence reference")
    return tuple(sorted(by_id.values(), key=lambda item: item.sort_key()))


class V2ExposureCoverage(OfficeV2Contract):
    objective_id: Identifier
    condition_id: Identifier
    entry_kind: AttackEntryKind
    stages: tuple[ExposureStage, ...] = Field(min_length=1)
    progress: V2ExposureProgress
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    coverage_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"coverage_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def stages_and_digest_match(self) -> Self:
        if self.progress.highest_stage is not self.stages[-1]:
            raise ValueError("exposure progress does not match cumulative stages")
        if self.coverage_digest != sha256_digest(self.digest_payload()):
            raise ValueError("exposure coverage digest does not match")
        return self


class V2MilestoneCoverage(OfficeV2Contract):
    objective_id: Identifier
    milestone_id: Identifier
    required: bool
    depends_on: tuple[Identifier, ...] = Field(default_factory=tuple)
    episode_outcome: MilestoneOutcome
    outcome_bits: V2MilestoneOutcomeBits
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    coverage_key_digest: Sha256Digest
    coverage_fact_digest: Sha256Digest

    def key_payload(self) -> dict[str, object]:
        return {
            "risk_catalog_digest": V2_RISK_CATALOG.catalog_digest,
            "objective_id": self.objective_id,
            "milestone_id": self.milestone_id,
        }

    def fact_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"coverage_fact_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def outcome_and_digests_match(self) -> Self:
        if self.outcome_bits != V2MilestoneOutcomeBits.from_episode_outcome(
            self.episode_outcome
        ):
            raise ValueError("milestone outcome bits do not match episode outcome")
        if self.coverage_key_digest != sha256_digest(self.key_payload()):
            raise ValueError("milestone coverage key digest does not match")
        if self.coverage_fact_digest != sha256_digest(self.fact_payload()):
            raise ValueError("milestone coverage fact digest does not match")
        return self


class V2PlannedObjectiveCoverage(OfficeV2Contract):
    objective_id: Identifier
    condition_id: Identifier
    entry_kind: AttackEntryKind
    classification: V2ObjectiveRiskClassification
    exposure: V2ExposureCoverage
    completion_kind: ObjectiveCompletionKind
    milestones: tuple[V2MilestoneCoverage, ...] = Field(min_length=1)
    planned_violation_ids: tuple[Identifier, ...] = Field(default_factory=tuple)
    objective_coverage_digest: Sha256Digest

    @field_validator("milestones")
    @classmethod
    def milestones_are_canonical(
        cls, value: tuple[V2MilestoneCoverage, ...]
    ) -> tuple[V2MilestoneCoverage, ...]:
        return tuple(sorted(value, key=lambda item: item.milestone_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"objective_coverage_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def identity_completion_and_digest_match(self) -> Self:
        if (
            self.classification.objective_id != self.objective_id
            or self.exposure.objective_id != self.objective_id
            or self.exposure.condition_id != self.condition_id
            or self.exposure.entry_kind is not self.entry_kind
        ):
            raise ValueError("planned risk coverage identity does not close")
        realized_required = sum(
            item.required and item.outcome_bits.realized_seen for item in self.milestones
        )
        required = sum(item.required for item in self.milestones)
        expected = (
            ObjectiveCompletionKind.FULL
            if required and realized_required == required
            else ObjectiveCompletionKind.PARTIAL
            if realized_required
            else ObjectiveCompletionKind.NONE
        )
        if self.completion_kind is not expected:
            raise ValueError("planned risk completion does not match milestones")
        if self.objective_coverage_digest != sha256_digest(self.digest_payload()):
            raise ValueError("planned objective coverage digest does not match")
        return self


class V2RiskDenominatorStats(OfficeV2Contract):
    family_total: int = Field(ge=0)
    objective_total: int = Field(ge=0)
    applicable_objective_total: int = Field(ge=0)
    reachable_objective_total: int = Field(ge=0)
    milestone_total: int = Field(ge=0)
    attempted_milestone_count: int = Field(ge=0)
    blocked_milestone_count: int = Field(ge=0)
    realized_milestone_count: int = Field(ge=0)
    none_objective_count: int = Field(ge=0)
    partial_objective_count: int = Field(ge=0)
    full_objective_count: int = Field(ge=0)


class V2PlannedRiskCoverage(OfficeV2Contract):
    coverage_identity_digest: Sha256Digest
    canonical_fact_digest: Sha256Digest
    risk_catalog_digest: Sha256Digest
    objectives: tuple[V2PlannedObjectiveCoverage, ...] = Field(default_factory=tuple)
    denominator: V2RiskDenominatorStats
    eligibility: V2EpisodeEligibilityFacts
    planned_risk_digest: Sha256Digest

    @field_validator("objectives")
    @classmethod
    def objectives_are_canonical(
        cls, value: tuple[V2PlannedObjectiveCoverage, ...]
    ) -> tuple[V2PlannedObjectiveCoverage, ...]:
        return tuple(sorted(value, key=lambda item: item.objective_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"planned_risk_digest"}, exclude_none=False
        )

    @model_validator(mode="after")
    def identity_and_digest_match(self) -> Self:
        if self.coverage_identity_digest != V2_COVERAGE_CONTRACT_IDENTITY.identity_digest:
            raise ValueError("planned risk coverage uses the wrong coverage identity")
        if self.risk_catalog_digest != V2_RISK_CATALOG.catalog_digest:
            raise ValueError("planned risk coverage uses the wrong risk catalog")
        if self.eligibility.canonical_fact_digest != self.canonical_fact_digest:
            raise ValueError("risk coverage and eligibility refer to different facts")
        if self.planned_risk_digest != sha256_digest(self.digest_payload()):
            raise ValueError("planned risk coverage digest does not match")
        return self


def _sealed(model_type: Any, payload: dict[str, object], digest_field: str) -> Any:
    draft = model_type.model_construct(
        **payload,
        **{digest_field: "sha256:" + "0" * 64},
    )
    return model_type(
        **payload,
        **{digest_field: sha256_digest(draft.digest_payload())},
    )


def _milestone_coverage(objective_id: str, fact: Any) -> V2MilestoneCoverage:
    evidence = _canonical_evidence(
        [
            ref
            for evaluation in (
                *fact.attempted_evaluations,
                *fact.blocked_evaluations,
                *fact.realized_evaluations,
            )
            for ref in evaluation.evidence_refs
        ]
    )
    if not evidence:
        raise V2RiskCoverageExtractionError("milestone lacks Oracle evidence")
    payload = {
        "objective_id": objective_id,
        "milestone_id": fact.milestone_id,
        "required": fact.required,
        "depends_on": fact.depends_on,
        "episode_outcome": fact.outcome,
        "outcome_bits": V2MilestoneOutcomeBits.from_episode_outcome(fact.outcome),
        "evidence_refs": evidence,
        "coverage_key_digest": sha256_digest(
            {
                "risk_catalog_digest": V2_RISK_CATALOG.catalog_digest,
                "objective_id": objective_id,
                "milestone_id": fact.milestone_id,
            }
        ),
    }
    draft = V2MilestoneCoverage.model_construct(
        **payload, coverage_fact_digest="sha256:" + "0" * 64
    )
    return V2MilestoneCoverage(
        **payload, coverage_fact_digest=sha256_digest(draft.fact_payload())
    )


def extract_v2_planned_risk_coverage(
    coverage_input: V2CoverageInput,
) -> V2PlannedRiskCoverage:
    if not isinstance(coverage_input, V2CoverageInput):
        raise V2RiskCoverageExtractionError("planned risk coverage requires V2CoverageInput")
    catalog = {item.objective_id: item for item in V2_RISK_CATALOG.objectives}
    violations_by_objective: dict[str, list[str]] = {}
    for violation in coverage_input.oracle_facts.security.violations:
        if violation.planned and violation.objective_id is not None:
            violations_by_objective.setdefault(violation.objective_id, []).append(
                violation.violation_id
            )

    objective_coverages = []
    for result in coverage_input.oracle_facts.security.planned_objectives:
        definition = catalog.get(result.objective_id)
        if definition is None:
            raise V2RiskCoverageExtractionError("Oracle references an unknown V2 objective")
        expected = {
            item.milestone_id: (item.required, item.depends_on)
            for item in definition.milestones
        }
        actual = {
            item.milestone_id: (item.required, item.depends_on)
            for item in result.milestone_facts
        }
        if actual != expected:
            raise V2RiskCoverageExtractionError(
                "Oracle milestone graph differs from the frozen risk catalog"
            )
        exposure = _sealed(
            V2ExposureCoverage,
            {
                "objective_id": result.objective_id,
                "condition_id": result.condition_id,
                "entry_kind": result.entry_kind,
                "stages": result.exposure_fact.stages,
                "progress": V2ExposureProgress(
                    highest_stage=result.exposure_fact.stages[-1]
                ),
                "evidence_refs": result.exposure_fact.evidence_refs,
            },
            "coverage_digest",
        )
        milestones = tuple(
            _milestone_coverage(result.objective_id, item)
            for item in result.milestone_facts
        )
        objective_coverages.append(
            _sealed(
                V2PlannedObjectiveCoverage,
                {
                    "objective_id": result.objective_id,
                    "condition_id": result.condition_id,
                    "entry_kind": result.entry_kind,
                    "classification": definition.classification,
                    "exposure": exposure,
                    "completion_kind": result.completion_kind,
                    "milestones": milestones,
                    "planned_violation_ids": tuple(
                        sorted(violations_by_objective.get(result.objective_id, ()))
                    ),
                },
                "objective_coverage_digest",
            )
        )

    all_milestones = [
        item for objective in objective_coverages for item in objective.milestones
    ]
    denominator = V2RiskDenominatorStats(
        family_total=len(V2_RISK_CATALOG.families),
        objective_total=len(V2_RISK_CATALOG.objectives),
        applicable_objective_total=sum(item.applicable for item in V2_RISK_CATALOG.objectives),
        reachable_objective_total=sum(item.reachable for item in V2_RISK_CATALOG.objectives),
        milestone_total=V2_RISK_CATALOG.milestone_count,
        attempted_milestone_count=sum(
            item.outcome_bits.attempted_seen for item in all_milestones
        ),
        blocked_milestone_count=sum(item.outcome_bits.blocked_seen for item in all_milestones),
        realized_milestone_count=sum(
            item.outcome_bits.realized_seen for item in all_milestones
        ),
        none_objective_count=sum(
            item.completion_kind is ObjectiveCompletionKind.NONE
            for item in objective_coverages
        ),
        partial_objective_count=sum(
            item.completion_kind is ObjectiveCompletionKind.PARTIAL
            for item in objective_coverages
        ),
        full_objective_count=sum(
            item.completion_kind is ObjectiveCompletionKind.FULL
            for item in objective_coverages
        ),
    )
    return _sealed(
        V2PlannedRiskCoverage,
        {
            "coverage_identity_digest": V2_COVERAGE_CONTRACT_IDENTITY.identity_digest,
            "canonical_fact_digest": coverage_input.canonical_fact_digest,
            "risk_catalog_digest": V2_RISK_CATALOG.catalog_digest,
            "objectives": tuple(objective_coverages),
            "denominator": denominator,
            "eligibility": build_v2_episode_eligibility_facts(coverage_input),
        },
        "planned_risk_digest",
    )


__all__ = [
    "V2ExposureCoverage",
    "V2MilestoneCoverage",
    "V2PlannedObjectiveCoverage",
    "V2PlannedRiskCoverage",
    "V2RiskCoverageExtractionError",
    "V2RiskDenominatorStats",
    "extract_v2_planned_risk_coverage",
]
