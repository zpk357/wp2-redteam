from __future__ import annotations

import json

import pytest

from scripts.warm_ollama_model import ModelWarmupError, warm_model


class _Response:
    def __init__(self, payload: object) -> None:
        self._raw = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self._raw


def test_warmup_sends_a_bounded_deterministic_chat_request() -> None:
    observed = {}

    def opener(request, *, timeout):
        observed["request"] = json.loads(request.data)
        observed["timeout"] = timeout
        return _Response({"done": True, "message": {"content": "OK"}})

    result = warm_model("http://ollama:11434", "qwen3:8b", opener=opener)

    assert result["passed"] is True
    assert observed["timeout"] == 180.0
    assert observed["request"]["think"] is False
    assert observed["request"]["keep_alive"] == "15m"
    assert observed["request"]["options"] == {
        "temperature": 0,
        "num_predict": 16,
        "seed": 0,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"done": False, "message": {"content": "OK"}},
        {"done": True, "message": {"content": ""}},
        {"done": True},
    ],
)
def test_warmup_rejects_incomplete_responses(payload: object) -> None:
    with pytest.raises(ModelWarmupError, match="incomplete"):
        warm_model(
            "http://ollama:11434",
            "qwen3:8b",
            opener=lambda *_args, **_kwargs: _Response(payload),
        )


def test_warmup_rejects_non_object_json() -> None:
    with pytest.raises(ModelWarmupError, match="not an object"):
        warm_model(
            "http://ollama:11434",
            "qwen3:8b",
            opener=lambda *_args, **_kwargs: _Response([]),
        )
