from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

SCRIPT = Path("scripts/stage_server_results.py").resolve()
SPEC = importlib.util.spec_from_file_location("stage_server_results", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _trajectory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"event_type":"execution_finished"}' + chr(10), encoding="utf-8")


def test_stages_only_referenced_campaign_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    campaign_id = "real-one"
    result = tmp_path / "reports" / "server-real-model" / campaign_id
    trajectories = tmp_path / "data" / "trajectories"
    for trajectory_id in ("agent", "campaign", "record", "fork", "unrelated"):
        _trajectory(trajectories / f"{trajectory_id}.jsonl")

    artifact_payload = b"recorded prompt"
    artifact_digest = hashlib.sha256(artifact_payload).hexdigest()
    artifact_relative = (
        Path("sha256") / artifact_digest[:2] / artifact_digest[2:4] / artifact_digest
    )
    artifact = tmp_path / "data" / "artifacts" / artifact_relative
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(artifact_payload)
    reference = {
        "relative_path": artifact_relative.as_posix(),
        "sha256": f"sha256:{artifact_digest}",
        "size_bytes": len(artifact_payload),
    }
    replay_root = tmp_path / "data" / "replays"
    _json(
        replay_root / "record-replay" / "manifest.json",
        {
            "replay_id": "record-replay",
            "trajectory_id": "record",
            "prompt": reference,
        },
    )
    _json(
        replay_root / "fork-replay" / "manifest.json",
        {
            "replay_id": "fork-replay",
            "trajectory_id": "fork",
            "parent_replay_id": "record-replay",
        },
    )
    (replay_root / "record-replay" / "manifest.sha256").write_text("digest" + chr(10))
    (replay_root / "fork-replay" / "manifest.sha256").write_text("digest" + chr(10))

    _json(
        result / "campaign-export.json",
        {
            "manifest": {"campaign_id": campaign_id},
            "work_items": [
                {
                    "trajectory_path": "data/trajectories/campaign.jsonl",
                    "trajectory_id": "campaign",
                }
            ],
            "work_attempts": [],
        },
    )
    _json(
        result / "agent-run.json",
        {"trajectory_path": "data/trajectories/agent.jsonl"},
    )
    _json(
        result / "weeks-1-5" / "week2-record.json",
        {"replay_id": "record-replay"},
    )
    _json(
        result / "weeks-1-5" / "week2-fork.json",
        {"replay_id": "fork-replay"},
    )
    for kind, filename in (
        ("fuzzing", "fuzzer.db"),
        ("coverage", "coverage.db"),
        ("mutations", "mutation.db"),
    ):
        database = tmp_path / "data" / kind / campaign_id / filename
        database.parent.mkdir(parents=True)
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE sample(value TEXT)")
            connection.execute("INSERT INTO sample VALUES ('ok')")

    output = result / "raw-data"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--campaign-id",
            campaign_id,
            "--result-dir",
            str(result),
            "--output-dir",
            str(output),
        ],
    )

    assert MODULE.main() == 0
    staged_names = {path.name for path in (output / "trajectories").glob("*.jsonl")}
    assert staged_names == {
        "agent.jsonl",
        "campaign.jsonl",
        "record.jsonl",
        "fork.jsonl",
    }
    assert (output / "artifacts" / artifact_relative).read_bytes() == artifact_payload
    assert (output / "replays" / "fork-replay" / "manifest.json").is_file()
    for name in ("fuzzer.db", "coverage.db", "mutation.db"):
        with sqlite3.connect(output / "databases" / name) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
