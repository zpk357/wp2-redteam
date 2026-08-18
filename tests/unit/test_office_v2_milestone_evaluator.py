from __future__ import annotations

from contextlib import suppress
from functools import cache

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_models import (
    AttackEntryKind,
    ObjectiveResolvedBinding,
)
from sandbox.scenarios.office_v2.attack_objectives import ATTACK_OBJECTIVES
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.models import (
    PrincipalKind,
    PrincipalStatus,
    ResourceKind,
    ResourceRef,
    TaskContract,
)
from sandbox.scenarios.office_v2.oracle_evidence import (
    OracleEvidenceBundle,
    _exchange,
)
from sandbox.scenarios.office_v2.oracle_models import (
    AssertionMatchStatus,
    ExposureFact,
    ExposureStage,
    MilestoneOutcome,
    ObjectiveCompletionKind,
)
from sandbox.scenarios.office_v2.security_oracle import (
    evaluate_compound_objective,
    evaluate_planned_objective,
)
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.contracts import (
    ArgumentSource,
    ArgumentSourceMode,
    ToolResultStatus,
)
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld
from tests.integration.test_office_v2_stage5_representatives import (
    _invoke_step,
    _ordered_steps,
    _witness_session,
    _WitnessSession,
)
from tests.unit.test_office_v2_utility_evaluator import _t10_bundle


def _objective(prefix: str):
    return next(item for item in ATTACK_OBJECTIVES if item.objective_id.startswith(prefix))


def _signed_exposure(raw: OracleEvidenceBundle, condition_id: str) -> ExposureFact:
    payload = {
        "condition_id": condition_id,
        "entry_kind": AttackEntryKind.DIRECT_TASK,
        "stages": (ExposureStage.PLANNED,),
        "evidence_refs": (raw.materialization_ref,),
    }
    draft = ExposureFact.model_construct(
        **payload,
        fact_digest="sha256:" + "0" * 64,
    )
    return ExposureFact(
        **payload,
        fact_digest=sha256_digest(draft.digest_payload()),
    )


@cache
def _base_bundle() -> OracleEvidenceBundle:
    return _t10_bundle()[0]


def _objective_bindings(session) -> tuple[ObjectiveResolvedBinding, ...]:
    bindings = []
    slots = {item.slot_id: item for item in session.objective.required_binding_slots}
    for slot_id, slot in slots.items():
        value = session.bindings.get(slot_id)
        if not slot.required_before:
            bindings.append(
                ObjectiveResolvedBinding(
                    slot_id=slot_id,
                    output_resource_kind=slot.resource_kinds[0],
                    evidence_refs=(f"binding-evidence.{slot_id}",),
                )
            )
        elif isinstance(value, ResourceRef):
            bindings.append(
                ObjectiveResolvedBinding(
                    slot_id=slot_id,
                    resource_refs=(value,),
                    evidence_refs=(f"binding-evidence.{slot_id}",),
                )
            )
        else:
            bindings.append(
                ObjectiveResolvedBinding(
                    slot_id=slot_id,
                    principal_ids=(session.recipient_id,),
                    evidence_refs=(f"binding-evidence.{slot_id}",),
                )
            )
    return tuple(sorted(bindings, key=lambda item: item.slot_id))


def _invoke_reference_step(session, assertion) -> None:
    source_messages = tuple(
        value
        for slot_id in assertion.binding_slots
        if isinstance((value := session.bindings.get(slot_id)), ResourceRef)
        and value.kind is ResourceKind.MAIL_MESSAGE
    )
    if assertion.tool_names == ("send_email",) and source_messages:
        source_message = source_messages[0]
        read_result = session.runtime.invoke(
            "read_email",
            {"message_id": source_message.resource_id},
        )
        assert read_result.status is ToolResultStatus.SUCCEEDED
        body_evidence = next(
            item
            for item in read_result.output_evidence
            if item.field_path == ("body",) and item.resource_ref == source_message
        )
        send_result = session.runtime.invoke(
            "send_email",
            {
                "to": [session.recipient_id],
                "subject": "Controlled source-lineage witness",
                "body": read_result.visible_output["body"],
                "in_reply_to": source_message.resource_id,
            },
            argument_sources=(
                ArgumentSource(
                    argument_path=("body",),
                    source_evidence_ids=(body_evidence.evidence_id,),
                    mode=ArgumentSourceMode.EXACT_VALUE,
                ),
            ),
        )
        assert send_result.status is ToolResultStatus.SUCCEEDED
        return
    if session.objective.objective_id.startswith("objective.a06.") and assertion.tool_names == (
        "send_email",
    ):
        replacement = session.bindings["replacement_event"]
        assert isinstance(replacement, ResourceRef)
        result = session.runtime.invoke(
            "send_email",
            {
                "to": [session.recipient_id],
                "subject": "Controlled replacement event notification",
                "body": "Tests-only state transition evidence.",
                "related_refs": [replacement.model_dump(mode="json")],
            },
        )
        assert result.status is ToolResultStatus.SUCCEEDED
        return
    _invoke_step(session, assertion)


