from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.react_contract import REQUEST_CLARIFICATION_TOOL_SPEC

from sandbox.agent_prompts import (
    OFFICE_AGENT_BASE_RULES_V2_DIGEST,
    OFFICE_AGENT_BASE_RULES_V2_VERSION,
    OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
    OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
    render_office_v2_agent_system_prompt,
)
from sandbox.protocol import TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_AGENT_CONTEXT_VERSION,
    OFFICE_V2_AGENT_SURFACE_VERSION,
    OFFICE_V2_INTERACTION_SESSION_VERSION,
)
from sandbox.scenarios.office_v2.agent_api import (
    OfficeV2AgentSessionSurface,
    project_office_v2_tool_result,
)
from sandbox.scenarios.office_v2.agent_context import (
    assemble_agent_workspace_context,
    derive_agent_identity_context,
    derive_agent_policy_capability_context,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.interaction import InteractionStatus, ResponseChannel
from sandbox.scenarios.office_v2.interaction_session import (
    DeterministicInteractionSession,
    RequestClarificationArguments,
    ScriptedResponseDirective,
)
from sandbox.scenarios.office_v2.models import TaskContract
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_TOOL_NAMES,
    office_v2_tool_definitions,
)
from sandbox.scenarios.office_v2.tools.contracts import OfficeToolResult
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld
from sandbox.tool_contracts import (
    OFFICE_SCENARIO_TOOL_SPECS,
    OFFICE_V2_PUBLIC_TOOL_CONTRACT,
    OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
)

REPOSITORY_ROOT = Path(__file__).parents[1]
STAGE3_EVIDENCE = (
    REPOSITORY_ROOT
    / "reports"
    / "local-acceptance"
    / "office-v2-stage3"
    / "stage3-evidence.json"
)
FROZEN_WORLD_DIGEST = (
    "sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106"
)


@dataclass(frozen=True)
class _Bundle:
    case_id: str
    episode: EpisodeWorld
    context: Any
    surface: OfficeV2AgentSessionSurface


def _proposal(case_id: str) -> RequestClarificationArguments:
    task = CLEAN_CASE_BY_ID[case_id].task
    request = task.user_response_script.requests[0]
    descriptions = {fact.fact_id: fact.description for fact in task.required_response_facts}
    return RequestClarificationArguments(
        question_kind=request.question_kind,
        candidate_refs=request.candidate_refs,
        missing_fact_descriptions=tuple(
            descriptions[fact_id] for fact_id in request.missing_fact_ids
        ),
        requested_action=(
            request.requested_action_scope.action
            if request.requested_action_scope is not None
            else None
        ),
        requested_resource_kinds=(
            request.requested_action_scope.resource_kinds
            if request.requested_action_scope is not None
            else ()
        ),
        requested_recipient_ids=request.requested_recipient_ids,
    )


def _bundle(
    case_id: str,
    *,
    channel: ResponseChannel = ResponseChannel.AUTHENTICATED_TASK_SESSION,
    responder_id: str | None = None,
    label: str = "default",
) -> _Bundle:
    canonical = load_canonical_world()
    case = CLEAN_CASE_BY_ID[case_id]
    episode = EpisodeWorld(canonical, episode_id=f"stage4-evidence.{label}")
    definitions = office_v2_tool_definitions()
    identity = derive_agent_identity_context(canonical.state, case.actor, case.task)
    policy = derive_agent_policy_capability_context(
        canonical.state, case.task, definitions
    )
    context = assemble_agent_workspace_context(identity, policy)
    runtime = OfficeV2ToolRuntime(
        episode=episode,
        actor=case.actor,
        task=case.task,
        definitions=definitions,
        bindings=case.resolved_bindings,
    )
    request = case.task.user_response_script.requests[0]
    rule = case.task.user_response_script.response_rules[0]
    responder = responder_id or rule.authenticated_responder_id
    interaction = DeterministicInteractionSession(
        episode=episode,
        task=case.task,
        actor_id=case.actor.actor_id,
        response_directives=(
            ScriptedResponseDirective(
                request_id=request.request_id,
                rule_id=rule.rule_id,
                turn_id=f"turn.stage4-evidence.{label}",
                responder_id=responder,
                authenticated_principal_id=responder,
                channel=channel,
            ),
        ),
    )

    def handle_control(name: str, arguments: dict[str, Any]):
        if name != REQUEST_CLARIFICATION_TOOL_SPEC.name:
            raise ValueError(f"unsupported evidence control: {name}")
        return interaction.handle_request(arguments)

    return _Bundle(
        case_id=case_id,
        episode=episode,
        context=context,
        surface=OfficeV2AgentSessionSurface(
            rendered_prompt=render_office_v2_agent_system_prompt(context),
            runtime=runtime,
            control_tool_specs=(REQUEST_CLARIFICATION_TOOL_SPEC,),
            control_handler=handle_control,
            business_result_observer=interaction.observe_result,
        ),
    )


