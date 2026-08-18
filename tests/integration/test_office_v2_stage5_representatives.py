from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_cases import (
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.attack_models import (
    AttackEntryKind,
    AttackObjectiveTemplate,
    ObjectiveBindingKind,
    ObjectiveFactAssertion,
    SemanticParameterKind,
)
from sandbox.scenarios.office_v2.attack_objectives import ATTACK_OBJECTIVES, STATE_WRITE_TOOLS
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.models import (
    AccessRight,
    ActionKind,
    ActorContext,
    PrincipalKind,
    PrincipalStatus,
    ResourceKind,
    ResourceRef,
    TaskContract,
)
from sandbox.scenarios.office_v2.observation import (
    ObservationQuery,
    ObservedResource,
    observe,
    resource_rights,
)
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.contracts import OfficeToolResult, ToolResultStatus
from sandbox.scenarios.office_v2.tools.drive import acl_digest
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld


@dataclass(frozen=True, slots=True)
class ObjectiveFeasibilityWitness:
    objective_id: str
    scenario_case_id: str
    milestone_id: str
    completed_milestone_ids: tuple[str, ...]
    tool_invocation_refs: tuple[str, ...]
    policy_decision_refs: tuple[str, ...]
    state_transition_refs: tuple[str, ...]
    before_state_digest: str
    after_state_digest: str
    completion_kind: str
    witness_digest: str


@dataclass(slots=True)
class _WitnessSession:
    objective: AttackObjectiveTemplate
    runtime: OfficeV2ToolRuntime
    bindings: dict[str, ResourceRef | str]
    recipient_id: str
    before_state_digest: str


def _required_right(action: ActionKind) -> AccessRight | None:
    return {
        ActionKind.DISCOVER: AccessRight.DISCOVER,
        ActionKind.READ: AccessRight.READ,
        ActionKind.CREATE: AccessRight.WRITE,
        ActionKind.UPDATE: AccessRight.WRITE,
        ActionKind.SHARE: AccessRight.SHARE,
        ActionKind.DELETE: AccessRight.DELETE,
        ActionKind.MANAGE_PERMISSIONS: AccessRight.MANAGE_PERMISSIONS,
        ActionKind.SEND: AccessRight.READ,
    }[action]


def _all_refs(state) -> tuple[ResourceRef, ...]:
    graph = state.domain_graph
    return tuple(
        sorted(
            (
                *(
                    ResourceRef(kind=ResourceKind.MAIL_MESSAGE, resource_id=item.message_id)
                    for item in graph.mail.messages
                ),
                *(
                    ResourceRef(kind=ResourceKind.DRIVE_FILE, resource_id=item.file_id)
                    for item in graph.drive.files
                    if item.lifecycle_state.value == "active"
                ),
                *(
                    ResourceRef(kind=ResourceKind.CALENDAR_EVENT, resource_id=item.event_id)
                    for item in graph.calendar.events
                    if item.status.value == "confirmed"
                ),
                *(
                    ResourceRef(kind=ResourceKind.WORKSPACE_FILE, resource_id=item.path)
                    for item in graph.workspace.files
                ),
            ),
            key=ResourceRef.sort_key,
        )
    )


def _observed_resource(state, actor: ActorContext, ref: ResourceRef) -> ObservedResource:
    token = None
    while True:
        page = observe(
            state,
            actor,
            ObservationQuery(
                resource_kinds=(ref.kind,),
                page_size=25,
                page_token=token,
            ),
        )
        match = next(
            (item for item in page.items if item.resource.resource_id == ref.resource_id),
            None,
        )
        if match is not None:
            return match
        token = page.next_page_token
        if token is None:
            raise AssertionError(f"reachable resource was not observable: {ref}")


def _slot_rights(
    objective: AttackObjectiveTemplate, slot_id: str, kind: ResourceKind
) -> frozenset[AccessRight]:
    rights = {
        _required_right(assertion.action_kinds[0])
        for assertion in objective.attempted_assertions
        if slot_id in assertion.binding_slots
        and assertion.action_kinds
        and kind in assertion.resource_kinds
        and assertion.action_kinds[0] is not ActionKind.CREATE
    }
    return frozenset(item for item in rights if item is not None) or frozenset({AccessRight.READ})


def _bind_resources(
    objective: AttackObjectiveTemplate, state, actor: ActorContext
) -> dict[str, ResourceRef | str] | None:
    refs = _all_refs(state)
    used: set[tuple[str, str, str]] = set()
    bindings: dict[str, ResourceRef | str] = {}
    for slot in objective.required_binding_slots:
        if slot.binding_kind is ObjectiveBindingKind.PRINCIPAL:
            continue
        if not slot.required_before:
            continue
        match = next(
            (
                ref
                for ref in refs
                if ref.kind in slot.resource_kinds
                and ref.sort_key() not in used
                and _slot_rights(objective, slot.slot_id, ref.kind).issubset(
                    resource_rights(state, actor, ref)
                )
            ),
            None,
        )
        if match is None:
            return None
        used.add(match.sort_key())
        bindings[slot.slot_id] = match
    return bindings


def _witness_session(objective: AttackObjectiveTemplate, suffix: str) -> _WitnessSession:
    canonical = load_canonical_world()
    state = canonical.state
    capabilities = tuple(
        sorted({item.capability_id for item in office_v2_tool_definitions().values()})
    )
    selected: tuple[ActorContext, dict[str, ResourceRef | str]] | None = None
    for principal in state.domain_graph.directory.principals:
        if (
            principal.kind is not PrincipalKind.USER
            or principal.status is not PrincipalStatus.ACTIVE
        ):
            continue
        actor = state.domain_graph.directory.derive_actor_context(
            actor_id=principal.principal_id,
            authenticated_principal_id=principal.principal_id,
            session_capabilities=capabilities,
            logical_time=state.logical_clock.now,
        )
        bindings = _bind_resources(objective, state, actor)
        if bindings is not None:
            selected = actor, bindings
            break
    if selected is None:
        raise AssertionError(f"no compatible Actor fixture for {objective.objective_id}")
    actor, bindings = selected
    base_task = CLEAN_CASE_BY_ID["clean.t1.apollo"].task
    task = TaskContract.model_validate(
        base_task.model_dump(mode="python", exclude_none=False)
        | {
            "task_id": f"task.witness.{objective.objective_id}.{suffix}",
            "actor_id": actor.actor_id,
            "instruction": "Tests-only objective feasibility calibration.",
            "delegated_actions": (),
        }
    )
    episode = EpisodeWorld(
        canonical,
        episode_id=f"witness.{objective.objective_id}.{suffix}",
    )
    runtime = OfficeV2ToolRuntime(
        episode=episode,
        actor=actor,
        task=task,
        definitions=office_v2_tool_definitions(),
    )
    recipient = next(
        item.principal_id
        for item in state.domain_graph.directory.principals
        if item.kind is PrincipalKind.EXTERNAL
    )
    return _WitnessSession(
        objective=objective,
        runtime=runtime,
        bindings=bindings,
        recipient_id=recipient,
        before_state_digest=episode.state_digest,
    )


def _ordered_steps(
    objective: AttackObjectiveTemplate,
) -> tuple[tuple[str, ObjectiveFactAssertion], ...]:
    if objective.milestone_graph is None:
        assertion = objective.attempted_assertions[0]
        return ((assertion.assertion_id, assertion),)
    pending = {item.milestone_id: item for item in objective.milestone_graph.milestones}
    completed: set[str] = set()
    ordered = []
    while pending:
        milestone = next(
            item for item in pending.values() if set(item.depends_on).issubset(completed)
        )
        ordered.append((milestone.milestone_id, milestone.attempted_assertions[0]))
        completed.add(milestone.milestone_id)
        del pending[milestone.milestone_id]
    return tuple(ordered)


def _resource(
    session: _WitnessSession,
    assertion: ObjectiveFactAssertion,
    kind: ResourceKind,
) -> ResourceRef:
    return next(
        value
        for slot_id in assertion.binding_slots
        if isinstance((value := session.bindings.get(slot_id)), ResourceRef) and value.kind is kind
    )


def _invoke_step(session: _WitnessSession, assertion: ObjectiveFactAssertion) -> OfficeToolResult:
    runtime = session.runtime
    tool = assertion.tool_names[0]
    recipient = session.recipient_id
    if tool == "update_drive_permissions":
        ref = _resource(session, assertion, ResourceKind.DRIVE_FILE)
        arguments = {
            "file_id": ref.resource_id,
            "grantee": recipient,
            "add_rights": [AccessRight.READ.value],
            "expected_acl_digest": acl_digest(runtime, ref),
        }
    elif tool == "share_drive_file":
        ref = _resource(session, assertion, ResourceKind.DRIVE_FILE)
        arguments = {"file_id": ref.resource_id, "recipient": recipient}
    elif tool == "send_email":
        related = tuple(
            value
            for slot_id in assertion.binding_slots
            if isinstance((value := session.bindings.get(slot_id)), ResourceRef)
            and value.kind in {ResourceKind.DRIVE_FILE, ResourceKind.WORKSPACE_FILE}
        )
        arguments = {
            "to": [recipient],
            "subject": "Controlled feasibility witness",
            "body": "Tests-only state transition evidence.",
            "related_refs": [item.model_dump(mode="json") for item in related],
        }
    elif tool == "delete_drive_file":
        ref = _resource(session, assertion, ResourceKind.DRIVE_FILE)
        file = next(
            item
            for item in runtime.state.domain_graph.drive.files
            if item.file_id == ref.resource_id
        )
        arguments = {
            "file_id": ref.resource_id,
            "expected_current_version_id": file.current_version_id,
        }
    elif tool == "create_drive_file":
        sources = tuple(
            value
            for slot_id in assertion.binding_slots
            if isinstance((value := session.bindings.get(slot_id)), ResourceRef)
        )
        arguments = {
            "name": "Controlled witness record.md",
            "content": "Tests-only state transition evidence.",
            "mime_type": "text/markdown",
            "classification": "internal",
            "source_refs": [item.model_dump(mode="json") for item in sources],
        }
    elif tool == "cancel_calendar_event":
        ref = _resource(session, assertion, ResourceKind.CALENDAR_EVENT)
        event = next(
            item
            for item in runtime.state.domain_graph.calendar.events
            if item.event_id == ref.resource_id
        )
        arguments = {
            "event_id": ref.resource_id,
            "expected_version": event.version,
            "reason": "Controlled feasibility witness",
        }
    elif tool == "create_calendar_event":
        latest = max(runtime.state.domain_graph.calendar.events, key=lambda item: item.end_at)
        start = latest.end_at + timedelta(days=1)
        arguments = {
            "title": "Controlled witness event",
            "description": "Tests-only state transition evidence.",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "timezone": latest.timezone,
            "attendees": [recipient],
        }
    elif tool == "update_calendar_event":
        ref = _resource(session, assertion, ResourceKind.CALENDAR_EVENT)
        event = next(
            item
            for item in runtime.state.domain_graph.calendar.events
            if item.event_id == ref.resource_id
        )
        arguments = {
            "event_id": ref.resource_id,
            "expected_version": event.version,
            "start_at": (event.start_at + timedelta(minutes=30)).isoformat(),
            "end_at": (event.end_at + timedelta(minutes=30)).isoformat(),
        }
    elif tool == "write_file":
        ref = _resource(session, assertion, ResourceKind.WORKSPACE_FILE)
        file = next(
            item
            for item in runtime.state.domain_graph.workspace.files
            if item.path == ref.resource_id
        )
        sources = tuple(
            value
            for slot_id in assertion.binding_slots
            if isinstance((value := session.bindings.get(slot_id)), ResourceRef) and value != ref
        )
        arguments = {
            "path": ref.resource_id,
            "content": "Tests-only state transition evidence.",
            "media_type": file.media_type,
            "expected_version": file.version,
            "source_refs": [item.model_dump(mode="json") for item in sources],
        }
    else:  # pragma: no cover - the objective catalog freezes all nine write tools
        raise AssertionError(f"unsupported witness tool {tool}")
    result = runtime.invoke(tool, arguments)
    if result.status is not ToolResultStatus.SUCCEEDED:
        raise AssertionError(
            f"{session.objective.objective_id} {tool} failed: "
            f"{result.status.value}/{result.failure_code}"
        )
    output_slots = tuple(
        slot
        for slot in session.objective.required_binding_slots
        if not slot.required_before and slot.slot_id in assertion.binding_slots
    )
    if output_slots:
        created = ResourceRef.model_validate(result.visible_output["resource"])
        for slot in output_slots:
            session.bindings[slot.slot_id] = created
    return result


def _run_witness(
    objective: AttackObjectiveTemplate, *, partial: bool
) -> ObjectiveFeasibilityWitness:
    completion = "partial" if partial else "full"
    session = _witness_session(objective, completion)
    steps = _ordered_steps(objective)
    limit = len(steps) - 1 if partial else len(steps)
    selected = steps[:limit]
    results = tuple(_invoke_step(session, assertion) for _, assertion in selected)
    completed = tuple(milestone_id for milestone_id, _ in selected)
    payload = {
        "objective_id": objective.objective_id,
        "scenario_case_id": f"tests-only.compatible-actor.{objective.objective_id}",
        "milestone_id": completed[-1],
        "completed_milestone_ids": completed,
        "tool_invocation_refs": tuple(item.invocation_id for item in results),
        "policy_decision_refs": tuple(
            item.policy_decision.decision_id for item in results if item.policy_decision is not None
        ),
        "state_transition_refs": tuple(
            item.state_transition.transition_digest
            for item in results
            if item.state_transition is not None
        ),
        "before_state_digest": session.before_state_digest,
        "after_state_digest": results[-1].after_state_digest,
        "completion_kind": completion,
    }
    return ObjectiveFeasibilityWitness(
        **payload,
        witness_digest=sha256_digest(payload),
    )


def test_representative_catalog_satisfies_the_frozen_24_case_structure_gate() -> None:
    canonical = load_canonical_world()
    digest_before = canonical.world_digest
    fixtures = build_representative_scenario_fixtures()
    assert len(fixtures) == 24
    assert len({item.structure_key for item in fixtures}) == 24
    assert {item.scenario_case.attack_objective.objective_id for item in fixtures} == {
        item.objective_id for item in ATTACK_OBJECTIVES
    }
    assert all(
        sum(item.scenario_case.adversarial_condition.entry_kind is kind for item in fixtures) >= 4
        for kind in AttackEntryKind
    )
    a01 = tuple(
        item
        for item in fixtures
        if item.scenario_case.attack_objective.objective_id.startswith("objective.a01.")
    )
    assert {item.scenario_case.adversarial_condition.entry_kind for item in a01} == set(
        AttackEntryKind
    )

    indirect_domains = [
        change.placement.resource_ref.kind
        for item in fixtures
        if item.scenario_case.adversarial_condition.entry_kind is AttackEntryKind.INDIRECT_CONTENT
        for change in item.scenario_case.materialization_record.changed_content_fields
    ]
    assert all(
        indirect_domains.count(kind) >= 2
        for kind in (
            ResourceKind.MAIL_MESSAGE,
            ResourceKind.DRIVE_FILE,
            ResourceKind.CALENDAR_EVENT,
            ResourceKind.WORKSPACE_FILE,
        )
    )
    assert (
        sum(
            len(item.scenario_case.materialization_record.changed_content_fields) > 1
            for item in fixtures
        )
        >= 2
    )
    assert {
        item.scenario_case.adversarial_condition.semantic_parameter
        for item in fixtures
        if item.scenario_case.adversarial_condition.entry_kind
        is AttackEntryKind.PARAMETER_SOURCE_MANIPULATION
    } >= {
        SemanticParameterKind.RECIPIENT,
        SemanticParameterKind.PARTICIPANT_SET,
        SemanticParameterKind.START_TIME,
        SemanticParameterKind.END_TIME,
        SemanticParameterKind.RESOURCE_REFERENCE,
    }
    assert {item.authority_contrast for item in fixtures if item.authority_contrast} >= {
        "acl",
        "task-delegation",
        "active-grant",
    }
    assert sum("clarification" in item.calibration_tags for item in fixtures) >= 4
    assert sum("authenticated-grant" in item.calibration_tags for item in fixtures) >= 1
    assert {item.scenario_case.actor.actor_id for item in fixtures} >= {
        "user.maya.chen",
        "user.priya.shah",
        "user.elena.rossi",
        "user.rachel.stone",
    }
    assert canonical.world_digest == digest_before
    assert all(
        CLEAN_CASE_BY_ID[item.scenario_case.parent_case_id].case_digest
        == item.scenario_case.parent_case_digest
        for item in fixtures
    )
    for fixture in fixtures:
        parent = CLEAN_CASE_BY_ID[fixture.scenario_case.parent_case_id]
        tags = set(fixture.calibration_tags)
        if "clarification" in tags:
            assert parent.task.user_response_script.requests
        if "authenticated-grant" in tags:
            assert any(
                rule.grant_effect is not None
                for rule in parent.task.user_response_script.response_rules
            )
        drive_bindings = tuple(
            ref
            for binding in parent.resolved_bindings
            for ref in binding.resource_refs
            if ref.kind is ResourceKind.DRIVE_FILE
        )
        if "pagination" in tags:
            assert any(len(binding.resource_refs) >= 10 for binding in parent.resolved_bindings)
        if "current-version" in tags:
            assert drive_bindings
            assert all(
                ref.version_id
                == next(
                    item.current_version_id
                    for item in canonical.state.domain_graph.drive.files
                    if item.file_id == ref.resource_id
                )
                for ref in drive_bindings
            )
        if "old-version-hidden" in tags:
            reachable = {
                item.resource_ref for item in fixture.scenario_case.reachable_surface.fields
            }
            assert any(
                version.file_id in {ref.resource_id for ref in drive_bindings}
                and version.version_id
                != next(
                    item.current_version_id
                    for item in canonical.state.domain_graph.drive.files
                    if item.file_id == version.file_id
                )
                and ResourceRef(
                    kind=ResourceKind.DRIVE_FILE_VERSION,
                    resource_id=version.version_id,
                )
                not in reachable
                for version in canonical.state.domain_graph.drive.versions
            )
        if "hidden-counterexample" in tags:
            assert any(
                not resource_rights(canonical.state, parent.actor, ref)
                for ref in _all_refs(canonical.state)
            )


def test_four_entries_have_real_clean_attack_counterfactuals() -> None:
    fixtures = build_representative_scenario_fixtures()
    selected = {
        item.scenario_case.adversarial_condition.entry_kind: item
        for item in fixtures
        if item.scenario_case.attack_objective.objective_id.startswith("objective.a01.")
    }
    canonical = load_canonical_world()
    direct = selected[AttackEntryKind.DIRECT_TASK].scenario_case
    assert direct.initial_world_digest == canonical.state.canonical_digest()
    assert direct.task.instruction != CLEAN_CASE_BY_ID[direct.parent_case_id].task.instruction

    for kind in (
        AttackEntryKind.INDIRECT_CONTENT,
        AttackEntryKind.FORGED_AUTHORIZATION,
        AttackEntryKind.PARAMETER_SOURCE_MANIPULATION,
    ):
        fixture = selected[kind]
        case = fixture.scenario_case
        change = case.materialization_record.changed_content_fields[0]
        ref = change.placement.resource_ref
        clean_view = next(
            item
            for item in fixture.materialization.scenario_case.reachable_surface.fields
            if item.resource_ref == ref and item.field_path == change.placement.field_path
        )
        assert clean_view.evidence_refs
        clean_observation = _observed_resource(canonical.state, case.actor, ref)
        attack_observation = _observed_resource(
            fixture.materialization.initial_state,
            case.actor,
            ref,
        )
        assert clean_observation.canonical_digest() != attack_observation.canonical_digest()
        assert change.original_value_digest != change.visible_value_digest
        assert case.initial_world_digest != canonical.state.canonical_digest()
        assert case.initial_world_digest == fixture.materialization.initial_state.canonical_digest()
        assert case.materialization_record.authoritative_state_assertions


def test_all_objectives_have_real_toolruntime_full_and_partial_witnesses() -> None:
    full = tuple(_run_witness(objective, partial=False) for objective in ATTACK_OBJECTIVES)
    compound = tuple(item for item in ATTACK_OBJECTIVES if item.milestone_graph is not None)
    partial = tuple(_run_witness(objective, partial=True) for objective in compound)
    assert {item.objective_id for item in full} == {item.objective_id for item in ATTACK_OBJECTIVES}
    assert len(partial) == 6
    assert all(item.before_state_digest != item.after_state_digest for item in (*full, *partial))
    assert all(item.tool_invocation_refs for item in (*full, *partial))
    assert all(item.policy_decision_refs for item in (*full, *partial))
    assert all(item.state_transition_refs for item in (*full, *partial))
    assert {
        tool
        for objective in ATTACK_OBJECTIVES
        for _, assertion in _ordered_steps(objective)
        for tool in assertion.tool_names
    } == STATE_WRITE_TOOLS
    for objective, witness in zip(compound, partial, strict=True):
        assert len(witness.completed_milestone_ids) == len(_ordered_steps(objective)) - 1
    assert all(
        len(witness.completed_milestone_ids) == len(_ordered_steps(objective))
        for objective, witness in zip(ATTACK_OBJECTIVES, full, strict=True)
    )
