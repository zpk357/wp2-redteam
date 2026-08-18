from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASES
from sandbox.scenarios.office_v2.task_catalog import TASK_BLUEPRINTS
from sandbox.scenarios.office_v2.utility_oracle import (
    UTILITY_ASSERTION_CATALOG,
    TaskAssertionPredicate,
    UtilityAssertionCatalog,
    UtilityPredicateKind,
    build_utility_assertion_catalog,
    compile_task_assertion_specs,
)


def test_catalog_defines_every_frozen_success_assertion_exactly_once() -> None:
    catalog = UTILITY_ASSERTION_CATALOG
    expected_templates = sum(len(item.goals) for item in TASK_BLUEPRINTS)
    expected_assertions = {
        assertion_id
        for case in CLEAN_CASES
        for goal in case.task.goal_graph.goals
        for assertion_id in goal.success_assertions
    }

    assert len(catalog.templates) == expected_templates == 42
    assert len(catalog.compiled_specs) == len(expected_assertions) == 101
    assert {item.assertion_id for item in catalog.compiled_specs} == expected_assertions
    assert UtilityAssertionCatalog.model_validate_json(catalog.model_dump_json()) == catalog
    assert build_utility_assertion_catalog() == catalog


def test_vocabulary_contains_the_frozen_state_and_provenance_predicates() -> None:
    assert set(UtilityPredicateKind) >= {
        UtilityPredicateKind.OBJECT_EXISTS,
        UtilityPredicateKind.FIELD_EQUALS,
        UtilityPredicateKind.FIELD_CONTAINS,
        UtilityPredicateKind.RELATION_EXISTS,
        UtilityPredicateKind.VERSION_CHANGED,
        UtilityPredicateKind.SOURCE_REFERENCES_OBSERVED,
        UtilityPredicateKind.PRINCIPAL_SET_EQUALS,
        UtilityPredicateKind.STATE_VALUE_EQUALS,
        UtilityPredicateKind.NO_EXTRA_SIDE_EFFECTS,
        UtilityPredicateKind.COMMITTED_ACTION_EFFECT,
        UtilityPredicateKind.SOURCE_PROPAGATED,
        UtilityPredicateKind.INTERACTION_CONFIRMED,
    }


def test_generic_predicates_do_not_contain_case_project_or_person_ids() -> None:
    predicate_text = "\n".join(
        predicate.model_dump_json()
        for template in UTILITY_ASSERTION_CATALOG.templates
        for predicate in template.predicates
    ).lower()
    forbidden = {
        case.case_id.lower() for case in CLEAN_CASES
    } | {
        case.actor.actor_id.lower() for case in CLEAN_CASES
    } | {"apollo", "borealis", "cedar", "delta", "evergreen"}
    assert all(value not in predicate_text for value in forbidden)


def test_unknown_duplicate_and_unbound_definitions_are_rejected() -> None:
    catalog = UTILITY_ASSERTION_CATALOG
    payload = catalog.model_dump(
        mode="python", exclude={"catalog_digest"}, exclude_none=False
    )
    payload["compiled_specs"] = payload["compiled_specs"][:-1]
    with pytest.raises(ValidationError, match="do not match clean case assertions"):
        UtilityAssertionCatalog(
            **payload,
            catalog_digest=sha256_digest(payload),
        )

    duplicate_payload = catalog.model_dump(
        mode="python", exclude={"catalog_digest"}, exclude_none=False
    )
    duplicate_payload["compiled_specs"] = (
        *duplicate_payload["compiled_specs"],
        duplicate_payload["compiled_specs"][0],
    )
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        UtilityAssertionCatalog(
            **duplicate_payload,
            catalog_digest=sha256_digest(duplicate_payload),
        )

    case = CLEAN_CASES[0]
    templates = list(UTILITY_ASSERTION_CATALOG.templates)
    template_index = next(
        index
        for index, item in enumerate(templates)
        if item.blueprint_id == case.blueprint_id
    )
    template = templates[template_index]
    predicate = template.predicates[0]
    changed_predicate = predicate.model_copy(
        update={"query_keys": (*predicate.query_keys, "unknown-query-key")}
    )
    templates[template_index] = template.model_copy(
        update={"predicates": (changed_predicate, *template.predicates[1:])}
    )
    with pytest.raises(ValueError, match="unbound query key"):
        compile_task_assertion_specs(case, tuple(templates))


def test_one_binding_digest_change_only_changes_assertions_that_use_it() -> None:
    case = CLEAN_CASES[0]
    templates = UTILITY_ASSERTION_CATALOG.templates
    before = {
        item.assertion_id: item
        for item in compile_task_assertion_specs(case, templates)
    }
    target = case.resolved_bindings[0]
    changed_binding = target.model_copy(
        update={"resolution_digest": sha256_digest({"changed": target.query_id})}
    )
    changed_case = case.model_copy(
        update={
            "resolved_bindings": tuple(
                changed_binding if item.query_id == target.query_id else item
                for item in case.resolved_bindings
            )
        }
    )
    after = {
        item.assertion_id: item
        for item in compile_task_assertion_specs(changed_case, templates)
    }
    expected_changed = {
        assertion_id
        for assertion_id, spec in before.items()
        if any(item.query_id == target.query_id for item in spec.bindings)
    }
    actual_changed = {
        assertion_id
        for assertion_id in before
        if before[assertion_id].spec_digest != after[assertion_id].spec_digest
    }
    assert expected_changed
    assert expected_changed != set(before)
    assert actual_changed == expected_changed


def test_reconcile_goal_is_structurally_source_propagated_not_case_special_cased() -> None:
    templates = UTILITY_ASSERTION_CATALOG.templates
    propagated = [
        template
        for template in templates
        if any(
            predicate.predicate_kind is UtilityPredicateKind.SOURCE_PROPAGATED
            for predicate in template.predicates
        )
    ]
    assert len(propagated) == 1
    predicate = next(
        item
        for item in propagated[0].predicates
        if item.predicate_kind is UtilityPredicateKind.SOURCE_PROPAGATED
    )
    assert predicate.query_keys
    assert predicate.dependency_goal_keys
    assert predicate.action_scopes


def test_predicate_rejects_an_untyped_arbitrary_payload() -> None:
    with pytest.raises(ValidationError):
        TaskAssertionPredicate.model_validate(
            {
                "predicate_kind": "field_equals",
                "query_keys": ["source"],
                "field_path": ["content"],
                "arbitrary": {"callback": "case-specific-python"},
            }
        )
