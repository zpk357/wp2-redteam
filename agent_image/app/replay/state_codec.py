"""Explicit JSON state export/import; pickle is intentionally unsupported."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sandbox.replay.models import CheckpointKind, CheckpointStateEnvelope, ResumePhase

AGENT_STATE_FIELDS = {
    "prompt",
    "step_count",
    "max_steps",
    "final_answer",
    "messages",
    "seen_call_ids",
    "submitted",
    "turn",
}


class StateCodec:
    version = "2.0"

    def export(
        self,
        state: dict[str, Any],
        tools,
        *,
        checkpoint_kind: CheckpointKind,
        resume_phase: ResumePhase,
        logical_time: int,
        next_model_decision_index: int,
        next_tool_interaction_index: int,
        runtime_id: str = "trace-react-v2",
    ) -> CheckpointStateEnvelope:
        tool_state = tools.export_state()
        return CheckpointStateEnvelope(
            checkpoint_kind=checkpoint_kind,
            resume_phase=resume_phase,
            logical_time=logical_time,
            next_model_decision_index=next_model_decision_index,
            next_tool_interaction_index=next_tool_interaction_index,
            agent_state={
                key: value
                for key, value in state.items()
                if key in AGENT_STATE_FIELDS
            },
            virtual_filesystem_state=tool_state["virtual_filesystem_state"],
            fake_shell_state=tool_state["fake_shell_state"],
            mock_api_state=tool_state["mock_api_state"],
            enterprise_tool_state=tool_state["enterprise_tool_state"],
            rng_states={},
            environment={"agent_runtime": runtime_id},
        )

    def restore(
        self,
        envelope: CheckpointStateEnvelope,
        tools,
        *,
        execution_id: str,
    ) -> dict[str, Any]:
        if envelope.scenario_state_codec is not None:
            raise ValueError("legacy state codec cannot restore versioned scenario state")
        if envelope.state_codec_version != self.version:
            raise ValueError("state codec version is incompatible")
        tool_state = {
            "virtual_filesystem_state": envelope.virtual_filesystem_state,
            "fake_shell_state": envelope.fake_shell_state,
            "mock_api_state": envelope.mock_api_state,
        }
        tool_state["enterprise_tool_state"] = envelope.enterprise_tool_state
        tools.import_state(tool_state)
        return {**envelope.agent_state, "execution_id": execution_id}


class OfficeV2StateCodec(StateCodec):
    version = "office-v2-state-codec-v1"

    def __init__(self, scenario_state_exporter: Callable[[], Any]) -> None:
        self._scenario_state_exporter = scenario_state_exporter

    def export(
        self,
        state: dict[str, Any],
        tools,
        *,
        checkpoint_kind: CheckpointKind,
        resume_phase: ResumePhase,
        logical_time: int,
        next_model_decision_index: int,
        next_tool_interaction_index: int,
        runtime_id: str = "trace-react-v2-office-v2",
    ) -> CheckpointStateEnvelope:
        if runtime_id != "trace-react-v2-office-v2":
            raise ValueError("Office V2 codec requires the Office V2 runtime identity")
        scenario_state = self._scenario_state_exporter()
        if hasattr(scenario_state, "model_dump"):
            scenario_state = scenario_state.model_dump(mode="json", exclude_none=False)
        return CheckpointStateEnvelope(
            state_codec_version=self.version,
            checkpoint_kind=checkpoint_kind,
            resume_phase=resume_phase,
            logical_time=logical_time,
            next_model_decision_index=next_model_decision_index,
            next_tool_interaction_index=next_tool_interaction_index,
            agent_state={
                key: value
                for key, value in state.items()
                if key in AGENT_STATE_FIELDS
            },
            virtual_filesystem_state={},
            fake_shell_state={},
            mock_api_state={},
            enterprise_tool_state={},
            scenario_state_codec=self.version,
            scenario_state=scenario_state,
            rng_states={},
            environment={"agent_runtime": runtime_id},
        )

    def restore(self, envelope, tools, *, execution_id: str) -> dict[str, Any]:
        del tools
        if (
            envelope.state_codec_version != self.version
            or envelope.scenario_state_codec != self.version
            or envelope.scenario_state is None
        ):
            raise ValueError("Office V2 state codec is incompatible")
        return {**envelope.agent_state, "execution_id": execution_id}
