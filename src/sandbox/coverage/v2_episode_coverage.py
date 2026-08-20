"""Compose Office V2 Episode facts and compare candidate batches fairly."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest
from sandbox.scenarios.office_v2.oracle_models import EvidenceRef, MilestoneOutcome

from .v2_behavior import V2BehaviorFeature, V2BehaviorProfile
from .v2_contracts import (
    V2_COVERAGE_CONTRACT_IDENTITY,
    V2CandidateBatchBaseline,
    V2EpisodeEligibilityFacts,
    V2LeakageProofGrade,
    V2RiskFamily,
)
from .v2_episode_behavior import extract_v2_behavior_profile
from .v2_input import V2CoverageInput
from .v2_risk_coverage import (
    V2MilestoneCoverage,
    V2PlannedRiskCoverage,
    extract_v2_planned_risk_coverage,
)
from .v2_unexpected_risk import (
    V2UnexpectedRiskCoverage,
    map_v2_unexpected_risks,
)


class V2EpisodeCoverageError(ValueError):
    """V2 Episode facts or a shared-baseline batch are inconsistent."""


class V2BehaviorRiskLinkKind(StrEnum):
    SAME_EXCHANGE = "same_exchange"
    CAUSAL_PREFIX = "causal_prefix"
    SAME_TRANSITION = "same_transition"
    SAME_EPISODE_ONLY = "same_episode_only"


class V2RiskContextCell(OfficeV2Contract):
    primary_scheduling_family: V2RiskFamily | None = None
    risk_facets: tuple[V2RiskFamily, ...] = Field(min_length=1)
    objective_id: Identifier | None = None
    milestone_id: Identifier | None = None
    violation_id: Identifier | None = None
    outcome: MilestoneOutcome
    entry_kind: Identifier
    source_domain: Identifier
    sink_domain: Identifier
    sink_action: Identifier
    carrier: Identifier
    recipient_kind: Identifier
    authorization_branch: Identifier
    planned_or_unexpected: Identifier
    leakage_proof_grade: V2LeakageProofGrade
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    context_key_digest: Sha256Digest
    context_fact_digest: Sha256Digest

    def key_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"evidence_refs", "context_key_digest", "context_fact_digest"},
            exclude_none=False,
        )

    def fact_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"context_fact_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digests_match(self) -> Self:
        if self.planned_or_unexpected == "planned":
            if self.objective_id is None or self.milestone_id is None:
                raise ValueError("planned risk context requires objective and milestone")
            if self.primary_scheduling_family is None or self.violation_id is not None:
                raise ValueError("planned risk context has invalid scheduling identity")
        elif self.planned_or_unexpected == "unexpected":
            if self.violation_id is None or self.milestone_id is not None:
                raise ValueError("unexpected risk context requires only a violation identity")
        else:
            raise ValueError("risk context branch must be planned or unexpected")
        if self.context_key_digest != sha256_digest(self.key_payload()):
            raise ValueError("risk context key digest does not match")
        if self.context_fact_digest != sha256_digest(self.fact_payload()):
            raise ValueError("risk context fact digest does not match")
        return self


class V2BehaviorRiskLink(OfficeV2Contract):
    behavior_feature_key_digest: Sha256Digest
    risk_fact_key_digest: Sha256Digest
    link_kind: V2BehaviorRiskLinkKind
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    link_key_digest: Sha256Digest
    link_fact_digest: Sha256Digest

    def key_payload(self) -> dict[str, object]:
        return {
            "behavior_feature_key_digest": self.behavior_feature_key_digest,
            "risk_fact_key_digest": self.risk_fact_key_digest,
            "link_kind": self.link_kind,
        }

    def fact_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"link_fact_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digests_match(self) -> Self:
        if self.link_key_digest != sha256_digest(self.key_payload()):
            raise ValueError("behavior-risk link key digest does not match")
        if self.link_fact_digest != sha256_digest(self.fact_payload()):
            raise ValueError("behavior-risk link fact digest does not match")
        return self


class V2EpisodeCoverageFacts(OfficeV2Contract):
    coverage_identity_digest: Sha256Digest
    input_digest: Sha256Digest
    canonical_fact_digest: Sha256Digest
    behavior: V2BehaviorProfile
    planned_risk: V2PlannedRiskCoverage
    unexpected_risk: V2UnexpectedRiskCoverage
    risk_context_cells: tuple[V2RiskContextCell, ...] = Field(default_factory=tuple)
    behavior_risk_links: tuple[V2BehaviorRiskLink, ...] = Field(default_factory=tuple)
    eligibility: V2EpisodeEligibilityFacts
    episode_coverage_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"episode_coverage_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def identities_and_digest_match(self) -> Self:
        if self.coverage_identity_digest != V2_COVERAGE_CONTRACT_IDENTITY.identity_digest:
            raise ValueError("Episode coverage uses the wrong contract identity")
        if any(
            digest != self.canonical_fact_digest
            for digest in (
                self.behavior.canonical_fact_digest,
                self.planned_risk.canonical_fact_digest,
                self.unexpected_risk.canonical_fact_digest,
                self.eligibility.canonical_fact_digest,
            )
        ):
            raise ValueError("Episode coverage components refer to different facts")
        if self.episode_coverage_digest != sha256_digest(self.digest_payload()):
            raise ValueError("Episode coverage digest does not match")
        return self


class V2CoverageSnapshot(OfficeV2Contract):
    coverage_identity_digest: Sha256Digest
    canonical_fact_digests: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    input_digests: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    primary_behavior_feature_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    secondary_behavior_feature_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    behavior_profile_digests: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    primary_scheduling_families: tuple[Identifier, ...] = Field(default_factory=tuple)
    risk_facets: tuple[Identifier, ...] = Field(default_factory=tuple)
    risk_objectives: tuple[Identifier, ...] = Field(default_factory=tuple)
    exposure_stage_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    milestone_outcome_bit_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    unexpected_violation_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    risk_context_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    behavior_risk_link_keys: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    snapshot_digest: Sha256Digest

    @field_validator("canonical_fact_digests", "input_digests")
    @classmethod
    def digests_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("coverage snapshot digests must be unique")
        return tuple(sorted(value))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"snapshot_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def identity_and_digest_match(self) -> Self:
        if self.coverage_identity_digest != V2_COVERAGE_CONTRACT_IDENTITY.identity_digest:
            raise ValueError("coverage snapshot uses the wrong identity")
        if self.snapshot_digest != sha256_digest(self.digest_payload()):
            raise ValueError("coverage snapshot digest does not match")
        return self


class V2CoverageDelta(OfficeV2Contract):
    candidate_id: Identifier
    canonical_fact_digest: Sha256Digest
    baseline_snapshot_digest: Sha256Digest
    new_primary_behavior_features: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    new_secondary_diversity_features: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    new_behavior_profile: Sha256Digest | None = None
    new_primary_scheduling_families: tuple[Identifier, ...] = Field(default_factory=tuple)
    new_risk_facets: tuple[Identifier, ...] = Field(default_factory=tuple)
    new_risk_objectives: tuple[Identifier, ...] = Field(default_factory=tuple)
    new_exposure_stages: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    new_milestone_outcome_bits: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    new_unexpected_violations: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    new_risk_contexts: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    new_behavior_risk_links: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    eligibility: V2EpisodeEligibilityFacts
    delta_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"delta_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.delta_digest != sha256_digest(self.digest_payload()):
            raise ValueError("coverage delta digest does not match")
        return self


class V2CandidateEpisode(OfficeV2Contract):
    candidate_id: Identifier
    episode_facts: V2EpisodeCoverageFacts


class V2CandidateBatchResult(OfficeV2Contract):
    batch_baseline: V2CandidateBatchBaseline
    deltas: tuple[V2CoverageDelta, ...]
    next_snapshot: V2CoverageSnapshot
    batch_result_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"batch_result_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def digest_matches(self) -> Self:
        if self.batch_result_digest != sha256_digest(self.digest_payload()):
            raise ValueError("candidate batch result digest does not match")
        return self


def _sealed(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(**payload, **{digest_field: "sha256:" + "0" * 64})
    return model_type(**payload, **{digest_field: sha256_digest(draft.digest_payload())})


def empty_v2_coverage_snapshot() -> V2CoverageSnapshot:
    return _sealed(
        V2CoverageSnapshot,
        {"coverage_identity_digest": V2_COVERAGE_CONTRACT_IDENTITY.identity_digest},
        "snapshot_digest",
    )


def _risk_evidence(milestone: V2MilestoneCoverage) -> tuple[EvidenceRef, ...]:
    return milestone.evidence_refs


def _link(feature: V2BehaviorFeature, risk_key: str, refs: tuple[EvidenceRef, ...]):
    feature_by_id = {item.evidence_id: item for item in feature.evidence_refs}
    risk_by_id = {item.evidence_id: item for item in refs}
    shared_ids = set(feature_by_id).intersection(risk_by_id)
    if shared_ids:
        shared = tuple(
            sorted((feature_by_id[item] for item in shared_ids), key=lambda x: x.sort_key())
        )
        kind = (
            V2BehaviorRiskLinkKind.SAME_TRANSITION
            if any(item.ref_kind.value == "state_transition" for item in shared)
            else V2BehaviorRiskLinkKind.SAME_EXCHANGE
        )
        evidence = shared
    else:
        feature_sequences = [
            item.sequence for item in feature.evidence_refs if item.sequence is not None
        ]
        risk_sequences = [item.sequence for item in refs if item.sequence is not None]
        if feature_sequences and risk_sequences and max(feature_sequences) <= min(risk_sequences):
            kind = V2BehaviorRiskLinkKind.CAUSAL_PREFIX
            evidence = tuple(sorted((*feature.evidence_refs, *refs), key=lambda x: x.sort_key()))
        else:
            kind = V2BehaviorRiskLinkKind.SAME_EPISODE_ONLY
            evidence = tuple(sorted((*feature.evidence_refs, *refs), key=lambda x: x.sort_key()))
    unique = {item.evidence_id: item for item in evidence}
    payload = {
        "behavior_feature_key_digest": feature.feature_key_digest,
        "risk_fact_key_digest": risk_key,
        "link_kind": kind,
        "evidence_refs": tuple(sorted(unique.values(), key=lambda item: item.sort_key())),
    }
    payload["link_key_digest"] = sha256_digest(
        {
            "behavior_feature_key_digest": feature.feature_key_digest,
            "risk_fact_key_digest": risk_key,
            "link_kind": kind,
        }
    )
    draft = V2BehaviorRiskLink.model_construct(**payload, link_fact_digest="sha256:" + "0" * 64)
    return V2BehaviorRiskLink(**payload, link_fact_digest=sha256_digest(draft.fact_payload()))


def _risk_contexts(coverage_input: V2CoverageInput, planned: V2PlannedRiskCoverage):
    exchanges = {
        item.sequence: item for item in coverage_input.behavior_source_facts.tool_exchanges
    }
    def carrier_for(refs: tuple[EvidenceRef, ...], entry_kind: str) -> str:
        carriers = set()
        for ref in refs:
            resource_ref = getattr(ref, "resource_ref", None)
            field_path = getattr(ref, "field_path", None)
            if resource_ref is None or not field_path:
                continue
            domain = resource_ref.kind.value.split("_")[0]
            normalized_path = ".".join(
                "[]" if isinstance(segment, int) else str(segment) for segment in field_path
            )
            carriers.add(f"{domain}.{normalized_path}")
        if carriers:
            return "+".join(sorted(carriers))
        return "direct_task" if entry_kind == "direct_task" else "unverified"

    def authorization_for(relevant) -> str:
        decisions = [
            exchange.policy_decision for exchange in relevant if exchange.policy_decision
        ]
        if not decisions:
            return "unverified"
        return (
            "delegated"
            if all(item.delegation_allowed is not False for item in decisions)
            else "delegation_missing"
        )

    cells = []
    for objective in planned.objectives:
        for milestone in objective.milestones:
            if milestone.episode_outcome is MilestoneOutcome.NOT_REACHED:
                continue
            sequences = {
                ref.sequence for ref in milestone.evidence_refs if ref.sequence is not None
            }
            relevant = [exchanges[item] for item in sorted(sequences) if item in exchanges]
            domains = sorted(
                {
                    kind.value.split("_")[0]
                    for exchange in relevant
                    for kind in exchange.resource_kinds
                }
            )
            actions = sorted(
                {exchange.action.value for exchange in relevant if exchange.action is not None}
            )
            payload = {
                "primary_scheduling_family": objective.classification.primary_scheduling_family,
                "risk_facets": objective.classification.risk_facets,
                "objective_id": objective.objective_id,
                "milestone_id": milestone.milestone_id,
                "violation_id": None,
                "outcome": milestone.episode_outcome,
                "entry_kind": objective.entry_kind.value,
                "source_domain": domains[0] if domains else "unverified",
                "sink_domain": domains[-1] if domains else "unverified",
                "sink_action": actions[-1] if actions else "unverified",
                "carrier": carrier_for(
                    milestone.evidence_refs, objective.entry_kind.value
                ),
                "recipient_kind": "unverified",
                "authorization_branch": authorization_for(relevant),
                "planned_or_unexpected": "planned",
                "leakage_proof_grade": V2LeakageProofGrade.UNVERIFIED,
                "evidence_refs": milestone.evidence_refs,
            }
            draft = V2RiskContextCell.model_construct(
                **payload,
                context_key_digest="sha256:" + "0" * 64,
                context_fact_digest="sha256:" + "0" * 64,
            )
            payload["context_key_digest"] = sha256_digest(draft.key_payload())
            draft = V2RiskContextCell.model_construct(
                **payload, context_fact_digest="sha256:" + "0" * 64
            )
            cells.append(
                V2RiskContextCell(
                    **payload, context_fact_digest=sha256_digest(draft.fact_payload())
                )
            )
    unexpected_entry_kind = (
        planned.objectives[0].entry_kind.value if len(planned.objectives) == 1 else "unverified"
    )
    unexpected = map_v2_unexpected_risks(coverage_input)
    for violation in unexpected.violations:
        relevant = [
            exchanges[sequence]
            for sequence in violation.exchange_sequences
            if sequence in exchanges
        ]
        actions = tuple(item.value for item in violation.action_kinds)
        domains = violation.resource_domains
        payload = {
            "primary_scheduling_family": None,
            "risk_facets": violation.risk_facets,
            "objective_id": violation.matched_objective_id,
            "milestone_id": None,
            "violation_id": violation.violation_id,
            "outcome": violation.outcome,
            "entry_kind": unexpected_entry_kind,
            "source_domain": domains[0] if domains else "unverified",
            "sink_domain": domains[-1] if domains else "unverified",
            "sink_action": actions[-1] if actions else "unverified",
            "carrier": carrier_for(violation.evidence_refs, unexpected_entry_kind),
            "recipient_kind": "unverified",
            "authorization_branch": authorization_for(relevant),
            "planned_or_unexpected": "unexpected",
            "leakage_proof_grade": V2LeakageProofGrade.UNVERIFIED,
            "evidence_refs": violation.evidence_refs,
        }
        draft = V2RiskContextCell.model_construct(
            **payload,
            context_key_digest="sha256:" + "0" * 64,
            context_fact_digest="sha256:" + "0" * 64,
        )
        payload["context_key_digest"] = sha256_digest(draft.key_payload())
        draft = V2RiskContextCell.model_construct(
            **payload, context_fact_digest="sha256:" + "0" * 64
        )
        cells.append(
            V2RiskContextCell(
                **payload, context_fact_digest=sha256_digest(draft.fact_payload())
            )
        )
    return tuple(sorted(cells, key=lambda item: item.context_key_digest))


def build_v2_episode_coverage_facts(
    coverage_input: V2CoverageInput,
) -> V2EpisodeCoverageFacts:
    behavior = extract_v2_behavior_profile(coverage_input)
    planned = extract_v2_planned_risk_coverage(coverage_input)
    unexpected = map_v2_unexpected_risks(coverage_input)
    contexts = _risk_contexts(coverage_input, planned)
    links = []
    for objective in planned.objectives:
        links.extend(
            _link(
                feature,
                objective.exposure.coverage_digest,
                objective.exposure.evidence_refs,
            )
            for feature in behavior.primary_features
        )
        for milestone in objective.milestones:
            if milestone.episode_outcome is MilestoneOutcome.NOT_REACHED:
                continue
            links.extend(
                _link(feature, milestone.coverage_key_digest, _risk_evidence(milestone))
                for feature in behavior.primary_features
            )
    for violation in unexpected.violations:
        links.extend(
            _link(
                feature,
                violation.unexpected_risk_digest,
                violation.evidence_refs,
            )
            for feature in behavior.primary_features
        )
    unique_links = {item.link_key_digest: item for item in links}
    payload = {
        "coverage_identity_digest": V2_COVERAGE_CONTRACT_IDENTITY.identity_digest,
        "input_digest": coverage_input.input_digest,
        "canonical_fact_digest": coverage_input.canonical_fact_digest,
        "behavior": behavior,
        "planned_risk": planned,
        "unexpected_risk": unexpected,
        "risk_context_cells": contexts,
        "behavior_risk_links": tuple(unique_links[key] for key in sorted(unique_links)),
        "eligibility": planned.eligibility,
    }
    return _sealed(V2EpisodeCoverageFacts, payload, "episode_coverage_digest")


def _fact_sets(facts: V2EpisodeCoverageFacts) -> dict[str, set[str]]:
    families = {
        item.classification.primary_scheduling_family.value
        for item in facts.planned_risk.objectives
    }
    facets = {
        facet.value
        for item in facts.planned_risk.objectives
        for facet in item.classification.risk_facets
    } | {facet.value for item in facts.unexpected_risk.violations for facet in item.risk_facets}
    exposure = {
        sha256_digest(
            {
                "objective_id": item.objective_id,
                "exposure_stage": stage,
            }
        )
        for item in facts.planned_risk.objectives
        for stage in item.exposure.stages
    }
    bits = set()
    for objective in facts.planned_risk.objectives:
        for milestone in objective.milestones:
            for name in ("attempted_seen", "blocked_seen", "realized_seen"):
                if getattr(milestone.outcome_bits, name):
                    bits.add(
                        sha256_digest(
                            {
                                "objective_id": objective.objective_id,
                                "milestone_id": milestone.milestone_id,
                                "outcome_bit": name,
                            }
                        )
                    )
    return {
        "primary": {item.feature_key_digest for item in facts.behavior.primary_features},
        "secondary": {item.feature_key_digest for item in facts.behavior.secondary_diversity},
        "profiles": {facts.behavior.profile_digest},
        "families": families,
        "facets": facets,
        "objectives": {item.objective_id for item in facts.planned_risk.objectives},
        "exposure": exposure,
        "bits": bits,
        "unexpected": {item.unexpected_risk_digest for item in facts.unexpected_risk.violations},
        "contexts": {item.context_key_digest for item in facts.risk_context_cells},
        "links": {item.link_key_digest for item in facts.behavior_risk_links},
    }


def _snapshot_sets(snapshot: V2CoverageSnapshot) -> dict[str, set[str]]:
    return {
        "primary": set(snapshot.primary_behavior_feature_keys),
        "secondary": set(snapshot.secondary_behavior_feature_keys),
        "profiles": set(snapshot.behavior_profile_digests),
        "families": set(snapshot.primary_scheduling_families),
        "facets": set(snapshot.risk_facets),
        "objectives": set(snapshot.risk_objectives),
        "exposure": set(snapshot.exposure_stage_keys),
        "bits": set(snapshot.milestone_outcome_bit_keys),
        "unexpected": set(snapshot.unexpected_violation_keys),
        "contexts": set(snapshot.risk_context_keys),
        "links": set(snapshot.behavior_risk_link_keys),
    }


def _delta(candidate_id: str, facts: V2EpisodeCoverageFacts, baseline: V2CoverageSnapshot):
    current = _fact_sets(facts)
    previous = _snapshot_sets(baseline)
    new = {name: tuple(sorted(values - previous[name])) for name, values in current.items()}
    payload = {
        "candidate_id": candidate_id,
        "canonical_fact_digest": facts.canonical_fact_digest,
        "baseline_snapshot_digest": baseline.snapshot_digest,
        "new_primary_behavior_features": new["primary"],
        "new_secondary_diversity_features": new["secondary"],
        "new_behavior_profile": next(iter(new["profiles"]), None),
        "new_primary_scheduling_families": new["families"],
        "new_risk_facets": new["facets"],
        "new_risk_objectives": new["objectives"],
        "new_exposure_stages": new["exposure"],
        "new_milestone_outcome_bits": new["bits"],
        "new_unexpected_violations": new["unexpected"],
        "new_risk_contexts": new["contexts"],
        "new_behavior_risk_links": new["links"],
        "eligibility": facts.eligibility,
    }
    return _sealed(V2CoverageDelta, payload, "delta_digest")


def _union_snapshot(baseline: V2CoverageSnapshot, episodes: tuple[V2EpisodeCoverageFacts, ...]):
    combined = _snapshot_sets(baseline)
    for facts in episodes:
        current = _fact_sets(facts)
        for name in combined:
            combined[name].update(current[name])
    payload = {
        "coverage_identity_digest": V2_COVERAGE_CONTRACT_IDENTITY.identity_digest,
        "canonical_fact_digests": tuple(
            sorted(
                {
                    *baseline.canonical_fact_digests,
                    *(item.canonical_fact_digest for item in episodes),
                }
            )
        ),
        # Acquisition-specific input digests belong to artifact audit, not coverage.
        # Keeping them out prevents direct/recording/replay of identical facts from
        # manufacturing different Campaign snapshots.
        "input_digests": baseline.input_digests,
        "primary_behavior_feature_keys": tuple(sorted(combined["primary"])),
        "secondary_behavior_feature_keys": tuple(sorted(combined["secondary"])),
        "behavior_profile_digests": tuple(sorted(combined["profiles"])),
        "primary_scheduling_families": tuple(sorted(combined["families"])),
        "risk_facets": tuple(sorted(combined["facets"])),
        "risk_objectives": tuple(sorted(combined["objectives"])),
        "exposure_stage_keys": tuple(sorted(combined["exposure"])),
        "milestone_outcome_bit_keys": tuple(sorted(combined["bits"])),
        "unexpected_violation_keys": tuple(sorted(combined["unexpected"])),
        "risk_context_keys": tuple(sorted(combined["contexts"])),
        "behavior_risk_link_keys": tuple(sorted(combined["links"])),
    }
    return _sealed(V2CoverageSnapshot, payload, "snapshot_digest")


def evaluate_v2_candidate_batch(
    *,
    batch_baseline: V2CandidateBatchBaseline,
    baseline_snapshot: V2CoverageSnapshot,
    candidates: tuple[V2CandidateEpisode, ...],
) -> V2CandidateBatchResult:
    if batch_baseline.baseline_snapshot_digest != baseline_snapshot.snapshot_digest:
        raise V2EpisodeCoverageError("candidate batch baseline snapshot digest differs")
    by_id = {item.candidate_id: item for item in candidates}
    if len(by_id) != len(candidates) or set(by_id) != set(batch_baseline.candidate_ids):
        raise V2EpisodeCoverageError("candidate batch membership differs from frozen baseline")
    ordered = tuple(by_id[item] for item in sorted(by_id))
    deltas = tuple(
        _delta(item.candidate_id, item.episode_facts, baseline_snapshot) for item in ordered
    )
    next_snapshot = _union_snapshot(
        baseline_snapshot, tuple(item.episode_facts for item in ordered)
    )
    payload = {
        "batch_baseline": batch_baseline,
        "deltas": deltas,
        "next_snapshot": next_snapshot,
    }
    return _sealed(V2CandidateBatchResult, payload, "batch_result_digest")


__all__ = [
    "V2BehaviorRiskLink",
    "V2BehaviorRiskLinkKind",
    "V2CandidateBatchResult",
    "V2CandidateEpisode",
    "V2CoverageDelta",
    "V2CoverageSnapshot",
    "V2EpisodeCoverageError",
    "V2EpisodeCoverageFacts",
    "V2RiskContextCell",
    "build_v2_episode_coverage_facts",
    "empty_v2_coverage_snapshot",
    "evaluate_v2_candidate_batch",
]
