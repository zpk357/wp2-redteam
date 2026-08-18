from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from sandbox.config import SandboxConfig, SandboxLimits
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler


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
