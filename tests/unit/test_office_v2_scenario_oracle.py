from __future__ import annotations

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_cases import (
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.oracle import evaluate_scenario_oracle
from sandbox.scenarios.office_v2.oracle_evidence import (
    build_oracle_evidence_bundle,
    build_termination_fact,
)
from sandbox.scenarios.office_v2.oracle_models import (
    SCENARIO_ORACLE_RESULT_ADAPTER,
    MilestoneOutcome,
    OracleResultStatus,
)


def _materialization(*, compound: bool):
    return next(
        fixture.materialization
        for fixture in build_representative_scenario_fixtures()
        if (fixture.materialization.scenario_case.attack_objective.milestone_graph is not None)
        is compound
    )


def _bundle(materialization, *, submitted: bool = False):
    case = materialization.scenario_case
    reason = "submit" if submitted else "max_steps"
    termination = build_termination_fact(
        evidence_id="termination.oracle.001",
        sequence=0,
        reason=reason,
        submitted=submitted,
        output_digest=sha256_digest({"reason": reason}),
    )
    return build_oracle_evidence_bundle(
        scenario_case=case,
        initialization_transition=materialization.initialization_transition,
        invocations=(),
        results=(),
        interaction_facts=(),
        timeline=None,
        termination=termination,
        final_state_digest=case.initial_world_digest,
    )


@pytest.mark.parametrize("compound", (False, True))
def test_scenario_oracle_composes_atomic_and_compound_objectives(
    compound: bool,
) -> None:
    materialization = _materialization(compound=compound)
    case = materialization.scenario_case
    result = evaluate_scenario_oracle(
        bundle=_bundle(materialization),
        scenario_case=case,
    )

    assert result.status is OracleResultStatus.COMPLETE
    assert result.scenario_case_id == case.case_id
    assert result.security.planned_objectives[0].objective_id == (
        case.attack_objective.objective_id
    )
    assert result.security.planned_objectives[0].milestone_facts
    assert all(
        item.outcome is MilestoneOutcome.NOT_REACHED
        for item in result.security.planned_objectives[0].milestone_facts
    )


def test_same_input_is_digest_stable_and_json_self_contained() -> None:
    materialization = _materialization(compound=True)
    bundle = _bundle(materialization)
    first = evaluate_scenario_oracle(
        bundle=bundle,
        scenario_case=materialization.scenario_case,
    )
    second = evaluate_scenario_oracle(
        bundle=bundle,
        scenario_case=materialization.scenario_case,
    )
    restored = SCENARIO_ORACLE_RESULT_ADAPTER.validate_json(first.model_dump_json())

    assert first == second == restored
    assert first.input_bundle_digest == bundle.bundle_digest
    assert first.evidence_closure
    assert {item.evidence_id for item in first.evidence_closure} == {
        bundle.task_ref.evidence_id,
        bundle.materialization_ref.evidence_id,
        bundle.initial_state_ref.evidence_id,
        bundle.final_state_ref.evidence_id,
        bundle.termination.evidence_id,
    }


def test_fact_change_changes_bundle_and_result_digests() -> None:
    materialization = _materialization(compound=True)
    stopped_bundle = _bundle(materialization, submitted=False)
    submitted_bundle = _bundle(materialization, submitted=True)
    stopped = evaluate_scenario_oracle(
        bundle=stopped_bundle,
        scenario_case=materialization.scenario_case,
    )
    submitted = evaluate_scenario_oracle(
        bundle=submitted_bundle,
        scenario_case=materialization.scenario_case,
    )

    assert stopped_bundle.bundle_digest != submitted_bundle.bundle_digest
    assert stopped.utility.fact_digest != submitted.utility.fact_digest
    assert stopped.result_digest != submitted.result_digest


def test_result_does_not_persist_adversarial_content() -> None:
    materialization = next(
        fixture.materialization
        for fixture in build_representative_scenario_fixtures()
        if fixture.materialization.scenario_case.adversarial_condition.entry_kind.value
        == "indirect_content"
    )
    condition = materialization.scenario_case.adversarial_condition
    result = evaluate_scenario_oracle(
        bundle=_bundle(materialization),
        scenario_case=materialization.scenario_case,
    )

    assert condition.adversarial_content not in result.model_dump_json()


def test_scenario_mismatch_fails_before_returning_a_partial_result() -> None:
    materialization = _materialization(compound=True)
    other = _materialization(compound=False)
    with pytest.raises(ValueError, match="task|scenario"):
        evaluate_scenario_oracle(
            bundle=_bundle(materialization),
            scenario_case=other.scenario_case,
        )
