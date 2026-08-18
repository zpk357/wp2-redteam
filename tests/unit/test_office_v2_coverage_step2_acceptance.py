from __future__ import annotations

from scripts.build_office_v2_coverage_step2_evidence import (
    build_step2_evidence,
    validate_step2_evidence,
)


def test_step2_acceptance_evidence_has_ten_passing_comparisons() -> None:
    payload = build_step2_evidence()

    validate_step2_evidence(payload)
    assert payload["status"] == "passed"
    assert len(payload["comparisons"]) == 10
    assert all(item["passed"] for item in payload["comparisons"])
    assert payload["limitations"] == {
        "docker_run_performed": False,
        "real_qwen_run_performed": False,
        "judge_run_performed": False,
        "corpus_or_mutation_implemented": False,
        "frozen_stage2_to_stage8_evidence_rebuilt": False,
    }