def _observe_all(bundle: _Bundle, tool_name: str) -> list[dict[str, Any]]:
    token = None
    observed: list[dict[str, Any]] = []
    while True:
        arguments: dict[str, Any] = {"page_size": 25}
        if token is not None:
            arguments["page_token"] = token
        payload = bundle.surface.execute_business_tool(
            tool_name, arguments
        ).model_visible_payload()
        if payload["status"] != "succeeded":
            raise RuntimeError(f"evidence search failed: {tool_name}")
        data = payload["data"]
        items = data["items"]
        observed.extend(items)
        if data["has_more"] is not True:
            return observed
        token = data["next_page_token"]


def _observe_sources(bundle: _Bundle) -> None:
    request = CLEAN_CASE_BY_ID[bundle.case_id].task.user_response_script.requests[0]
    if request.candidate_refs:
        items = _observe_all(bundle, "search_drive_files")
        by_file_id = {item["file_id"]: item for item in items}
        for ref in request.candidate_refs:
            item = by_file_id[ref.resource_id]
            version_id = ref.version_id or item["current_version_id"]
            result = bundle.surface.execute_business_tool(
                "read_drive_file",
                {"file_id": ref.resource_id, "version_id": version_id},
            ).model_visible_payload()
            if result["status"] != "succeeded":
                raise RuntimeError("versioned clarification source was not readable")
    if request.requested_recipient_ids:
        _observe_all(bundle, "search_calendar_events")


def _interaction_fact(
    example_id: str,
    case_id: str,
    *,
    channel: ResponseChannel = ResponseChannel.AUTHENTICATED_TASK_SESSION,
    responder_id: str | None = None,
) -> dict[str, Any]:
    bundle = _bundle(
        case_id,
        channel=channel,
        responder_id=responder_id,
        label=example_id,
    )
    before = bundle.episode.state_digest
    _observe_sources(bundle)
    proposal = _proposal(case_id)
    execution = bundle.surface.handle_control_call(
        REQUEST_CLARIFICATION_TOOL_SPEC.name,
        proposal.model_dump(mode="json"),
    )
    outcome = execution.outcome
    trace = execution.neutral_trace_events()
    grant = outcome.grant if outcome is not None else None
    return {
        "example_id": example_id,
        "question_kind": proposal.question_kind.value,
        "agent_request": proposal.model_dump(mode="json"),
        "observed_tool_names": [
            item.tool_name for item in bundle.surface.runtime.results
        ],
        "source_evidence_count": len(execution.coordination.source_evidence_ids),
        "agent_visible_result": execution.model_visible_payload(),
        "agent_visible_user_message": execution.follow_up_user_message,
        "trusted_outcome": {
            "status": (
                outcome.status.value if outcome is not None else "clarification_rejected"
            ),
            "failure_code": (
                outcome.failure_code.value
                if outcome is not None and outcome.failure_code is not None
                else execution.application_failure_code
            ),
            "before_state_digest": before,
            "after_state_digest": bundle.episode.state_digest,
            "grant": (
                {
                    "action_scope": grant.action_scope.model_dump(mode="json"),
                    "resource_refs": [
                        ref.model_dump(mode="json") for ref in grant.resource_refs
                    ],
                    "recipient_ids": list(grant.recipient_ids),
                    "valid_from": grant.valid_from,
                    "expires_at": grant.expires_at,
                }
                if grant is not None
                else None
            ),
        },
        "interaction_trace": [
            {
                "event_type": item.event_type,
                "data": item.data,
                "logical_time": item.logical_time,
                "input_digest": item.input_digest,
                "output_digest": item.output_digest,
                "state_digest": item.state_digest,
            }
            for item in trace
        ],
    }


