#!/usr/bin/env python3
"""Load a locked Ollama model before a time-bounded Agent validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


class ModelWarmupError(RuntimeError):
    """Ollama did not complete a valid warmup response."""


def warm_model(
    endpoint: str,
    model_name: str,
    *,
    timeout: float = 180.0,
    keep_alive: str = "15m",
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "model": model_name,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "stream": False,
            "think": False,
            "keep_alive": keep_alive,
            "options": {"temperature": 0, "num_predict": 16, "seed": 0},
        },
        separators=(",", ":"),
    ).encode()
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/api/chat",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        raise ModelWarmupError("Ollama model warmup request failed") from exc
    if len(raw) > 1024 * 1024:
        raise ModelWarmupError("Ollama model warmup response exceeded the byte limit")
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ModelWarmupError("Ollama model warmup returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ModelWarmupError("Ollama model warmup response is not an object")
    message = value.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if value.get("done") is not True or not isinstance(content, str) or not content.strip():
        raise ModelWarmupError("Ollama model warmup response is incomplete")
    return {
        "schema_version": "1.0",
        "passed": True,
        "model_name": model_name,
        "response_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "response_bytes": len(raw),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://ollama:11434")
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--keep-alive", default="15m")
    args = parser.parse_args()
    try:
        result = warm_model(
            args.endpoint,
            args.model,
            timeout=args.timeout,
            keep_alive=args.keep_alive,
        )
        print(json.dumps(result, indent=2))
        return 0
    except ModelWarmupError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
