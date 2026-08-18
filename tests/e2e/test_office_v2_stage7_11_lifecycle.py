from __future__ import annotations

import json
import os
from pathlib import Path

import docker
import pytest

from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import SandboxConfig, TraceConfig
from sandbox.protocol import ExecutionStatus
from sandbox.replay.digests import sha256_digest
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from tests.e2e.test_office_v2_stage7_docker import _request

pytestmark = pytest.mark.skipif(
    os.environ.get("TRACE_G_RUN_DOCKER_E2E") != "1",
    reason="set TRACE_G_RUN_DOCKER_E2E=1 to run real Docker tests",
)


async def _terminal_events(runtime, handle, request):
    events = []
    async for page in runtime.poll_and_stream_events(handle, request):
        events.extend(page.events)
    result = await runtime.get_result(handle, request.execution_id)
    return result, events


async def test_stage7_11_timeout_cancel_and_zero_residue(tmp_path: Path) -> None:
    client = docker.from_env()
    client.ping()
    config = SandboxConfig(
        image=os.getenv("TRACE_G_STAGE7_E2E_IMAGE", "trace-g-office-v2:stage7-local"),
        workspace_storage="archive_volume",
        execution_timeout_seconds=30,
        startup_timeout_seconds=30,
    )
    tracing = TraceConfig(output_dir=tmp_path / "trajectories", pull_interval_seconds=0.01)
    scheduler = DockerSandboxScheduler(config, client=client)
    runtime = RuntimeClient(tracing, docker_client=client)
    facts = []

    timeout_request = _request("clean.t2.delta", max_steps=40).model_copy(
        update={
            "execution_id": "episode-stage7-11-timeout",
            "timeout_seconds": 1,
        }
    )
    cancel_request = _request("clean.t2.delta", max_steps=40).model_copy(
        update={"execution_id": "episode-stage7-11-cancel"}
    )

    for mode, request in (("timeout", timeout_request), ("cancel", cancel_request)):
        handle = None
        try:
            handle = await scheduler.create(
                request.execution_id,
                config.image,
                config.limits,
            )
            await scheduler.wait_until_ready(handle)
            await runtime.submit(handle, request)
            if mode == "cancel":
                await runtime.cancel(handle, request.execution_id)
            result, events = await _terminal_events(runtime, handle, request)
            expected_status = (
                ExecutionStatus.TIMED_OUT if mode == "timeout" else ExecutionStatus.CANCELLED
            )
            expected_event = (
                "execution_timed_out" if mode == "timeout" else "execution_cancelled"
            )
            assert result.status is expected_status
            assert events[-1].event_type == expected_event
            facts.append(
                {
                    "mode": mode,
                    "status": result.status.value,
                    "error_code": result.error_code,
                    "terminal_event": events[-1].event_type,
                    "trace_count": result.trace_count,
                }
            )
        finally:
            if handle is not None:
                await scheduler.destroy(handle)

    owner_labels = {
        "label": [
            "trace-g.component=agent-sandbox",
            f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
        ]
    }
    volume_labels = {
        "label": [
            "trace-g.component=workspace-volume",
            f"trace-g.owner-instance={scheduler.scheduler_instance_id}",
        ]
    }
    remaining_containers = client.containers.list(all=True, filters=owner_labels)
    remaining_volumes = client.volumes.list(filters=volume_labels)
    assert remaining_containers == []
    assert remaining_volumes == []

    output = os.environ.get("TRACE_G_STAGE7_11_EVIDENCE_OUTPUT")
    if output:
        image = client.images.get(config.image)
        payload = {
            "schema_version": "office-v2-stage7-11-evidence-v1",
            "identity": {
                "image": config.image,
                "image_id": image.id,
                "image_size_bytes": image.attrs["Size"],
            },
            "episodes": facts,
            "cleanup": {
                "remaining_owned_containers": len(remaining_containers),
                "remaining_owned_volumes": len(remaining_volumes),
            },
            "limitations": {"real_model_used": False, "server_used": False},
        }
        payload["evidence_digest"] = sha256_digest(payload)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
