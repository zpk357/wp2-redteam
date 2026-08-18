from __future__ import annotations

import json
import struct
from types import SimpleNamespace

import pytest

import sandbox.client.runtime_client as runtime_client_module
from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import TraceConfig
from sandbox.scheduler.models import SandboxHandle


class FakeSocket:
    def __init__(self) -> None:
        self._sock = self
        self.request = b""
        self.response = b""

    def sendall(self, data: bytes) -> None:
        size = struct.unpack(">Q", data[:8])[0]
        self.request += data[8:]
        assert len(self.request) == size
        request = json.loads(self.request)
        self.response = json.dumps(
            {"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}
        ).encode("utf-8")

    def close(self) -> None:
        pass


class FakeApi:
    def __init__(self, channel: FakeSocket) -> None:
        self.channel = channel
        self.command = None
        self.environment = None

    def exec_create(self, container_id, command, **kwargs):
        assert container_id == "container-1"
        self.command = command
        self.environment = kwargs["environment"]
        assert kwargs["stdin"] is True
        assert kwargs["tty"] is False
        return {"Id": "exec-1"}

    def exec_start(self, exec_id, **kwargs):
        assert exec_id == "exec-1"
        assert kwargs == {"tty": False, "socket": True}
        return self.channel

    def exec_inspect(self, exec_id):
        assert exec_id == "exec-1"
        return {"ExitCode": 0}


class FakeContainers:
    def __init__(self, container) -> None:
        self.container = container

    def get(self, container_id: str):
        assert container_id == "container-1"
        return self.container


def test_exec_transport_uses_fixed_argument_array_and_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = FakeSocket()
    api = FakeApi(channel)
    container = SimpleNamespace(id="container-1", client=SimpleNamespace(api=api))
    docker_client = SimpleNamespace(containers=FakeContainers(container))
    monkeypatch.setattr(
        runtime_client_module,
        "frames_iter",
        lambda active, tty: iter(((1, active.response),)),
    )
    client = RuntimeClient(TraceConfig(), docker_client=docker_client)
    handle = SandboxHandle(
        execution_id="exec-1",
        container_id="container-1",
        runtime_url="http://127.0.0.1:8080",
        transport="docker_exec",
        capability_token="token",
        image_digest="sha256:test",
        scheduler_instance_id="scheduler-1",
    )
    result = client._exec_rpc(
        handle,
        {
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "execution.submit",
            "params": {"prompt": "'; rm -rf /; echo '"},
        },
    )
    assert result["result"] == {"ok": True}
    assert api.command == ["python", "-m", "app.rpc_client"]
    assert api.environment == {"SANDBOX_TOKEN": "token"}
    assert json.loads(channel.request)["params"]["prompt"] == "'; rm -rf /; echo '"


def test_runtime_client_uses_configured_rpc_timeout() -> None:
    client = RuntimeClient(
        TraceConfig(request_timeout_seconds=42.0),
        docker_client=SimpleNamespace(),
    )

    assert client.request_timeout == 42.0