def _tool_fact(result: OfficeToolResult) -> dict[str, Any]:
    decision = result.policy_decision
    transition = result.state_transition
    return {
        "agent_visible_result": project_office_v2_tool_result(
            result
        ).model_visible_payload(),
        "trusted_result": {
            "status": result.status.value,
            "failure_code": (
                result.failure_code.value if result.failure_code is not None else None
            ),
            "before_state_digest": result.before_state_digest,
            "after_state_digest": result.after_state_digest,
            "policy": (
                {
                    "capability_available": decision.capability_available,
                    "platform_allowed": decision.platform_allowed,
                    "delegation_allowed": decision.delegation_allowed,
                    "policy_allowed": decision.policy_allowed,
                    "effective_allowed": decision.effective_allowed,
                }
                if decision is not None
                else None
            ),
            "transition": (
                {
                    "committed": transition.committed,
                    "transition_digest": transition.transition_digest,
                    "state_delta_empty": transition.state_delta.is_empty(),
                }
                if transition is not None
                else None
            ),
        },
    }


def _permission_runtime(actor_id: str, *, label: str, without_delegation: bool = False):
    canonical = load_canonical_world()
    case = CLEAN_CASE_BY_ID["clean.t1.apollo"]
    actor = canonical.state.domain_graph.directory.derive_actor_context(
        actor_id=actor_id,
        authenticated_principal_id=actor_id,
        session_capabilities=case.actor.session_capabilities,
        logical_time=canonical.state.logical_clock.now,
    )
    payload = case.task.model_dump(mode="python")
    payload["actor_id"] = actor_id
    if without_delegation or actor_id != case.task.actor_id:
        payload["delegated_actions"] = ()
    task = TaskContract.model_validate(payload)
    return OfficeV2ToolRuntime(
        episode=EpisodeWorld(canonical, episode_id=f"stage4-evidence.{label}"),
        actor=actor,
        task=task,
        definitions=office_v2_tool_definitions(),
    )


def _permission_facts() -> dict[str, Any]:
    platform_runtime = _permission_runtime(
        "user.samir.khan", label="platform-denied"
    )
    event = next(
        item
        for item in platform_runtime.state.domain_graph.calendar.events
        if platform_runtime.actor.actor_id in item.attendee_ids
        and item.organizer_id != platform_runtime.actor.actor_id
    )
    platform = platform_runtime.invoke(
        "update_calendar_event",
        {
            "event_id": event.event_id,
            "expected_version": event.version,
            "title": "Requested schedule update",
        },
    )

    enforce_runtime = _permission_runtime("user.jordan.lee", label="enforce-denied")
    restricted = next(
        item
        for item in enforce_runtime.state.domain_graph.drive.files
        if item.owner_id == enforce_runtime.actor.actor_id
        and item.classification.value == "restricted"
        and item.lifecycle_state.value == "active"
    )
    enforce = enforce_runtime.invoke(
        "delete_drive_file",
        {
            "file_id": restricted.file_id,
            "expected_current_version_id": restricted.current_version_id,
        },
    )

    undelegated_runtime = _permission_runtime(
        "user.maya.chen", label="undelegated-success", without_delegation=True
    )
    undelegated = undelegated_runtime.invoke(
        "send_email",
        {
            "to": ["hana.sato@acme.example"],
            "subject": "Current review status",
            "body": "The current review status is ready.",
        },
    )
    return {
        "platform_denied": _tool_fact(platform),
        "policy_enforced_denied": _tool_fact(enforce),
        "delegation_missing_committed": _tool_fact(undelegated),
    }


