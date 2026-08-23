from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.adapter.deepseek_harness_adapter import DeepSeekHarnessAdapter
from app.replay.replay_adapter import ReplayAdapter

from sandbox.config import TraceConfig, WeekOneConfig
from sandbox.models import RecordingOptions
from sandbox.models import TestCase as ReplayTestCase
from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.canonical import canonical_json_bytes
from sandbox.replay.exceptions import ArtifactIntegrityError
from sandbox.replay.manifest import ManifestStore, seal_manifest
from sandbox.replay.models import (
    CheckpointKind,
    CheckpointStateEnvelope,
    ForkInjection,
    RecordedForkInjection,
    ReplayForkRequest,
    ReplayMode,
    ReplayRequest,
    StateCheckpoint,
)
from sandbox.replay.normalizer import normalize_behavior_trace
from sandbox.replay.replay_engine import ReplayEngine
from tests.harness_support import harness_compound_request, harness_request


def test_harness_incomplete_recording_preserves_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_out = tmp_path / "incomplete-out"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(replay_out))
    request = harness_request().model_copy(
        update={"recording": RecordingOptions(enabled=True)}
    )
    episode_dir = tmp_path / "episode"
    episode_dir.mkdir()
    (episode_dir / "driver-progress.json").write_text(
        json.dumps(
            {
                "schema_version": "deepseek-harness-h5-progress-v1",
                "execution_id": request.execution_id,
                "status": "cancelled",
                "activity_count": 1,
                "decision_count": 3,
                "token_usage": {"prompt_tokens": 72, "completion_tokens": 30},
            }
        ),
        encoding="utf-8",
    )
    adapter = DeepSeekHarnessAdapter()
    adapter.last_bridge_summary = {"reason": "terminated", "record_count": 2}
    adapter._write_incomplete_recording(request, episode_dir)

    determinism = json.loads((replay_out / "determinism-config.json").read_bytes())
    audit = [
        json.loads(line)
        for line in (replay_out / "recording-audit.jsonl").read_bytes().splitlines()
    ]
    incomplete = next(
        event
        for event in audit
        if event["event_type"] == "harness_execution_incomplete"
    )
    assert determinism["recording_complete"] is False
    assert determinism["incomplete_reason"] == "harness_execution_incomplete"
    assert incomplete["token_usage"] == {
        "prompt_tokens": 72,
        "completion_tokens": 30,
    }
    assert incomplete["bridge_record_count"] == 2


