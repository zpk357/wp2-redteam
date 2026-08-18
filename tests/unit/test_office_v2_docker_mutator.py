from __future__ import annotations

import json

import pytest

from sandbox.mutation.v2_brief import build_minimal_fact_brief
from sandbox.mutation.v2_docker import DockerOllamaV2MutationProvider
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
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.removed = False

    def wait(self, *, timeout: int) -> dict[str, int]:
        assert timeout >= 30
        return {"StatusCode": 0}

    def logs(self, *, stdout: bool, stderr: bool) -> bytes:
        return self.response if stdout and not stderr else b""

    def remove(self, *, force: bool) -> None:
        assert force is True
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
        client=client,
    )

    result = await provider.generate(plan=item, brief=brief, attempt_index=1)

    assert result.candidate.slot_values[0].generated_content == "variant"
    assert result.attempt.input_tokens == 20
    assert client.containers.run_kwargs["network_mode"] == "none"
    assert client.containers.run_kwargs["read_only"] is True
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
