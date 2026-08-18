from __future__ import annotations

import json
from pathlib import Path

from sandbox.replay.digests import sha256_digest
from scripts.validate_office_v2_stage7_9_evidence import (
    validate_stage7_9_evidence,
)

EVIDENCE = (
    Path(__file__).resolve().parents[2]
    / "reports"
    / "local-acceptance"
    / "office-v2-stage7-9"
    / "stage7-9-evidence.json"
)


def test_stage7_9_evidence_is_valid_and_self_addressed() -> None:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    digest = payload.pop("evidence_digest")

    validate_stage7_9_evidence(payload)

    assert sha256_digest(payload) == digest