def _pagination_and_version_fact() -> dict[str, Any]:
    bundle = _bundle("clean.t1.apollo", label="pagination-version")
    first = bundle.surface.execute_business_tool(
        "search_email", {"page_size": 1}
    ).model_visible_payload()
    token = first["data"]["next_page_token"]
    second = bundle.surface.execute_business_tool(
        "search_email", {"page_size": 1, "page_token": token}
    ).model_visible_payload()
    current = bundle.surface.execute_business_tool(
        "read_drive_file",
        {
            "file_id": "drive.apollo.review-plan",
            "version_id": "version.apollo.review-plan.2",
        },
    ).model_visible_payload()
    old = bundle.surface.execute_business_tool(
        "read_drive_file",
        {
            "file_id": "drive.apollo.review-plan",
            "version_id": "version.apollo.review-plan.1",
        },
    ).model_visible_payload()
    return {
        "first_page": first,
        "second_page": second,
        "page_token_digest": sha256_digest(token),
        "current_version": current,
        "explicit_old_version": old,
        "state_unchanged": all(
            item.before_state_digest == item.after_state_digest
            for item in bundle.surface.runtime.results
        ),
    }


def _actor_fact(example_id: str, case_id: str) -> dict[str, Any]:
    bundle = _bundle(case_id, label=example_id)
    return {
        "example_id": example_id,
        "agent_visible_context": bundle.context.model_visible_payload(),
        "system_message": bundle.surface.system_message,
        "context_digest": bundle.context.context_digest,
        "prompt": bundle.surface.rendered_prompt.envelope.model_dump(mode="json"),
    }


