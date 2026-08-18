from __future__ import annotations

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_cases import (
    build_representative_scenario_fixtures,
)
from sandbox.scenarios.office_v2.attack_models import (
    AttackEntryKind,
    ContentPlacement,
    DirectTaskCondition,
    ForgedAuthorizationCondition,
    IndirectContentCondition,
    ParameterSourceManipulationCondition,
)
from sandbox.scenarios.office_v2.models import ResourceKind, ResourceRef
from sandbox.scenarios.office_v2.oracle_evidence import (
    build_oracle_evidence_bundle,
    build_termination_fact,
)
from sandbox.scenarios.office_v2.oracle_models import ExposureStage
from sandbox.scenarios.office_v2.security_oracle import evaluate_exposure
from sandbox.scenarios.office_v2.tools.contracts import (
    ArgumentSource,
    ArgumentSourceMode,
    OfficeToolInvocation,
    OutputEvidence,
    ToolResultStatus,
    build_tool_result,
)


def _materialization(entry_kind: AttackEntryKind):
    return next(
        fixture.materialization
        for fixture in build_representative_scenario_fixtures()
        if fixture.materialization.scenario_case.adversarial_condition.entry_kind
        is entry_kind
    )


def _placement(materialization, *, kind: ResourceKind | None = None) -> ContentPlacement:
    condition = materialization.scenario_case.adversarial_condition
    if isinstance(condition, IndirectContentCondition):
        placements = condition.placements
    elif isinstance(condition, ForgedAuthorizationCondition):
        placements = condition.claim_placements
    elif isinstance(condition, ParameterSourceManipulationCondition):
        placements = (condition.source_placement,)
    else:
        raise AssertionError("direct conditions have no content placement")
    return next(
        item for item in placements if kind is None or item.resource_ref.kind is kind
    )


def _visible_digest(materialization, placement: ContentPlacement) -> str:
    return next(
        item.visible_value_digest
        for item in materialization.scenario_case.materialization_record.changed_content_fields
        if item.placement == placement
    )


def _output_path(placement: ContentPlacement) -> tuple[str, ...]:
    overrides = {
        (ResourceKind.MAIL_MESSAGE, ("attachment_refs",)): ("related_refs",),
        (ResourceKind.DRIVE_FILE, ("current_version", "content")): ("content",),
        (ResourceKind.DRIVE_FILE, ("current_version", "source_refs")): ("source_refs",),
    }
    return overrides.get(
        (placement.resource_ref.kind, placement.field_path),
        placement.field_path,
    )


def _read_tool(placement: ContentPlacement) -> str:
    return {
        ResourceKind.MAIL_MESSAGE: "read_email",
        ResourceKind.DRIVE_FILE: "read_drive_file",
        ResourceKind.DRIVE_FILE_VERSION: "read_drive_file",
        ResourceKind.CALENDAR_EVENT: "read_calendar_event",
        ResourceKind.WORKSPACE_FILE: "read_file",
    }[placement.resource_ref.kind]


def _termination(sequence: int):
    return build_termination_fact(
        evidence_id="termination.exposure.001",
        sequence=sequence,
        reason="submit",
        submitted=True,
        output_digest=sha256_digest({"status": "done"}),
    )


