from __future__ import annotations

from collections import Counter, defaultdict

from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_CATALOG, CLEAN_CASES
from sandbox.scenarios.office_v2.models import QuestionKind, ResourceRelation
from sandbox.scenarios.office_v2.resolution import binding_matches_state
from sandbox.scenarios.office_v2.task_catalog import (
    TASK_BLUEPRINT_BY_ID,
    TASK_BLUEPRINT_CATALOG_DIGEST,
    TASK_BLUEPRINTS,
)

FROZEN_WORLD_DIGEST = "sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106"
FROZEN_BLUEPRINT_CATALOG_DIGEST = (
    "sha256:865b1a1d42d9485d3bbf5f990dcfbf0bf6a5ad196ddad414a32354fe5c4a3f00"
)
FROZEN_CLEAN_CASE_CATALOG_DIGEST = (
    "sha256:fd06d46b562433f8a513192c5dc7299838c57205e131cf43643c9cc450f0ae06"
)


def test_task_and_clean_case_catalogs_are_frozen_at_required_size() -> None:
    assert len(TASK_BLUEPRINTS) == len(TASK_BLUEPRINT_BY_ID) == 10
    assert TASK_BLUEPRINT_CATALOG_DIGEST == FROZEN_BLUEPRINT_CATALOG_DIGEST
    assert CLEAN_CASE_CATALOG.catalog_digest == FROZEN_CLEAN_CASE_CATALOG_DIGEST
    assert Counter(case.blueprint_id for case in CLEAN_CASES) == {
        "t1-review-from-approved-brief": 3,
        "t2-reschedule-from-current-roster": 3,
        "t3-cancel-superseded-review": 2,
        "t4-build-drive-brief": 2,
        "t5-approved-internal-distribution": 2,
        "t6-maintain-project-access": 2,
        "t7-archive-obsolete-draft": 2,
        "t8-reconcile-attachment-set": 2,
        "t9-meeting-follow-up-package": 3,
        "t10-workspace-to-drive-handoff": 3,
    }


def test_blueprints_encode_behavioral_structure_without_tool_sequences() -> None:
    assert sum(len(item.domains) >= 3 for item in TASK_BLUEPRINTS) >= 6
    assert {
        item.blueprint_id
        for item in TASK_BLUEPRINTS
        if any(goal.clarification_query_key for goal in item.goals)
    } == {
        "t1-review-from-approved-brief",
        "t2-reschedule-from-current-roster",
        "t5-approved-internal-distribution",
        "t9-meeting-follow-up-package",
    }

    attachment_blueprint = TASK_BLUEPRINT_BY_ID["t8-reconcile-attachment-set"]
    delivery_query = next(
        item for item in attachment_blueprint.queries if item.query_key == "delivery-mail"
    )
    discrepancy_goal = next(
        item for item in attachment_blueprint.goals if item.goal_key == "record-discrepancies"
    )
    assert delivery_query.related_query_key == "drive-set"
    assert delivery_query.relation is ResourceRelation.ATTACHMENT
    assert discrepancy_goal.branch_query_key == "drive-set"

    dumped_keys = {
        key
        for blueprint in TASK_BLUEPRINTS
        for key in blueprint.model_dump(mode="json", exclude_none=False)
    }
    assert dumped_keys.isdisjoint(
        {"tool_sequence", "attack_objective", "adversarial_content", "carrier"}
    )
    for blueprint in TASK_BLUEPRINTS:
        assert {scope.sort_key() for scope in blueprint.allowed_side_effects}.isdisjoint(
            scope.sort_key() for scope in blueprint.forbidden_side_effects
        )
        for query in blueprint.queries:
            serialized = query.model_dump_json()
            assert all(prefix not in serialized for prefix in ("drive.", "mail.", "event."))


def test_clean_cases_freeze_valid_late_bindings_without_mutating_world() -> None:
    world = load_canonical_world()
    assert world.world_digest == CLEAN_CASE_CATALOG.world_digest == FROZEN_WORLD_DIGEST
    for case in CLEAN_CASES:
        assert case.base_world_digest == FROZEN_WORLD_DIGEST
        assert case.actor.actor_id == case.task.actor_id
        assert {item.query_id for item in case.resolved_bindings} == {
            item.query_id for item in case.task.resource_queries
        }
        assert all(binding_matches_state(item, world.state) for item in case.resolved_bindings)


def test_clean_cases_vary_actor_resources_and_interaction_shape() -> None:
    signatures: dict[str, set[tuple[object, ...]]] = defaultdict(set)
    for case in CLEAN_CASES:
        signatures[case.blueprint_id].add(
            (
                case.actor.actor_id,
                tuple(
                    ref.sort_key()
                    for binding in case.resolved_bindings
                    for ref in binding.resource_refs
                ),
                tuple(request.question_kind for request in case.task.user_response_script.requests),
            )
        )
    assert all(
        len(signatures[blueprint_id]) == expected
        for blueprint_id, expected in Counter(case.blueprint_id for case in CLEAN_CASES).items()
    )


def test_interactions_cover_disambiguation_missing_value_and_authorization() -> None:
    requests = [
        request for case in CLEAN_CASES for request in case.task.user_response_script.requests
    ]
    assert len(requests) == 11
    assert Counter(item.question_kind for item in requests) == {
        QuestionKind.DISAMBIGUATION: 7,
        QuestionKind.MISSING_VALUE: 1,
        QuestionKind.AUTHORIZATION: 3,
    }
    authorization_rules = [
        rule
        for case in CLEAN_CASES
        for rule in case.task.user_response_script.response_rules
        if rule.grant_effect is not None
    ]
    assert len(authorization_rules) == 3
    assert all(rule.grant_effect.duration_ticks == 5 for rule in authorization_rules)
    assert (
        sum(
            goal.branch_condition is not None
            for case in CLEAN_CASES
            for goal in case.task.goal_graph.goals
        )
        == 2
    )
