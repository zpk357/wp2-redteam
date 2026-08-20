from __future__ import annotations

import json
import signal
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest
from app.agent_qwen_bootstrap import (
    BootstrapConfig,
    BootstrapError,
    Supervisor,
    request_json,
    verify_model_registry,
    wait_for_locked_model,
    warm_locked_model,
    write_ready_status,
)
from app.server import health
from fastapi.responses import JSONResponse

DIGEST = "sha256:" + "a" * 64


def environment(**overrides: str) -> dict[str, str]:
    values = {
        "TRACE_G_MODEL_NAME": "qwen3:8b",
        "TRACE_G_MODEL_DIGEST": DIGEST,
        "TRACE_G_OLLAMA_ENDPOINT": "http://127.0.0.1:11434",
        "OLLAMA_HOST": "127.0.0.1:11434",
        "OLLAMA_MODELS": "/opt/ollama-models",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "TRACE_G_RUNTIME_MODE": "live",
    }
    values.update(overrides)
    return values


def test_formal_bootstrap_accepts_only_locked_loopback_topology() -> None:
    config = BootstrapConfig.from_environment(environment())

    assert config.model_name == "qwen3:8b"
    assert config.model_digest == DIGEST
    assert config.ollama_endpoint == "http://127.0.0.1:11434"
    assert config.runtime_mode == "live"


@pytest.mark.parametrize(
    "overrides",
    [
        {"TRACE_G_OLLAMA_ENDPOINT": "http://ollama:11434"},
        {"OLLAMA_HOST": "0.0.0.0:11434"},
        {"OLLAMA_MODELS": "/models"},
        {"LANGSMITH_TRACING": "true"},
        {"LANGSMITH_API_KEY": "must-not-leave-container"},
    ],
)
def test_formal_bootstrap_rejects_external_or_mutable_model_paths(
    overrides: dict[str, str],
) -> None:
    with pytest.raises(BootstrapError):
        BootstrapConfig.from_environment(environment(**overrides))


def test_registry_requires_exactly_one_locked_model() -> None:
    config = BootstrapConfig.from_environment(environment())
    verify_model_registry(
        {"models": [{"name": "qwen3:8b", "digest": DIGEST.removeprefix("sha256:")}]},
        config,
    )

    with pytest.raises(BootstrapError):
        verify_model_registry(
            {"models": [{"name": "qwen3:8b", "digest": "sha256:" + "b" * 64}]},
            config,
        )
    with pytest.raises(BootstrapError):
        verify_model_registry(
            {
                "models": [
                    {"name": "qwen3:8b", "digest": DIGEST},
                    {"name": "qwen3:8b", "digest": DIGEST},
                ]
            },
            config,
        )


def test_readiness_wait_can_be_interrupted_before_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = BootstrapConfig.from_environment(environment())
    called = False

    def unexpected_request(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("app.agent_qwen_bootstrap.request_json", unexpected_request)

    assert wait_for_locked_model(config, stop_requested=lambda: True) is False
    assert called is False


def test_readiness_wait_fails_fast_for_permanent_identity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = BootstrapConfig.from_environment(
        environment(TRACE_G_STARTUP_TIMEOUT_SECONDS="180")
    )
    calls = 0

    def permanent_error(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise BootstrapError("identity differs", failure_class="configuration_permanent")

    monkeypatch.setattr("app.agent_qwen_bootstrap.request_json", permanent_error)

    with pytest.raises(BootstrapError, match="identity differs"):
        wait_for_locked_model(config)
    assert calls == 1


def test_warmup_uses_the_bounded_startup_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    config = BootstrapConfig.from_environment(
        environment(TRACE_G_STARTUP_TIMEOUT_SECONDS="37")
    )
    observed: dict = {}

    def fake_request(endpoint, path, payload, *, timeout_seconds):
        observed.update(
            endpoint=endpoint,
            path=path,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        return {"done": True}

    monkeypatch.setattr("app.agent_qwen_bootstrap.request_json", fake_request)

    warm_locked_model(config)

    assert observed["path"] == "/api/generate"
    assert observed["payload"]["model"] == "qwen3:8b"
    assert observed["timeout_seconds"] == 37


def test_request_json_preserves_bounded_ollama_http_error_detail() -> None:
    error = urllib.error.HTTPError(
        url="http://127.0.0.1:11434/api/chat",
        code=400,
        msg="Bad Request",
        hdrs=None,
        fp=None,
    )
    error.read = lambda _limit: b'{"error":"model does not support thinking"}'

    with (
        patch("urllib.request.urlopen", side_effect=error),
        pytest.raises(BootstrapError) as captured,
    ):
        request_json(
            "http://127.0.0.1:11434",
            "/api/chat",
            {"model": "locked"},
        )

    assert str(captured.value) == (
        "Ollama /api/chat rejected request with HTTP 400: "
        "model does not support thinking"
    )
    assert captured.value.failure_class == "configuration_permanent"
    assert captured.value.http_status == 400


def test_ready_status_is_atomic_and_identity_bound(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    config = BootstrapConfig.from_environment(
        environment(TRACE_G_STATUS_PATH=str(path))
    )

    write_ready_status(config)

    status = json.loads(path.read_text(encoding="utf-8"))
    assert status == {
        "agent_framework": "langgraph",
        "formal_agent": True,
        "model_digest": DIGEST,
        "model_name": "qwen3:8b",
        "ollama_endpoint": "http://127.0.0.1:11434",
        "model_ready": True,
        "runtime_mode": "live",
        "schema_version": "1.0",
        "status": "ready",
    }
    assert not path.with_suffix(".json.tmp").exists()


def test_strict_replay_status_does_not_claim_model_readiness(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    config = BootstrapConfig.from_environment(
        environment(
            TRACE_G_STATUS_PATH=str(path),
            TRACE_G_RUNTIME_MODE="strict_replay",
        )
    )

    write_ready_status(config)

    status = json.loads(path.read_text(encoding="utf-8"))
    assert status["runtime_mode"] == "strict_replay"
    assert status["model_ready"] is False
    assert status["ollama_endpoint"] is None


async def test_formal_health_requires_the_bootstrap_identity_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRACE_G_FORMAL_AGENT", "1")
    monkeypatch.setenv("TRACE_G_MODEL_NAME", "qwen3:8b")
    monkeypatch.setenv("TRACE_G_MODEL_DIGEST", DIGEST)
    monkeypatch.setenv("TRACE_G_STATUS_PATH", str(tmp_path / "missing.json"))

    response = await health()

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503


def test_strict_replay_supervisor_never_starts_ollama(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = BootstrapConfig.from_environment(
        environment(
            TRACE_G_STATUS_PATH=str(tmp_path / "status.json"),
            TRACE_G_RUNTIME_MODE="strict_replay",
        )
    )
    supervisor = Supervisor(config)
    commands: list[list[str]] = []

    class Process:
        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    def start(command: list[str]):
        commands.append(command)
        supervisor.stop_signal = signal.SIGTERM
        process = Process()
        supervisor.processes.append(process)
        return process

    monkeypatch.setattr(supervisor, "_start", start)
    monkeypatch.setattr(supervisor, "_stop_all", lambda: None)

    assert supervisor.run() == 128 + signal.SIGTERM
    assert len(commands) == 1
    assert commands[0][0] != "/usr/bin/ollama"
