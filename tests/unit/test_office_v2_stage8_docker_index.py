from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.build_office_v2_stage8_docker_index import (
    build_stage8_docker_index,
    validate_stage8_docker_index,
)


def test_stage8_docker_index_reuses_complete_existing_evidence() -> None:
    evidence = build_stage8_docker_index()

    assert evidence["decision"]["episodes_rerun"] is False
    assert evidence["all_required_docker_evidence_present"] is True
    assert all(item["passed"] for item in evidence["coverage"].values())
    assert evidence["limitations"]["real_qwen_used"] is False


def test_stage8_docker_index_rejects_missing_required_evidence() -> None:
    evidence = build_stage8_docker_index()
    evidence.pop("evidence_digest")
    tampered = deepcopy(evidence)
    tampered["coverage"]["strict_replay"]["passed"] = False

    with pytest.raises(ValueError, match="Docker evidence requirement"):
        validate_stage8_docker_index(tampered)
