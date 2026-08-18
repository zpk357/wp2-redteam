from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from sandbox.agent_prompts import (
    OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
    OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
)
from sandbox.coverage.models import CampaignCoverageFeedback, CoverageSaturationSummary
from sandbox.scenarios.office_docker_mutator import DockerOfficeMutationProvider
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX
from sandbox.scenarios.office_mutation import OfficeMutationPlanner
from sandbox.scenarios.office_mutation_batch import (
    OfficeMutationBatchPolicy,
    OfficeMutationSubBatchRequest,
)

IMAGE_ID = "sha256:" + "a" * 64
MODEL_DIGEST = "sha256:" + "b" * 64


def _feedback() -> CampaignCoverageFeedback:
    return CampaignCoverageFeedback(
        campaign_id="office-docker-mutator-test",
        taxonomy_version="risk-taxonomy-v1",
        taxonomy_digest="sha256:" + "1" * 64,
        risk_mapping_version="office-risk-mapping-v1",
        risk_mapping_digest="sha256:" + "2" * 64,
        risk_scope_version="office-risk-scope-v1",
        risk_scope_digest="sha256:" + "3" * 64,
        include_empty=True,
        observed_behavior_paths=0,
        saturation=CoverageSaturationSummary(
            observations=0,
            trailing_without_behavior_gain=0,
            max_without_behavior_gain=0,
            trailing_without_execution_risk_gain=0,
            max_without_execution_risk_gain=0,
            trailing_without_any_gain=0,
            max_without_any_gain=0,
        ),
    )


class _Container:
    def __init__(self, run_kwargs: dict) -> None:
        encoded = run_kwargs["environment"]["TRACE_G_MUTATION_REQUEST_B64"]
        self.request = json.loads(base64.b64decode(encoded, validate=True))
        self.removed = False

    def wait(self, *, timeout: int):
        assert timeout == 630
        return {"StatusCode": 0}

    def logs(self, *, stdout: bool, stderr: bool):
        if stderr:
            return b""
        response = {
            "schema_version": "1.0",
            "request_digest": self.request["request_digest"],
            "prompt_version": OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
            "prompt_digest": OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
            "model_name": "qwen3:8b",
            "model_digest": MODEL_DIGEST,
            "expressions": ["first semantic variant", "second semantic variant"],
            "done_reason": "stop",
        }
        return json.dumps(response).encode()

    def remove(self, *, force: bool) -> None:
        assert force is True
        self.removed = True


class _Containers:
    def __init__(self) -> None:
        self.kwargs: dict | None = None
        self.container: _Container | None = None

    def run(self, _image: str, **kwargs):
        self.kwargs = kwargs
        self.container = _Container(kwargs)
        return self.container


def _client():
    labels = {
        "org.trace-g.runtime": "self-contained-agent-qwen",
        "org.trace-g.model.name": "qwen3:8b",
        "org.trace-g.model.digest": MODEL_DIGEST,
        "org.trace-g.mutator-prompt.version": OFFICE_MUTATOR_SYSTEM_PROMPT_VERSION,
        "org.trace-g.mutator-prompt.digest": OFFICE_MUTATOR_SYSTEM_PROMPT_DIGEST,
    }
    containers = _Containers()
    return SimpleNamespace(
        images=SimpleNamespace(
            get=lambda _ref: SimpleNamespace(
                id=IMAGE_ID,
                attrs={"Config": {"Labels": labels}},
            )
        ),
        containers=containers,
    )


@pytest.mark.asyncio
async def test_docker_mutator_uses_locked_isolated_role_and_catalog_context() -> None:
    client = _client()
    provider = DockerOfficeMutationProvider(
        image_ref="agent:test",
        image_id=IMAGE_ID,
        model_name="qwen3:8b",
        model_digest=MODEL_DIGEST,
        timeout_seconds=600,
        client=client,
    )
    parent = OFFICE_V1_TEST_MATRIX.attack_cases[0]
    plan = OfficeMutationPlanner().plan(
        parent=parent,
        feedback=_feedback(),
        provider_identity=provider.identity,
        operator_id="target-preserving-expression-rewrite",
        random_seed=17,
        requested_count=2,
        max_output_tokens=1_024,
        expected_path="coverage-gap:data_exfiltration:depth-2",
    )
    request = OfficeMutationSubBatchRequest.create(
        plan=plan,
        policy=OfficeMutationBatchPolicy.create(),
        path="0",
        ordinal_offset=0,
        retry_index=0,
        requested_count=2,
    )

    result = await provider.mutate_sub_batch(plan, parent, request)

    assert len(result.candidates) == 2
    assert client.containers.kwargs["network_mode"] == "none"
    assert client.containers.kwargs["entrypoint"] == [
        "python",
        "-m",
        "app.office_mutator_worker",
    ]
    assert client.containers.container.removed is True
    worker_request = client.containers.container.request
    assert worker_request["feedback_digest"] == plan.feedback_digest
    assert worker_request["planned_context"]["attack_objective"]["objective_id"] == (
        plan.planned_components.objective_id
    )
    assert worker_request["planned_context"]["benign_task"]["task_id"] == (
        plan.planned_components.task_id
    )
