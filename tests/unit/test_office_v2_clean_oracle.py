from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASES
from sandbox.scenarios.office_v2.oracle import evaluate_clean_scenario_oracle
from sandbox.scenarios.office_v2.oracle_evidence import (
    build_clean_oracle_evidence_bundle,
    build_termination_fact,
)


def test_clean_case_has_no_fabricated_attack_intent() -> None:
    clean_case = CLEAN_CASES[0]
    termination = build_termination_fact(
        evidence_id="termination.clean.001",
        sequence=0,
        reason="max_steps",
        submitted=False,
        output_digest=sha256_digest({"reason": "max_steps"}),
    )
    bundle = build_clean_oracle_evidence_bundle(
        clean_case=clean_case,
        invocations=(),
        results=(),
        interaction_facts=(),
        timeline=None,
        termination=termination,
        initial_state_digest=clean_case.base_world_digest,
        final_state_digest=clean_case.base_world_digest,
    )

    result = evaluate_clean_scenario_oracle(
        bundle=bundle,
        clean_case=clean_case,
    )

    assert result.scenario_case_id == clean_case.case_id
    assert result.scenario_case_digest == clean_case.case_digest
    assert result.security.exposure_facts == ()
    assert result.security.planned_objectives == ()
    assert result.security.violations == ()
    assert result.utility.goal_facts
