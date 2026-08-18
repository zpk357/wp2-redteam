from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.build_office_v2_stage8_structure_evidence import (
    build_stage8_structure_evidence,
    validate_stage8_structure_evidence,
)


def test_stage8_structure_evidence_recomputes_all_frozen_gates() -> None:
    evidence = build_stage8_structure_evidence()

    assert evidence["all_required_gates_passed"] is True
    assert evidence["gates"]["task_blueprints"]["actual"] == 10
    assert evidence["gates"]["clean_cases"]["actual"] == 24
    assert evidence["gates"]["normalized_reference_paths"]["actual"] == 12
    assert evidence["gates"]["attack_objectives"]["actual"] == 12
    assert len(evidence["gates"]["compound_objectives"]["actual"]) == 6
    assert len(evidence["gates"]["distinct_state_write_tools"]["actual"]) == 9
    assert evidence["gates"]["trusted_temporary_grants"]["actual"] == 2
    assert evidence["gates"]["unchanged_rejections"]["actual"] == 2
    assert evidence["conditional_same_expression_binding_gate"]["applicable"] is False


def test_stage8_structure_evidence_rejects_a_failed_or_tampered_gate() -> None:
    evidence = build_stage8_structure_evidence()
    evidence.pop("evidence_digest")
    tampered = deepcopy(evidence)
    tampered["gates"]["attack_objectives"]["passed"] = False

    with pytest.raises(ValueError, match="structural gate"):
        validate_stage8_structure_evidence(tampered)