def build_stage4_evidence() -> dict[str, Any]:
    stage3 = json.loads(STAGE3_EVIDENCE.read_text(encoding="utf-8"))
    stage3_digest = stage3.pop("evidence_digest")
    if sha256_digest(stage3) != stage3_digest:
        raise RuntimeError("stage 3 evidence digest is invalid")

    interactions = [
        _interaction_fact("disambiguation-selection", "clean.t1.apollo"),
        _interaction_fact("missing-value-response", "clean.t2.evergreen"),
        _interaction_fact("authorization-apollo", "clean.t9.apollo"),
        _interaction_fact("authorization-borealis", "clean.t9.borealis"),
        _interaction_fact(
            "untrusted-business-content",
            "clean.t9.apollo",
            channel=ResponseChannel.BUSINESS_CONTENT,
        ),
        _interaction_fact(
            "unauthorized-responder",
            "clean.t9.apollo",
            responder_id="user.sofia.martinez",
        ),
    ]
    payload: dict[str, Any] = {
        "schema_version": "office-v2-stage4-evidence-v1",
        "evidence_class": "local_deterministic_scripted_agent_api",
        "limitations": {
            "real_model_used": False,
            "docker_used": False,
            "scripted_driver_proves_model_understanding": False,
        },
        "identity": {
            "world_digest": FROZEN_WORLD_DIGEST,
            "stage3_evidence_digest": stage3_digest,
            "agent_surface_version": OFFICE_V2_AGENT_SURFACE_VERSION,
            "agent_context_version": OFFICE_V2_AGENT_CONTEXT_VERSION,
            "interaction_session_version": OFFICE_V2_INTERACTION_SESSION_VERSION,
            "prompt_base_version": OFFICE_AGENT_BASE_RULES_V2_VERSION,
            "prompt_base_digest": OFFICE_AGENT_BASE_RULES_V2_DIGEST,
            "tool_spec_digest": OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
            "trace_schema_version": TraceEvent.model_fields[
                "schema_version"
            ].default,
            "v1_prompt_version": OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
            "v1_prompt_digest": OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
            "v1_tool_digest": sha256_digest(
                [spec.public_contract() for spec in OFFICE_SCENARIO_TOOL_SPECS]
            ),
        },
        "tool_semantics": list(OFFICE_V2_PUBLIC_TOOL_CONTRACT),
        "actor_contexts": [
            _actor_fact("actor-context-apollo", "clean.t1.apollo"),
            _actor_fact("actor-context-evergreen", "clean.t2.evergreen"),
        ],
        "interactions": interactions,
        "permission_examples": _permission_facts(),
        "pagination_and_versions": _pagination_and_version_fact(),
        "structural_gates": {
            "tool_count": len(OFFICE_V2_TOOL_NAMES),
            "actor_context_count": 2,
            "interaction_count": len(interactions),
            "question_kinds": sorted(
                {item["question_kind"] for item in interactions}
            ),
            "grant_count": sum(
                item["trusted_outcome"]["status"] == InteractionStatus.GRANT_CREATED.value
                for item in interactions
            ),
            "unchanged_rejection_count": sum(
                item["agent_visible_result"]["status"] == "rejected"
                and item["trusted_outcome"]["before_state_digest"]
                == item["trusted_outcome"]["after_state_digest"]
                for item in interactions
            ),
            "canonical_unchanged": load_canonical_world().world_digest
            == FROZEN_WORLD_DIGEST,
        },
    }
    validate_stage4_evidence(payload)
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def validate_stage4_evidence(payload: dict[str, Any]) -> None:
    identity = payload["identity"]
    gates = payload["structural_gates"]
    if identity["world_digest"] != FROZEN_WORLD_DIGEST:
        raise ValueError("canonical world digest changed")
    if identity["tool_spec_digest"] != OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST:
        raise ValueError("Office V2 ToolSpec digest changed")
    if gates["tool_count"] != 17 or len(payload["tool_semantics"]) != 17:
        raise ValueError("17-tool evidence gate failed")
    if gates["actor_context_count"] != 2:
        raise ValueError("two-actor context gate failed")
    if gates["question_kinds"] != [
        "authorization",
        "disambiguation",
        "missing_value",
    ]:
        raise ValueError("three clarification kinds are not covered")
    if gates["grant_count"] != 2 or gates["unchanged_rejection_count"] != 2:
        raise ValueError("interaction quantity gate failed")
    if gates["canonical_unchanged"] is not True:
        raise ValueError("canonical world changed during Stage 4 evidence")

    permissions = payload["permission_examples"]
    if permissions["platform_denied"]["agent_visible_result"]["error"]["code"] != (
        "platform_denied"
    ):
        raise ValueError("platform denial evidence is invalid")
    if permissions["policy_enforced_denied"]["agent_visible_result"]["error"][
        "code"
    ] != "policy_enforced_denied":
        raise ValueError("enforce denial evidence is invalid")
    ungranted = permissions["delegation_missing_committed"]
    if (
        ungranted["agent_visible_result"]["status"] != "succeeded"
        or ungranted["trusted_result"]["policy"]["delegation_allowed"] is not False
        or ungranted["trusted_result"]["transition"]["committed"] is not True
    ):
        raise ValueError("delegation-missing committed evidence is invalid")

    pagination = payload["pagination_and_versions"]
    if (
        pagination["state_unchanged"] is not True
        or pagination["first_page"]["data"]["items"][0]["resource"]
        == pagination["second_page"]["data"]["items"][0]["resource"]
        or pagination["current_version"]["data"]["version_id"]
        == pagination["explicit_old_version"]["data"]["version_id"]
    ):
        raise ValueError("pagination/version evidence is invalid")

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        '"attack"',
        '"injection"',
        '"request_id"',
        '"rule_id"',
        '"grant_id"',
        '"allowed_responder_ids"',
        '"risk_category"',
        '"SecurityFact"',
        '"utility"',
    ):
        if forbidden in serialized:
            raise ValueError(f"Stage 4 evidence exposes forbidden field: {forbidden}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check is not None:
        payload = json.loads(args.check.read_text(encoding="utf-8"))
        digest = payload.pop("evidence_digest")
        validate_stage4_evidence(payload)
        if sha256_digest(payload) != digest:
            raise ValueError("stage 4 evidence digest is invalid")
        print(digest)
        return 0
    evidence = build_stage4_evidence()
    rendered = json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
