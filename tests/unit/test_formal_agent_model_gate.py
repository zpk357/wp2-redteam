from __future__ import annotations

import pytest
from app.adapter.base import AdapterConfigurationError
from app.adapter.factory import AdapterFactory
from app.adapter.langgraph_react_runtime import LangGraphReactRuntime
from app.protocol import ExecutionRequest, ModelOptions, ModelProvider

from sandbox.scenarios.office_v2.cli_entry import (
    build_office_v2_public_request,
    office_v2_public_case,
)

DIGEST = "sha256:" + "a" * 64


def request(*, endpoint: str = "http://127.0.0.1:11434") -> ExecutionRequest:
    value = build_office_v2_public_request(
        office_v2_public_case("clean.t2.delta"),
        execution_id="exec-formal",
        model_name="qwen3:8b",
        model_digest=DIGEST,
        seed=0,
        max_steps=40,
        timeout_seconds=120,
    )
    if endpoint == "http://127.0.0.1:11434":
        return value
    model = value.model.model_copy(update={"endpoint": endpoint})
    envelope = value.office_v2_execution.model_copy(update={"model_identity": model})
    return value.model_copy(update={"model": model, "office_v2_execution": envelope})


@pytest.fixture(autouse=True)
def formal_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_G_FORMAL_AGENT", "1")
    monkeypatch.setenv("TRACE_G_MODEL_NAME", "qwen3:8b")
    monkeypatch.setenv("TRACE_G_MODEL_DIGEST", DIGEST)
    monkeypatch.setenv("TRACE_G_OLLAMA_ENDPOINT", "http://127.0.0.1:11434")


def test_formal_agent_accepts_only_the_in_container_model_identity() -> None:
    adapter = AdapterFactory().create(request())

    assert isinstance(adapter, LangGraphReactRuntime)


@pytest.mark.parametrize(
    "candidate",
    [
        ExecutionRequest(
            execution_id="exec-formal",
            case_id="case-formal",
            prompt="Handle the office task.",
        ),
        request(endpoint="http://ollama:11434"),
        request().model_copy(
            update={
                "model": request().model.model_copy(
                    update={"model_digest": "sha256:" + "b" * 64}
                )
            }
        ),
    ],
)
def test_formal_agent_rejects_fake_external_or_wrong_digest_models(
    candidate: ExecutionRequest,
) -> None:
    with pytest.raises(AdapterConfigurationError):
        AdapterFactory().create(candidate)


def test_formal_agent_rejects_legacy_live_request_even_with_locked_model() -> None:
    legacy = ExecutionRequest(
        execution_id="exec-legacy-formal",
        case_id="legacy-case",
        prompt="Legacy request.",
        model=ModelOptions(
            provider=ModelProvider.OLLAMA,
            model_name="qwen3:8b",
            model_digest=DIGEST,
            endpoint="http://127.0.0.1:11434",
        ),
    )

    with pytest.raises(
        AdapterConfigurationError,
        match="requires an Office V2 envelope",
    ):
        AdapterFactory().create(legacy)
