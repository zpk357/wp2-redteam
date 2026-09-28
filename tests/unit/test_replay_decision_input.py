"""Rebuilding a decision's exact input from a recording (VE-03 / VE-04).

The read side must hand back the recorded messages when they are intact and say
exactly what is missing when they are not: no checkpoint, no artifact, an
unreadable artifact, no message list, or a digest that does not match. Nothing
here needs Docker or a real model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sandbox.protocol import ToolReplayMode
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.decision_input import (
    load_decision_input,
    load_decision_inputs,
)
from sandbox.replay.digests import sha256_digest
from sandbox.replay.models import (
    CheckpointKind,
    CheckpointStateEnvelope,
    RecordedModelDecision,
    ReplayManifest,
    ResumePhase,
    StateCheckpoint,
)

DIGEST = "sha256:" + "0" * 64
DEFAULT_MESSAGES = [
    {"role": "system", "content": "system prompt"},
    {"role": "user", "content": "task"},
]


class Recording:
    def __init__(self, tmp_path: Path, *, messages=None, digest=None) -> None:
        self.store = ArtifactStore(tmp_path / "artifacts")
        self.messages = messages if messages is not None else DEFAULT_MESSAGES
        envelope = CheckpointStateEnvelope(
            checkpoint_kind=CheckpointKind.BEFORE_MODEL,
            resume_phase=ResumePhase.CALL_MODEL,
            logical_time=1,
            next_model_decision_index=0,
            next_tool_interaction_index=0,
            agent_state={"prompt": "task", "messages": self.messages},
            virtual_filesystem_state={},
            fake_shell_state={},
            mock_api_state={},
            environment={"agent_runtime": "trace-react-v2"},
        )
        self.state = self.store.put_bytes(
            canonical_json_bytes(envelope), media_type="application/json"
        )
        self.checkpoint = StateCheckpoint(
            checkpoint_id="checkpoint-1",
            execution_id="execution-1",
            sequence=1,
            logical_time=1,
            kind=CheckpointKind.BEFORE_MODEL,
            resume_phase=ResumePhase.CALL_MODEL,
            resume_sequence=2,
            state_digest=self.state.sha256,
            state_artifact=self.state,
        )
        self.checkpoints = self.store.put_bytes(
            canonical_json_bytes(self.checkpoint) + b"\n",
            media_type="application/x-ndjson",
        )
        self.decision = RecordedModelDecision(
            decision_id="decision-1",
            sequence=1,
            decision_index=0,
            before_checkpoint_id="checkpoint-1",
            input_digest=digest if digest is not None else sha256_digest(self.messages),
            output_digest=DIGEST,
            action={"kind": "call"},
            model_name="test-model",
            model_version="test",
        )
        self.decisions = self.store.put_bytes(
            canonical_json_bytes(self.decision) + b"\n",
            media_type="application/x-ndjson",
        )
        placeholder = self.store.put_bytes(b"{}", media_type="application/json")
        self.manifest = ReplayManifest(
            replay_id="replay-test",
            trajectory_id="trajectory-test",
            created_at=datetime(2026, 9, 20, tzinfo=UTC),
            case_id="case-test",
            scenario_id="office-workspace-v2",
            seed=1,
            image_ref="test-image",
            runtime_version="0.2.0",
            agent_version="trace-react-v2",
            default_tool_replay_mode=ToolReplayMode.EXECUTE_AND_VERIFY,
            prompt_digest=DIGEST,
            initial_state_digest=DIGEST,
            normalized_behavior_trace_digest=DIGEST,
            determinism_config_digest=DIGEST,
            prompt=placeholder,
            events=placeholder,
            initial_state=placeholder,
            determinism_config=placeholder,
            model_decisions=self.decisions,
            tool_records=placeholder,
            checkpoints=self.checkpoints,
        )
        self.placeholder = placeholder


def test_exact_input_is_rebuilt_and_matches_the_recorded_digest(tmp_path: Path) -> None:
    recording = Recording(tmp_path)

    inputs = load_decision_inputs(recording.store, recording.manifest)

    assert len(inputs) == 1
    entry = inputs[0]
    assert entry.status == "exact"
    assert entry.exact is True
    assert entry.recomputed_digest == recording.decision.input_digest
    assert [dict(message) for message in entry.messages] == recording.messages


def test_digest_mismatch_is_reported_and_messages_are_not_claimed(
    tmp_path: Path,
) -> None:
    recording = Recording(tmp_path, digest="sha256:" + "b" * 64)

    entry = load_decision_input(recording.store, recording.manifest, recording.decision)

    assert entry.status == "digest_mismatch"
    assert entry.exact is False
    assert entry.recomputed_digest == sha256_digest(recording.messages)
    assert entry.reason is not None


def test_missing_checkpoint_is_reported(tmp_path: Path) -> None:
    recording = Recording(tmp_path)
    decision = recording.decision.model_copy(
        update={"before_checkpoint_id": "checkpoint-absent"}
    )

    entry = load_decision_input(recording.store, recording.manifest, decision)

    assert entry.status == "checkpoint_missing"
    assert "checkpoint-absent" in (entry.reason or "")


def test_non_recoverable_checkpoint_is_reported_with_its_reasons(
    tmp_path: Path,
) -> None:
    recording = Recording(tmp_path)
    broken = StateCheckpoint(
        checkpoint_id="checkpoint-1",
        execution_id="execution-1",
        sequence=1,
        logical_time=1,
        kind=CheckpointKind.BEFORE_MODEL,
        resume_phase=ResumePhase.CALL_MODEL,
        resume_sequence=2,
        recoverable=False,
        non_recoverable_reasons=["model_call_did_not_reach_recording"],
    )
    broken_ref = recording.store.put_bytes(
        canonical_json_bytes(broken) + b"\n", media_type="application/x-ndjson"
    )
    manifest = recording.manifest.model_copy(update={"checkpoints": broken_ref})

    entry = load_decision_input(recording.store, manifest, recording.decision)

    assert entry.status == "artifact_missing"
    assert "model_call_did_not_reach_recording" in (entry.reason or "")


def test_missing_artifact_file_is_reported(tmp_path: Path) -> None:
    recording = Recording(tmp_path)
    artifact_path = recording.store.root / Path(
        *recording.state.relative_path.split("/")
    )
    artifact_path.unlink()

    entry = load_decision_input(recording.store, recording.manifest, recording.decision)

    assert entry.status == "artifact_missing"
    assert entry.recomputed_digest is None


def test_state_without_a_message_list_is_reported(tmp_path: Path) -> None:
    recording = Recording(tmp_path)
    envelope = CheckpointStateEnvelope(
        checkpoint_kind=CheckpointKind.BEFORE_MODEL,
        resume_phase=ResumePhase.CALL_MODEL,
        logical_time=1,
        next_model_decision_index=0,
        next_tool_interaction_index=0,
        agent_state={"prompt": "task"},
        virtual_filesystem_state={},
        fake_shell_state={},
        mock_api_state={},
    )
    state_ref = recording.store.put_bytes(
        canonical_json_bytes(envelope), media_type="application/json"
    )
    checkpoint = recording.checkpoint.model_copy(
        update={"state_artifact": state_ref, "state_digest": state_ref.sha256}
    )
    checkpoint_ref = recording.store.put_bytes(
        canonical_json_bytes(checkpoint) + b"\n", media_type="application/x-ndjson"
    )
    manifest = recording.manifest.model_copy(update={"checkpoints": checkpoint_ref})

    entry = load_decision_input(recording.store, manifest, recording.decision)

    assert entry.status == "messages_unavailable"
    assert entry.messages == ()
