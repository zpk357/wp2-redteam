from __future__ import annotations

import http.client
import json
import urllib.error
from io import BytesIO

import pytest
from app.agent.ollama_react_provider import (
    OllamaReactProvider,
    OllamaReactProviderError,
)
from app.agent.react_contract import SUBMIT_TOOL_SPEC, ReactMessage
from app.protocol import ModelOptions, ModelProvider
from app.replay.react_decision_recorder import ReactDecisionRecorder, RecordedReactProvider
from app.tools.base import ToolRegistry
from sandbox.replay.models import RECORDED_MODEL_TOKEN_USAGE_KEY

DIGEST = "sha256:" + "a" * 64


class Response:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.payload = json.dumps(payload).encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class Opener:
    def __init__(self, chat_payload: object) -> None:
        self.chat_payload = chat_payload
        self.requests = []

    def __call__(self, request, timeout: int):
        self.requests.append((request, timeout))
        if request.full_url.endswith("/api/tags"):
            return Response({"models": [{"name": "qwen3:8b", "digest": DIGEST}]})
        return Response(self.chat_payload)


def options() -> ModelOptions:
    return ModelOptions(
        provider=ModelProvider.OLLAMA,
        model_name="qwen3:8b",
        model_digest=DIGEST,
        endpoint="http://ollama:11434",
    )


async def test_native_tool_call_and_tool_result_protocol() -> None:
    opener = Opener(
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "search_email", "arguments": {"query": "Bob"}}}
                ],
            },
            "done_reason": "stop",
            "prompt_eval_count": 123,
            "eval_count": 45,
        }
    )
    provider = OllamaReactProvider(options(), opener=opener)
    registry = ToolRegistry()
    registry.enable_workspace_scenario("trace-workspace-meeting-injection-v1")
    messages = (
        ReactMessage(role="user", content="Find Bob"),
        ReactMessage(
            role="tool",
            name="read_email",
            call_id="call-1",
            content={"allowed": True, "output": {"email_id": "email-bob-001"}},
        ),
    )

    turn = await provider.generate(messages, (*registry.specs, SUBMIT_TOOL_SPEC), seed=42)

    assert turn.tool_calls[0].name == "search_email"
    assert provider.last_token_usage == {
        "prompt_tokens": 123,
        "completion_tokens": 45,
    }
    chat_request = opener.requests[-1][0]
    payload = json.loads(chat_request.data)
    assert payload["messages"][-1]["role"] == "tool"
    assert payload["messages"][-1]["tool_name"] == "read_email"
    assert payload["options"]["seed"] == 42
    assert payload["think"] is True
    assert payload["options"] == {
        "num_ctx": 8192,
        "num_predict": 4096,
        "temperature": 0.2,
        "top_k": 20,
        "top_p": 0.8,
        "seed": 42,
    }
    assert {tool["function"]["name"] for tool in payload["tools"]} >= {
        "search_email",
        "submit",
    }


async def test_digest_mismatch_is_not_transient() -> None:
    opener = Opener({"message": {"role": "assistant", "content": "done"}})
    provider = OllamaReactProvider(
        options().model_copy(update={"model_digest": "sha256:" + "b" * 64}),
        opener=opener,
    )

    with pytest.raises(OllamaReactProviderError) as error:
        await provider.generate((ReactMessage(role="user", content="test"),), (), seed=1)

    assert error.value.error_code == "ollama_model_digest_mismatch"


async def test_recording_preserves_usage_without_changing_replayed_turn() -> None:
    opener = Opener(
        {
            "message": {"role": "assistant", "content": "done"},
            "done_reason": "stop",
            "prompt_eval_count": 12,
            "eval_count": 3,
        }
    )
    recorder = ReactDecisionRecorder(OllamaReactProvider(options(), opener=opener))
    messages = (ReactMessage(role="user", content="test"),)

    expected = await recorder.generate(messages, (), seed=1)
    recorded = recorder.decisions[0]
    replayed = await RecordedReactProvider(recorder.decisions).generate(
        messages, (), seed=1
    )

    assert replayed == expected
    assert recorded.action[RECORDED_MODEL_TOKEN_USAGE_KEY] == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
    }


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (408, "RuntimeTransportError"),
        (400, "ollama_provider_configuration_error"),
        (413, "ollama_provider_configuration_error"),
        (429, "RuntimeTransportError"),
        (500, "RuntimeTransportError"),
        (501, "ollama_provider_configuration_error"),
        (502, "RuntimeTransportError"),
        (503, "RuntimeTransportError"),
        (504, "RuntimeTransportError"),
        (505, "ollama_provider_configuration_error"),
    ],
)
async def test_http_recovery_uses_closed_allowlist(status: int, code: str) -> None:
    def raising(status: int):
        def opener(request, timeout):
            if request.full_url.endswith("/api/tags"):
                return Response({"models": [{"name": "qwen3:8b", "digest": DIGEST}]})
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "error",
                {},
                BytesIO(b'{"error":"limited failure detail"}'),
            )

        return opener

    provider = OllamaReactProvider(options(), opener=raising(status))
    with pytest.raises(OllamaReactProviderError) as error:
        await provider.generate((ReactMessage(role="user", content="test"),), (), seed=1)

    assert error.value.error_code == code
    assert error.value.audit["http_status"] == status
    assert error.value.audit["request_digest"].startswith("sha256:")
    assert error.value.audit["response_bytes"] > 0
    assert error.value.audit["response_digest"].startswith("sha256:")
    assert error.value.audit["response_summary"] == '{"error":"limited failure detail"}'


async def test_malformed_message_is_classified_as_response_integrity() -> None:
    provider = OllamaReactProvider(options(), opener=Opener({"message": None}))

    with pytest.raises(OllamaReactProviderError) as error:
        await provider.generate((ReactMessage(role="user", content="test"),), (), seed=1)

    assert error.value.error_code == "ollama_response_integrity_error"
    assert error.value.audit["response_bytes"] > 0
    assert error.value.audit["response_truncated"] is False


async def test_incomplete_http_body_is_classified_as_truncation() -> None:
    class IncompleteResponse(Response):
        def read(self, limit: int) -> bytes:
            del limit
            raise http.client.IncompleteRead(b'{"message":', 100)

    class IncompleteOpener(Opener):
        def __call__(self, request, timeout: int):
            self.requests.append((request, timeout))
            if request.full_url.endswith("/api/tags"):
                return Response({"models": [{"name": "qwen3:8b", "digest": DIGEST}]})
            return IncompleteResponse({})

    provider = OllamaReactProvider(options(), opener=IncompleteOpener({}))

    with pytest.raises(OllamaReactProviderError) as error:
        await provider.generate((ReactMessage(role="user", content="test"),), (), seed=1)

    assert error.value.error_code == "ollama_response_truncated"
    assert error.value.audit["response_truncated"] is True
    assert error.value.audit["response_bytes"] == len(b'{"message":')
