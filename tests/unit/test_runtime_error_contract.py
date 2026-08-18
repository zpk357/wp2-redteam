from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest
from docker.errors import DockerException

from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import TraceConfig
from sandbox.errors import RuntimeTimeoutError, RuntimeTransportError


def test_runtime_call_timeout_is_not_a_protocol_error() -> None:
    client = RuntimeClient(
        TraceConfig(request_timeout_seconds=0.01),
        docker_client=MagicMock(),
    )

    def slow_call(_handle, _envelope):
        time.sleep(0.05)
        return {}

    client._exec_rpc = slow_call
    with pytest.raises(RuntimeTimeoutError):
        asyncio.run(client._call(MagicMock(), "execution.get", {}, timeout=0.01))


def test_docker_exec_failure_is_transport_error() -> None:
    docker_client = MagicMock()
    docker_client.containers.get.side_effect = DockerException("daemon unavailable")
    client = RuntimeClient(TraceConfig(), docker_client=docker_client)
    handle = MagicMock(transport="docker_exec", container_id="missing")

    with pytest.raises(RuntimeTransportError):
        client._exec_rpc(handle, {"jsonrpc": "2.0", "id": "request", "method": "test"})