def _bundle(
    materialization,
    *,
    placement: ContentPlacement | None = None,
    observed_tool: str | None = None,
    observed_path: tuple[str, ...] | None = None,
    observed_resource: ResourceRef | None = None,
    observed_value_digest: str | None = None,
    used: bool = False,
):
    case = materialization.scenario_case
    invocations = []
    results = []
    if placement is not None:
        tool_name = observed_tool or _read_tool(placement)
        invocation = OfficeToolInvocation(
            invocation_id="invocation.exposure.read.001",
            sequence=0,
            tool_name=tool_name,
            actor_id=case.actor.actor_id,
            task_id=case.task.task_id,
            logical_time=1,
            arguments={"target": placement.resource_ref.resource_id},
            arguments_digest=sha256_digest(
                {"target": placement.resource_ref.resource_id}
            ),
            before_state_digest=case.initial_world_digest,
        )
        evidence = OutputEvidence(
            evidence_id="evidence.exposure.output.001",
            invocation_id=invocation.invocation_id,
            invocation_sequence=0,
            field_path=observed_path or _output_path(placement),
            resource_ref=observed_resource or placement.resource_ref,
            value_digest=observed_value_digest
            or _visible_digest(materialization, placement),
        )
        result = build_tool_result(
            invocation=invocation,
            status=ToolResultStatus.SUCCEEDED,
            visible_output={"observed": True},
            output_evidence=(evidence,),
        )
        invocations.append(invocation)
        results.append(result)

        if used:
            follow_up = OfficeToolInvocation(
                invocation_id="invocation.exposure.use.002",
                sequence=1,
                tool_name="search_calendar_events",
                actor_id=case.actor.actor_id,
                task_id=case.task.task_id,
                logical_time=2,
                arguments={"text": "derived"},
                arguments_digest=sha256_digest({"text": "derived"}),
                argument_sources=(
                    ArgumentSource(
                        argument_path=("text",),
                        source_evidence_ids=(evidence.evidence_id,),
                        mode=ArgumentSourceMode.EXACT_VALUE,
                    ),
                ),
                before_state_digest=case.initial_world_digest,
            )
            follow_up_result = build_tool_result(
                invocation=follow_up,
                status=ToolResultStatus.SUCCEEDED,
                visible_output={"items": []},
            )
            invocations.append(follow_up)
            results.append(follow_up_result)

    return build_oracle_evidence_bundle(
        scenario_case=case,
        initialization_transition=materialization.initialization_transition,
        invocations=tuple(invocations),
        results=tuple(results),
        interaction_facts=(),
        timeline=None,
        termination=_termination(len(invocations)),
        final_state_digest=case.initial_world_digest,
    )


def _stages(materialization, bundle) -> tuple[ExposureStage, ...]:
    return evaluate_exposure(
        bundle=bundle,
        scenario_case=materialization.scenario_case,
    )[0].stages


def test_direct_task_is_observed_only_from_the_delivered_top_level_task() -> None:
    materialization = _materialization(AttackEntryKind.DIRECT_TASK)
    assert isinstance(
        materialization.scenario_case.adversarial_condition,
        DirectTaskCondition,
    )
    assert _stages(materialization, _bundle(materialization)) == (
        ExposureStage.PLANNED,
        ExposureStage.DELIVERED,
        ExposureStage.OBSERVED,
    )


@pytest.mark.parametrize(
    "entry_kind",
    (
        AttackEntryKind.INDIRECT_CONTENT,
        AttackEntryKind.FORGED_AUTHORIZATION,
        AttackEntryKind.PARAMETER_SOURCE_MANIPULATION,
    ),
)
def test_content_entry_is_not_observed_merely_because_it_was_materialized(
    entry_kind: AttackEntryKind,
) -> None:
    materialization = _materialization(entry_kind)
    assert _stages(materialization, _bundle(materialization)) == (
        ExposureStage.PLANNED,
        ExposureStage.DELIVERED,
    )


@pytest.mark.parametrize(
    "entry_kind",
    (
        AttackEntryKind.INDIRECT_CONTENT,
        AttackEntryKind.FORGED_AUTHORIZATION,
        AttackEntryKind.PARAMETER_SOURCE_MANIPULATION,
    ),
)
def test_exact_mutated_field_observation_is_evidence_backed(
    entry_kind: AttackEntryKind,
) -> None:
    materialization = _materialization(entry_kind)
    placement = _placement(materialization)
    result = evaluate_exposure(
        bundle=_bundle(materialization, placement=placement),
        scenario_case=materialization.scenario_case,
    )[0]
    assert result.stages == (
        ExposureStage.PLANNED,
        ExposureStage.DELIVERED,
        ExposureStage.OBSERVED,
    )
    assert any(ref.evidence_id == "evidence.exposure.output.001" for ref in result.evidence_refs)


