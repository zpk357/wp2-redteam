from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_g5_source_archive import allowed_source_path
from scripts.validate_g5_server_results import validate, write_integrity_manifest
from scripts.verify_g5_server_kit import G5KitError, validate_lock, verify_artifacts

ROOT = Path(__file__).resolve().parents[2]
DIGEST = "sha256:" + "a" * 64
MODEL_DIGEST = "sha256:" + "b" * 64


def _lock() -> dict:
    return {
        "schema_version": "1.0",
        "gate": "5.G5",
        "agent_image": {
            "reference": "trace-redteam-agent-qwen:g4-local",
            "image_id": DIGEST,
            "archive_sha256": "c" * 64,
            "labels": {
                "org.trace-g.runtime": "self-contained-agent-qwen",
                "org.trace-g.agent-framework": "langgraph",
                "org.trace-g.model.name": "qwen3:8b",
                "org.trace-g.model.digest": MODEL_DIGEST,
            },
        },
        "controller_image": {
            "reference": "trace-redteam-controller:server",
            "image_id": "sha256:" + "d" * 64,
            "archive_sha256": "e" * 64,
        },
        "model_name": "qwen3:8b",
        "model_digest": MODEL_DIGEST,
        "source": {"archive": "source/source.tar", "sha256": "f" * 64},
        "g4_acceptance": {"path": "evidence/g4.json", "sha256": "1" * 64},
        "forbidden_external_artifacts": [
            "ollama_image",
            "ollama_model_archive",
            "host_model_mount",
        ],
    }


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _launch(mode: str) -> dict:
    return {
        "mode": mode,
        "image_digest": DIGEST,
        "ollama_process_present": mode == "live",
        "removed": True,
        "isolation": {
            "network_mode": "none",
            "read_only_rootfs": True,
            "bind_mount_count": 0,
            "docker_socket_mounted": False,
            "gpu_device_requests": 1 if mode == "live" else 0,
        },
    }


def test_g5_lock_requires_self_contained_agent_and_external_model_bans() -> None:
    validate_lock(_lock())
    invalid = _lock()
    invalid["forbidden_external_artifacts"] = []

    with pytest.raises(G5KitError, match="forbid external model"):
        validate_lock(invalid)


def test_g5_artifact_verification_rejects_lock_file_mismatch(tmp_path: Path) -> None:
    lock = _lock()
    paths = {
        "images/agent.tar": b"agent",
        "images/controller.tar": b"controller",
        "source/source.tar": b"source",
        "evidence/g4.json": b"g4",
    }
    lock["agent_image"]["archive"] = "images/agent.tar"
    lock["controller_image"]["archive"] = "images/controller.tar"
    lock["agent_image"]["archive_sha256"] = hashlib.sha256(b"agent").hexdigest()
    lock["controller_image"]["archive_sha256"] = hashlib.sha256(b"controller").hexdigest()
    lock["source"]["sha256"] = hashlib.sha256(b"source").hexdigest()
    lock["g4_acceptance"]["sha256"] = hashlib.sha256(b"g4").hexdigest()
    for relative, raw in paths.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    assert set(verify_artifacts(lock, tmp_path)) == {
        "agent_image",
        "controller_image",
        "source",
        "g4_acceptance",
    }
    (tmp_path / "source" / "source.tar").write_bytes(b"tampered")

    with pytest.raises(G5KitError, match="source artifact digest"):
        verify_artifacts(lock, tmp_path)


def test_g5_result_requires_live_qwen_and_qwen_free_strict_replay(tmp_path: Path) -> None:
    lock = _lock()
    lock_path = tmp_path / "lock.json"
    _write(lock_path, lock)
    _write(
        tmp_path / "acceptance.json",
        {
            "gate": "5.G5",
            "image_id": DIGEST,
            "model_name": "qwen3:8b",
            "model_digest": MODEL_DIGEST,
            "parent_strict_status": "matched",
            "child_strict_status": "matched",
            "parent_immutable": True,
            "launches": [
                _launch("live"),
                _launch("strict_replay"),
                _launch("live"),
                _launch("strict_replay"),
            ],
        },
    )
    _write(
        tmp_path / "host-evidence.json",
        {
            "passed": True,
            "agent_container_residue": [],
            "workspace_volume_residue": [],
        },
    )
    write_integrity_manifest(tmp_path)

    result = validate(tmp_path, lock_path)

    assert result["passed"] is True


def test_g5_result_rejects_ollama_in_strict_replay(tmp_path: Path) -> None:
    lock = _lock()
    lock_path = tmp_path / "lock.json"
    _write(lock_path, lock)
    launches = [
        _launch("live"),
        _launch("strict_replay"),
        _launch("live"),
        _launch("strict_replay"),
    ]
    launches[1]["ollama_process_present"] = True
    _write(
        tmp_path / "acceptance.json",
        {
            "gate": "5.G5",
            "image_id": DIGEST,
            "model_name": "qwen3:8b",
            "model_digest": MODEL_DIGEST,
            "parent_strict_status": "matched",
            "child_strict_status": "matched",
            "parent_immutable": True,
            "launches": launches,
        },
    )
    _write(
        tmp_path / "host-evidence.json",
        {
            "passed": True,
            "agent_container_residue": [],
            "workspace_volume_residue": [],
        },
    )
    write_integrity_manifest(tmp_path)

    result = validate(tmp_path, lock_path)

    assert result["passed"] is False
    assert "strict_has_no_ollama" in result["failed_checks"]


def test_g5_server_scripts_do_not_restore_external_ollama_topology() -> None:
    prepare = (ROOT / "scripts" / "prepare_g5_server_kit.ps1").read_text(
        encoding="utf-8"
    )
    stage = (ROOT / "scripts" / "server_stage_g5.sh").read_text(encoding="utf-8")
    run = (ROOT / "scripts" / "server_run_g5_gate.sh").read_text(encoding="utf-8")

    assert "ollama-0.32.1.tar" not in prepare
    assert "ollama-models-qwen3-8b.tar" not in prepare
    assert "ollama-0.32.1.tar" not in stage
    assert "docker compose" not in run
    assert "--gate 5.G5" in run
    assert "--network" not in run
    assert "cmp -s \"$LOCK\"" in stage
    assert run.index('test ! -e "$OUTPUT_ROOT"') < run.index("trap save_failure EXIT")


@pytest.mark.parametrize(
    "path",
    [
        "reports/result.json",
        "data/campaign.db",
        ".pytest-tmp-new/secret.txt",
        "nested/__pycache__/module.pyc",
        "deploy/.env",
        "keys/id_ed25519",
        "state.sqlite3",
    ],
)
def test_g5_source_archive_filter_rejects_runtime_and_secret_files(path: str) -> None:
    assert allowed_source_path(path) is False


def test_g5_source_archive_filter_keeps_current_runtime_sources() -> None:
    assert allowed_source_path("scripts/server_run_g5_gate.sh") is True
    assert allowed_source_path("agent_image/app/runtime.py") is True
