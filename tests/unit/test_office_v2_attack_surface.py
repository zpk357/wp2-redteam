from __future__ import annotations

from sandbox.scenarios.office_v2.attack_models import AttackEntryKind
from sandbox.scenarios.office_v2.attack_surface import (
    ATTACKABLE_FIELD_CATALOG,
    REACHABLE_ATTACK_SURFACE_CATALOG,
    REACHABLE_ATTACK_SURFACES,
    derive_reachable_attack_surface,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASES
from sandbox.scenarios.office_v2.models import AccessRight, ResourceKind
from sandbox.scenarios.office_v2.observation import resource_rights


def test_field_registry_is_typed_four_domain_and_excludes_authority_state() -> None:
    kinds = {item.resource_kind for item in ATTACKABLE_FIELD_CATALOG.fields}
    assert {
        ResourceKind.MAIL_MESSAGE,
        ResourceKind.DRIVE_FILE,
        ResourceKind.CALENDAR_EVENT,
        ResourceKind.WORKSPACE_FILE,
    }.issubset(kinds)
    serialized = ATTACKABLE_FIELD_CATALOG.model_dump_json().casefold()
    for forbidden in ("delegation_grant", "policy_rule", "role_assignment", "state_digest"):
        assert forbidden not in serialized
    assert all(
        AttackEntryKind.DIRECT_TASK not in item.allowed_entry_kinds
        for item in ATTACKABLE_FIELD_CATALOG.fields
    )


def test_all_clean_cases_have_deterministic_actor_visible_reachable_fields() -> None:
    world = load_canonical_world()
    before = world.state.canonical_digest()
    assert len(REACHABLE_ATTACK_SURFACES) == len(CLEAN_CASES) == 24
    assert (
        derive_reachable_attack_surface(CLEAN_CASES[0], world=world) == REACHABLE_ATTACK_SURFACES[0]
    )
    assert world.state.canonical_digest() == before

    aggregate_kinds = set()
    for case, surface in zip(CLEAN_CASES, REACHABLE_ATTACK_SURFACES, strict=True):
        query_ids = {item.query_id for item in case.task.resource_queries}
        goal_ids = {item.goal_id for item in case.task.goal_graph.goals}
        assert surface.fields
        for field in surface.fields:
            aggregate_kinds.add(field.resource_ref.kind)
            assert set(field.source_query_ids).issubset(query_ids)
            assert set(field.source_goal_ids).issubset(goal_ids)
            assert AccessRight.READ in resource_rights(world.state, case.actor, field.resource_ref)

    assert aggregate_kinds == {
        ResourceKind.MAIL_MESSAGE,
        ResourceKind.DRIVE_FILE,
        ResourceKind.CALENDAR_EVENT,
        ResourceKind.WORKSPACE_FILE,
    }
    assert (
        REACHABLE_ATTACK_SURFACE_CATALOG.model_validate_json(
            REACHABLE_ATTACK_SURFACE_CATALOG.model_dump_json()
        )
        == REACHABLE_ATTACK_SURFACE_CATALOG
    )
