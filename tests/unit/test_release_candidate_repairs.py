from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_image.app.adapter.deepseek_harness_adapter import (
    HARNESS_MODEL_DIGEST,
    HARNESS_MODEL_NAME,
)
from sandbox.cli import _scenario_model_options, build_parser
from sandbox.fuzzer.v2_cli import _agent_model_options
from sandbox.protocol import AgentRuntimeKind, ModelProvider
from sandbox.release_candidate import (
    ReleaseCandidateError,
    release_manifest_digest,
    validate_release_candidate,
)
from scripts.verify_online_ollama_store import main as verify_online_store

ROOT = Path(__file__).resolve().parents[2]


def _scenario_args(
    *,
    model_name: str = HARNESS_MODEL_NAME,
    model_digest: str = HARNESS_MODEL_DIGEST,
):
    return build_parser().parse_args(
        [
            "scenario",
            "run",
            "--case",
            "clean.t2.delta",
            "--image",
            "trace-g-deepseek-harness:h6",
            "--agent-runtime",
            "deepseek_harness",
            "--model-name",
            model_name,
            "--model-digest",
            model_digest,
        ]
    )


def test_harness_cli_profiles_select_fixture_or_locked_ollama() -> None:
    scenario_model = _scenario_model_options(_scenario_args())
    campaign_model = _agent_model_options(
        AgentRuntimeKind.DEEPSEEK_HARNESS,
        SimpleNamespace(model_name="qwen", manifest_digest="sha256:" + "a" * 64),
    )

    assert scenario_model.provider is ModelProvider.FAKE
    assert scenario_model.model_name == HARNESS_MODEL_NAME
    assert scenario_model.model_digest == HARNESS_MODEL_DIGEST
    assert scenario_model.endpoint is None
    assert campaign_model.provider is ModelProvider.OLLAMA
    assert campaign_model.model_name == "qwen"
    assert campaign_model.model_digest == "sha256:" + "a" * 64
    assert campaign_model.endpoint == "http://127.0.0.1:11434"

    direct = _scenario_model_options(
        _scenario_args(
            model_name="qwen3.5:27b-q4_K_M",
            model_digest="sha256:" + "b" * 64,
        )
    )
    assert direct.provider is ModelProvider.OLLAMA
    assert direct.endpoint == "http://127.0.0.1:11434"


def test_real_harness_event_conversion_preserves_result_feedback() -> None:
    events = [
        {
            "type": "assistant/message",
            "data": {
                "usage": {"inputTokens": 11, "outputTokens": 3},
                "message": {
                    "content": [
                        {
                            "type": "tool-call",
                            "name": "mcp__office_v2__read_file",
                            "arguments": '{"file_id":"f-1"}',
                        }
                    ]
                },
            },
        },
        {
            "type": "tool/result",
            "data": {
                "message": {
                    "content": [
                        {
                            "type": "tool-result",
                            "content": [{"type": "text", "text": '{"ok":true}'}],
                        }
                    ]
                }
            },
        },
        {
            "type": "assistant/message",
            "data": {
                "usage": {"inputTokens": 7, "outputTokens": 2},
                "message": {
                    "content": [
                        {
                            "type": "tool-call",
                            "name": "mcp__office_v2__submit",
                            "arguments": '{"answer":"done"}',
                        }
                    ]
                },
            },
        },
        {
            "type": "assistant/message",
            "data": {"message": {"content": [{"type": "text", "text": "done"}]}},
        },
    ]
    script = f"""
import {{ realModelRuntime }} from './agent_variants/deepseek_harness/runtime/model_runtime.mjs'
const runtime = realModelRuntime({{
  provider:'ollama', endpoint:'http://127.0.0.1:11434', model_name:'locked'
}})
runtime.ingest({json.dumps(events, separators=(",", ":"))})
console.log(JSON.stringify({{decisions: runtime.decisions, tokenUsage: runtime.tokenUsage}}))
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["tokenUsage"] == {"prompt_tokens": 18, "completion_tokens": 5}
    assert payload["decisions"][1]["prior_tool_result_sha256"] == (
        "sha256:" + hashlib.sha256(b'{"ok":true}').hexdigest()
    )
    assert [item["kind"] for item in payload["decisions"]] == [
        "tool_call",
        "submit",
        "final_text",
    ]


def test_harness_build_uses_the_package_lock_and_correct_cancel_boundary() -> None:
    dockerfile = (ROOT / "agent_variants/deepseek_harness/Dockerfile.dev").read_text()
    probe = (
        ROOT / "agent_variants/deepseek_harness/runtime/container_probe.py"
    ).read_text()

    assert "RUN npm ci --no-audit --no-fund" in dockerfile
    assert "COPY agent_variants/deepseek_harness/node_modules" not in dockerfile
    assert 'root.glob("trace-g-h4-*/bridge-records.ndjson")' in probe
    assert "cancel_boundary_observed" in probe


def test_online_model_verifier_checks_manifest_and_every_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "models"
    manifest_path = store / "manifests/registry.ollama.ai/library/qwen/test"
    blobs = store / "blobs"
    manifest_path.parent.mkdir(parents=True)
    blobs.mkdir(parents=True)
    descriptors = []
    for content in (b"config", b"layer"):
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        (blobs / digest.replace(":", "-")).write_bytes(content)
        descriptors.append({"digest": digest, "size": len(content)})
    manifest = {"config": descriptors[0], "layers": [descriptors[1]]}
    manifest_path.write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )
    release = {
        "model": {
            "name": "qwen:test",
            "manifest_digest": (
                "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            ),
            "config_digest": descriptors[0]["digest"],
            "layer_digests": [descriptors[1]["digest"]],
        }
    }
    release["release_manifest_digest"] = release_manifest_digest(release)
    release_path = tmp_path / "release.json"
    output = tmp_path / "verification.json"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_online_ollama_store.py",
            "--store",
            str(store),
            "--release",
            str(release_path),
            "--output",
            str(output),
        ],
    )

    assert verify_online_store() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["verified_bytes"] == 11
    (blobs / descriptors[1]["digest"].replace(":", "-")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="blob differs"):
        verify_online_store()


def test_rc2_release_identity_is_sealed_but_not_server_ready(tmp_path: Path) -> None:
    source = ROOT / "config/releases/v0.2.0-rc.2.json"
    payload = validate_release_candidate(source)

    assert payload["deployment_policy"]["deployment_ready"] is False
    assert len(payload["model"]["layer_digests"]) == 3
    with pytest.raises(ReleaseCandidateError, match="not deployment-ready"):
        validate_release_candidate(source, require_deployment_ready=True)

    tampered = json.loads(source.read_text(encoding="utf-8"))
    tampered["model"]["name"] = "different"
    destination = tmp_path / "tampered.json"
    destination.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ReleaseCandidateError, match="digest differs"):
        validate_release_candidate(destination)
