"""Recording session and stable-boundary checkpoint persistence."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.replay.react_decision_recorder import ReactDecisionRecorder
from app.replay.state_codec import StateCodec
from app.replay.tool_recorder import ToolRecorder
from sandbox.protocol import RecordingOptions, TraceEvent
from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.digests import sha256_bytes
from sandbox.replay.models import (
    DEFAULT_INJECTIONS,
    ArtifactRef,
    CheckpointKind,
    ResumePhase,
    StateCheckpoint,
)


class RecordingSession:
    def __init__(
        self,
        request,
        model,
        tools,
        output_dir: Path | None = None,
        *,
        start_node: str = "agent",
        model_recorder_factory=None,
        runtime_id: str = "trace-react-v2",
        system_prompt_version: str | None = None,
        system_prompt_digest: str | None = None,
        state_codec=None,
        producer_runtime_kind: str | None = None,
        producer_runtime_version: str | None = None,
        producer_runtime_composition_digest: str | None = None,
    ) -> None:
        if (system_prompt_version is None) != (system_prompt_digest is None):
            raise ValueError(
                "system prompt version and digest must be provided together"
            )
        producer_identity = (
            producer_runtime_kind,
            producer_runtime_version,
            producer_runtime_composition_digest,
        )
        if any(value is not None for value in producer_identity) and not all(
            isinstance(value, str) and value for value in producer_identity
        ):
            raise ValueError("producer runtime identity must be provided as one complete tuple")
        options = request.recording or RecordingOptions(enabled=True)
        self.request = request
        self.output_dir = output_dir or Path(
            os.environ.get("REPLAY_OUTPUT_DIR", "/workspace/replay-out")
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.codec = state_codec or StateCodec()
        recorder_factory = model_recorder_factory or ReactDecisionRecorder
        self.model = recorder_factory(model)
        self.tools = ToolRecorder(tools, replay_mode=options.default_tool_replay_mode)
        self.checkpoints: list[StateCheckpoint] = []
        self.audit_events: list[dict[str, Any]] = []
        self.initial_state_bytes: bytes | None = None
        self.start_node = start_node
        self.runtime_id = runtime_id
        self.system_prompt_version = system_prompt_version
        self.system_prompt_digest = system_prompt_digest
        self.producer_runtime_kind = producer_runtime_kind
        self.producer_runtime_version = producer_runtime_version
        self.producer_runtime_composition_digest = (
            producer_runtime_composition_digest
        )
        if producer_runtime_kind is not None:
            self.audit_events.append(
                {
                    "event_type": "producer_runtime_bound",
                    "producer_runtime_kind": producer_runtime_kind,
                    "producer_runtime_version": producer_runtime_version,
                    "producer_runtime_composition_digest": (
                        producer_runtime_composition_digest
                    ),
                }
            )

    def start(self, state: dict[str, Any]) -> None:
        checkpoint = self._capture(
            state,
            kind=CheckpointKind.NODE_COMMIT,
            resume_phase=ResumePhase.ENTER_NEXT_NODE,
            sequence=0,
            node_name="start",
        )
        self.initial_state_bytes = self._state_bytes(checkpoint)

    def before_model(self, state: dict[str, Any], sequence: int) -> StateCheckpoint:
        checkpoint = self._capture(
            state,
            kind=CheckpointKind.BEFORE_MODEL,
            resume_phase=ResumePhase.CALL_MODEL,
            sequence=sequence,
            node_name="agent",
        )
        self.model.set_context(sequence=sequence, before_checkpoint_id=checkpoint.checkpoint_id)
        return checkpoint

    def after_model(self, state: dict[str, Any], sequence: int) -> StateCheckpoint:
        checkpoint = self._capture(
            state,
            kind=CheckpointKind.AFTER_MODEL,
            resume_phase=ResumePhase.APPLY_MODEL_DECISION,
            sequence=sequence,
            node_name="agent",
        )
        self.model.attach_after_checkpoint(checkpoint.checkpoint_id)
        return checkpoint

    def before_tool(self, state: dict[str, Any], sequence: int) -> StateCheckpoint:
        checkpoint = self._capture(
            state,
            kind=CheckpointKind.BEFORE_TOOL,
            resume_phase=ResumePhase.CALL_TOOL,
            sequence=sequence,
            node_name="tool",
        )
        self.tools.set_context(sequence=sequence, before_checkpoint_id=checkpoint.checkpoint_id)
        return checkpoint

    def after_tool(self, state: dict[str, Any], sequence: int) -> StateCheckpoint:
        checkpoint = self._capture(
            state,
            kind=CheckpointKind.AFTER_TOOL,
            resume_phase=ResumePhase.APPLY_TOOL_RESULT,
            sequence=sequence,
            node_name="tool",
        )
        self.tools.attach_after_checkpoint(checkpoint.checkpoint_id)
        return checkpoint

    def finalize(
        self,
        state: dict[str, Any],
        events: list[TraceEvent],
        *,
        extra_artifacts: dict[str, Any] | None = None,
    ) -> None:
        self._capture(
            state,
            kind=CheckpointKind.NODE_COMMIT,
            resume_phase=ResumePhase.ENTER_NEXT_NODE,
            sequence=max(0, len(events) - 1),
            node_name="finalize",
        )
        self._write_artifacts(
            events,
            complete=True,
            incomplete_reason=None,
            extra_artifacts=extra_artifacts,
        )

    def finalize_incomplete(
        self,
        events: list[TraceEvent],
        *,
        reason: str,
    ) -> None:
        self.audit_events.append(
            {
                "event_type": "recording_incomplete",
                "reason": reason,
                "truncated_artifacts": ["events.jsonl", "checkpoints.jsonl"],
            }
        )
        self.checkpoints = [
            checkpoint.model_copy(
                update={
                    "recoverable": False,
                    "non_recoverable_reasons": [f"recording incomplete: {reason}"],
                }
            )
            for checkpoint in self.checkpoints
        ]
        self._write_artifacts(
            events,
            complete=False,
            incomplete_reason=reason,
            extra_artifacts=None,
        )

    def _write_artifacts(
        self,
        events: list[TraceEvent],
        *,
        complete: bool,
        incomplete_reason: str | None,
        extra_artifacts: dict[str, Any] | None,
    ) -> None:
        if self.initial_state_bytes is None:
            raise RuntimeError("recording session was not started")
        self._write("prompt.json", canonical_json_bytes({"prompt": self.request.prompt}))
        self._write("initial-state.json", self.initial_state_bytes)
        self._write(
            "determinism-config.json",
            canonical_json_bytes(
                {
                    "seed": self.request.seed,
                    "max_steps": self.request.max_steps,
                    "timeout_seconds": self.request.timeout_seconds,
                    "start_node": self.start_node,
                    "execution_backend": self.request.execution_backend.value,
                    "system_prompt_version": self.system_prompt_version,
                    "system_prompt_digest": self.system_prompt_digest,
                    "state_codec_version": self.codec.version,
                    "producer_runtime_kind": self.producer_runtime_kind,
                    "producer_runtime_version": self.producer_runtime_version,
                    "producer_runtime_composition_digest": (
                        self.producer_runtime_composition_digest
                    ),
                    "metadata": self.request.metadata,
                    "recording_complete": complete,
                    "incomplete_reason": incomplete_reason,
                    "model": (
                        self.request.model.model_dump(mode="json")
                        if self.request.model is not None
                        else None
                    ),
                    "office_v2_execution": (
                        self.request.office_v2_execution.model_dump(
                            mode="json", exclude_none=False
                        )
                        if self.request.office_v2_execution is not None
                        else None
                    ),
                }
            ),
        )
        self._write_jsonl("events.jsonl", [event.model_dump(mode="json") for event in events])
        self._write_jsonl(
            "model-decisions.jsonl",
            [decision.model_dump(mode="json") for decision in self.model.decisions],
        )
        self._write_jsonl(
            "tool-records.jsonl",
            [record.model_dump(mode="json") for record in self.tools.interactions],
        )
        self._write_jsonl(
            "checkpoints.jsonl",
            [checkpoint.model_dump(mode="json") for checkpoint in self.checkpoints],
        )
        self._write_jsonl("recording-audit.jsonl", self.audit_events)
        for relative_path, value in (extra_artifacts or {}).items():
            self._write(relative_path, canonical_json_bytes(value))

    def record_external_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        side_effect_digest_before: str,
        side_effect_digest_after: str,
        policy_decision: str,
    ) -> None:
        self.tools.record_external(
            {"name": name, "arguments": arguments},
            result,
            side_effect_digest_before=side_effect_digest_before,
            side_effect_digest_after=side_effect_digest_after,
            policy_decision=policy_decision,
        )

    def _capture(
        self,
        state: dict[str, Any],
        *,
        kind: CheckpointKind,
        resume_phase: ResumePhase,
        sequence: int,
        node_name: str,
    ) -> StateCheckpoint:
        envelope = self.codec.export(
            state,
            self.tools,
            checkpoint_kind=kind,
            resume_phase=resume_phase,
            logical_time=len(self.checkpoints),
            next_model_decision_index=len(self.model.decisions),
            next_tool_interaction_index=len(self.tools.interactions),
            runtime_id=self.runtime_id,
        )
        payload = canonical_json_bytes(envelope)
        digest = sha256_bytes(payload)
        hex_digest = digest.removeprefix("sha256:")
        relative_path = f"states/{hex_digest}.json"
        self._write(relative_path, payload)
        allowed_injection_types = list(DEFAULT_INJECTIONS[kind])
        if self.runtime_id == "trace-react-v2" and kind not in {
            CheckpointKind.BEFORE_MODEL,
            CheckpointKind.AFTER_TOOL,
            CheckpointKind.NODE_COMMIT,
        }:
            allowed_injection_types = []
        if self.runtime_id == "trace-react-v2-office-v2":
            allowed_injection_types = []
            scenario_state = envelope.scenario_state or {}
            session_state = scenario_state.get("session", {})
            if (
                kind is CheckpointKind.BEFORE_MODEL
                and envelope.next_model_decision_index == 0
                and envelope.next_tool_interaction_index == 0
                and not scenario_state.get("tool_invocations")
                and not scenario_state.get("tool_results")
                and not scenario_state.get("interaction_events")
                and not session_state.get("history")
            ):
                allowed_injection_types = ["carrier_payload_replace"]
        if (
            self.runtime_id == "trace-react-v2"
            and self.tools.office is not None
            and kind
            in {
                CheckpointKind.BEFORE_MODEL,
                CheckpointKind.AFTER_TOOL,
                CheckpointKind.NODE_COMMIT,
            }
        ):
            allowed_injection_types.append("carrier_payload_replace")
        checkpoint = StateCheckpoint(
            checkpoint_id=f"checkpoint-{uuid4().hex}",
            execution_id=self.request.execution_id,
            sequence=max(0, sequence),
            logical_time=len(self.checkpoints),
            kind=kind,
            node_name=node_name,
            resume_phase=resume_phase,
            resume_sequence=max(0, sequence + 1),
            state_codec_version=envelope.state_codec_version,
            state_digest=digest,
            state_artifact=ArtifactRef(
                media_type="application/json",
                sha256=digest,
                size_bytes=len(payload),
                relative_path=relative_path,
            ),
            next_model_decision_index=len(self.model.decisions),
            next_tool_interaction_index=len(self.tools.interactions),
            allowed_injection_types=allowed_injection_types,
        )
        self.checkpoints.append(checkpoint)
        self.audit_events.append(
            {
                "event_type": "checkpoint_created",
                "checkpoint_id": checkpoint.checkpoint_id,
                "kind": kind.value,
                "state_digest": digest,
            }
        )
        return checkpoint

    def _state_bytes(self, checkpoint: StateCheckpoint) -> bytes:
        if checkpoint.state_artifact is None:
            raise RuntimeError("checkpoint has no state artifact")
        return (self.output_dir / checkpoint.state_artifact.relative_path).read_bytes()

    def _write_jsonl(self, relative_path: str, records: list[dict[str, Any]]) -> None:
        payload = b"".join(canonical_json_bytes(record) + b"\n" for record in records)
        self._write(relative_path, payload)

    def _write(self, relative_path: str, payload: bytes) -> None:
        destination = self.output_dir / Path(*relative_path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        temporary.write_bytes(payload)
        os.replace(temporary, destination)
