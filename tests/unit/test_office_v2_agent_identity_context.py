from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.scenarios.office_v2.agent_context import (
    AgentIdentityContextFragment,
    ContextEvidenceSourceKind,
    derive_agent_identity_context,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASES
from sandbox.scenarios.office_v2.models import IssuerAuthentication, TaskContract


def _task_with_authentication(
    task: TaskContract, authentication: IssuerAuthentication
) -> TaskContract:
    payload = task.model_dump(mode="python")
    payload["issuer_authentication"] = authentication
    return TaskContract.model_validate(payload)


def test_three_actors_derive_distinct_authoritative_identity_contexts() -> None:
    world = load_canonical_world()
    world_digest = world.world_digest
    cases = (CLEAN_CASES[0], CLEAN_CASES[1], CLEAN_CASES[2])

    contexts = tuple(
        derive_agent_identity_context(world.state, case.actor, case.task)
        for case in cases
    )

    assert len({item.actor_display_name for item in contexts}) == 3
    assert len({item.actor_email for item in contexts}) == 3
    assert len({item.context_version for item in contexts}) == 1
    assert len({item.fragment_digest for item in contexts}) == 3
    assert all(item.organization_name == "Acme Systems" for item in contexts)
    assert all(item.role_names == ("Department Manager",) for item in contexts)
    principals = {
        item.principal_id: item
        for item in world.state.domain_graph.directory.principals
    }
    for context, case in zip(contexts, cases, strict=True):
        assert context.group_names == tuple(
            sorted(principals[group_id].display_name for group_id in case.actor.active_group_ids)
        )
    assert load_canonical_world().world_digest == world_digest


@pytest.mark.parametrize("authentication", tuple(IssuerAuthentication))
def test_issuer_authentication_is_preserved_exactly(
    authentication: IssuerAuthentication,
) -> None:
    world = load_canonical_world()
    case = CLEAN_CASES[0]
    task = _task_with_authentication(case.task, authentication)

    context = derive_agent_identity_context(world.state, case.actor, task)

    assert context.task_issuer_authentication is authentication
    assert context.task_issuer_display_name == "Maya Chen"
    assert context.model_visible_payload()["task_issuer_authentication"] == (
        authentication.value
    )


def test_identity_context_is_deterministic_and_sources_are_hidden() -> None:
    world = load_canonical_world()
    case = CLEAN_CASES[0]

    first = derive_agent_identity_context(world.state, case.actor, case.task)
    second = derive_agent_identity_context(world.state, case.actor, case.task)

    assert first == second
    evidence = {item.visible_field_path: item for item in first.evidence_fields}
    assert evidence["actor_display_name"].source_object_id == case.actor.actor_id
    assert evidence["logical_time"].source_kind is ContextEvidenceSourceKind.CLOCK
    assert evidence["task_issuer_authentication"].source_object_id == (
        case.task.task_id
    )
    visible = first.model_visible_payload()
    serialized = str(visible)
    assert case.actor.actor_id not in serialized
    assert case.task.task_id not in serialized
    assert "directory_digest" not in serialized
    assert "evidence" not in visible


def test_identity_context_rejects_actor_or_clock_mismatch() -> None:
    world = load_canonical_world()
    case = CLEAN_CASES[0]
    stale_actor = case.actor.model_copy(update={"logical_time": 999})

    with pytest.raises(ValueError, match="does not match the current directory"):
        derive_agent_identity_context(world.state, stale_actor, case.task)

    other_case = next(item for item in CLEAN_CASES if item.actor.actor_id != case.actor.actor_id)
    with pytest.raises(ValueError, match="TaskContract actor"):
        derive_agent_identity_context(world.state, case.actor, other_case.task)


def test_identity_fragment_rejects_visible_or_evidence_tampering() -> None:
    world = load_canonical_world()
    case = CLEAN_CASES[0]
    context = derive_agent_identity_context(world.state, case.actor, case.task)
    payload = context.model_dump(mode="json")
    payload["actor_display_name"] = "Forged Display Name"

    with pytest.raises(ValidationError, match="identity evidence value"):
        AgentIdentityContextFragment.model_validate(payload)
