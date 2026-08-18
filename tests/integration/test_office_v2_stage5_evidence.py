from __future__ import annotations

import json
from pathlib import Path

from sandbox.replay.digests import sha256_digest
from scripts.build_office_v2_stage5_evidence import validate_stage5_evidence

REPOSITORY_ROOT = Path(__file__).parents[2]
EVIDENCE_PATH = (
    REPOSITORY_ROOT / "reports" / "local-acceptance" / "office-v2-stage5" / "stage5-evidence.json"
)


def test_checked_in_stage5_evidence_is_valid_and_self_digesting() -> None:
    payload = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    digest = payload.pop("evidence_digest")

    validate_stage5_evidence(payload)

    assert sha256_digest(payload) == digest
    assert digest == ("sha256:b44931cdd4e7e2173771f2b356860dc937fb253ff4a0fce8cf9a2f77bb50fe04")
    assert payload["limitations"] == {
        "real_model_used": False,
        "docker_used": False,
        "scripted_driver_proves_model_understanding": False,
        "witness_proves_toolruntime_feasibility_only": True,
        "stage6_oracle_used": False,
        "coverage_or_mutation_used": False,
        "representatives_are_production_search_space": False,
    }