def _bundle_for_steps(objective, step_count: int):
    session = _witness_session(objective, f"milestone-{step_count}")
    steps = _ordered_steps(objective)
    for _, assertion in steps[:step_count]:
        _invoke_reference_step(session, assertion)
    raw = _base_bundle()
    exchanges = tuple(
        _exchange(invocation, result)
        for invocation, result in zip(
            session.runtime.invocations,
            session.runtime.results,
            strict=True,
        )
    )
    bundle = OracleEvidenceBundle.model_construct(
        **{
            name: getattr(raw, name)
            for name in OracleEvidenceBundle.model_fields
            if name
            not in {
                "bundle_digest",
                "objective_bindings",
                "tool_exchanges",
            }
        },
        objective_bindings=_objective_bindings(session),
        tool_exchanges=exchanges,
        bundle_digest="sha256:" + "0" * 64,
    )
    return bundle, _signed_exposure(raw, f"condition.{objective.objective_id}")


def _blocked_bundle(objective):
    source = _witness_session(objective, "blocked")
    first_assertion = _ordered_steps(objective)[0][1]
    canonical = load_canonical_world()
    definitions = office_v2_tool_definitions()
    capabilities = tuple(sorted({item.capability_id for item in definitions.values()}))
    blocked = None
    for principal in canonical.state.domain_graph.directory.principals:
        if (
            principal.kind is not PrincipalKind.USER
            or principal.status is not PrincipalStatus.ACTIVE
            or principal.principal_id == source.runtime.actor.actor_id
        ):
            continue
        actor = canonical.state.domain_graph.directory.derive_actor_context(
            actor_id=principal.principal_id,
            authenticated_principal_id=principal.principal_id,
            session_capabilities=capabilities,
            logical_time=canonical.state.logical_clock.now,
        )
        task = TaskContract.model_validate(
            source.runtime.task.model_dump(mode="python", exclude_none=False)
            | {"actor_id": actor.actor_id}
        )
        episode = EpisodeWorld(
            canonical,
            episode_id=f"milestone-blocked-{objective.objective_id}-{actor.actor_id}",
        )
        runtime = OfficeV2ToolRuntime(
            episode=episode,
            actor=actor,
            task=task,
            definitions=definitions,
        )
        candidate = _WitnessSession(
            objective=objective,
            runtime=runtime,
            bindings=source.bindings,
            recipient_id=source.recipient_id,
            before_state_digest=episode.state_digest,
        )
        with suppress(AssertionError):
            _invoke_reference_step(candidate, first_assertion)
        if (
            runtime.results
            and runtime.results[0].status is ToolResultStatus.BLOCKED
            and runtime.results[0].policy_decision is not None
            and runtime.results[0].policy_decision.capability_available
        ):
            blocked = candidate
            break
    if blocked is None:
        return None
    runtime = blocked.runtime

    raw = _base_bundle()
    exchange = _exchange(runtime.invocations[0], runtime.results[0])
    bundle = OracleEvidenceBundle.model_construct(
        **{
            name: getattr(raw, name)
            for name in OracleEvidenceBundle.model_fields
            if name not in {"bundle_digest", "objective_bindings", "tool_exchanges"}
        },
        objective_bindings=_objective_bindings(blocked),
        tool_exchanges=(exchange,),
        bundle_digest="sha256:" + "0" * 64,
    )
    return bundle, _signed_exposure(raw, f"condition.{objective.objective_id}.blocked")


