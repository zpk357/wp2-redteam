"""COMP-A: the completion fixture keeps the real Office path and two-phase contract.

These tests use a controlled provider/model port, but all tool calls go through the same Office
runtime used by a server episode.  The provider records the actual React conversation so the
action handoff can be checked for both its trusted prompt and the earlier tool results.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from app.agent.react_contract import ReactToolCall, ReactTurn
from app.structured_v1_model_port import ReactProviderModelPort

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
import test_structured_fixture_branching as helpers

from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.scenarios.structured_v1.fixtures import summary_delivery_e2 as E2
from sandbox.scenarios.structured_v1.fixtures import (
    summary_delivery_e2_completion as COMPLETION,
)
from sandbox.structured_v1.bundle import EpisodeBundle, verify_bundle
from sandbox.structured_v1.drivers import ScriptedModelPort, drive_structured_v1_episode
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.submission import build_envelope


def _budget(**overrides):
    return helpers._budget().model_copy(update={
        "max_model_calls": 16,
        "max_tool_calls": 16,
        **overrides,
    })


def _envelope(fixture, *, episode_id: str, budget=None):
    return build_envelope(
        helpers._normal_control_case(fixture),
        manifest=fixture.manifest,
        base_world=fixture.base_world,
        overlay=fixture.overlay,
        budget=budget or _budget(),
        episode_id=episode_id,
        arm_id="completion-diagnostic",
    ).envelope


def _run(model, *, episode_id="completion-diagnostic", budget=None):
    fixture = load_fixture(COMPLETION.FIXTURE_ID)
    assets, tooling = helpers._store(fixture)
    bundle = drive_structured_v1_episode(
        _envelope(fixture, episode_id=episode_id, budget=budget),
        assets=assets,
        tooling=tooling,
        model=model,
        clock=helpers._QuietClock(),
    )
    verify_bundle(bundle)
    assert EpisodeBundle.model_validate_json(bundle.model_dump_json()) == bundle
    return fixture, bundle


class _HandoffProvider:
    """Choose a real read, stop investigation, then choose one allowed delivery."""

    version = "completion-diagnostic-provider-v1"
    last_token_usage = {"prompt_tokens": 20, "completion_tokens": 10}

    def __init__(self, channel: str = "mail") -> None:
        self.channel = channel
        self.inputs = []

    async def generate(self, messages, tools, *, seed):
        del tools, seed
        self.inputs.append(messages)
        tool_messages = [item for item in messages if item.role == "tool"]
        if not tool_messages:
            return ReactTurn(tool_calls=[ReactToolCall(
                name="search_drive_files", arguments={"page_size": 25},
            )])
        if len(tool_messages) == 1:
            return ReactTurn(tool_calls=[ReactToolCall(
                name="read_drive_file", arguments={"file_id": E2.PUBLIC_ANCHOR_FILE},
            )])
        # A plain assistant stop closes investigation and causes the controller to append the
        # frozen action task.  It must not erase the search/read tool messages.
        if not any(item.role == "user" and item.content == COMPLETION.ACTION_TASK
                   for item in messages):
            return ReactTurn(assistant_text="调查完成，进入行动阶段。")
        if len(tool_messages) == 2:
            if self.channel == "mail":
                return ReactTurn(tool_calls=[ReactToolCall(
                    name="send_email",
                    arguments={
                        "to": ["partner.contact"],
                        "subject": "合作简报",
                        "body": E2.PUBLIC_ANCHOR_BODY,
                        "related_refs": [{
                            "kind": "drive_file", "resource_id": E2.PUBLIC_ANCHOR_FILE,
                        }],
                    },
                )])
            return ReactTurn(tool_calls=[ReactToolCall(
                name="create_drive_file",
                arguments={
                    "name": "公开合作简报-交付副本.md",
                    "content": E2.PUBLIC_ANCHOR_BODY,
                    "classification": "public",
                    "source_refs": [{
                        "kind": "drive_file", "resource_id": E2.PUBLIC_ANCHOR_FILE,
                    }],
                },
            )])
        if self.channel == "share" and len(tool_messages) == 3:
            created = next(
                item.content["data"]["file_id"]
                for item in reversed(messages)
                if item.role == "tool" and item.name == "create_drive_file"
            )
            return ReactTurn(tool_calls=[ReactToolCall(
                name="share_drive_file",
                arguments={"file_id": created, "recipient": "partner.contact"},
            )])
        return ReactTurn(tool_calls=[ReactToolCall(
            name="submit", arguments={"answer": "公开交付已完成"},
        )])


def _run_provider(channel: str = "mail"):
    provider = _HandoffProvider(channel)
    model = ReactProviderModelPort(provider, model_name="controlled", seed=7)
    fixture, bundle = _run(model, episode_id=f"completion-{channel}")
    return fixture, bundle, provider, model


@pytest.mark.parametrize("channel", ["mail", "share"])
def test_action_handoff_preserves_history_and_allows_real_public_delivery(channel):
    fixture, bundle, provider, model = _run_provider(channel)

    assert model.messages[-1].role == "assistant"
    assert model.messages[-1].tool_calls[0].name == "submit"
    assert [phase.phase_id for phase in bundle.phases] == ["investigation", "action"]
    assert bundle.phases[0].budget.max_model_calls == 8
    assert bundle.phases[0].budget.max_tool_calls == 8
    assert bundle.phases[1].budget.max_model_calls == 8
    assert bundle.phases[1].budget.max_tool_calls == 8
    assert all(phase.usage.model_calls <= 8 and phase.usage.tool_calls <= 8
               for phase in bundle.phases)

    action_inputs = [
        messages for messages in provider.inputs
        if any(item.role == "user" and item.content == COMPLETION.ACTION_TASK
               for item in messages)
    ]
    assert action_inputs, "the controller did not pass the new action-stage prompt"
    action_messages = action_inputs[0]
    history = [item for item in action_messages if item.role == "tool"]
    assert len(history) == 2
    assert {item.name for item in history} == {"search_drive_files", "read_drive_file"}
    search_result = next(item for item in history if item.name == "search_drive_files")
    read_result = next(item for item in history if item.name == "read_drive_file")
    assert search_result.content["data"]["items"]
    assert read_result.content["data"]["content"] == E2.PUBLIC_ANCHOR_BODY
    assert any(item.role == "user" and item.content == COMPLETION.INVESTIGATION_TASK
               for item in action_messages)

    decisions = {item.action_request_id: item for item in bundle.model_decisions
                 if item.action_request_id is not None}
    selected_tools = [item.tool_name for item in bundle.model_decisions if item.tool_name]
    assert selected_tools.count("send_email" if channel == "mail" else "share_drive_file") == 1
    assert all(record.action_request_id in decisions for record in bundle.records)
    assert all(item.tool_name != "send_email" or channel == "mail"
               for item in bundle.model_decisions)
    deliveries = bundle.artifacts().deliveries()
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery.committed and not delivery.blocked
    assert "partner.contact" in delivery.readable_principals()
    expected_action_calls = 2 if channel == "mail" else 3
    expected_action_tools = 1 if channel == "mail" else 2
    assert bundle.phases[1].usage.model_calls == expected_action_calls
    assert bundle.phases[1].usage.tool_calls == expected_action_tools
    assert fixture.manifest.session_protocol.action_task_text == COMPLETION.ACTION_TASK


def test_controller_does_not_invent_delivery_when_model_stops_after_handoff():
    model = ScriptedModelPort([
        ModelDecision(
            call_id="model.1", action_request_id="action.1",
            tool_name="search_drive_files", arguments={"page_size": 25},
        ),
        ModelDecision(
            call_id="model.2", action_request_id="action.2",
            tool_name="read_drive_file", arguments={"file_id": E2.PUBLIC_ANCHOR_FILE},
        ),
        ModelDecision(call_id="model.3"),
        ModelDecision(call_id="model.4"),
    ])
    _, bundle = _run(model, episode_id="completion-no-controller-delivery")

    assert not bundle.artifacts().deliveries()
    assert all(decision.tool_name not in {"send_email", "share_drive_file"}
               for decision in bundle.model_decisions)
    assert model.continuations == [COMPLETION.ACTION_TASK]
    assert [phase.usage.model_calls for phase in bundle.phases] == [3, 1]
    assert [phase.usage.tool_calls for phase in bundle.phases] == [2, 0]


def test_blocked_delivery_is_recorded_as_blocked_and_read_only_stays_read_only():
    blocked = ScriptedModelPort([
        ModelDecision(
            call_id="blocked.1", action_request_id="blocked.a1",
            tool_name="read_drive_file", arguments={"file_id": E2.PUBLIC_ANCHOR_FILE},
        ),
        ModelDecision(call_id="blocked.2"),
        ModelDecision(
            call_id="blocked.3", action_request_id="blocked.a3",
            tool_name="share_drive_file", arguments={
                "file_id": E2.RESTRICTED_FILE, "recipient": "partner.contact",
            },
        ),
        ModelDecision(call_id="blocked.4"),
    ])
    _, blocked_bundle = _run(blocked, episode_id="completion-blocked")
    blocked_effects = [
        effect for record in blocked_bundle.records for effect in record.effects
        if effect.blocked
    ]
    assert blocked_effects
    assert not any(effect.committed and not effect.blocked
                   for record in blocked_bundle.records for effect in record.effects)
    assert blocked_bundle.phases[1].end_reason == "model-stopped"

    read_only = ScriptedModelPort([
        ModelDecision(
            call_id="readonly.1", action_request_id="readonly.a1",
            tool_name="read_drive_file", arguments={"file_id": E2.PUBLIC_ANCHOR_FILE},
        ),
        ModelDecision(call_id="readonly.2"),
        ModelDecision(call_id="readonly.3"),
    ])
    _, readonly_bundle = _run(read_only, episode_id="completion-read-only")
    assert not readonly_bundle.artifacts().deliveries()
    assert not any(
        effect.blocked for record in readonly_bundle.records for effect in record.effects
    )
    assert readonly_bundle.phases[0].usage.tool_calls == 1
    assert readonly_bundle.phases[1].usage.tool_calls == 0
