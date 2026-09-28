from __future__ import annotations

from sandbox.scenarios.office_v2.attack_models import AttackEntryKind
from sandbox.scenarios.office_v2.attack_objectives import (
    ATTACK_OBJECTIVE_CATALOG,
    ATTACK_OBJECTIVES,
    STATE_WRITE_TOOLS,
)

EXPECTED_WRITE_TOOLS = {
    "cancel_calendar_event",
    "create_calendar_event",
    "create_drive_file",
    "delete_drive_file",
    "send_email",
    "share_drive_file",
    "update_calendar_event",
    "update_drive_permissions",
    "write_file",
}


def test_objective_catalog_has_the_frozen_shape_and_tool_coverage() -> None:
    assert len(ATTACK_OBJECTIVES) == 12
    assert sum(item.milestone_graph is not None for item in ATTACK_OBJECTIVES) == 6
    assert STATE_WRITE_TOOLS == EXPECTED_WRITE_TOOLS
    assert set(ATTACK_OBJECTIVES[0].compatible_entry_kinds) == set(AttackEntryKind)
    assert (
        ATTACK_OBJECTIVE_CATALOG.model_validate_json(ATTACK_OBJECTIVE_CATALOG.model_dump_json())
        == ATTACK_OBJECTIVE_CATALOG
    )


def test_objectives_are_resource_independent_declarative_templates() -> None:
    serialized = ATTACK_OBJECTIVE_CATALOG.model_dump_json().casefold()
    for fixed_case_value in ("apollo", "jordan", "maya", "nadia", "clean.t"):
        assert fixed_case_value not in serialized
    assert all(item.precondition_assertions for item in ATTACK_OBJECTIVES)
    assert all(item.attempted_assertions for item in ATTACK_OBJECTIVES)
    assert all(
        item.realized_assertions or item.milestone_graph is not None for item in ATTACK_OBJECTIVES
    )
