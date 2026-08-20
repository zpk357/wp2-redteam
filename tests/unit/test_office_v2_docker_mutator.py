from __future__ import annotations

import json

import pytest

from sandbox.mutation.v2_brief import build_minimal_fact_brief
from sandbox.mutation.v2_docker import DockerOllamaV2MutationProvider
from sandbox.mutation.v2_provider import ProviderFailureClass, V2ProviderFailure
from sandbox.replay.digests import sha256_digest
from tests.unit.test_office_v2_controlled_mutation_contracts import plan


class FakeImage:
    id = "sha256:image"

    def __init__(self, labels: dict[str, str]) -> None:
        self.attrs = {"Config": {"Labels": labels}}


class FakeImages:
    def __init__(self, image: FakeImage) -> None:
        self.image = image

    def get(self, _reference: str) -> FakeImage:
        return self.image


class FakeContainer:
    def __init__(
        self,
        response: bytes,
        *,
        status_code: int = 0,
        errors: bytes = b"",
        cleanup_error: bool = False,
    ) -> None:
        self.response = response
        self.status_code = status_code
        self.errors = errors
        self.cleanup_error = cleanup_error
        self.removed = False

    def wait(self, *, timeout: int) -> dict[str, int]:
        assert timeout >= 30
        return {"StatusCode": self.status_code}

    def logs(self, *, stdout: bool, stderr: bool) -> bytes:
        return self.response if stdout and not stderr else self.errors

    def remove(self, *, force: bool) -> None:
        assert force is True
        if self.cleanup_error:
            raise RuntimeError("injected cleanup failure")
        self.removed = True


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self.container = container
        self.run_kwargs: dict[str, object] = {}

    def run(self, _image_ref: str, **kwargs) -> FakeContainer:
        self.run_kwargs = kwargs
        return self.container


class FakeClient:
    def __init__(self, image: FakeImage, container: FakeContainer) -> None:
        self.images = FakeImages(image)
        self.containers = FakeContainers(container)


@pytest.mark.asyncio
async def test_docker_v2_mutator_uses_fresh_locked_role_and_cleans_up() -> None:
    model_digest = sha256_digest({"model": "qwen3.5:27b-q4_K_M"})
    item = plan(
        provider_id="provider-docker-ollama-v2",
        model_identity_digest=model_digest,
    )
    brief = build_minimal_fact_brief(
        plan=item,
        frontier_description="Exercise an uncovered policy boundary.",
        operator_instructions=("Change only the payload expression.",),
        scenario_facts=(),
        parent_payload_texts=("Parent payload",),
    )
    response = json.dumps(
        {
            "schema_version": "office-v2-mutator-worker-v1",
            "model_name": "qwen3.5:27b-q4_K_M",
            "model_digest": model_digest,
            "plan_digest": item.plan_digest,
            "brief_digest": brief.brief_digest,
            "attempt_index": 1,
            "candidate": {
                "slot_values": [
                    {"payload_slot_id": "slot-1", "generated_content": "variant"}
                ],
                "expression_metadata": {},
            },
            "prompt_eval_count": 20,
            "eval_count": 8,
            "done_reason": "stop",
        }
    ).encode()
    container = FakeContainer(response)
    client = FakeClient(
        FakeImage(
            {
                "org.trace-g.runtime": "self-contained-mutator-qwen",
                "org.trace-g.role": "mutator",
                "org.trace-g.model.name": "qwen3.5:27b-q4_K_M",
                "org.trace-g.model.digest": model_digest,
            }
        ),
        container,
    )
    provider = DockerOllamaV2MutationProvider(
        image_ref="mutator:test",
        image_id="sha256:image",
        model_name="qwen3.5:27b-q4_K_M",
        model_identity_digest=model_digest,
        campaign_id="campaign-1",
        client=client,
    )

    result = await provider.generate(plan=item, brief=brief, attempt_index=1)

    assert result.candidate.slot_values[0].generated_content == "variant"
    assert result.attempt.input_tokens == 20
    assert client.containers.run_kwargs["network_mode"] == "none"
    assert client.containers.run_kwargs["read_only"] is True
    assert client.containers.run_kwargs["labels"]["trace-g.campaign-id"] == "campaign-1"
    assert client.containers.run_kwargs["labels"]["trace-g.work-item-id"].startswith(
        "mutation."
    )
    assert client.containers.run_kwargs["labels"]["trace-g.attempt"] == "1"
    assert container.removed is True


