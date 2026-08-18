from __future__ import annotations

import json
from pathlib import Path

from sandbox.replay.digests import sha256_digest
from scripts.build_office_v2_stage4_evidence import validate_stage4_evidence

REPOSITORY_ROOT = Path(__file__).parents[2]
EVIDENCE_PATH = (
    REPOSITORY_ROOT
    / "reports"
    / "local-acceptance"
    / "office-v2-stage4"
    / "stage4-evidence.json"
)


def test_checked_in_stage4_evidence_is_valid_and_self_digesting() -> None:
    payload = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    digest = payload.pop("evidence_digest")

    validate_stage4_evidence(payload)

    assert sha256_digest(payload) == digest
    assert payload["limitations"] == {
        "real_model_used": False,
        "docker_used": False,
        "scripted_driver_proves_model_understanding": False,
    }
