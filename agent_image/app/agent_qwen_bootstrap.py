"""PID 1 supervisor for the self-contained Agent-Qwen image."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Literal

LOOPBACK_ENDPOINT = "http://127.0.0.1:11434"
LOCKED_MODEL_DIR = "/opt/ollama-models"
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
DIGEST_INPUT_PATTERN = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")


class BootstrapError(RuntimeError):
    """The formal Agent-Qwen runtime failed a closed identity or startup check."""


@dataclass(frozen=True)
class BootstrapConfig:
    model_name: str
    model_digest: str
    ollama_endpoint: str
    ollama_models: str
    status_path: Path
    startup_timeout_seconds: float
    shutdown_timeout_seconds: float
    runtime_mode: Literal["live", "strict_replay"]

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> BootstrapConfig:
        values = os.environ if environ is None else environ
        model_name = values.get("TRACE_G_MODEL_NAME", "")
        model_digest = values.get("TRACE_G_MODEL_DIGEST", "").lower()
        endpoint = values.get("TRACE_G_OLLAMA_ENDPOINT", LOOPBACK_ENDPOINT).rstrip("/")
        model_dir = values.get("OLLAMA_MODELS", LOCKED_MODEL_DIR)
        runtime_mode = values.get("TRACE_G_RUNTIME_MODE", "live")
        if runtime_mode not in {"live", "strict_replay"}:
            raise BootstrapError("TRACE_G_RUNTIME_MODE must be live or strict_replay")
        if not model_name or any(char.isspace() for char in model_name):
            raise BootstrapError("TRACE_G_MODEL_NAME must be a non-empty model identifier")
        if DIGEST_PATTERN.fullmatch(model_digest) is None:
            raise BootstrapError("TRACE_G_MODEL_DIGEST must be a canonical SHA-256 digest")
        if endpoint != LOOPBACK_ENDPOINT:
            raise BootstrapError("formal Ollama endpoint must be http://127.0.0.1:11434")
        if values.get("OLLAMA_HOST", "") != "127.0.0.1:11434":
            raise BootstrapError("OLLAMA_HOST must bind only to 127.0.0.1:11434")
        if model_dir != LOCKED_MODEL_DIR:
            raise BootstrapError("formal Ollama model directory must be /opt/ollama-models")
        if _truthy(values.get("LANGSMITH_TRACING")) or _truthy(
            values.get("LANGCHAIN_TRACING_V2")
        ):
            raise BootstrapError("external LangSmith tracing is forbidden in formal mode")
        if values.get("LANGSMITH_API_KEY") or values.get("LANGCHAIN_API_KEY"):
            raise BootstrapError("external tracing API keys are forbidden in formal mode")
        return cls(
            model_name=model_name,
            model_digest=model_digest,
            ollama_endpoint=endpoint,
            ollama_models=model_dir,
            status_path=Path(values.get("TRACE_G_STATUS_PATH", "/tmp/agent-qwen-status.json")),
            startup_timeout_seconds=_positive_float(
                values.get("TRACE_G_STARTUP_TIMEOUT_SECONDS", "180"),
                "TRACE_G_STARTUP_TIMEOUT_SECONDS",
            ),
            shutdown_timeout_seconds=_positive_float(
                values.get("TRACE_G_SHUTDOWN_TIMEOUT_SECONDS", "10"),
                "TRACE_G_SHUTDOWN_TIMEOUT_SECONDS",
            ),
            runtime_mode=runtime_mode,
        )


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_float(value: str, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise BootstrapError(f"{name} must be numeric") from exc
    if parsed <= 0:
        raise BootstrapError(f"{name} must be positive")
    return parsed


def request_json(
    endpoint: str,
    path: str,
    payload: dict | None = None,
    *,
    timeout_seconds: float = 5,
) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{endpoint}{path}",
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            raw = response.read(1024 * 1024 + 1)
    except TimeoutError as exc:
        raise BootstrapError(f"Ollama {path} timed out") from exc
    if len(raw) > 1024 * 1024:
        raise BootstrapError(f"Ollama {path} response exceeded the byte limit")
    try:
        result = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise BootstrapError(f"Ollama {path} returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise BootstrapError(f"Ollama {path} returned a non-object response")
    return result


def verify_model_registry(payload: dict, config: BootstrapConfig) -> None:
    models = payload.get("models")
    if not isinstance(models, list):
        raise BootstrapError("Ollama model registry has no models list")
    matches = [
        entry
        for entry in models
        if isinstance(entry, dict) and entry.get("name") == config.model_name
    ]
    observed_digest = (
        _canonical_digest(matches[0].get("digest")) if len(matches) == 1 else None
    )
    if observed_digest != config.model_digest:
        raise BootstrapError("Ollama model registry does not match the locked model identity")


def _canonical_digest(value: object) -> str | None:
    match = DIGEST_INPUT_PATTERN.fullmatch(str(value))
    return f"sha256:{match.group(1).lower()}" if match is not None else None


def wait_for_locked_model(
    config: BootstrapConfig,
    *,
    stop_requested: Callable[[], bool] = lambda: False,
) -> bool:
    deadline = time.monotonic() + config.startup_timeout_seconds
    last_error = "Ollama did not answer"
    while time.monotonic() < deadline:
        if stop_requested():
            return False
        try:
            payload = request_json(config.ollama_endpoint, "/api/tags")
            verify_model_registry(payload, config)
            return True
        except (BootstrapError, OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
            time.sleep(0.25)
    raise BootstrapError(f"locked Ollama model failed readiness: {last_error}")


def warm_locked_model(config: BootstrapConfig) -> None:
    response = request_json(
        config.ollama_endpoint,
        "/api/generate",
        {
            "model": config.model_name,
            "prompt": "Reply with OK.",
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": 8, "seed": 0},
        },
        timeout_seconds=config.startup_timeout_seconds,
    )
    if response.get("done") is not True:
        raise BootstrapError("locked Ollama model warm-up did not complete")


def write_ready_status(config: BootstrapConfig) -> None:
    payload = {
        "schema_version": "1.0",
        "status": "ready",
        "formal_agent": True,
        "model_name": config.model_name,
        "model_digest": config.model_digest,
        "ollama_endpoint": (
            config.ollama_endpoint if config.runtime_mode == "live" else None
        ),
        "model_ready": config.runtime_mode == "live",
        "runtime_mode": config.runtime_mode,
        "agent_framework": "langgraph",
    }
    config.status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config.status_path.with_suffix(config.status_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(config.status_path)


class Supervisor:
    def __init__(self, config: BootstrapConfig) -> None:
        self.config = config
        self.processes: list[subprocess.Popen] = []
        self.stop_signal: int | None = None

    def request_stop(self, signum: int, _frame: FrameType | None) -> None:
        self.stop_signal = signum

    def run(self) -> int:
        previous_handlers = {
            signum: signal.signal(signum, self.request_stop)
            for signum in (signal.SIGTERM, signal.SIGINT)
        }
        try:
            ollama = None
            if self.config.runtime_mode == "live":
                ollama = self._start(["/usr/bin/ollama", "serve"])
                ready = wait_for_locked_model(
                    self.config,
                    stop_requested=lambda: self.stop_signal is not None,
                )
                if not ready:
                    return 128 + int(self.stop_signal or signal.SIGTERM)
                warm_locked_model(self.config)
            write_ready_status(self.config)
            runtime = self._start(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.server:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8080",
                    "--no-access-log",
                ]
            )
            while self.stop_signal is None:
                if ollama is not None and ollama.poll() is not None:
                    raise BootstrapError("Ollama exited while Agent Runtime was active")
                if runtime.poll() is not None:
                    raise BootstrapError("Agent Runtime exited before container shutdown")
                time.sleep(0.2)
            return 128 + self.stop_signal
        except BootstrapError as exc:
            print(f"agent-qwen bootstrap failed: {exc}", file=sys.stderr, flush=True)
            return 1
        finally:
            self._stop_all()
            self.config.status_path.unlink(missing_ok=True)
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)

    def _start(self, command: list[str]) -> subprocess.Popen:
        process = subprocess.Popen(command, start_new_session=True)  # noqa: S603
        self.processes.append(process)
        return process

    def _stop_all(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is None:
                _signal_process(process, signal.SIGTERM)
        deadline = time.monotonic() + self.config.shutdown_timeout_seconds
        for process in reversed(self.processes):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                _signal_process(process, signal.SIGKILL)
                process.wait(timeout=2)


def _signal_process(process: subprocess.Popen, signum: signal.Signals) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signum)
            return
        except ProcessLookupError:
            return
    if signum == signal.SIGTERM:
        process.terminate()
    else:
        process.kill()


def main() -> int:
    try:
        config = BootstrapConfig.from_environment()
    except BootstrapError as exc:
        print(f"agent-qwen configuration rejected: {exc}", file=sys.stderr)
        return 2
    return Supervisor(config).run()


if __name__ == "__main__":
    raise SystemExit(main())
