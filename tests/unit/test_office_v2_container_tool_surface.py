from __future__ import annotations

import json

from app.agent.react_contract import (
    REQUEST_CLARIFICATION_TOOL_SPEC,
    SUBMIT_TOOL_SPEC,
)
from app.office_v2_session import OfficeV2ContainerSession, load_office_v2_session

from sandbox.agent_prompts import render_office_v2_agent_system_prompt
from sandbox.protocol import ModelOptions
from sandbox.scenarios.office_v2.agent_context import (
    assemble_agent_workspace_context,
    derive_agent_identity_context,
    derive_agent_policy_capability_context,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.tool_contracts import OFFICE_V2_TOOL_SPECS


def _session(episode_id: str) -> OfficeV2ContainerSession:
    canonical = load_canonical_world()
    case = CLEAN_CASE_BY_ID["clean.t2.evergreen"]
    envelope = build_v2_execution_envelope(
        case,
        initial_state=canonical.state,
        model_identity=ModelOptions(provider="fake", model_name="stage7-scripted"),
    )
    return load_office_v2_session(envelope, episode_id=episode_id)


def _surface(session: OfficeV2ContainerSession, *, observer=None):
    context = assemble_agent_workspace_context(
        derive_agent_identity_context(
            session.episode.state,
            session.runtime.actor,
            session.runtime.task,
        ),
        derive_agent_policy_capability_context(
            session.episode.state,
            session.runtime.task,
            session.runtime.definitions,
        ),
    )
    return session.build_agent_surface(
        rendered_prompt=render_office_v2_agent_system_prompt(context),
        control_tool_specs=(REQUEST_CLARIFICATION_TOOL_SPEC, SUBMIT_TOOL_SPEC),
        control_handler=lambda name, arguments: (name, arguments),
        business_result_observer=observer,
    )


def test_surface_reuses_frozen_17_tool_catalog_and_single_runtime() -> None:
    session = _session("episode.stage7.tool-surface")
    surface = _surface(session)
    definitions = office_v2_tool_definitions()

    assert surface.runtime is session.runtime
    assert surface.business_tool_specs is OFFICE_V2_TOOL_SPECS
    assert len(surface.business_tool_specs) == 17
    assert {
        spec.name for spec in surface.business_tool_specs
    }.isdisjoint(spec.name for spec in surface.control_tool_specs)
    assert all(
        spec.definition is definitions[spec.name]
        for spec in surface.business_tool_specs
    )


def test_model_sees_projection_while_session_keeps_complete_trusted_result() -> None:
    session = _session("episode.stage7.visible-projection")
    observed = []
    surface = _surface(session, observer=observed.append)

    projection = surface.execute_business_tool(
        "read_drive_file",
        {
            "file_id": "drive.evergreen.public-brief",
            "version_id": "version.evergreen.public-brief.1",
        },
    )

    visible = projection.model_visible_payload()
    serialized_visible = json.dumps(visible, sort_keys=True)
    assert visible["status"] == "succeeded"
    assert session.trusted_tool_results == (projection.trusted_result,)
    assert observed == [projection.trusted_result]
    assert session.runtime.results[-1] is projection.trusted_result
    assert projection.trusted_result.policy_decision is not None
    assert projection.trusted_result.output_evidence
    for hidden_name in (
        "policy_decision",
        "state_transition",
        "output_evidence",
        "before_state_digest",
        "after_state_digest",
    ):
        assert hidden_name not in serialized_visible


def test_invalid_arguments_are_stable_and_do_not_bypass_trusted_sidecar() -> None:
    session = _session("episode.stage7.invalid-arguments")
    before = session.episode.state_digest
    projection = _surface(session).execute_business_tool("read_drive_file", {})

    assert projection.model_visible_payload() == {
        "status": "rejected",
        "data": {},
        "error": {
            "code": "invalid_arguments",
            "message": "The tool arguments do not match the required business schema.",
            "retryable": False,
        },
    }
    assert session.trusted_tool_results == (projection.trusted_result,)
    assert session.episode.state_digest == before


def test_later_call_gets_hidden_exact_sources_from_prior_visible_result() -> None:
    session = _session("episode.stage7.argument-sources")
    surface = _surface(session)
    search = surface.execute_business_tool(
        "search_drive_files",
        {"page_size": 25},
    ).model_visible_payload()
    assert search["status"] == "succeeded"
    hit = next(
        item
        for item in search["data"]["items"]
        if item["file_id"] == "drive.evergreen.public-brief"
    )

    read = surface.execute_business_tool(
        "read_drive_file",
        {
            "file_id": hit["file_id"],
            "version_id": hit["current_version_id"],
        },
    )

    assert read.model_visible_payload()["status"] == "succeeded"
    invocation = session.runtime.invocations[-1]
    assert {source.argument_path for source in invocation.argument_sources} == {
        ("file_id",),
        ("version_id",),
    }
    visible = json.dumps(read.model_visible_payload(), sort_keys=True)
    assert "source_evidence_ids" not in visible


def test_sessions_keep_independent_tool_results_and_control_calls() -> None:
    left = _session("episode.stage7.surface-left")
    right = _session("episode.stage7.surface-right")
    left_surface = _surface(left)
    right_surface = _surface(right)

    left_surface.execute_business_tool("search_drive_files", {"page_size": 25})
    control = right_surface.handle_control_call("submit", {"answer": "done"})

    assert len(left.trusted_tool_results) == 1
    assert right.trusted_tool_results == ()
    assert control == ("submit", {"answer": "done"})
