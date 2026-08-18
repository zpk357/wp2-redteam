from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.build_office_v2_stage8_v1_disposition import (
    build_stage8_v1_disposition,
    validate_stage8_v1_disposition,
)


def test_stage8_v1_disposition_disables_formal_entry_and_retains_source() -> None:
    evidence = build_stage8_v1_disposition()

    assert evidence["v2_import_boundary"]["passed"] is True
    assert evidence["legacy_production_entry_active"] is False
    assert evidence["formal_entry_disabled"] is True
    assert evidence["status"] == "formal_v1_entry_disabled"
    assert evidence["deletion_performed"] is False
    assert evidence["production_reachability"][
        "legacy_coverage_implementation_present"
    ]["active"] is True
    assert evidence["formal_entry_preconditions"][
        "formal_live_runtime_requires_v2"
    ]["met"] is True


def test_stage8_v1_disposition_rejects_false_blocked_status() -> None:
    evidence = build_stage8_v1_disposition()
    evidence.pop("evidence_digest")
    tampered = deepcopy(evidence)
    tampered["status"] = "blocked"

    with pytest.raises(ValueError, match="status does not match"):
        validate_stage8_v1_disposition(tampered)
