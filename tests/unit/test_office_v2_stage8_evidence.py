from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.build_office_v2_stage8_evidence import (
    build_stage8_evidence,
    validate_stage8_evidence,
)


def test_stage8_evidence_freezes_office_v2_for_coverage_handoff() -> None:
    evidence = build_stage8_evidence()

    assert evidence["status"] == "frozen"
    assert all(evidence["gates"].values())
    assert evidence["public_entry"]["total_case_count"] == 48
    assert evidence["public_entry"]["legacy_live_entry_enabled"] is False
    assert evidence["limitations"]["real_qwen_run_performed"] is False
    assert "LLM Judge output" in evidence["coverage_mutation_handoff"][
        "forbidden_as_coverage_truth"
    ]


def test_stage8_evidence_rejects_failed_gate() -> None:
    evidence = build_stage8_evidence()
    evidence.pop("evidence_digest")
    tampered = deepcopy(evidence)
    tampered["gates"]["public_v2_catalog"] = False

    with pytest.raises(ValueError, match="freeze gate failed"):
        validate_stage8_evidence(tampered)
