from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.agent_prompts import (
    OFFICE_AGENT_BASE_RULES_V2,
    OFFICE_AGENT_BASE_RULES_V2_DIGEST,
    OFFICE_AGENT_BASE_RULES_V2_VERSION,
    OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
    OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
    render_office_v2_agent_system_prompt,
)
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.agent_context import (
    AgentRenderedSystemPrompt,
    assemble_agent_workspace_context,
    derive_agent_identity_context,
    derive_agent_policy_capability_context,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASES
from sandbox.scenarios.office_v2.models import TaskContract
from sandbox.scenarios.office_v2.tools import OFFICE_V2_TOOL_NAMES, office_v2_tool_definitions
from sandbox.tool_contracts import OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST

FROZEN_V1_PROMPT_DIGEST = (
    "sha256:92ae83233a88d52241b3c6bfa458e37dfeace167937310f60b36b64ae22cdaf1"
)


def _context(case_index: int = 0, *, delegated: bool = True):
    world = load_canonical_world()
    case = CLEAN_CASES[case_index]
    task = case.task
    if not delegated:
        payload = task.model_dump(mode="python")
        payload["delegated_actions"] = ()
        task = TaskContract.model_validate(payload)
    identity = derive_agent_identity_context(world.state, case.actor, task)
    policy = derive_agent_policy_capability_context(
        world.state, task, office_v2_tool_definitions()
    )
    return world, case, assemble_agent_workspace_context(identity, policy)


def test_v2_prompt_is_deterministic_and_binds_all_four_digests() -> None:
    _, _, context = _context()

    first = render_office_v2_agent_system_prompt(context)
    second = render_office_v2_agent_system_prompt(context)

    assert first == second
    assert first.envelope.base_version == OFFICE_AGENT_BASE_RULES_V2_VERSION
    assert first.envelope.base_digest == OFFICE_AGENT_BASE_RULES_V2_DIGEST
    assert first.envelope.context_digest == context.context_digest
    assert first.envelope.tool_spec_digest == OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST
    assert first.envelope.system_message_digest == sha256_digest(first.system_message)


def test_v2_prompt_is_readable_and_changes_with_authoritative_actor_context() -> None:
    _, _, first_context = _context(0)
    _, _, second_context = _context(1)

    first = render_office_v2_agent_system_prompt(first_context)
    second = render_office_v2_agent_system_prompt(second_context)

    assert first.system_message != second.system_message
    assert first.envelope.context_digest != second.envelope.context_digest
    assert first_context.actor_display_name in first.system_message
    assert first_context.organization_name in first.system_message
    assert first_context.task_issuer_authentication.value in first.system_message
    assert "Visible enterprise policy:" in first.system_message
    assert "Available business tools:" in first.system_message
    assert all(name in first.system_message for name in OFFICE_V2_TOOL_NAMES)


def test_v2_prompt_has_no_internal_ids_digests_or_forbidden_design_language() -> None:
    world, case, context = _context()
    rendered = render_office_v2_agent_system_prompt(context)
    lowered = rendered.system_message.lower()

    for forbidden in (
        "synthetic",
        "test matrix",
        "attack",
        "injection",
        "safe",
        "vulnerable",
        "state digest",
        "world digest",
        "evidence id",
    ):
        assert forbidden not in lowered
    assert case.case_id not in rendered.system_message
    assert case.task.task_id not in rendered.system_message
    assert case.base_world_digest not in rendered.system_message
    for rule in world.state.policy_rules:
        assert rule.rule_id not in rendered.system_message
    for query in case.task.resource_queries:
        assert query.query_id not in rendered.system_message


def test_missing_delegation_is_not_rendered_as_missing_resource_access() -> None:
    _, _, context = _context(delegated=False)

    rendered = render_office_v2_agent_system_prompt(context)

    assert "No task delegation is currently stated." in rendered.system_message
    assert "access to a specific resource is checked when that tool is called" in (
        rendered.system_message
    )
    assert all(name in rendered.system_message for name in OFFICE_V2_TOOL_NAMES)


def test_v1_identity_is_unchanged_and_rendered_message_tampering_is_rejected() -> None:
    assert OFFICE_AGENT_SYSTEM_PROMPT_VERSION == "office-agent-system-prompt-v1"
    assert OFFICE_AGENT_SYSTEM_PROMPT_DIGEST == FROZEN_V1_PROMPT_DIGEST
    assert sha256_digest(OFFICE_AGENT_BASE_RULES_V2) == OFFICE_AGENT_BASE_RULES_V2_DIGEST
    _, _, context = _context()
    rendered = render_office_v2_agent_system_prompt(context)
    payload = rendered.model_dump(mode="json")
    payload["system_message"] += "Forged line.\n"

    with pytest.raises(ValidationError, match="does not match prompt envelope"):
        AgentRenderedSystemPrompt.model_validate(payload)


def test_v2_renderer_rejects_non_context_input() -> None:
    with pytest.raises(TypeError, match="AgentWorkspaceContext"):
        render_office_v2_agent_system_prompt({})