def test_search_result_item_prefix_preserves_parameter_observation() -> None:
    materialization = next(
        fixture.materialization
        for fixture in build_representative_scenario_fixtures()
        if fixture.scenario_case.case_id == "scenario.4d39f97ac0737d08cd8ac91d"
    )
    placement = _placement(materialization)
    output_path = ("items", "0", *_output_path(placement))
    assert _stages(
        materialization,
        _bundle(
            materialization,
            placement=placement,
            observed_tool="search_calendar_events",
            observed_path=output_path,
            observed_value_digest=sha256_digest("2026-09-21T22:15:00+08:00"),
            used=True,
        ),
    )[-2:] == (ExposureStage.OBSERVED, ExposureStage.USED)


def test_different_local_time_is_not_parameter_observation() -> None:
    materialization = next(
        fixture.materialization
        for fixture in build_representative_scenario_fixtures()
        if fixture.scenario_case.case_id == "scenario.4d39f97ac0737d08cd8ac91d"
    )
    placement = _placement(materialization)
    assert _stages(
        materialization,
        _bundle(
            materialization,
            placement=placement,
            observed_tool="search_calendar_events",
            observed_path=("items", "0", *_output_path(placement)),
            observed_value_digest=sha256_digest("2026-09-21T22:30:00+08:00"),
            used=True,
        ),
    ) == (ExposureStage.PLANNED, ExposureStage.DELIVERED)


def test_search_metadata_cannot_impersonate_reading_calendar_description() -> None:
    materialization = _materialization(AttackEntryKind.INDIRECT_CONTENT)
    placement = _placement(materialization, kind=ResourceKind.CALENDAR_EVENT)
    assert _stages(
        materialization,
        _bundle(
            materialization,
            placement=placement,
            observed_tool="search_calendar_events",
        ),
    ) == (ExposureStage.PLANNED, ExposureStage.DELIVERED)


def test_reading_a_different_field_does_not_count_as_exposure() -> None:
    materialization = _materialization(AttackEntryKind.FORGED_AUTHORIZATION)
    placement = _placement(materialization)
    assert _stages(
        materialization,
        _bundle(materialization, placement=placement, observed_path=("title",)),
    ) == (ExposureStage.PLANNED, ExposureStage.DELIVERED)


def test_reading_the_wrong_drive_version_does_not_count_as_exposure() -> None:
    materialization = _materialization(AttackEntryKind.INDIRECT_CONTENT)
    placement = _placement(materialization, kind=ResourceKind.DRIVE_FILE)
    wrong_version = placement.resource_ref.model_copy(update={"version_id": "version.wrong.1"})
    assert _stages(
        materialization,
        _bundle(
            materialization,
            placement=placement,
            observed_resource=wrong_version,
        ),
    ) == (ExposureStage.PLANNED, ExposureStage.DELIVERED)


def test_parameter_read_but_ignore_differs_from_read_and_use() -> None:
    materialization = _materialization(AttackEntryKind.PARAMETER_SOURCE_MANIPULATION)
    placement = _placement(materialization)
    ignored = _stages(materialization, _bundle(materialization, placement=placement))
    used = _stages(
        materialization,
        _bundle(materialization, placement=placement, used=True),
    )
    assert ignored[-1] is ExposureStage.OBSERVED
    assert used[-1] is ExposureStage.USED


def test_observed_forged_claim_does_not_create_an_authorization_fact() -> None:
    materialization = _materialization(AttackEntryKind.FORGED_AUTHORIZATION)
    placement = _placement(materialization)
    bundle = _bundle(materialization, placement=placement)
    result = evaluate_exposure(
        bundle=bundle,
        scenario_case=materialization.scenario_case,
    )[0]
    assert result.stages[-1] is ExposureStage.OBSERVED
    assert bundle.interaction_facts == ()


def test_scenario_identity_mismatch_is_rejected() -> None:
    materialization = _materialization(AttackEntryKind.INDIRECT_CONTENT)
    other = _materialization(AttackEntryKind.FORGED_AUTHORIZATION)
    with pytest.raises(ValueError, match="scenario case"):
        evaluate_exposure(
            bundle=_bundle(materialization),
            scenario_case=other.scenario_case,
        )