def test_docker_v2_mutator_rejects_agent_image_identity() -> None:
    model_digest = sha256_digest({"model": "qwen3.5:27b-q4_K_M"})
    client = FakeClient(
        FakeImage(
            {
                "org.trace-g.runtime": "self-contained-agent-qwen",
                "org.trace-g.role": "agent",
                "org.trace-g.model.name": "qwen3.5:27b-q4_K_M",
                "org.trace-g.model.digest": model_digest,
            }
        ),
        FakeContainer(b""),
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        DockerOllamaV2MutationProvider(
            image_ref="agent:test",
            image_id="sha256:image",
            model_name="qwen3.5:27b-q4_K_M",
            model_identity_digest=model_digest,
            client=client,
        )


@pytest.mark.asyncio
async def test_docker_v2_mutator_preserves_permanent_worker_failure() -> None:
    model_digest = sha256_digest({"model": "qwen3.5:27b-q4_K_M"})
    item = plan(
        provider_id="provider-docker-ollama-v2",
        model_identity_digest=model_digest,
    )
    brief = build_minimal_fact_brief(
        plan=item,
        frontier_description="Exercise a validation boundary.",
        operator_instructions=("Change only the frozen payload expression.",),
        scenario_facts=(),
        parent_payload_texts=("Parent payload",),
    )
    error = json.dumps(
        {
            "schema_version": "office-v2-mutator-worker-error-v1",
            "failure_class": "configuration_permanent",
            "http_status": 400,
            "error_type": "BootstrapError",
            "summary": "Ollama rejected the response schema",
        }
    ).encode()
    container = FakeContainer(b"", status_code=1, errors=error)
    client = FakeClient(
        FakeImage(
            {
                "org.trace-g.runtime": "self-contained-mutator-qwen",
                "org.trace-g.role": "mutator",
                "org.trace-g.model.name": "qwen3.5:27b-q4_K_M",
                "org.trace-g.model.digest": model_digest,
            }
        ),
        container,
    )
    provider = DockerOllamaV2MutationProvider(
        image_ref="mutator:test",
        image_id="sha256:image",
        model_name="qwen3.5:27b-q4_K_M",
        model_identity_digest=model_digest,
        client=client,
    )

    with pytest.raises(V2ProviderFailure) as captured:
        await provider.generate(plan=item, brief=brief, attempt_index=1)

    assert (
        captured.value.attempt.failure_class
        is ProviderFailureClass.CONFIGURATION_PERMANENT
    )
    assert captured.value.attempt.http_status == 400
    assert container.removed is True


@pytest.mark.asyncio
async def test_docker_v2_mutator_cleanup_does_not_mask_primary_failure() -> None:
    model_digest = sha256_digest({"model": "qwen3.5:27b-q4_K_M"})
    item = plan(
        provider_id="provider-docker-ollama-v2",
        model_identity_digest=model_digest,
    )
    brief = build_minimal_fact_brief(
        plan=item,
        frontier_description="Exercise a validation boundary.",
        operator_instructions=("Change only the frozen payload expression.",),
        scenario_facts=(),
        parent_payload_texts=("Parent payload",),
    )
    error = json.dumps(
        {
            "schema_version": "office-v2-mutator-worker-error-v1",
            "failure_class": "configuration_permanent",
            "http_status": 400,
        }
    ).encode()
    container = FakeContainer(
        b"", status_code=1, errors=error, cleanup_error=True
    )
    client = FakeClient(
        FakeImage(
            {
                "org.trace-g.runtime": "self-contained-mutator-qwen",
                "org.trace-g.role": "mutator",
                "org.trace-g.model.name": "qwen3.5:27b-q4_K_M",
                "org.trace-g.model.digest": model_digest,
            }
        ),
        container,
    )
    provider = DockerOllamaV2MutationProvider(
        image_ref="mutator:test",
        image_id="sha256:image",
        model_name="qwen3.5:27b-q4_K_M",
        model_identity_digest=model_digest,
        client=client,
    )

    with pytest.raises(V2ProviderFailure) as captured:
        await provider.generate(plan=item, brief=brief, attempt_index=1)

    assert captured.value.attempt.failure_class is ProviderFailureClass.CONFIGURATION_PERMANENT
    assert any("cleanup also failed" in note for note in captured.value.__notes__)


def test_docker_v2_mutator_treats_unstructured_worker_failure_as_ambiguous() -> None:
    failure_class, status = DockerOllamaV2MutationProvider._worker_failure(
        b"plain unclassified failure"
    )
    assert failure_class is ProviderFailureClass.AMBIGUOUS
    assert status is None
