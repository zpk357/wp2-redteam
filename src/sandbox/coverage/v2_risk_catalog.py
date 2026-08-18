"""Compile the Office V2 risk denominator from the frozen objective catalog."""

from __future__ import annotations

from typing import Self

from pydantic import Field, field_validator, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_models import AttackEntryKind
from sandbox.scenarios.office_v2.attack_objectives import (
    ATTACK_OBJECTIVE_CATALOG,
    ATTACK_OBJECTIVES,
)
from sandbox.scenarios.office_v2.models import Identifier, OfficeV2Contract, Sha256Digest

from .v2_contracts import (
    V2LeakageProofGrade,
    V2ObjectiveRiskClassification,
    V2RiskFamily,
    build_v2_objective_risk_classification,
)

_RISK_CATEGORY_CLASSIFICATION = {
    "risk.external-sharing-chain": (
        V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,
        (
            V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,
            V2RiskFamily.UNAUTHORIZED_OPERATION_PERMISSION_EXPANSION,
        ),
    ),
    "risk.unauthorized-sharing": (
        V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,
        (
            V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,
            V2RiskFamily.UNAUTHORIZED_OPERATION_PERMISSION_EXPANSION,
        ),
    ),
    "risk.information-exposure": (
        V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,
        (V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,),
    ),
    "risk.resource-destruction": (
        V2RiskFamily.DESTRUCTIVE_OPERATION,
        (V2RiskFamily.DESTRUCTIVE_OPERATION,),
    ),
    "risk.integrity-propagation": (
        V2RiskFamily.WRONG_MODIFICATION_PROPAGATION,
        (V2RiskFamily.WRONG_MODIFICATION_PROPAGATION,),
    ),
    "risk.calendar-redirection": (
        V2RiskFamily.WRONG_MODIFICATION_PROPAGATION,
        (V2RiskFamily.WRONG_MODIFICATION_PROPAGATION,),
    ),
    "risk.parameter-integrity": (
        V2RiskFamily.WRONG_MODIFICATION_PROPAGATION,
        (V2RiskFamily.WRONG_MODIFICATION_PROPAGATION,),
    ),
    "risk.composite-information-exposure": (
        V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,
        (V2RiskFamily.SENSITIVE_INFORMATION_DISCLOSURE,),
    ),
    "risk.workspace-integrity": (
        V2RiskFamily.WRONG_MODIFICATION_PROPAGATION,
        (V2RiskFamily.WRONG_MODIFICATION_PROPAGATION,),
    ),
    "risk.unauthorized-calendar-action": (
        V2RiskFamily.UNAUTHORIZED_OPERATION_PERMISSION_EXPANSION,
        (V2RiskFamily.UNAUTHORIZED_OPERATION_PERMISSION_EXPANSION,),
    ),
    "risk.permission-expansion": (
        V2RiskFamily.UNAUTHORIZED_OPERATION_PERMISSION_EXPANSION,
        (V2RiskFamily.UNAUTHORIZED_OPERATION_PERMISSION_EXPANSION,),
    ),
    "risk.cross-domain-integrity": (
        V2RiskFamily.WRONG_MODIFICATION_PROPAGATION,
        (V2RiskFamily.WRONG_MODIFICATION_PROPAGATION,),
    ),
}

V2_RISK_CONTEXT_DIMENSIONS = (
    "primary_scheduling_family",
    "risk_facets",
    "objective",
    "milestone",
    "outcome",
    "entry_kind",
    "source_domain",
    "sink_domain",
    "sink_action",
    "carrier",
    "recipient_kind",
    "authorization_branch",
    "planned_or_unexpected",
    "leakage_proof_grade",
)


class V2RiskMilestoneDefinition(OfficeV2Contract):
    objective_id: Identifier
    milestone_id: Identifier
    depends_on: tuple[Identifier, ...] = Field(default_factory=tuple)
    required: bool = True


class V2RiskObjectiveDefinition(OfficeV2Contract):
    objective_id: Identifier
    objective_digest: Sha256Digest
    classification: V2ObjectiveRiskClassification
    compatible_entry_kinds: tuple[AttackEntryKind, ...] = Field(min_length=1)
    milestones: tuple[V2RiskMilestoneDefinition, ...] = Field(min_length=1)
    applicable: bool = True
    reachable: bool = True
    unreachable_reason_codes: tuple[Identifier, ...] = Field(default_factory=tuple)

    @field_validator("milestones")
    @classmethod
    def milestones_are_canonical(
        cls, value: tuple[V2RiskMilestoneDefinition, ...]
    ) -> tuple[V2RiskMilestoneDefinition, ...]:
        return tuple(sorted(value, key=lambda item: item.milestone_id))


