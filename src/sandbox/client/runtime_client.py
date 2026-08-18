"""Authenticated asynchronous JSON-RPC client for one Runtime container."""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import docker
from docker.errors import DockerException, NotFound
from docker.utils.socket import consume_socket_output, demux_adaptor, frames_iter

from sandbox.client.jsonrpc import parse_response, request_envelope
from sandbox.config import TraceConfig
from sandbox.errors import ProtocolError, RuntimeTimeoutError, RuntimeTransportError
from sandbox.models import ExecutionRequest, ExecutionResult, TracePage
from sandbox.protocol import RUNTIME_TERMINAL_GRACE_SECONDS
from sandbox.replay.models import (
    ReplayCheckpointsRequest,
    ReplayForkRequest,
    ReplayRequest,
    StateCheckpoint,
)
from sandbox.scheduler.models import SandboxHandle

MAX_RPC_TRANSPORT_BYTES = 1024 * 1024


class RuntimeClient:
    def __init__(
        self,
        trace_config: TraceConfig,
        *,
        request_timeout: float | None = None,
        docker_client: Any | None = None,
    ) -> None:
        self.trace_config = trace_config
        self.request_timeout = (
            trace_config.request_timeout_seconds
            if request_timeout is None
            else request_timeout
        )
        self.docker_client = docker_client or docker.from_env()

    async def submit(self, handle: SandboxHandle, request: ExecutionRequest) -> None:
        result = await self._call(
            handle,
            "execution.submit",
            request.model_dump(mode="json"),
            timeout=self.request_timeout,
        )
        if result.get("execution_id") != request.execution_id:
            raise ProtocolError("Runtime acknowledged a different execution_id")

    async def replay_submit(self, handle: SandboxHandle, request: ReplayRequest) -> None:
        result = await self._call(
            handle,
            "replay.submit",
            request.model_dump(mode="json"),
            timeout=self.request_timeout,
        )
        if result.get("execution_id") != request.execution_id:
            raise ProtocolError("Runtime acknowledged a different replay execution_id")

    async def replay_checkpoints(
        self,
        handle: SandboxHandle,
        request: ReplayCheckpointsRequest,
    ) -> list[StateCheckpoint]:
        result = await self._call(
            handle,
            "replay.checkpoints",
            request.model_dump(mode="json"),
            timeout=self.request_timeout,
        )
        return [StateCheckpoint.model_validate(item) for item in result]

    async def replay_fork_submit(
        self,
        handle: SandboxHandle,
        request: ReplayForkRequest,
    ) -> None:
        result = await self._call(
            handle,
            "replay.fork",
            request.model_dump(mode="json"),
            timeout=self.request_timeout,
        )
        if result.get("execution_id") != request.execution_id:
            raise ProtocolError("Runtime acknowledged a different fork execution_id")

    async def get_result(
        self,
        handle: SandboxHandle,
        execution_id: str,
    ) -> ExecutionResult:
        result = await self._call(
            handle,
            "execution.get",
            {"execution_id": execution_id},
            timeout=self.request_timeout,
        )
        return ExecutionResult.model_validate(result)

    async def events(
        self,
        handle: SandboxHandle,
        execution_id: str,
        *,
        after_sequence: int,
    ) -> TracePage:
        result = await self._call(
            handle,
            "execution.events",
            {
                "execution_id": execution_id,
                "after_sequence": after_sequence,
                "limit": self.trace_config.page_size,
            },
            timeout=self.request_timeout,
        )
        return TracePage.model_validate(result)

    async def cancel(self, handle: SandboxHandle, execution_id: str) -> None:
        await self._call(
            handle,
            "execution.cancel",
            {"execution_id": execution_id},
            timeout=self.request_timeout,
        )

    async def poll_and_stream_events(
        self,
        handle: SandboxHandle,
        request: ExecutionRequest,
    ) -> AsyncIterator[TracePage]:
        async for page in self.poll_execution_events(
            handle,
            request.execution_id,
            timeout_seconds=request.timeout_seconds,
        ):
            yield page

    async def poll_execution_events(
        self,
        handle: SandboxHandle,
        execution_id: str,
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[TracePage]:
        after_sequence = -1
        deadline = (
            asyncio.get_running_loop().time()
            + timeout_seconds
            + RUNTIME_TERMINAL_GRACE_SECONDS
        )
        while True:
            if asyncio.get_running_loop().time() > deadline:
                await self.cancel(handle, execution_id)
                raise RuntimeTimeoutError(
                    "Runtime polling exceeded the execution deadline"
                )
            page = await self.events(
                handle,
                execution_id,
                after_sequence=after_sequence,
            )
            if page.events:
                expected = after_sequence + 1
                for event in page.events:
                    if event.sequence != expected:
                        raise ProtocolError(
                            f"trace page expected sequence {expected}, got {event.sequence}"
                        )
                    expected += 1
                after_sequence = page.events[-1].sequence
                yield page
            if page.terminal:
                return
            await asyncio.sleep(self.trace_config.pull_interval_seconds)

    async def _call(
        self,
        handle: SandboxHandle,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        request_id = uuid4().hex
        envelope = request_envelope(request_id, method, params)
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(self._exec_rpc, handle, envelope),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise RuntimeTimeoutError(f"Runtime request timed out for {method}") from exc
        return parse_response(payload, request_id)

    def _exec_rpc(self, handle: SandboxHandle, envelope: dict[str, Any]) -> Any:
        if handle.transport != "docker_exec":
            raise ProtocolError(f"unsupported Runtime transport: {handle.transport}")
        encoded = json.dumps(
            envelope, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > MAX_RPC_TRANSPORT_BYTES:
            raise ProtocolError("Runtime RPC request exceeds the transport byte limit")
        try:
            container = self.docker_client.containers.get(handle.container_id)
            api = container.client.api
            created = api.exec_create(
                container.id,
                ["python", "-m", "app.rpc_client"],
                stdout=True,
                stderr=True,
                stdin=True,
                tty=False,
                environment={"SANDBOX_TOKEN": handle.capability_token},
            )
            channel = api.exec_start(created["Id"], tty=False, socket=True)
            try:
                writable = getattr(channel, "_sock", channel)
                writable.sendall(struct.pack(">Q", len(encoded)) + encoded)
                demuxed = (
                    demux_adaptor(stream_id, chunk)
                    for stream_id, chunk in frames_iter(channel, tty=False)
                )
                stdout, stderr = consume_socket_output(demuxed, demux=True)
            finally:
                channel.close()
            exit_code = api.exec_inspect(created["Id"])["ExitCode"]
        except (DockerException, NotFound) as exc:
            raise RuntimeTransportError("Docker Exec transport failed") from exc
        if exit_code != 0:
            message = (stderr or b"").decode("utf-8", errors="replace")[-2_000:]
            raise ProtocolError(f"Runtime RPC helper failed: {message}")
        if len(stdout or b"") > MAX_RPC_TRANSPORT_BYTES:
            raise ProtocolError("Runtime RPC response exceeds the transport byte limit")
        try:
            return json.loads((stdout or b"").decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProtocolError("Runtime RPC helper returned invalid JSON") from exc
