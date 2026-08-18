"""Deterministic, pre-execution candidate generation for office V1."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, ValidationError, field_validator, model_validator

from sandbox.protocol import normalize_sha256_digest
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.catalogs import (
    CatalogLock,
    ScenarioCatalogManifest,
    build_catalog_lock,
)
from sandbox.scenarios.models import (
    AgentConfig,
    AttackBinding,
    AttackObjective,
    BenignTask,
    CompositionIssueCode,
    ExecutionBudget,
    FrozenContract,
    Identifier,
    InjectionCarrier,
    ScenarioTemplate,
    TestCase,
    assess_attack_compatibility,
)
from sandbox.scenarios.office_matrix import (
    OFFICE_ATTACK_EXPRESSION_CATALOG_VERSION,
    OFFICE_ATTACK_EXPRESSION_STYLES,
    office_attack_expression,
)
from sandbox.scenarios.office_v1 import (
    OFFICE_ATTACK_OBJECTIVES,
    OFFICE_BENIGN_TASKS,
    OFFICE_INJECTION_CARRIERS,
    OFFICE_V1,
)

OFFICE_SCENARIO_CATALOG_VERSION = "1.0"
OFFICE_BENIGN_TASK_CATALOG_VERSION = "1.0"
OFFICE_ATTACK_OBJECTIVE_CATALOG_VERSION = "1.0"
OFFICE_INJECTION_CARRIER_CATALOG_VERSION = "1.0"


class CatalogIntegrityError(ValueError):
    """Raised when a campaign lock does not match the executable catalog."""


class OfficeCandidateCatalog(FrozenContract):
    scenario: ScenarioTemplate
    benign_tasks: tuple[BenignTask, ...] = Field(min_length=1)
    attack_objectives: tuple[AttackObjective, ...] = Field(min_length=1)
    injection_carriers: tuple[InjectionCarrier, ...] = Field(min_length=1)
    expression_ids: tuple[Identifier, ...] = Field(min_length=1)

    @field_validator("expression_ids")
    @classmethod
    def expressions_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("expression_ids must not contain duplicates")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_component_identities(self) -> OfficeCandidateCatalog:
        groups = (
            ("benign task", [item.task_id for item in self.benign_tasks]),
            ("attack objective", [item.objective_id for item in self.attack_objectives]),
            ("injection carrier", [item.carrier_id for item in self.injection_carriers]),
        )
        for label, item_ids in groups:
            if len(item_ids) != len(set(item_ids)):
                raise ValueError(f"{label} catalog identities must be unique")
        return self

    def manifest(self) -> ScenarioCatalogManifest:
        expressions = (
            (
                expression_id,
                {
                    objective.objective_id: office_attack_expression(
                        objective, expression_id  # type: ignore[arg-type]
                    )
                    for objective in self.attack_objectives
                },
            )
            for expression_id in self.expression_ids
        )
        return ScenarioCatalogManifest(
            scenario=build_catalog_lock(
                catalog_id="office-scenarios",
                catalog_version=OFFICE_SCENARIO_CATALOG_VERSION,
                items=((self.scenario.template_id, self.scenario),),
            ),
            benign_tasks=build_catalog_lock(
                catalog_id="office-benign-tasks",
                catalog_version=OFFICE_BENIGN_TASK_CATALOG_VERSION,
                items=tuple((item.task_id, item) for item in self.benign_tasks),
            ),
            attack_objectives=build_catalog_lock(
                catalog_id="office-attack-objectives",
                catalog_version=OFFICE_ATTACK_OBJECTIVE_CATALOG_VERSION,
                items=tuple(
                    (item.objective_id, item) for item in self.attack_objectives
                ),
            ),
            injection_carriers=build_catalog_lock(
                catalog_id="office-injection-carriers",
                catalog_version=OFFICE_INJECTION_CARRIER_CATALOG_VERSION,
                items=tuple(
                    (item.carrier_id, item) for item in self.injection_carriers
                ),
            ),
            attack_expressions=build_catalog_lock(
                catalog_id="office-attack-expressions",
                catalog_version=OFFICE_ATTACK_EXPRESSION_CATALOG_VERSION,
                items=tuple(expressions),
            ),
        )


OFFICE_V1_CANDIDATE_CATALOG = OfficeCandidateCatalog(
    scenario=OFFICE_V1,
    benign_tasks=OFFICE_BENIGN_TASKS,
    attack_objectives=OFFICE_ATTACK_OBJECTIVES,
    injection_carriers=OFFICE_INJECTION_CARRIERS,
    expression_ids=OFFICE_ATTACK_EXPRESSION_STYLES,
)
OFFICE_V1_CATALOG_MANIFEST = OFFICE_V1_CANDIDATE_CATALOG.manifest()


class CandidateGenerationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CandidateRejectionCode(StrEnum):
    UNKNOWN_TASK = "unknown_task"
    UNKNOWN_OBJECTIVE = "unknown_objective"
    UNKNOWN_CARRIER = "unknown_carrier"
    UNKNOWN_EXPRESSION = "unknown_expression"
    INVALID_BUDGET = "invalid_budget"
    INCOMPATIBLE_COMPOSITION = "incompatible_composition"
    INVALID_TEST_CASE = "invalid_test_case"


class CandidateSelection(FrozenContract):
    selection_id: Identifier
    task_id: Identifier
    objective_id: Identifier
    carrier_id: Identifier
    expression_id: Identifier
    agent: AgentConfig
    budget: dict[str, Any] = Field(
        default_factory=lambda: ExecutionBudget().model_dump(mode="python")
    )
    seed: int = 0
    parent_case_id: Identifier | None = None
    content_digest: str | None = None

    @model_validator(mode="after")
    def validate_digest(self) -> CandidateSelection:
        calculated = sha256_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if self.content_digest is not None:
            supplied = normalize_sha256_digest(self.content_digest)
            if supplied != calculated:
                raise ValueError("candidate selection content_digest does not match")
        object.__setattr__(self, "content_digest", calculated)
        return self

    def assert_integrity(self) -> None:
        current = sha256_digest(
            self.model_dump(mode="json", exclude={"content_digest"})
        )
        if current != self.content_digest:
            raise ValueError("candidate selection no longer matches content_digest")


class CandidateRejection(FrozenContract):
    code: CandidateRejectionCode
    issue_codes: tuple[CompositionIssueCode, ...] = Field(default_factory=tuple)
    detail: str = Field(min_length=1, max_length=2_000)

    @field_validator("issue_codes")
    @classmethod
    def issues_are_canonical(
        cls, value: tuple[CompositionIssueCode, ...]
    ) -> tuple[CompositionIssueCode, ...]:
        if len(value) != len(set(value)):
            raise ValueError("candidate rejection issue_codes must not contain duplicates")
        return tuple(sorted(value, key=lambda item: item.value))


class CandidateGenerationResult(FrozenContract):
    selection_id: Identifier
    request_digest: str
    status: CandidateGenerationStatus
    candidate: TestCase | None = None
    rejection: CandidateRejection | None = None
    result_digest: str | None = None

    @field_validator("request_digest")
    @classmethod
    def request_digest_is_canonical(cls, value: str) -> str:
        return normalize_sha256_digest(value)

    @model_validator(mode="after")
    def validate_result(self) -> CandidateGenerationResult:
        if self.status == CandidateGenerationStatus.ACCEPTED:
            if self.candidate is None or self.rejection is not None:
                raise ValueError("accepted generation result requires only a candidate")
            self.candidate.assert_integrity()
        elif self.rejection is None or self.candidate is not None:
            raise ValueError("rejected generation result requires only a rejection")
        calculated = sha256_digest(
            self.model_dump(mode="json", exclude={"result_digest"})
        )
        if self.result_digest is not None:
            supplied = normalize_sha256_digest(self.result_digest)
            if supplied != calculated:
                raise ValueError("candidate generation result_digest does not match")
        object.__setattr__(self, "result_digest", calculated)
        return self

    def assert_integrity(self) -> None:
        if self.candidate is not None:
            self.candidate.assert_integrity()
        current = sha256_digest(
            self.model_dump(mode="json", exclude={"result_digest"})
        )
        if current != self.result_digest:
            raise ValueError("candidate generation result no longer matches result_digest")


class OfficeCandidateGenerator:
    """Resolve locked office components and reuse TestCase as the final safety gate."""

    def __init__(
        self,
        manifest: ScenarioCatalogManifest,
        catalog: OfficeCandidateCatalog = OFFICE_V1_CANDIDATE_CATALOG,
    ) -> None:
        manifest.assert_integrity()
        self.manifest = manifest
        self.catalog = catalog
        self.assert_catalog_integrity()
        self._tasks = {item.task_id: item for item in catalog.benign_tasks}
        self._objectives = {
            item.objective_id: item for item in catalog.attack_objectives
        }
        self._carriers = {
            item.carrier_id: item for item in catalog.injection_carriers
        }

    def assert_catalog_integrity(self) -> None:
        """Verify that the executable catalogs still match the campaign locks."""
        current = self.catalog.manifest()
        current.assert_integrity()
        for field_name in (
            "scenario",
            "benign_tasks",
            "attack_objectives",
            "injection_carriers",
            "attack_expressions",
        ):
            supplied_lock: CatalogLock = getattr(self.manifest, field_name)
            current_lock: CatalogLock = getattr(current, field_name)
            if supplied_lock != current_lock:
                raise CatalogIntegrityError(
                    f"campaign {field_name} catalog lock does not match executable catalog"
                )

    @classmethod
    def from_campaign_manifest(
        cls,
        campaign_manifest: object,
        catalog: OfficeCandidateCatalog = OFFICE_V1_CANDIDATE_CATALOG,
    ) -> OfficeCandidateGenerator:
        manifest = getattr(campaign_manifest, "scenario_catalogs", None)
        if manifest is None:
            raise CatalogIntegrityError("campaign manifest is missing scenario catalog locks")
        return cls(manifest, catalog)

    def generate(self, selection: CandidateSelection) -> CandidateGenerationResult:
        selection.assert_integrity()
        self.assert_catalog_integrity()
        request_digest = sha256_digest(
            {
                "catalog_manifest_digest": self.manifest.content_digest,
                "selection": selection,
            }
        )
        task = self._tasks.get(selection.task_id)
        if task is None:
            return self._rejected(
                selection, request_digest, CandidateRejectionCode.UNKNOWN_TASK,
                f"unknown benign task: {selection.task_id}",
            )
        objective = self._objectives.get(selection.objective_id)
        if objective is None:
            return self._rejected(
                selection, request_digest, CandidateRejectionCode.UNKNOWN_OBJECTIVE,
                f"unknown attack objective: {selection.objective_id}",
            )
        carrier = self._carriers.get(selection.carrier_id)
        if carrier is None:
            return self._rejected(
                selection, request_digest, CandidateRejectionCode.UNKNOWN_CARRIER,
                f"unknown injection carrier: {selection.carrier_id}",
            )
        if selection.expression_id not in self.catalog.expression_ids:
            return self._rejected(
                selection, request_digest, CandidateRejectionCode.UNKNOWN_EXPRESSION,
                f"unknown attack expression: {selection.expression_id}",
            )
        try:
            budget = ExecutionBudget.model_validate(selection.budget)
        except ValidationError as exc:
            return self._rejected(
                selection,
                request_digest,
                CandidateRejectionCode.INVALID_BUDGET,
                self._validation_detail(exc),
            )

        payload = office_attack_expression(
            objective, selection.expression_id  # type: ignore[arg-type]
        )
        try:
            attack = AttackBinding(
                objective=objective,
                carrier=carrier,
                payload=payload,
            )
        except ValidationError as exc:
            return self._rejected(
                selection,
                request_digest,
                CandidateRejectionCode.INVALID_TEST_CASE,
                self._validation_detail(exc),
            )
        assessment = assess_attack_compatibility(task, attack)
        if not assessment.compatible:
            return self._rejected(
                selection,
                request_digest,
                CandidateRejectionCode.INCOMPATIBLE_COMPOSITION,
                "; ".join(issue.message for issue in assessment.issues),
                issue_codes=tuple(issue.code for issue in assessment.issues),
            )
        try:
            candidate = TestCase(
                case_id="office-candidate-" + request_digest.removeprefix("sha256:")[:24],
                scenario=self.catalog.scenario,
                benign_task=task,
                attack=attack,
                agent=selection.agent,
                budget=budget,
                seed=selection.seed,
                parent_case_id=selection.parent_case_id,
            )
        except ValidationError as exc:
            return self._rejected(
                selection,
                request_digest,
                CandidateRejectionCode.INVALID_TEST_CASE,
                self._validation_detail(exc),
            )
        return CandidateGenerationResult(
            selection_id=selection.selection_id,
            request_digest=request_digest,
            status=CandidateGenerationStatus.ACCEPTED,
            candidate=candidate,
        )

    @staticmethod
    def _validation_detail(error: ValidationError) -> str:
        return "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_url=False)
        )[:2_000]

    @staticmethod
    def _rejected(
        selection: CandidateSelection,
        request_digest: str,
        code: CandidateRejectionCode,
        detail: str,
        *,
        issue_codes: tuple[CompositionIssueCode, ...] = (),
    ) -> CandidateGenerationResult:
        return CandidateGenerationResult(
            selection_id=selection.selection_id,
            request_digest=request_digest,
            status=CandidateGenerationStatus.REJECTED,
            rejection=CandidateRejection(
                code=code,
                issue_codes=issue_codes,
                detail=detail,
            ),
        )
