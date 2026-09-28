from __future__ import annotations

from sandbox.fuzzer.v2_seed_pools import RiskType, build_initial_seed_catalog
from sandbox.fuzzer.v2_target_oracle import (
    TARGET_ORACLE_BY_TARGET,
    build_formal_target_scenario_supports,
)
from sandbox.scenarios.office_v2.attack_models import DirectTaskCondition
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.fork import rematerialize_office_v2_scenario_text
from sandbox.scenarios.office_v2.models import PrincipalKind, Sensitivity


def _binding(support, slot_id: str):
    return next(
        item for item in support.scenario_case.objective_bindings
        if item.slot_id == slot_id
    )


def test_target_oracles_match_the_exact_four_by_four_seed_catalog() -> None:
    catalog = build_initial_seed_catalog()
    roots = tuple(seed for pool in catalog.pools for seed in pool.root_seeds)

    assert len(TARGET_ORACLE_BY_TARGET) == len(roots) == 16
    assert set(TARGET_ORACLE_BY_TARGET) == {seed.attack_target for seed in roots}
    assert {
        risk_type: sum(
            oracle.risk_type is risk_type
            for oracle in TARGET_ORACLE_BY_TARGET.values()
        )
        for risk_type in RiskType
    } == dict.fromkeys(RiskType, 4)
    for seed in roots:
        oracle = TARGET_ORACLE_BY_TARGET[seed.attack_target]
        assert oracle.risk_type is seed.risk_type
        assert oracle.steps


def test_every_formal_seed_has_an_independent_direct_task_fixture() -> None:
    supports = build_formal_target_scenario_supports()

    assert len(supports) == 16
    assert len({item.seed.seed_id for item in supports}) == 16
    assert len({item.scenario_case.case_id for item in supports}) == 16
    for item in supports:
        case = item.scenario_case
        assert isinstance(case.adversarial_condition, DirectTaskCondition)
        assert case.task.instruction == item.seed.base_text
        assert item.target_oracle.attack_target == item.seed.attack_target
        assert item.target_oracle.risk_type is item.seed.risk_type
        assert (
            case.attack_objective.objective_id
            == item.target_oracle.scenario_source_objective_id
        )


def test_every_formal_seed_has_concrete_agent_task_execution_support() -> None:
    supports = build_formal_target_scenario_supports()

    for item in supports:
        support = item.agent_task_execution_support()
        rendered = "\n".join(support.summaries).lower()
        assert support.summaries
        assert item.seed.attack_target.lower() not in rendered
        assert item.target_oracle.scenario_source_objective_id not in rendered
        assert item.seed.risk_type.value not in rendered
        assert "oracle" not in rendered
        for binding in item.scenario_case.objective_bindings:
            for ref in binding.resource_refs:
                assert ref.resource_id in rendered
            for principal_id in binding.principal_ids:
                assert principal_id in rendered

    by_seed = {item.seed.seed_id: item for item in supports}
    workspace = by_seed[
        "modification.workspace-record"
    ].agent_task_execution_support()
    assert any("replacement record content" in line.lower() for line in workspace.summaries)
    calendar = by_seed[
        "unauthorized.external-calendar-event"
    ].agent_task_execution_support()
    assert any("new event start time" in line.lower() for line in calendar.summaries)


def test_target_specific_fixtures_bind_resources_with_the_claimed_context() -> None:
    supports = {
        item.seed.seed_id: item for item in build_formal_target_scenario_supports()
    }

    restricted_targets = (
        "disclosure.drive-attachment-send",
        "destructive.restricted-drive-delete",
    )
    for seed_id in restricted_targets:
        support = supports[seed_id]
        slot = "source_file" if seed_id.startswith("disclosure") else "target_file"
        ref = _binding(support, slot).resource_refs[0]
        file = next(
            item for item in support.materialization.initial_state.domain_graph.drive.files
            if item.file_id == ref.resource_id
        )
        assert file.classification is Sensitivity.RESTRICTED, seed_id

    support = supports["destructive.shared-drive-delete"]
    ref = _binding(support, "target_file").resource_refs[0]
    external_ids = {
        item.principal_id
        for item in support.materialization.initial_state.domain_graph.directory.principals
        if item.kind is PrincipalKind.EXTERNAL
    }
    assert any(
        item.resource.resource_id == ref.resource_id
        and item.recipient_id in external_ids
        and item.status.value == "active"
        for item in support.materialization.initial_state.domain_graph.drive.share_records
    )

    for seed_id in (
        "unauthorized.calendar-attendee-update",
        "destructive.external-calendar-cancel",
        "destructive.conflicting-calendar-cancel",
    ):
        support = supports[seed_id]
        ref = _binding(support, "original_event").resource_refs[0]
        state = support.materialization.initial_state
        event = next(
            item for item in state.domain_graph.calendar.events
            if item.event_id == ref.resource_id
        )
        if seed_id == "unauthorized.calendar-attendee-update":
            assert event.organizer_id != support.scenario_case.actor.actor_id
        elif seed_id == "destructive.external-calendar-cancel":
            external_ids = {
                item.principal_id
                for item in state.domain_graph.directory.principals
                if item.kind is PrincipalKind.EXTERNAL
            }
            assert set(event.attendee_ids).intersection(external_ids)
        else:
            assert any(
                item.event_id != event.event_id
                and item.status.value != "cancelled"
                and item.start_at < event.end_at
                and event.start_at < item.end_at
                and set(item.attendee_ids).intersection(event.attendee_ids)
                for item in state.domain_graph.calendar.events
            )


def test_text_rematerialization_preserves_all_formal_execution_bindings() -> None:
    canonical = load_canonical_world()

    for index, support in enumerate(build_formal_target_scenario_supports(), start=101):
        source = support.scenario_case
        result = rematerialize_office_v2_scenario_text(
            source_case=source,
            canonical_world=canonical,
            generated_content=f"受控目标保持改写 {index}",
            purpose=support.compatibility_decision.purpose,
            seed=index,
        )
        rematerialized = result.scenario_case

        assert rematerialized.actor == source.actor
        assert rematerialized.parent_case_id == source.parent_case_id
        assert rematerialized.attack_objective == source.attack_objective
        assert rematerialized.objective_bindings == source.objective_bindings
        assert rematerialized.task.instruction == f"受控目标保持改写 {index}"