@pytest.mark.asyncio
async def test_harness_recording_strict_replay_preserves_idle_followup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_out = tmp_path / "replay-out"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(replay_out))
    request = harness_request("clean.t9.apollo", trusted_reply=True).model_copy(
        update={"recording": RecordingOptions(enabled=True), "seed": 42}
    )
    adapter = DeepSeekHarnessAdapter()
    source_events = tuple([event async for event in adapter.execute(request)])

    determinism = json.loads((replay_out / "determinism-config.json").read_bytes())
    decisions = [
        json.loads(line)
        for line in (replay_out / "model-decisions.jsonl").read_bytes().splitlines()
    ]
    checkpoints = [
        StateCheckpoint.model_validate_json(line)
        for line in (replay_out / "checkpoints.jsonl").read_bytes().splitlines()
    ]
    idle_index = next(
        index
        for index, decision in enumerate(decisions)
        if decision["action"].get("assistant_text")
        == "Waiting for the authenticated task-session response."
    )
    idle_after = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.checkpoint_id == decisions[idle_index]["after_checkpoint_id"]
    )
    next_before = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.checkpoint_id == decisions[idle_index + 1]["before_checkpoint_id"]
    )
    idle_state = CheckpointStateEnvelope.model_validate_json(
        (replay_out / idle_after.state_artifact.relative_path).read_bytes()
    )
    resumed_state = CheckpointStateEnvelope.model_validate_json(
        (replay_out / next_before.state_artifact.relative_path).read_bytes()
    )

    assert determinism["producer_runtime_kind"] == "deepseek_harness"
    assert determinism["producer_runtime_version"] == adapter.version
    assert determinism["producer_runtime_composition_digest"].startswith("sha256:")
    assert idle_state.agent_state["messages"][-1]["role"] == "assistant"
    assert resumed_state.agent_state["messages"][-2]["role"] == "assistant"
    assert resumed_state.agent_state["messages"][-1]["role"] == "user"
    assert adapter.last_v2_recording_state is not None
    assert adapter.last_v2_oracle_artifact is not None

    artifacts = ArtifactStore(tmp_path / "artifacts")
    engine = ReplayEngine(
        WeekOneConfig(tracing=TraceConfig(output_dir=tmp_path / "trajectories")),
        None,
        None,
        None,
        ManifestStore(tmp_path / "replays"),
        artifacts,
        None,
    )
    downloaded = {
        path.relative_to(replay_out).as_posix(): path.read_bytes()
        for path in replay_out.rglob("*")
        if path.is_file()
    }
    manifest = engine._build_manifest(
        replay_id="replay-harness-h5",
        case=ReplayTestCase(
            case_id=request.case_id,
            prompt=request.prompt,
            scenario_id=request.scenario_id,
            seed=request.seed,
            metadata=request.metadata,
        ),
        image_ref="trace-g-deepseek-harness:h5-test",
        image_digest="sha256:" + "2" * 64,
        events=list(source_events),
        downloaded=downloaded,
    )
    replay_in = tmp_path / "replay-in"
    (replay_in / "artifacts").parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(artifacts.root, replay_in / "artifacts")
    (replay_in / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    replay = ReplayAdapter(replay_in)
    replay_events = tuple(
        [
            event
            async for event in replay.execute(
                ReplayRequest(
                    execution_id="episode.h5.strict-replay",
                    replay_run_id="run-h5-strict",
                    source_replay_id=manifest.replay_id,
                    mode=ReplayMode.STRICT,
                    manifest_relative_path="manifest.json",
                )
            )
        ]
    )

    assert normalize_behavior_trace(source_events) == normalize_behavior_trace(
        replay_events
    )
    assert replay.last_final_state_digest == checkpoints[-1].state_digest
    assert replay.last_checkpoint_digests
    assert len(replay.last_checkpoint_digests) == len(checkpoints)

    tampered = seal_manifest(
        manifest.model_copy(
            update={
                "manifest_digest": None,
                "metadata": {
                    **manifest.metadata,
                    "producer_runtime_kind": "langgraph",
                },
            }
        )
    )
    (replay_in / "manifest.json").write_bytes(canonical_json_bytes(tampered))
    with pytest.raises(ArtifactIntegrityError, match="producer identities differ"):
        ReplayAdapter(replay_in).load(
            ReplayRequest(
                execution_id="episode.h5.tampered",
                replay_run_id="run-h5-tampered",
                source_replay_id=tampered.replay_id,
                mode=ReplayMode.STRICT,
                manifest_relative_path="manifest.json",
            )
        )


@pytest.mark.asyncio
async def test_harness_parent_uses_langgraph_verification_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_out = tmp_path / "parent-out"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(parent_out))
    request = harness_compound_request("full").model_copy(
        update={"recording": RecordingOptions(enabled=True), "seed": 42}
    )
    parent_adapter = DeepSeekHarnessAdapter()
    parent_events = tuple([event async for event in parent_adapter.execute(request)])
    artifacts = ArtifactStore(tmp_path / "artifacts")
    engine = ReplayEngine(
        WeekOneConfig(tracing=TraceConfig(output_dir=tmp_path / "trajectories")),
        None,
        None,
        None,
        ManifestStore(tmp_path / "replays"),
        artifacts,
        None,
    )
    parent_downloaded = {
        path.relative_to(parent_out).as_posix(): path.read_bytes()
        for path in parent_out.rglob("*")
        if path.is_file()
    }
    parent = engine._build_manifest(
        replay_id="replay-harness-h5-parent",
        case=ReplayTestCase(
            case_id=request.case_id,
            prompt=request.prompt,
            scenario_id=request.scenario_id,
            seed=request.seed,
            metadata=request.metadata,
        ),
        image_ref="trace-g-deepseek-harness:h5-test",
        image_digest="sha256:" + "3" * 64,
        events=list(parent_events),
        downloaded=parent_downloaded,
    )
    parent_checkpoints = [
        StateCheckpoint.model_validate_json(line)
        for line in artifacts.read_bytes(parent.checkpoints).splitlines()
    ]
    checkpoint = next(
        item
        for item in parent_checkpoints
        if item.kind is CheckpointKind.BEFORE_MODEL
        and "carrier_payload_replace" in item.allowed_injection_types
    )
    replay_in = tmp_path / "fork-in"
    shutil.copytree(artifacts.root, replay_in / "artifacts")
    (replay_in / "manifest.json").write_bytes(canonical_json_bytes(parent))
    immutable_parent = {
        path.relative_to(replay_in).as_posix(): path.read_bytes()
        for path in replay_in.rglob("*")
        if path.is_file()
    }
    replacement = "Controlled replacement content for the isolated Office fixture."
    injection = ForkInjection(type="carrier_payload_replace", content=replacement)
    fork_request = ReplayForkRequest(
        execution_id="episode.h5.fork",
        child_replay_id="replay-harness-h5-child",
        manifest_relative_path="manifest.json",
        checkpoint_id=checkpoint.checkpoint_id,
        injection=injection,
    )
    child_out = tmp_path / "child-out"
    monkeypatch.setenv("REPLAY_OUTPUT_DIR", str(child_out))
    fork_adapter = ReplayAdapter(replay_in)
    child_events = tuple([event async for event in fork_adapter.execute_fork(fork_request)])

    assert immutable_parent == {
        path.relative_to(replay_in).as_posix(): path.read_bytes()
        for path in replay_in.rglob("*")
        if path.is_file()
    }
    child_determinism = json.loads((child_out / "determinism-config.json").read_bytes())
    assert child_determinism["producer_runtime_kind"] == "langgraph"
    assert child_determinism["metadata"]["verification_only"] is True
    assert fork_adapter.last_final_state_digest is not None

    child_downloaded = {
        path.relative_to(child_out).as_posix(): path.read_bytes()
        for path in child_out.rglob("*")
        if path.is_file()
    }
    child_execution = child_determinism["office_v2_execution"]
    child_case = child_execution["scenario_case_payload"]
    recorded_injection = RecordedForkInjection(
        **injection.model_dump(mode="json"),
        content_digest="sha256:"
        + hashlib.sha256(replacement.encode("utf-8")).hexdigest(),
        operator="h5-test",
        created_at=datetime.now(UTC),
    )
    child = engine._build_manifest(
        replay_id=fork_request.child_replay_id,
        case=ReplayTestCase(
            case_id=child_case["case_id"],
            prompt=child_case["task"]["instruction"],
            scenario_id=child_execution["scenario_id"],
            seed=child_case["seed"],
            metadata=child_determinism["metadata"],
        ),
        image_ref=parent.image_ref,
        image_digest=parent.image_digest,
        events=list(child_events),
        downloaded=child_downloaded,
        parent_manifest=parent,
        fork_checkpoint=checkpoint,
        recorded_injection=recorded_injection,
    )

    assert child.metadata["parent_prefix_producer_runtime_kind"] == "deepseek_harness"
    assert child.metadata["producer_runtime_kind"] == "langgraph"
    assert child.metadata["fork_engine_kind"] == "langgraph_live_and_record"
    assert child.metadata["mixed_runtime_lineage"] is True
