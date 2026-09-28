from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.scenarios.office_v2.agent_context import (
    AgentPolicyCapabilityFragment,
    ContextEvidenceSourceKind,
    assemble_agent_workspace_context,
    derive_agent_identity_context,
    derive_agent_policy_capability_context,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASES
from sandbox.scenarios.office_v2.models import DecisionMode, TaskContract
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_EXCLUDED_TOOL_NAMES,
    OFFICE_V2_TOOL_NAMES,
    office_v2_tool_definitions,
)


def _without_delegation(task: TaskContract) -> TaskContract:
    payload = task.model_dump(mode="python")
    payload["delegated_actions"] = ()
    return TaskContract.model_validate(payload)


def test_policy_delegation_and_tools_remain_separate_authority_dimensions() -> None:
    world = load_canonical_world()
    case = CLEAN_CASES[0]
    definitions = office_v2_tool_definitions()

    delegated = derive_agent_policy_capability_context(
        world.state, case.task, definitions
    )
    undelegated = derive_agent_policy_capability_context(
        world.state, _without_delegation(case.task), definitions
    )

    assert set(delegated.available_business_tool_names) == set(OFFICE_V2_TOOL_NAMES)
    assert set(undelegated.available_business_tool_names) == set(OFFICE_V2_TOOL_NAMES)
    assert len(delegated.available_business_tool_names) == 17
    assert not set(OFFICE_V2_EXCLUDED_TOOL_NAMES).intersection(
        delegated.available_business_tool_names
    )
    assert delegated.delegated_action_summaries
    assert undelegated.delegated_action_summaries == ()
    assert {
        item.decision_mode for item in delegated.visible_policy_summaries
    } == {DecisionMode.ENFORCE, DecisionMode.AUDIT}

    visible = delegated.model_visible_payload()
    assert set(visible) == {
        "delegated_action_summaries",
        "visible_policy_summaries",
        "available_business_tool_names",
    }
    assert "platform_allowed" not in str(visible)
    assert "policy_decision" not in str(visible)


def test_policy_capability_context_does_not_leak_internal_scope_ids() -> None:
    world = load_canonical_world()
    case = CLEAN_CASES[0]

    fragment = derive_agent_policy_capability_context(
        world.state, case.task, office_v2_tool_definitions()
    )

    serialized = str(fragment.model_visible_payload())
    for rule in world.state.policy_rules:
        assert rule.rule_id not in serialized
    for delegation in case.task.delegated_actions:
        assert delegation.delegation_id not in serialized
        for query_id in delegation.resource_query_ids:
            assert query_id not in serialized
        for recipient_id in delegation.recipient_ids:
            assert recipient_id not in serialized
    assert all(
        item.source_object_id not in serialized
        for item in fragment.evidence_fields
        if item.source_kind
        in {ContextEvidenceSourceKind.POLICY, ContextEvidenceSourceKind.TASK}
    )


def test_agent_workspace_context_assembles_all_authoritative_fragments() -> None:
    world = load_canonical_world()
    case = CLEAN_CASES[0]
    identity = derive_agent_identity_context(world.state, case.actor, case.task)
    policy = derive_agent_policy_capability_context(
        world.state, case.task, office_v2_tool_definitions()
    )

    first = assemble_agent_workspace_context(identity, policy)
    second = assemble_agent_workspace_context(identity, policy)

    assert first == second
    assert first.context_digest == second.context_digest
    assert first.actor_display_name == identity.actor_display_name
    assert first.delegated_action_summaries == policy.delegated_action_summaries
    assert first.visible_policy_summaries == policy.visible_policy_summaries
    assert first.available_business_tool_names == tuple(sorted(OFFICE_V2_TOOL_NAMES))


def test_case_specific_tool_subset_is_rejected() -> None:
    world = load_canonical_world()
    case = CLEAN_CASES[0]
    definitions = dict(office_v2_tool_definitions())
    definitions.pop("delete_drive_file")

    with pytest.raises(ValueError, match="frozen 17-tool catalog"):
        derive_agent_policy_capability_context(world.state, case.task, definitions)


def test_policy_capability_fragment_rejects_visible_tampering() -> None:
    world = load_canonical_world()
    case = CLEAN_CASES[0]
    fragment = derive_agent_policy_capability_context(
        world.state, case.task, office_v2_tool_definitions()
    )
    payload = fragment.model_dump(mode="json")
    payload["available_business_tool_names"] = payload[
        "available_business_tool_names"
    ][1:]

    with pytest.raises(ValidationError, match="all 17 Office V2 tools"):
        AgentPolicyCapabilityFragment.model_validate(payload)
