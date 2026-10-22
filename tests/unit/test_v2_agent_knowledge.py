from __future__ import annotations

from types import SimpleNamespace

import pytest

from sandbox.scenarios.office_v2.agent_knowledge import (
    PreActionKnowledgeClass,
    ResourceKnowledge,
    build_pre_action_knowledge,
    classify_pre_action_knowledge,
)
from sandbox.scenarios.office_v2.models import AccessRight, ActionKind, ResourceKind, ResourceRef
from sandbox.scenarios.office_v2.policy import DecisionOutcome


def test_pre_action_knowledge_records_visible_read_only_resource() -> None:
    resource = ResourceRef(kind=ResourceKind.DRIVE_FILE, resource_id="drive.restricted")
    knowledge = build_pre_action_knowledge(
        invocation_id="invocation.001",
        sequence=0,
        actor_id="user.actor",
        task_id="task.001",
        logical_time=0,
        before_state_digest="sha256:" + "1" * 64,
        action=ActionKind.DELETE,
        required_right=AccessRight.DELETE,
        resources=(
            ResourceKnowledge(
                resource=resource,
                visible_before_action=True,
                rights_visible=True,
                observed_rights=(AccessRight.READ,),
                owner_or_organizer_id="user.owner",
                evidence_refs=("evidence.resource.001",),
            ),
        ),
        delegation_statement_visible=False,
        policy_statement_visible=True,
        agent_context_digest="sha256:" + "2" * 64,
        system_prompt_digest="sha256:" + "3" * 64,
        classification=PreActionKnowledgeClass.VISIBLE_READ_ONLY,
        evidence_refs=("evidence.resource.001", "evidence.policy.001"),
    )

    assert knowledge.all_targets_visible
    assert knowledge.classification is PreActionKnowledgeClass.VISIBLE_READ_ONLY
    assert knowledge.required_right is AccessRight.DELETE
    assert knowledge.knowledge_digest.startswith("sha256:")


def test_hidden_resource_cannot_claim_observed_permissions() -> None:
    resource = ResourceRef(kind=ResourceKind.WORKSPACE_FILE, resource_id="/workspace/a.txt")

    try:
        ResourceKnowledge(
            resource=resource,
            visible_before_action=False,
            observed_rights=(AccessRight.READ,),
        )
    except ValueError as exc:
        assert "hidden resource" in str(exc)
    else:
        raise AssertionError("hidden resources must not carry observed rights")


def test_hidden_resource_cannot_claim_a_synthetic_evidence_reference() -> None:
    with pytest.raises(ValueError, match="hidden resource"):
        ResourceKnowledge(
            resource=ResourceRef(
                kind=ResourceKind.DRIVE_FILE,
                resource_id="drive.hidden",
            ),
            visible_before_action=False,
            evidence_refs=("evidence.synthetic",),
        )


def test_visible_resource_requires_prior_evidence() -> None:
    with pytest.raises(ValueError, match="requires prior evidence"):
        ResourceKnowledge(
            resource=ResourceRef(
                kind=ResourceKind.DRIVE_FILE,
                resource_id="drive.visible",
            ),
            visible_before_action=True,
        )


def test_knowledge_classification_requires_visible_context_for_denial_labels() -> None:
    decision = SimpleNamespace(
        outcome=DecisionOutcome.ALLOWED,
        platform_allowed=True,
        delegation_allowed=False,
        effective_allowed=True,
    )
    assert classify_pre_action_knowledge(
        resources=(),
        required_right=None,
        delegation_statement_visible=False,
        policy_statement_visible=False,
        policy_decision=decision,
    ) is PreActionKnowledgeClass.AUTHORITY_UNKNOWN


@pytest.mark.parametrize(
    ("decision", "required_right", "delegation_visible", "policy_visible", "expected"),
    (
        (
            SimpleNamespace(
                outcome=DecisionOutcome.ALLOWED,
                platform_allowed=True,
                delegation_allowed=True,
                effective_allowed=True,
            ),
            AccessRight.READ,
            True,
            True,
            PreActionKnowledgeClass.EXPLICITLY_ALLOWED,
        ),
        (
            SimpleNamespace(
                outcome=DecisionOutcome.PLATFORM_DENIED,
                platform_allowed=False,
                delegation_allowed=True,
                effective_allowed=False,
            ),
            AccessRight.DELETE,
            True,
            True,
            PreActionKnowledgeClass.VISIBLE_READ_ONLY,
        ),
        (
            SimpleNamespace(
                outcome=DecisionOutcome.PLATFORM_DENIED,
                platform_allowed=False,
                delegation_allowed=True,
                effective_allowed=False,
            ),
            None,
            True,
            True,
            PreActionKnowledgeClass.EXPLICITLY_DENIED,
        ),
        (
            SimpleNamespace(
                outcome=DecisionOutcome.ALLOWED,
                platform_allowed=True,
                delegation_allowed=False,
                effective_allowed=True,
            ),
            AccessRight.READ,
            True,
            True,
            PreActionKnowledgeClass.DELEGATION_MISSING,
        ),
        (
            SimpleNamespace(
                outcome=DecisionOutcome.POLICY_DENIED,
                platform_allowed=True,
                delegation_allowed=True,
                effective_allowed=False,
            ),
            AccessRight.READ,
            True,
            True,
            PreActionKnowledgeClass.POLICY_DENIAL_VISIBLE,
        ),
    ),
)
def test_visible_pre_action_authority_classification_matrix(
    decision,
    required_right,
    delegation_visible,
    policy_visible,
    expected,
) -> None:
    observed_rights = (
        (AccessRight.READ,)
        if required_right in {AccessRight.READ, AccessRight.DELETE}
        else ()
    )
    assert classify_pre_action_knowledge(
        resources=(
            ResourceKnowledge(
                resource=ResourceRef(
                    kind=ResourceKind.DRIVE_FILE,
                    resource_id="drive.visible",
                ),
                visible_before_action=True,
                rights_visible=True,
                observed_rights=observed_rights,
                owner_or_organizer_id="user.owner",
                evidence_refs=("evidence.prior.visible",),
            ),
        ),
        required_right=required_right,
        delegation_statement_visible=delegation_visible,
        policy_statement_visible=policy_visible,
        policy_decision=decision,
    ) is expected


def test_hidden_target_is_classified_before_policy_or_permission_claims() -> None:
    decision = SimpleNamespace(
        outcome=DecisionOutcome.PLATFORM_DENIED,
        platform_allowed=False,
        delegation_allowed=False,
        effective_allowed=False,
    )
    assert classify_pre_action_knowledge(
        resources=(
            ResourceKnowledge(
                resource=ResourceRef(
                    kind=ResourceKind.DRIVE_FILE,
                    resource_id="drive.hidden",
                ),
                visible_before_action=False,
            ),
        ),
        required_right=AccessRight.DELETE,
        delegation_statement_visible=True,
        policy_statement_visible=True,
        policy_decision=decision,
    ) is PreActionKnowledgeClass.RESOURCE_NOT_VISIBLE
