from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.stage_trace_workspace_results import stage_trace_workspace_results


def _write_json(path: Path, value: object) -> bytes:
    raw = json.dumps(value).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _reference(path: Path, artifact_root: Path, *, media_type: str) -> dict:
    raw = path.read_bytes()
    return {
        "media_type": media_type,
        "relative_path": path.relative_to(artifact_root).as_posix(),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def test_stager_copies_only_referenced_workspace_evidence(tmp_path: Path) -> None:
    result_dir = tmp_path / "reports" / "server-real-model" / "case" / "trace-workspace"
    trajectories = tmp_path / "data" / "trajectories"
    artifacts = tmp_path / "data" / "artifacts"
    replays = tmp_path / "data" / "replays"
    for name in ("clean", "injected", "unrelated"):
        path = trajectories / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'{{"name":"{name}"}}\n', encoding="utf-8")
    for name in ("clean", "injected"):
        _write_json(
            result_dir / f"{name}-run.json",
            {"trajectory_path": str((trajectories / f"{name}.jsonl").relative_to(tmp_path))},
        )

    state = artifacts / "sha256" / "state.json"
    _write_json(state, {"state": "recorded"})
    checkpoints = artifacts / "sha256" / "checkpoints.jsonl"
    state_ref = _reference(state, artifacts, media_type="application/json")
    checkpoints_raw = json.dumps({"state_artifact": state_ref}).encode() + b"\n"
    checkpoints.parent.mkdir(parents=True, exist_ok=True)
    checkpoints.write_bytes(checkpoints_raw)
    events = artifacts / "sha256" / "events.jsonl"
    events.write_text('{"event_type":"execution_finished"}\n', encoding="utf-8")
    unrelated = artifacts / "sha256" / "unrelated.json"
    _write_json(unrelated, {"unrelated": True})
    events_ref = _reference(events, artifacts, media_type="application/x-ndjson")
    checkpoint_ref = _reference(
        checkpoints, artifacts, media_type="application/x-ndjson"
    )

    replay_dir = replays / "replay-one"
    manifest = {
        "replay_id": "replay-one",
        "events": events_ref,
        "checkpoints": checkpoint_ref,
    }
    _write_json(replay_dir / "manifest.json", manifest)
    (replay_dir / "manifest.sha256").write_text("sha256:manifest\n", encoding="ascii")
    run_dir = replay_dir / "runs" / "run-one"
    run_dir.mkdir(parents=True)
    (run_dir / "trajectory.jsonl").write_text(
        '{"event_type":"execution_finished"}\n', encoding="utf-8"
    )
    _write_json(run_dir / "result.json", {"status": "matched"})

    _write_json(
        result_dir / "validation.json",
        {"passed": True, "replay_id": "replay-one", "replay_run_id": "run-one"},
    )
    _write_json(
        result_dir / "injected-record.json",
        {"replay_id": "replay-one", "events": events_ref},
    )
    _write_json(
        result_dir / "injected-strict.json",
        {"replay_run_id": "run-one"},
    )

    output = result_dir / "trace-workspace-data"
    summary = stage_trace_workspace_results("case", result_dir, tmp_path, output)

    assert summary["trajectory_count"] == 4
    assert summary["artifact_count"] == 3
    assert (output / "artifacts" / "sha256" / "events.jsonl").is_file()
    assert (output / "artifacts" / "sha256" / "checkpoints.jsonl").is_file()
    assert (output / "artifacts" / "sha256" / "state.json").is_file()
    assert not (output / "artifacts" / "sha256" / "unrelated.json").exists()
    assert not (output / "trajectories" / "unrelated.jsonl").exists()
