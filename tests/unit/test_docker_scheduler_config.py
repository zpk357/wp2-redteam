from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from docker.errors import NotFound
from pydantic import ValidationError

from sandbox.config import SandboxConfig, SandboxLimits
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scheduler.models import SandboxHandle


def _handle() -> SandboxHandle:
    return SandboxHandle(
        execution_id="exec-1",
        container_id="container-1",
        runtime_url="http://127.0.0.1:8080",
        capability_token="token",
        scheduler_instance_id="scheduler-1",
    )


def test_destroy_is_idempotent() -> None:
    """The host stops the container to observe isolation, then finalization stops it again."""

    state = {"removed": False}
    container = MagicMock()
    container.remove.side_effect = lambda **kwargs: state.update(removed=True)

    def get(container_id):
        if state["removed"]:
            raise NotFound("gone")
        return container

    client = MagicMock()
    client.containers.get.side_effect = get
    scheduler = DockerSandboxScheduler(
        SandboxConfig(), client=client, scheduler_instance_id="scheduler-1"
    )

    scheduler._destroy_sync(_handle())
    scheduler._destroy_sync(_handle())

    assert container.remove.call_count == 1


def test_scheduler_uses_configured_network_mode() -> None:
    container = MagicMock()
    container.id = "container-1"
    container.image.attrs = {"RepoDigests": []}
    container.image.id = "sha256:test"
    client = MagicMock()
    client.containers.run.return_value = container
    scheduler = DockerSandboxScheduler(
        SandboxConfig(network_mode="custom-model-network"),
        client=client,
        scheduler_instance_id="scheduler-1",
    )

    scheduler._create_sync("exec-1", "image:test", SandboxLimits())

    assert client.containers.run.call_args.kwargs["network_mode"] == "custom-model-network"


def test_scheduler_requests_only_the_configured_gpu() -> None:
    container = MagicMock()
    container.id = "container-1"
    container.image.attrs = {"RepoDigests": []}
    container.image.id = "sha256:test"
    client = MagicMock()
    client.containers.run.return_value = container
    scheduler = DockerSandboxScheduler(
        SandboxConfig(gpu_device="0"),
        client=client,
        scheduler_instance_id="scheduler-1",
    )

    scheduler._create_sync("exec-1", "image:test", SandboxLimits())

    request = client.containers.run.call_args.kwargs["device_requests"][0]
    assert request["DeviceIDs"] == ["0"]
    assert request["Capabilities"] == [["gpu"]]


def test_scheduler_does_not_request_gpu_by_default() -> None:
    container = MagicMock()
    container.id = "container-1"
    container.image.attrs = {"RepoDigests": []}
    container.image.id = "sha256:test"
    client = MagicMock()
    client.containers.run.return_value = container
    scheduler = DockerSandboxScheduler(
        SandboxConfig(), client=client, scheduler_instance_id="scheduler-1"
    )

    scheduler._create_sync("exec-1", "image:test", SandboxLimits())

    assert "device_requests" not in client.containers.run.call_args.kwargs


def test_gpu_device_rejects_broad_or_ambiguous_selection() -> None:
    with pytest.raises(ValidationError):
        SandboxConfig(gpu_device="all")


def test_health_probe_attaches_output_to_wait_for_exit_status() -> None:
    client = MagicMock()
    container = client.containers.get.return_value

    def exec_run(command, *, stdout, stderr):
        return (0 if stdout or stderr else None, b"")

    container.exec_run.side_effect = exec_run
    scheduler = DockerSandboxScheduler(SandboxConfig(), client=client)

    assert scheduler._runtime_health_probe("container-1") is True


def test_scheduler_marks_strict_replay_container_mode() -> None:
    container = MagicMock()
    container.id = "container-1"
    container.image.attrs = {"RepoDigests": []}
    container.image.id = "sha256:test"
    client = MagicMock()
    client.containers.run.return_value = container
    scheduler = DockerSandboxScheduler(
        SandboxConfig(), client=client, scheduler_instance_id="scheduler-1"
    )

    scheduler._create_sync(
        "exec-1", "image:test", SandboxLimits(), execution_mode="strict_replay"
    )

    assert (
        client.containers.run.call_args.kwargs["environment"]["TRACE_G_RUNTIME_MODE"]
        == "strict_replay"
    )
    assert "device_requests" not in client.containers.run.call_args.kwargs
