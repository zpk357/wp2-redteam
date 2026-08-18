from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from docker.errors import APIError
from pydantic import ValidationError

from sandbox.config import SandboxConfig, SandboxLimits
from sandbox.errors import (
    InfrastructureError,
    PermanentInfrastructureError,
    SandboxConfigurationError,
)
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler


def _client(*, internal: bool) -> MagicMock:
    container = MagicMock()
    container.id = "container-1"
    container.image.attrs = {"RepoDigests": []}
    container.image.id = "sha256:image"
    network = MagicMock()
    network.name = "trace-g-model-internal"
    network.attrs = {
        "Driver": "bridge",
        "Internal": internal,
        "Labels": {"trace-g.network-policy": "ollama-only"},
    }
    client = MagicMock()
    client.networks.get.return_value = network
    client.containers.run.return_value = container
    return client


def _config() -> SandboxConfig:
    return SandboxConfig(
        ollama_endpoint="http://ollama:11434",
        model_network_name="trace-g-model-internal",
    )


def test_ollama_requires_internal_service_endpoint() -> None:
    assert _config().ollama_endpoint == "http://ollama:11434"
    with pytest.raises(ValidationError, match="internal model network"):
        SandboxConfig(
            ollama_endpoint="http://host.docker.internal:11434",
            model_network_name="trace-g-model-internal",
        )
    with pytest.raises(ValidationError, match="internal model network"):
        SandboxConfig(
            ollama_endpoint="http://ollama:11434/api/chat",
            model_network_name="trace-g-model-internal",
        )


def test_scheduler_rejects_labeled_bridge_that_still_has_egress() -> None:
    scheduler = DockerSandboxScheduler(_config(), client=_client(internal=False))
    with pytest.raises(SandboxConfigurationError, match="internal network"):
        scheduler._create_sync("exec-1", "image:test", SandboxLimits())


def test_scheduler_uses_internal_network_without_host_gateway() -> None:
    client = _client(internal=True)
    scheduler = DockerSandboxScheduler(_config(), client=client)
    scheduler._create_sync("exec-1", "image:test", SandboxLimits())
    create = client.containers.run.call_args.kwargs
    assert create["network_mode"] == "trace-g-model-internal"
    assert create["extra_hosts"] is None


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
def test_selected_docker_api_statuses_are_transient(status_code: int) -> None:
    response = MagicMock(status_code=status_code)
    with pytest.raises(InfrastructureError) as captured:
        DockerSandboxScheduler._raise_classified_api_error(
            APIError("temporary", response=response),
            "create failed",
        )
    assert type(captured.value) is InfrastructureError


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 409, 422])
def test_docker_api_configuration_statuses_are_not_retryable(status_code: int) -> None:
    response = MagicMock(status_code=status_code)
    with pytest.raises(SandboxConfigurationError):
        DockerSandboxScheduler._raise_classified_api_error(
            APIError("invalid request", response=response),
            "create failed",
        )


def test_unselected_server_error_is_permanent() -> None:
    response = MagicMock(status_code=501)
    with pytest.raises(PermanentInfrastructureError):
        DockerSandboxScheduler._raise_classified_api_error(
            APIError("not implemented", response=response),
            "create failed",
        )
