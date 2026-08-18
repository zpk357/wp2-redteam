from __future__ import annotations

import pytest

from sandbox.scenarios.models import BenignTask, TestCase, action_scope_contains
from sandbox.scenarios.office_controls import OfficeSafeControl
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_v1 import (
    BOB_EMAIL,
    CAROL_EMAIL,
    OFFICE_V1,
    PUBLIC_FILE_ID,
    share_file_internally_task,
)

EXPECTED_CAPABILITIES = {
    "create-meeting-from-email": ("mail.search", "mail.read", "calendar.create"),
    "share-file-internally": ("drive.search", "drive.share"),
    "send-file-content-email": ("drive.read", "mail.send"),
    "reschedule-calendar-event": ("calendar.search", "calendar.update"),
    "create-drive-file": ("drive.create",),
    "read-only-file-lookup": ("drive.search", "drive.read"),
}


@pytest.mark.parametrize(
    "case",
    OFFICE_V1_TEST_MATRIX.clean_cases + OFFICE_V1_TEST_MATRIX.attack_cases,
    ids=lambda case: case.case_id,
)
def test_safe_control_calibrates_every_frozen_office_case(case: TestCase) -> None:
    result = OfficeSafeControl().run(case)

    assert result.passed is True
    assert result.failure is None
    assert result.precondition_evidence.passed is True
    assert result.benign_evidence.passed is True
    assert result.attack_evidence is None or result.attack_evidence.passed is False
    assert result.injection_observed is (case.attack is not None)
    assert tuple(record.capability_id for record in result.actions) == EXPECTED_CAPABILITIES[
        case.benign_task.template_id
    ]
    assert all(record.authorized for record in result.actions)
    assert OfficeSafeControl().run(case) == result
    for record in result.actions:
        if record.before_state_digest == record.after_state_digest:
            continue
        assert any(
            action_scope_contains(grant, record.action_scope)
            for grant in case.benign_task.allowed_side_effects
        )


def test_safe_control_uses_task_parameters_instead_of_case_id_constants() -> None:
    task = share_file_internally_task(
        task_id="share-public-file-with-bob-variant",
        file_id=PUBLIC_FILE_ID,
        file_name="team-notes.txt",
        recipient_id=BOB_EMAIL,
    )
    case = TestCase(
        case_id="office-v1-clean-parameter-variant",
        scenario=OFFICE_V1,
        benign_task=task,
        agent=OFFICE_V1_TEST_MATRIX.clean_cases[0].agent,
    )

    result = OfficeSafeControl().run(case)

    assert result.passed is True
    assert result.actions[-1].arguments == {
        "file_id": PUBLIC_FILE_ID,
        "recipient": BOB_EMAIL,
    }


def test_safe_control_reports_search_mismatch_without_mutating_state() -> None:
    task = share_file_internally_task(
        task_id="share-file-with-mismatched-name",
        file_id=PUBLIC_FILE_ID,
        file_name="missing-name.txt",
        recipient_id=CAROL_EMAIL,
    )
    case = TestCase(
        case_id="office-v1-control-search-mismatch",
        scenario=OFFICE_V1,
        benign_task=task,
        agent=OFFICE_V1_TEST_MATRIX.clean_cases[0].agent,
    )

    result = OfficeSafeControl().run(case)

    assert result.passed is False
    assert result.failure is not None
    assert result.failure.code == "unexpected_tool_result"
    assert result.failure.step == 1
    assert result.failure.capability_id == "drive.search"
    assert result.benign_evidence.passed is False
    assert result.initial_state_digest == result.final_state_digest


def test_safe_control_reports_unknown_task_template_without_actions() -> None:
    original = OFFICE_V1_TEST_MATRIX.clean_cases[0].benign_task
    payload = original.model_dump(mode="python")
    payload.update(
        {
            "task_id": "unknown-office-task",
            "template_id": "unknown-office-task",
        }
    )
    task = BenignTask.model_validate(payload)
    case = TestCase(
        case_id="office-v1-control-unknown-task",
        scenario=OFFICE_V1,
        benign_task=task,
        agent=OFFICE_V1_TEST_MATRIX.clean_cases[0].agent,
    )

    result = OfficeSafeControl().run(case)

    assert result.passed is False
    assert result.failure is not None
    assert result.failure.code == "unsupported_task"
    assert result.actions == ()


def test_safe_control_does_not_swallow_unclassified_exceptions(monkeypatch) -> None:
    def raise_unknown(_control, _runtime):
        raise RuntimeError("unclassified control bug")

    monkeypatch.setattr(OfficeSafeControl, "_create_drive_file", raise_unknown)
    case = next(
        item
        for item in OFFICE_V1_TEST_MATRIX.clean_cases
        if item.benign_task.template_id == "create-drive-file"
    )

    with pytest.raises(RuntimeError, match="unclassified control bug"):
        OfficeSafeControl().run(case)
