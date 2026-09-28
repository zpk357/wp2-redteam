from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.scenarios.models import ActionScope, BenignTask, ToolEvidence
from sandbox.scenarios.models import TestCase as ScenarioTestCase
from sandbox.scenarios.office_v1 import (
    ATTACK_RECIPIENT,
    BOB_EMAIL,
    CAROL_EMAIL,
    FAKE_AGENT,
    OFFICE_BENIGN_TASKS,
    OFFICE_V1,
    PUBLIC_FILE_ID,
    create_drive_file_task,
    read_only_file_lookup_task,
    share_file_internally_task,
)


def _case_for(task: BenignTask, index: int = 0) -> ScenarioTestCase:
    return ScenarioTestCase(
        case_id=f"office-benign-{index:03d}",
        scenario=OFFICE_V1,
        benign_task=task,
        agent=FAKE_AGENT,
    )


def test_office_catalog_has_six_distinct_parameterized_task_families() -> None:
    template_ids = [task.template_id for task in OFFICE_BENIGN_TASKS]

    assert len(OFFICE_BENIGN_TASKS) == 6
    assert len(set(template_ids)) == 6
    assert all(task.parameters for task in OFFICE_BENIGN_TASKS)


def test_every_default_task_composes_as_a_clean_frozen_case() -> None:
    for index, task in enumerate(OFFICE_BENIGN_TASKS):
        case = _case_for(task, index)

        case.assert_integrity()
        assert case.attack is None
        assert set(task.required_capabilities).issubset(OFFICE_V1.provided_capabilities)
        assert set(task.allowed_side_effects).issubset(task.authorized_actions)


def test_every_parameter_delegation_has_one_declared_content_exposure() -> None:
    for task in OFFICE_BENIGN_TASKS:
        for delegation in task.parameter_delegations:
            matches = [
                exposure
                for exposure in task.content_exposures
                if exposure.source == delegation.source
            ]
            assert len(matches) == 1


def test_catalog_separates_read_only_and_state_changing_tasks() -> None:
    read_only = next(
        task for task in OFFICE_BENIGN_TASKS if task.template_id == "read-only-file-lookup"
    )
    state_changing = [task for task in OFFICE_BENIGN_TASKS if task is not read_only]

    assert read_only.allowed_side_effects == ()
    assert any(isinstance(item, ToolEvidence) for item in read_only.success_evidence)
    assert all(task.allowed_side_effects for task in state_changing)


def test_normal_tasks_do_not_authorize_the_external_attack_principal() -> None:
    for task in OFFICE_BENIGN_TASKS:
        recipients = {
            recipient
            for action in task.authorized_actions
            for recipient in action.recipient_ids
        }
        assert ATTACK_RECIPIENT not in recipients
        assert ATTACK_RECIPIENT not in task.instruction


def test_share_task_parameters_change_grants_evidence_and_case_digest() -> None:
    carol_task = share_file_internally_task(
        task_id="share-team-notes-carol-variant",
        file_id=PUBLIC_FILE_ID,
        file_name="team-notes.txt",
        recipient_id=CAROL_EMAIL,
    )
    bob_task = share_file_internally_task(
        task_id="share-team-notes-bob-variant",
        file_id=PUBLIC_FILE_ID,
        file_name="team-notes.txt",
        recipient_id=BOB_EMAIL,
    )

    carol_case = _case_for(carol_task, 10)
    bob_case = _case_for(bob_task, 11)

    assert carol_task.template_id == bob_task.template_id
    assert carol_task.parameters["recipient_id"] == CAROL_EMAIL
    assert bob_task.parameters["recipient_id"] == BOB_EMAIL
    assert carol_task.authorized_actions != bob_task.authorized_actions
    assert carol_task.success_evidence != bob_task.success_evidence
    assert carol_case.content_digest != bob_case.content_digest


def test_unknown_resource_and_recipient_are_rejected_when_composed() -> None:
    missing_file = share_file_internally_task(
        task_id="invalid-missing-file",
        file_id="missing-file",
        file_name="missing.txt",
        recipient_id=CAROL_EMAIL,
    )
    unknown_recipient = share_file_internally_task(
        task_id="invalid-unknown-recipient",
        file_id=PUBLIC_FILE_ID,
        file_name="team-notes.txt",
        recipient_id="unknown@example.internal",
    )

    with pytest.raises(ValidationError, match="references unknown item"):
        _case_for(missing_file, 20)
    with pytest.raises(ValidationError, match="references unknown item|unknown recipients"):
        _case_for(unknown_recipient, 21)


def test_task_rejects_side_effect_that_is_not_authorized() -> None:
    task = OFFICE_BENIGN_TASKS[0]
    payload = task.model_dump(mode="python")
    payload["allowed_side_effects"] = [
        ActionScope(
            capability_id="drive.share",
            resource=None,
            recipient_ids=(CAROL_EMAIL,),
        ).model_dump(mode="python")
    ]

    with pytest.raises(ValidationError, match="must also appear in authorized_actions"):
        BenignTask.model_validate(payload)


def test_read_only_task_requires_tool_or_final_state_evidence() -> None:
    task = read_only_file_lookup_task(
        task_id="read-team-notes-variant",
        file_id=PUBLIC_FILE_ID,
        file_name="team-notes.txt",
    )
    payload = task.model_dump(mode="python")
    payload["success_evidence"] = [{"kind": "model_answer", "text": "done"}]

    with pytest.raises(ValidationError, match="union_tag_invalid"):
        BenignTask.model_validate(payload)


def test_create_file_task_rejects_unknown_sensitivity() -> None:
    with pytest.raises(ValueError, match="unsupported file sensitivity"):
        create_drive_file_task(
            task_id="invalid-file-sensitivity",
            file_name="notes.txt",
            content="notes",
            sensitivity="secret",
        )
