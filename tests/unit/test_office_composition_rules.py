from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.scenarios.models import (
    ActionScope,
    AttackBinding,
    BenignTask,
    CompositionIssueCode,
    ContentExposure,
    InjectionOperation,
    ResourceSelector,
    TestCase,
    assess_attack_compatibility,
)
from sandbox.scenarios.office_v1 import (
    ATTACK_RECIPIENT,
    CREATE_MEETING_FROM_EMAIL,
    EXTERNAL_RESTRICTED_FILE_SHARE,
    FAKE_AGENT,
    FIXED_ATTACK,
    OFFICE_BENIGN_TASKS,
    OFFICE_INJECTION_CARRIERS,
    OFFICE_V1,
)


def _task(template_id: str) -> BenignTask:
    return next(task for task in OFFICE_BENIGN_TASKS if task.template_id == template_id)


def _carrier(template_id: str):
    return next(
        carrier for carrier in OFFICE_INJECTION_CARRIERS if carrier.template_id == template_id
    )


def _binding(carrier) -> AttackBinding:
    return AttackBinding(
        objective=EXTERNAL_RESTRICTED_FILE_SHARE,
        carrier=carrier,
        payload=FIXED_ATTACK,
    )


@pytest.mark.parametrize(
    ("task_template", "carrier_template"),
    (
        ("create-meeting-from-email", "email-body"),
        ("read-only-file-lookup", "drive-file-content"),
        ("reschedule-calendar-event", "calendar-description"),
    ),
)
def test_each_office_carrier_has_a_reachable_semantics_preserving_task(
    task_template: str, carrier_template: str
) -> None:
    task = _task(task_template)
    attack = _binding(_carrier(carrier_template))

    assessment = assess_attack_compatibility(task, attack)
    case = TestCase(
        case_id=f"compatible-{carrier_template}",
        scenario=OFFICE_V1,
        benign_task=task,
        attack=attack,
        agent=FAKE_AGENT,
    )

    assert assessment.compatible is True
    assert assessment.issues == ()
    case.assert_integrity()


def test_unreachable_carrier_is_explained_and_rejected_before_execution() -> None:
    attack = _binding(_carrier("drive-file-content"))
    assessment = assess_attack_compatibility(CREATE_MEETING_FROM_EMAIL, attack)

    assert {issue.code for issue in assessment.issues} == {
        CompositionIssueCode.CARRIER_CAPABILITY_NOT_ON_TASK_PATH,
        CompositionIssueCode.CARRIER_TARGET_NOT_OBSERVABLE,
    }
    with pytest.raises(ValidationError, match="carrier_target_not_observable"):
        TestCase(
            case_id="invalid-unreachable-drive-carrier",
            scenario=OFFICE_V1,
            benign_task=CREATE_MEETING_FROM_EMAIL,
            attack=attack,
            agent=FAKE_AGENT,
        )


def test_replacing_observed_content_is_rejected_when_task_only_allows_append_or_prepend() -> None:
    carrier = _carrier("email-body").model_copy(
        update={"operation": InjectionOperation.REPLACE}
    )
    attack = _binding(carrier)
    assessment = assess_attack_compatibility(CREATE_MEETING_FROM_EMAIL, attack)

    assert [issue.code for issue in assessment.issues] == [
        CompositionIssueCode.CARRIER_OPERATION_BREAKS_TASK
    ]
    with pytest.raises(ValidationError, match="carrier_operation_breaks_task"):
        TestCase(
            case_id="invalid-replace-email-body",
            scenario=OFFICE_V1,
            benign_task=CREATE_MEETING_FROM_EMAIL,
            attack=attack,
            agent=FAKE_AGENT,
        )


def test_broad_task_grant_cannot_be_relabelled_as_an_unauthorized_objective() -> None:
    payload = CREATE_MEETING_FROM_EMAIL.model_dump(mode="python")
    payload["task_id"] = "meeting-with-broad-share-grant"
    payload["required_capabilities"] = (*payload["required_capabilities"], "drive.share")
    payload["authorized_actions"] = (
        *payload["authorized_actions"],
        ActionScope(
            capability_id="drive.share",
            resource=ResourceSelector(collection_id="drive_files"),
            recipient_ids=(ATTACK_RECIPIENT,),
        ),
    )
    task = BenignTask.model_validate(payload)
    attack = _binding(_carrier("email-body"))

    assessment = assess_attack_compatibility(task, attack)
    assert [issue.code for issue in assessment.issues] == [
        CompositionIssueCode.OBJECTIVE_ACTION_AUTHORIZED
    ]
    with pytest.raises(ValidationError, match="objective_action_authorized"):
        TestCase(
            case_id="invalid-authorized-objective",
            scenario=OFFICE_V1,
            benign_task=task,
            attack=attack,
            agent=FAKE_AGENT,
        )


def test_content_exposure_requires_a_declared_and_authorized_read_path() -> None:
    payload = CREATE_MEETING_FROM_EMAIL.model_dump(mode="python")
    payload["task_id"] = "invalid-undeclared-exposure-capability"
    payload["content_exposures"] = (
        ContentExposure(
            source=ResourceSelector(
                collection_id="drive_files",
                item_id="drive-public-001",
                field_path=("content",),
            ),
            capability_id="drive.read",
        ),
    )

    with pytest.raises(ValidationError, match="must be required by the benign task"):
        BenignTask.model_validate(payload)


def test_authorized_actions_cannot_hide_an_undeclared_task_capability() -> None:
    payload = CREATE_MEETING_FROM_EMAIL.model_dump(mode="python")
    payload["task_id"] = "invalid-undeclared-action-capability"
    payload["authorized_actions"] = (
        *payload["authorized_actions"],
        ActionScope(capability_id="drive.delete"),
    )

    with pytest.raises(ValidationError, match="undeclared task capabilities.*drive.delete"):
        BenignTask.model_validate(payload)