@pytest.mark.parametrize(
    ("step_count", "completion", "realized_count"),
    (
        (0, ObjectiveCompletionKind.NONE, 0),
        (1, ObjectiveCompletionKind.PARTIAL, 1),
        (2, ObjectiveCompletionKind.PARTIAL, 2),
        (3, ObjectiveCompletionKind.FULL, 3),
    ),
)
def test_a01_reports_exact_zero_to_three_milestone_progress(
    step_count: int,
    completion: ObjectiveCompletionKind,
    realized_count: int,
) -> None:
    objective = _objective("objective.a01.")
    bundle, exposure = _bundle_for_steps(objective, step_count)

    result = evaluate_compound_objective(
        objective=objective,
        exposure_fact=exposure,
        bundle=bundle,
    )

    assert result.completion_kind is completion
    assert len(result.realized_milestone_ids) == realized_count
    assert (
        sum(item.outcome is MilestoneOutcome.REALIZED for item in result.milestone_facts)
        == realized_count
    )


@pytest.mark.parametrize(
    "objective_prefix",
    (
        "objective.a05.",
        "objective.a06.",
        "objective.a07.",
        "objective.a08.",
        "objective.a12.",
    ),
)
def test_other_compound_objectives_preserve_partial_and_full_progress(
    objective_prefix: str,
) -> None:
    objective = _objective(objective_prefix)
    step_count = len(_ordered_steps(objective))
    partial_bundle, partial_exposure = _bundle_for_steps(objective, step_count - 1)
    full_bundle, full_exposure = _bundle_for_steps(objective, step_count)

    partial = evaluate_compound_objective(
        objective=objective,
        exposure_fact=partial_exposure,
        bundle=partial_bundle,
    )
    full = evaluate_compound_objective(
        objective=objective,
        exposure_fact=full_exposure,
        bundle=full_bundle,
    )

    assert partial.completion_kind is ObjectiveCompletionKind.PARTIAL
    assert len(partial.realized_milestone_ids) == step_count - 1
    assert full.completion_kind is ObjectiveCompletionKind.FULL
    assert len(full.realized_milestone_ids) == step_count


@pytest.mark.parametrize(
    "objective",
    ATTACK_OBJECTIVES,
    ids=lambda item: item.objective_id,
)
def test_all_objectives_have_attempted_blocked_realized_positive_and_negative_examples(
    objective,
) -> None:
    step_count = len(_ordered_steps(objective))
    empty_bundle, empty_exposure = _bundle_for_steps(objective, 0)
    blocked_pair = _blocked_bundle(objective)
    full_bundle, full_exposure = _bundle_for_steps(objective, step_count)

    empty = evaluate_planned_objective(
        objective=objective,
        exposure_fact=empty_exposure,
        bundle=empty_bundle,
    )
    full = evaluate_planned_objective(
        objective=objective,
        exposure_fact=full_exposure,
        bundle=full_bundle,
    )

    assert all(item.outcome is MilestoneOutcome.NOT_REACHED for item in empty.milestone_facts)
    if blocked_pair is not None:
        blocked_bundle, blocked_exposure = blocked_pair
        blocked = evaluate_planned_objective(
            objective=objective,
            exposure_fact=blocked_exposure,
            bundle=blocked_bundle,
        )
        assert any(item.outcome is MilestoneOutcome.BLOCKED for item in blocked.milestone_facts)
        assert any(
            evaluation.match_status is AssertionMatchStatus.MATCHED
            for milestone in blocked.milestone_facts
            for evaluation in milestone.attempted_evaluations
        )
        assert all(
            evaluation.match_status is AssertionMatchStatus.UNMATCHED
            for milestone in blocked.milestone_facts
            for evaluation in milestone.realized_evaluations
        )
    else:
        assert all(
            evaluation.match_status is AssertionMatchStatus.UNMATCHED
            for milestone in empty.milestone_facts
            for evaluation in milestone.blocked_evaluations
        )
    assert full.completion_kind is ObjectiveCompletionKind.FULL
    assert all(item.outcome is MilestoneOutcome.REALIZED for item in full.milestone_facts)
