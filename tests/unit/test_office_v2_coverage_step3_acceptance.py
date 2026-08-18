from __future__ import annotations

from sandbox.replay.digests import sha256_digest
from scripts.build_office_v2_coverage_step3_evidence import build_evidence


def test_step3_evidence_is_self_checking_and_covers_frozen_contracts() -> None:
    evidence = build_evidence()
    digest = evidence.pop("evidence_digest")
    assert digest == sha256_digest(evidence)
    assert evidence["component_count"] == 6
    assert evidence["risk_frontier"] == {
        **evidence["risk_frontier"],
        "family_count": 4,
        "objective_count": 12,
        "milestone_frontier_count": 23,
    }
    assert evidence["behavior_frontier"]["distinct_frontier_count"] == 2
    assert evidence["baseline"]["complete"] is True
    assert evidence["single_candidate_contract"]["candidate_counts"] == (1, 1, 1)
    assert evidence["completion_contract"]["status"] == "saturated"
    assert all(not value for value in evidence["prohibited_runtime_used"].values())
