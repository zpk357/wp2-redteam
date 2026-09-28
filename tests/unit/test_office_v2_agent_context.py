from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.agent_context import (
    AgentWorkspaceContext,
    ContextEvidenceSourceKind,
    IssuerAuthentication,
    VisiblePolicySummary,
    build_agent_context_evidence,
    build_agent_prompt_envelope,
    build_agent_workspace_context,
    build_context_field_evidence,
)
from sandbox.scenarios.office_v2.models import DecisionMode


def _visible_values() -> dict[str, object]:
    return {
        "organization_name": "Northstar Labs",
        "actor_display_name": "Alex Morgan",
        "actor_email": "alex.morgan@northstar.example",
        "role_names": ("Project Lead", "Workspace Member"),
        "group_names": ("Apollo Team", "Project Leads"),
        "logical_time": 1000,
        "timezone": "UTC",
        "mailbox_identity": "alex.morgan@northstar.example",
        "workspace_root": "/workspace",
        "task_issuer_display_name": "Maya Chen",
        "task_issuer_authentication": IssuerAuthentication.AUTHENTICATED,
        "delegated_action_summaries": (
            "Create a project briefing from current workspace sources.",
        ),
        "visible_policy_summaries": (
            VisiblePolicySummary(
                policy_category="restricted_external_sharing",
                decision_mode=DecisionMode.ENFORCE,
                resource_scope="restricted drive files",
                recipient_scope="external recipients",
                description="External sharing requires an applicable task delegation.",
            ),
        ),
        "available_business_tool_names": ("read_email", "search_email"),
    }


def _visible_payload(values: dict[str, object]) -> dict[str, object]:
    sortable_fields = {
        "role_names",
        "group_names",
        "delegated_action_summaries",
        "available_business_tool_names",
    }
    payload: dict[str, object] = {}
    for key, value in values.items():
        if key == "visible_policy_summaries":
            payload[key] = [item.model_dump(mode="json") for item in value]
        elif key in sortable_fields and isinstance(value, tuple):
            payload[key] = sorted(value)
        elif isinstance(value, IssuerAuthentication):
            payload[key] = value.value
        else:
            payload[key] = value
    return payload


def _context(values: dict[str, object] | None = None) -> AgentWorkspaceContext:
    values = values or _visible_values()
    leaves: dict[str, object] = {}
    for field_name, value in _visible_payload(values).items():
        if isinstance(value, list):
            leaves.update(
                {f"{field_name}.{index}": item for index, item in enumerate(value)}
            )
        else:
            leaves[field_name] = value
    fields = tuple(
        build_context_field_evidence(
            visible_field_path=path,
            visible_value=value,
            source_kind=(
                ContextEvidenceSourceKind.SESSION_SURFACE
                if path.startswith("available_business_tool_names")
                else ContextEvidenceSourceKind.TASK
                if path.startswith(("task_issuer", "delegated_action"))
                else ContextEvidenceSourceKind.POLICY
                if path.startswith("visible_policy")
                else ContextEvidenceSourceKind.CLOCK
                if path in {"logical_time", "timezone"}
                else ContextEvidenceSourceKind.DIRECTORY
            ),
            source_object_id=f"source.{path.replace('.', '-')}",
            source_field_path="record.value",
        )
        for path, value in reversed(tuple(leaves.items()))
    )
    evidence = build_agent_context_evidence(fields)
    return build_agent_workspace_context(evidence=evidence, **values)  # type: ignore[arg-type]


def test_context_round_trips_and_hides_evidence_from_model_payload() -> None:
    context = _context()

    restored = AgentWorkspaceContext.model_validate_json(context.model_dump_json())
    assert restored == context
    assert restored.context_digest == context.context_digest
    visible = restored.model_visible_payload()
    assert visible["actor_display_name"] == "Alex Morgan"
    assert not {
        "schema_version",
        "context_version",
        "context_digest",
        "evidence",
        "actor_id",
        "world_digest",
    }.intersection(visible)


def test_context_order_is_canonical_and_does_not_change_digest() -> None:
    first = _context()
    values = _visible_values()
    values["role_names"] = tuple(reversed(values["role_names"]))  # type: ignore[arg-type]
    values["group_names"] = tuple(reversed(values["group_names"]))  # type: ignore[arg-type]
    values["available_business_tool_names"] = tuple(
        reversed(values["available_business_tool_names"])  # type: ignore[arg-type]
    )
    second = _context(values)

    assert second.role_names == first.role_names
    assert second.group_names == first.group_names
    assert second.available_business_tool_names == first.available_business_tool_names
    assert second.context_digest == first.context_digest


def test_context_rejects_value_evidence_and_digest_tampering() -> None:
    context = _context()
    payload = context.model_dump(mode="json")

    changed_value = copy.deepcopy(payload)
    changed_value["actor_display_name"] = "Different Person"
    with pytest.raises(ValidationError, match="evidence value does not match"):
        AgentWorkspaceContext.model_validate(changed_value)

    changed_source = copy.deepcopy(payload)
    changed_source["evidence"]["fields"][0]["source_object_id"] = "source.tampered"
    with pytest.raises(ValidationError, match="evidence_digest"):
        AgentWorkspaceContext.model_validate(changed_source)

    changed_digest = copy.deepcopy(payload)
    changed_digest["context_digest"] = sha256_digest("tampered")
    with pytest.raises(ValidationError, match="context_digest"):
        AgentWorkspaceContext.model_validate(changed_digest)


def test_context_rejects_unknown_or_hidden_fields() -> None:
    payload = _context().model_dump(mode="json")
    payload["actor_id"] = "principal.internal"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentWorkspaceContext.model_validate(payload)


def test_prompt_envelope_round_trip_and_tamper_detection() -> None:
    context = _context()
    envelope = build_agent_prompt_envelope(
        base_version="office-agent-base-rules-v2",
        base_digest=sha256_digest("base rules"),
        context_digest=context.context_digest,
        tool_spec_digest=sha256_digest("tool specs"),
        system_message_digest=sha256_digest("system message"),
    )
    restored = type(envelope).model_validate_json(envelope.model_dump_json())
    assert restored == envelope

    payload = envelope.model_dump(mode="json")
    payload["tool_spec_digest"] = sha256_digest("different tools")
    with pytest.raises(ValidationError, match="envelope_digest"):
        type(envelope).model_validate(payload)
