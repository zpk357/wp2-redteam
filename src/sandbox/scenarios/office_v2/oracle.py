"""Pure composition of deterministic Office V2 oracle evaluators."""

from __future__ import annotations

from collections.abc import Iterable

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_models import MaterializedScenarioCase
from sandbox.scenarios.office_v2.clean_cases import CleanCaseMaterialization
from sandbox.scenarios.office_v2.oracle_evidence import OracleEvidenceBundle
from sandbox.scenarios.office_v2.oracle_models import (
    CompleteScenarioOracleResult,
    EvidenceRef,
    SecurityFactSet,
    UtilityResult,
)
from sandbox.scenarios.office_v2.security_oracle import (
    evaluate_exposure,
    evaluate_planned_objective,
    evaluate_policy_violations,
)
from sandbox.scenarios.office_v2.utility_oracle import (
    UTILITY_ASSERTION_CATALOG,
    UtilityAssertionCatalog,
    evaluate_utility,
)


def _canonical_refs(*groups: Iterable[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    by_id: dict[str, EvidenceRef] = {}
    for ref in (item for group in groups for item in group):
        existing = by_id.setdefault(ref.evidence_id, ref)
        if existing != ref:
            raise ValueError("oracle evidence closure contains conflicting ids")
    return tuple(sorted(by_id.values(), key=lambda item: item.sort_key()))


def _bundle_evidence_closure(bundle: OracleEvidenceBundle) -> tuple[EvidenceRef, ...]:
    return _canonical_refs(
        (
            bundle.task_ref,
            bundle.materialization_ref,
            bundle.initial_state_ref,
            bundle.final_state_ref,
            bundle.termination.evidence_ref(),
        ),
        (ref for exchange in bundle.tool_exchanges for ref in exchange.evidence_refs()),
        (item.evidence_ref() for item in bundle.interaction_facts),
        (
            item.transition_ref
            for item in bundle.interaction_facts
            if item.transition_ref is not None
        ),
    )


def _security_fact_set(
    *,
    bundle: OracleEvidenceBundle,
    scenario_case: MaterializedScenarioCase,
) -> SecurityFactSet:
    exposure = evaluate_exposure(bundle=bundle, scenario_case=scenario_case)[0]
    objective = evaluate_planned_objective(
        objective=scenario_case.attack_objective,
        exposure_fact=exposure,
        bundle=bundle,
    )
    payload = {
        "exposure_facts": (exposure,),
        "planned_objectives": (objective,),
        "violations": evaluate_policy_violations(
            bundle=bundle,
            scenario_case=scenario_case,
        ),
    }
    draft = SecurityFactSet.model_construct(
        **payload,
        fact_digest="sha256:" + "0" * 64,
    )
    return SecurityFactSet(
        **payload,
        fact_digest=sha256_digest(draft.digest_payload()),
    )


def _clean_security_fact_set(
    *,
    bundle: OracleEvidenceBundle,
    clean_case: CleanCaseMaterialization,
) -> SecurityFactSet:
    payload = {
        "exposure_facts": (),
        "planned_objectives": (),
        "violations": evaluate_policy_violations(
            bundle=bundle,
            scenario_case=clean_case,
        ),
    }
    draft = SecurityFactSet.model_construct(
        **payload,
        fact_digest="sha256:" + "0" * 64,
    )
    return SecurityFactSet(
        **payload,
        fact_digest=sha256_digest(draft.digest_payload()),
    )


def _complete_result(
    *,
    bundle: OracleEvidenceBundle,
    scenario_case_id: str,
    scenario_case_digest: str,
    utility: UtilityResult,
    security: SecurityFactSet,
) -> CompleteScenarioOracleResult:
    payload = {
        "scenario_case_id": scenario_case_id,
        "scenario_case_digest": scenario_case_digest,
        "input_bundle_digest": bundle.bundle_digest,
        "initial_state_digest": bundle.identity.initial_state_digest,
        "final_state_digest": bundle.identity.final_state_digest,
        "utility": utility,
        "security": security,
        "evidence_closure": _bundle_evidence_closure(bundle),
    }
    draft = CompleteScenarioOracleResult.model_construct(
        **payload,
        result_digest="sha256:" + "0" * 64,
    )
    return CompleteScenarioOracleResult(
        **payload,
        result_digest=sha256_digest(draft.digest_payload()),
    )


def evaluate_scenario_oracle(
    *,
    bundle: OracleEvidenceBundle,
    scenario_case: MaterializedScenarioCase,
    utility_catalog: UtilityAssertionCatalog = UTILITY_ASSERTION_CATALOG,
) -> CompleteScenarioOracleResult:
    """Compose already-derived facts without re-deciding business semantics."""

    utility = evaluate_utility(
        bundle=bundle,
        task=scenario_case.task,
        catalog=utility_catalog,
    )
    security = _security_fact_set(bundle=bundle, scenario_case=scenario_case)
    return _complete_result(
        bundle=bundle,
        scenario_case_id=scenario_case.case_id,
        scenario_case_digest=scenario_case.content_digest,
        utility=utility,
        security=security,
    )


def evaluate_clean_scenario_oracle(
    *,
    bundle: OracleEvidenceBundle,
    clean_case: CleanCaseMaterialization,
    utility_catalog: UtilityAssertionCatalog = UTILITY_ASSERTION_CATALOG,
) -> CompleteScenarioOracleResult:
    """Evaluate clean execution facts without a fabricated planned objective."""

    utility = evaluate_utility(
        bundle=bundle,
        task=clean_case.task,
        catalog=utility_catalog,
    )
    security = _clean_security_fact_set(bundle=bundle, clean_case=clean_case)
    return _complete_result(
        bundle=bundle,
        scenario_case_id=clean_case.case_id,
        scenario_case_digest=clean_case.case_digest,
        utility=utility,
        security=security,
    )


__all__ = ["evaluate_clean_scenario_oracle", "evaluate_scenario_oracle"]
