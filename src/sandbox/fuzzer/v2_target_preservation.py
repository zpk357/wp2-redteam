"""Auditable preservation evidence for one mutated formal attack target."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from sandbox.mutation.v2_brief import MinimalFactBrief
from sandbox.mutation.v2_candidate import ParsedMutationCandidate, normalize_generated_text
from sandbox.mutation.v2_contracts import MutationPlan
from sandbox.mutation.v2_provider import MutationProviderAttempt
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import OfficeV2Contract, Sha256Digest

from .v2_seed_pools import FrozenSeed


class TargetPreservationStatus(StrEnum):
    PRESERVED = "preserved"
    DRIFTED = "drifted"
    UNVERIFIED = "unverified"


class TargetPreservationMethod(StrEnum):
    TRUSTED_RULE_FIXTURE = "trusted_rule_fixture"
    INDEPENDENT_JUDGE = "independent_judge"


class TargetPreservationAssessment(OfficeV2Contract):
    seed_id: str
    attack_target: str
    parent_text_digest: Sha256Digest
    mutation_plan_digest: Sha256Digest
    candidate_digest: Sha256Digest
    generated_content_digest: Sha256Digest
    status: TargetPreservationStatus
    method: TargetPreservationMethod | None = None
    evidence_refs: tuple[Sha256Digest, ...] = Field(default_factory=tuple)
    assessment_digest: Sha256Digest

    @model_validator(mode="after")
    def evidence_and_digest_match(self) -> Self:
        if self.status is TargetPreservationStatus.UNVERIFIED:
            if self.method is not None or self.evidence_refs:
                raise ValueError("unverified target preservation cannot claim evidence")
        elif self.method is None or not self.evidence_refs:
            raise ValueError("verified target preservation requires method and evidence")
        if self.assessment_digest != sha256_digest(
            self.model_dump(mode="json", exclude={"assessment_digest"})
        ):
            raise ValueError("target preservation assessment digest does not match")
        return self


def _seal_assessment(payload: dict[str, object]) -> TargetPreservationAssessment:
    draft = TargetPreservationAssessment.model_construct(
        **payload,
        assessment_digest="sha256:" + "0" * 64,
    )
    return TargetPreservationAssessment(
        **payload,
        assessment_digest=sha256_digest(
            draft.model_dump(mode="json", exclude={"assessment_digest"})
        ),
    )


def unverified_target_preservation(
    *,
    seed: FrozenSeed,
    plan: MutationPlan,
    candidate: ParsedMutationCandidate,
) -> TargetPreservationAssessment:
    return _seal_assessment(
        {
            "seed_id": seed.seed_id,
            "attack_target": seed.attack_target,
            "parent_text_digest": sha256_digest({"content": seed.base_text}),
            "mutation_plan_digest": plan.plan_digest,
            "candidate_digest": candidate.candidate_digest,
            "generated_content_digest": candidate.normalized_content_digest,
            "status": TargetPreservationStatus.UNVERIFIED,
        }
    )


def assess_trusted_rule_fixture_target_preservation(
    *,
    seed: FrozenSeed,
    plan: MutationPlan,
    brief: MinimalFactBrief,
    candidate: ParsedMutationCandidate,
    attempt: MutationProviderAttempt,
) -> TargetPreservationAssessment:
    """Recompute the local deterministic fixture output; never use for an LLM."""

    actual = dict(candidate.slot_values)
    expected = {
        slot.payload_slot_id: normalize_generated_text((
            f"Controlled test variant {attempt.attempt_index}: "
            f"{brief.parent_base_texts[index % len(brief.parent_base_texts)]} "
            f"Variant {index + 1} [{brief.mutation_plan_digest[-8:]}]."
        )[: slot.max_length])
        for index, slot in enumerate(brief.slots)
    }
    preserved = actual == expected and brief.parent_base_texts == (seed.base_text,)
    evidence = sha256_digest(
        {
            "method": TargetPreservationMethod.TRUSTED_RULE_FIXTURE.value,
            "plan": plan.plan_digest,
            "brief": brief.brief_digest,
            "attempt": attempt.attempt_digest,
            "expected": expected,
            "actual": actual,
        }
    )
    return _seal_assessment(
        {
            "seed_id": seed.seed_id,
            "attack_target": seed.attack_target,
            "parent_text_digest": sha256_digest({"content": seed.base_text}),
            "mutation_plan_digest": plan.plan_digest,
            "candidate_digest": candidate.candidate_digest,
            "generated_content_digest": candidate.normalized_content_digest,
            "status": (
                TargetPreservationStatus.PRESERVED
                if preserved
                else TargetPreservationStatus.DRIFTED
            ),
            "method": TargetPreservationMethod.TRUSTED_RULE_FIXTURE,
            "evidence_refs": (evidence,),
        }
    )


__all__ = [
    "TargetPreservationAssessment",
    "TargetPreservationMethod",
    "TargetPreservationStatus",
    "assess_trusted_rule_fixture_target_preservation",
    "unverified_target_preservation",
]
