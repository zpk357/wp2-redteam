from __future__ import annotations

import json
import urllib.error
from io import BytesIO

import pytest

from sandbox.engine.case_source import TemplateCaseSource
from sandbox.mutation.config import MutationProviderConfig
from sandbox.mutation.exceptions import MutationProviderError, MutationProviderFailureKind
from sandbox.mutation.models import (
    MutationPlan,
    MutationProviderKind,
    MutationSeed,
    PlannedMutation,
    RawMutationBatch,
    RawMutationCandidate,
)
from sandbox.mutation.normalizer import prompt_digest
from sandbox.mutation.providers.ollama import OllamaMutationProvider


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _size: int) -> bytes:
        return self.payload


async def test_ollama_provider_parses_strict_structured_batch() -> None:
    raw_batch = RawMutationBatch(
        candidates=[
            RawMutationCandidate(
                prompt="变异 Prompt",
                operator_id="roleplay_wrapper",
                target_risks=["unauthorized_file_read"],
            )
        ]
    )
    envelope = json.dumps(
        {
            "message": {"content": raw_batch.model_dump_json()},
            "prompt_eval_count": 17,
            "eval_count": 9,
            "total_duration": 123456,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    tags = json.dumps(
        {"models": [{"name": "local-model", "digest": "a" * 64}]}
    ).encode("utf-8")

    chat_payload = {}

    def opener(request, *, timeout):
        assert timeout == 10
        if request.full_url == "http://127.0.0.1:11434/api/tags":
            return FakeResponse(tags)
        assert request.full_url == "http://127.0.0.1:11434/api/chat"
        chat_payload.update(json.loads(request.data))
        return FakeResponse(envelope)

    config = MutationProviderConfig(
        kind=MutationProviderKind.OLLAMA,
        provider_version="ollama-mutator-v1",
        model_name="local-model",
        model_digest="sha256:" + "a" * 64,
        endpoint="http://127.0.0.1:11434",
        timeout_seconds=10,
    )
    provider = OllamaMutationProvider(config, opener=opener)
    case = TemplateCaseSource().generate("path-absolute-001", seed=42)
    seed = MutationSeed(
        seed_id=case.case_id,
        case=case,
        prompt_sha256=prompt_digest(case.prompt),
    )
    plan = MutationPlan(
        plan_id="sha256:" + "1" * 64,
        feedback_digest="sha256:" + "2" * 64,
        items=[
            PlannedMutation(
                operator_id="roleplay_wrapper",
                target_risks=["unauthorized_file_read"],
                target_depths={"unauthorized_file_read": 1},
                requested_count=1,
                initial_priority=1.0,
            )
        ],
        oversample_count=1,
    )

    result = await provider.generate(seed, plan, count=1, random_seed=42)

    assert result.candidates[0].prompt == "变异 Prompt"
    assert result.prompt_eval_count == 17
    assert result.eval_count == 9
    assert result.total_duration_ns == 123456
    assert result.response_digest.startswith("sha256:")
    assert provider.model_digest == "sha256:" + "a" * 64
    assert chat_payload["think"] is False
    assert chat_payload["options"]["num_predict"] == 288
    assert "maxLength" not in json.dumps(chat_payload["format"])


async def test_ollama_provider_classifies_and_audits_truncated_json() -> None:
    tags = json.dumps(
        {"models": [{"name": "local-model", "digest": "a" * 64}]}
    ).encode("utf-8")
    envelope = json.dumps(
        {
            "message": {"content": '{"candidates":[{"prompt":"cut off"'},
            "done_reason": "length",
        }
    ).encode("utf-8")

    def opener(request, *, timeout):
        assert timeout == 10
        if request.full_url == "http://127.0.0.1:11434/api/tags":
            return FakeResponse(tags)
        return FakeResponse(envelope)

    provider = OllamaMutationProvider(
        MutationProviderConfig(
            kind=MutationProviderKind.OLLAMA,
            provider_version="ollama-mutator-v1",
            model_name="local-model",
            model_digest="sha256:" + "a" * 64,
            endpoint="http://127.0.0.1:11434",
            timeout_seconds=10,
        ),
        opener=opener,
    )
    case = TemplateCaseSource().generate("path-absolute-001", seed=42)
    seed = MutationSeed(
        seed_id=case.case_id,
        case=case,
        prompt_sha256=prompt_digest(case.prompt),
    )
    plan = MutationPlan(
        plan_id="sha256:" + "1" * 64,
        feedback_digest="sha256:" + "2" * 64,
        items=[
            PlannedMutation(
                operator_id="roleplay_wrapper",
                target_risks=["unauthorized_file_read"],
                target_depths={"unauthorized_file_read": 1},
                requested_count=1,
                initial_priority=1.0,
            )
        ],
        oversample_count=4,
    )

    with pytest.raises(MutationProviderError) as captured:
        await provider.generate(seed, plan, count=4, random_seed=42)

    error = captured.value
    assert error.kind == MutationProviderFailureKind.TRUNCATED
    assert error.recoverable is True
    assert error.request_digest is not None
    assert error.response_digest is not None
    assert error.response_bytes == len(envelope)
    assert error.done_reason == "length"
    assert "cut off" in error.response_summary


def test_ollama_provider_rejects_model_digest_drift() -> None:
    tags = json.dumps(
        {"models": [{"name": "local-model", "digest": "b" * 64}]}
    ).encode("utf-8")


    def opener(request, *, timeout):
        assert request.full_url == "http://127.0.0.1:11434/api/tags"
        assert timeout == 10
        return FakeResponse(tags)

    config = MutationProviderConfig(
        kind=MutationProviderKind.OLLAMA,
        provider_version="ollama-mutator-v1",
        model_name="local-model",
        model_digest="sha256:" + "a" * 64,
        endpoint="http://127.0.0.1:11434",
        timeout_seconds=10,
    )

    with pytest.raises(MutationProviderError, match="does not match"):
        OllamaMutationProvider(config, opener=opener)


@pytest.mark.parametrize(
    ("status_code", "recoverable"),
    [
        (408, True),
        (413, True),
        (429, True),
        (500, True),
        (501, False),
        (502, True),
        (503, True),
        (504, True),
        (505, False),
    ],
)
async def test_ollama_provider_uses_closed_http_recovery_allowlist(
    status_code: int,
    recoverable: bool,
) -> None:
    tags = json.dumps(
        {"models": [{"name": "local-model", "digest": "a" * 64}]}
    ).encode("utf-8")

    def opener(request, *, timeout):
        assert timeout == 10
        if request.full_url == "http://127.0.0.1:11434/api/tags":
            return FakeResponse(tags)
        raise urllib.error.HTTPError(
            request.full_url,
            status_code,
            "failure",
            {},
            BytesIO(b'{"error":"failure"}'),
        )

    provider = OllamaMutationProvider(
        MutationProviderConfig(
            kind=MutationProviderKind.OLLAMA,
            provider_version="ollama-mutator-v1",
            model_name="local-model",
            model_digest="sha256:" + "a" * 64,
            endpoint="http://127.0.0.1:11434",
            timeout_seconds=10,
        ),
        opener=opener,
    )
    case = TemplateCaseSource().generate("path-absolute-001", seed=42)
    seed = MutationSeed(
        seed_id=case.case_id,
        case=case,
        prompt_sha256=prompt_digest(case.prompt),
    )
    plan = MutationPlan(
        plan_id="sha256:" + "1" * 64,
        feedback_digest="sha256:" + "2" * 64,
        items=[
            PlannedMutation(
                operator_id="roleplay_wrapper",
                target_risks=["unauthorized_file_read"],
                target_depths={"unauthorized_file_read": 1},
                requested_count=1,
                initial_priority=1.0,
            )
        ],
        oversample_count=1,
    )

    with pytest.raises(MutationProviderError) as captured:
        await provider.generate(seed, plan, count=1, random_seed=42)

    assert captured.value.http_status == status_code
    assert captured.value.recoverable is recoverable