class V2RiskCatalog(OfficeV2Contract):
    source_objective_catalog_digest: Sha256Digest
    families: tuple[V2RiskFamily, ...] = Field(min_length=4, max_length=4)
    objectives: tuple[V2RiskObjectiveDefinition, ...] = Field(min_length=12, max_length=12)
    milestone_count: int = Field(ge=1)
    context_dimensions: tuple[Identifier, ...]
    leakage_proof_grades: tuple[V2LeakageProofGrade, ...]
    taxonomy_digest: Sha256Digest
    scope_digest: Sha256Digest
    mapping_digest: Sha256Digest
    catalog_digest: Sha256Digest

    @field_validator("objectives")
    @classmethod
    def objectives_are_canonical(
        cls, value: tuple[V2RiskObjectiveDefinition, ...]
    ) -> tuple[V2RiskObjectiveDefinition, ...]:
        return tuple(sorted(value, key=lambda item: item.objective_id))

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"catalog_digest"}, exclude_none=False)

    @model_validator(mode="after")
    def counts_and_digests_match(self) -> Self:
        if self.families != tuple(sorted(V2RiskFamily, key=lambda item: item.value)):
            raise ValueError("risk catalog must contain exactly the four V2 families")
        if self.milestone_count != sum(len(item.milestones) for item in self.objectives):
            raise ValueError("risk milestone count does not match objectives")
        if self.milestone_count != 23:
            raise ValueError("Office V2 risk denominator must contain 23 milestones")
        if self.catalog_digest != sha256_digest(self.digest_payload()):
            raise ValueError("risk catalog digest does not match")
        return self


def compile_v2_risk_catalog() -> V2RiskCatalog:
    objectives = []
    for objective in ATTACK_OBJECTIVES:
        if len(objective.risk_category_ids) != 1:
            raise ValueError("V2 objective must have one frozen source risk category")
        primary, facets = _RISK_CATEGORY_CLASSIFICATION[objective.risk_category_ids[0]]
        classification = build_v2_objective_risk_classification(
            objective_id=objective.objective_id,
            primary_scheduling_family=primary,
            risk_facets=facets,
        )
        if objective.milestone_graph is None:
            milestones = (
                V2RiskMilestoneDefinition(
                    objective_id=objective.objective_id,
                    milestone_id=f"milestone.atomic.{objective.objective_id}",
                ),
            )
        else:
            milestones = tuple(
                V2RiskMilestoneDefinition(
                    objective_id=objective.objective_id,
                    milestone_id=item.milestone_id,
                    depends_on=item.depends_on,
                    required=item.required,
                )
                for item in objective.milestone_graph.milestones
            )
        objectives.append(
            V2RiskObjectiveDefinition(
                objective_id=objective.objective_id,
                objective_digest=objective.content_digest,
                classification=classification,
                compatible_entry_kinds=objective.compatible_entry_kinds,
                milestones=milestones,
            )
        )
    canonical = tuple(sorted(objectives, key=lambda item: item.objective_id))
    families = tuple(sorted(V2RiskFamily, key=lambda item: item.value))
    grades = tuple(sorted(V2LeakageProofGrade, key=lambda item: item.value))
    payload = {
        "source_objective_catalog_digest": ATTACK_OBJECTIVE_CATALOG.catalog_digest,
        "families": families,
        "objectives": canonical,
        "milestone_count": sum(len(item.milestones) for item in canonical),
        "context_dimensions": V2_RISK_CONTEXT_DIMENSIONS,
        "leakage_proof_grades": grades,
        "taxonomy_digest": sha256_digest({"families": families, "grades": grades}),
        "scope_digest": sha256_digest(
            {
                "objectives": tuple(
                    (item.objective_id, item.applicable, item.reachable)
                    for item in canonical
                )
            }
        ),
        "mapping_digest": sha256_digest(
            {
                "classifications": tuple(
                    item.classification.classification_digest for item in canonical
                ),
                "context_dimensions": V2_RISK_CONTEXT_DIMENSIONS,
            }
        ),
    }
    draft = V2RiskCatalog.model_construct(
        **payload,
        catalog_digest="sha256:" + "0" * 64,
    )
    return V2RiskCatalog(
        **payload,
        catalog_digest=sha256_digest(draft.digest_payload()),
    )


V2_RISK_CATALOG = compile_v2_risk_catalog()

__all__ = [
    "V2_RISK_CATALOG",
    "V2_RISK_CONTEXT_DIMENSIONS",
    "V2RiskCatalog",
    "V2RiskMilestoneDefinition",
    "V2RiskObjectiveDefinition",
    "compile_v2_risk_catalog",
]
