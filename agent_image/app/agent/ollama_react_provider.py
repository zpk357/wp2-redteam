"""Native Ollama tool-calling provider for the TRACE-G React loop."""

from __future__ import annotations

import asyncio
import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable

from app.agent.react_contract import ReactMessage, ReactToolCall, ReactTurn
from app.protocol import ModelOptions
from sandbox.protocol import normalize_sha256_digest
from sandbox.replay.digests import sha256_bytes
from sandbox.tool_contracts import ToolSpec

TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class OllamaReactProviderError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        audit: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.audit = audit or {}


class OllamaReactProvider:
    max_response_bytes = 1024 * 1024

    def __init__(
        self,
        options: ModelOptions,
        *,
        opener: Callable = urllib.request.urlopen,
    ) -> None:
        if options.endpoint is None or options.model_digest is None:
            raise ValueError("Ollama endpoint and locked model digest are required")
        self.options = options
        self.endpoint = options.endpoint.rstrip("/")
        self.opener = opener
        self.version = f"ollama-react:{options.model_name}@{options.model_digest}"
        self._digest_verified = False
        self._verification_lock = asyncio.Lock()
        self.last_token_usage: dict[str, int] | None = None

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        await self._ensure_digest_verified()
        payload = {
            "model": self.options.model_name,
            "messages": [self._message_payload(message) for message in messages],
            "tools": [self._tool_payload(tool) for tool in tools],
            "stream": False,
            "think": True,
            "options": {
                "num_ctx": 8192,
                "num_predict": 4096,
                "temperature": 0.2,
                "top_p": 0.8,
                "top_k": 20,
                **({"seed": seed} if seed is not None else {}),
            },
        }
        raw, audit = await asyncio.to_thread(self._request_json, "/api/chat", payload)
        self.last_token_usage = self._parse_token_usage(raw, audit=audit)
        return self._parse_turn(raw, audit=audit)

    @staticmethod
    def _parse_token_usage(envelope: dict, *, audit: dict | None = None) -> dict[str, int]:
        try:
            prompt_tokens = envelope["prompt_eval_count"]
            completion_tokens = envelope["eval_count"]
            if (
                not isinstance(prompt_tokens, int)
                or isinstance(prompt_tokens, bool)
                or prompt_tokens < 0
                or not isinstance(completion_tokens, int)
                or isinstance(completion_tokens, bool)
                or completion_tokens < 0
            ):
                raise TypeError("token counts must be non-negative integers")
        except (KeyError, TypeError) as exc:
            raise OllamaReactProviderError(
                "ollama_response_integrity_error",
                "Ollama response is missing valid token usage",
                audit=audit,
            ) from exc
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }

    async def _ensure_digest_verified(self) -> None:
        if self._digest_verified:
            return
        async with self._verification_lock:
            if self._digest_verified:
                return
            raw, audit = await asyncio.to_thread(self._request_json, "/api/tags", None)
            try:
                models = raw["models"]
                if not isinstance(models, list):
                    raise TypeError("models is not a list")
                matches = [
                    item
                    for item in models
                    if isinstance(item, dict) and item.get("name") == self.options.model_name
                ]
            except (KeyError, TypeError) as exc:
                raise OllamaReactProviderError(
                    "ollama_response_integrity_error",
                    "Ollama model registry response is invalid",
                    audit=audit,
                ) from exc
            if len(matches) != 1:
                raise OllamaReactProviderError(
                    "ollama_model_digest_mismatch",
                    "Ollama model digest does not match the locked profile",
                    audit=audit,
                )
            try:
                actual = normalize_sha256_digest(matches[0].get("digest"))
            except (TypeError, ValueError) as exc:
                raise OllamaReactProviderError(
                    "ollama_response_integrity_error",
                    "Ollama model registry digest is invalid",
                    audit=audit,
                ) from exc
            if actual != self.options.model_digest:
                raise OllamaReactProviderError(
                    "ollama_model_digest_mismatch",
                    "Ollama model digest does not match the locked profile",
                    audit=audit,
                )
            self._digest_verified = True

    def _request_json(self, path: str, payload: dict | None) -> tuple[dict, dict]:
        request_body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        request_digest = sha256_bytes(request_body) if request_body is not None else None
        request = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=request_body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        try:
            with self.opener(request, timeout=self.options.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
                http_status = getattr(response, "status", 200)
        except TimeoutError as exc:
            raise OllamaReactProviderError(
                "RuntimeTimeoutError",
                "Ollama request timed out",
                audit=self._audit(request_digest=request_digest),
            ) from exc
        except urllib.error.HTTPError as exc:
            try:
                error_raw = exc.read(self.max_response_bytes + 1)
            except (OSError, ValueError):
                error_raw = b""
            truncated = len(error_raw) > self.max_response_bytes
            code = (
                "RuntimeTransportError"
                if exc.code in TRANSIENT_HTTP_STATUS_CODES
                else "ollama_provider_configuration_error"
            )
            raise OllamaReactProviderError(
                code,
                f"Ollama HTTP status {exc.code}",
                audit=self._audit(
                    request_digest=request_digest,
                    raw=error_raw,
                    http_status=exc.code,
                    truncated=truncated,
                ),
            ) from exc
        except http.client.IncompleteRead as exc:
            partial = bytes(exc.partial)
            raise OllamaReactProviderError(
                "ollama_response_truncated",
                "Ollama response ended before the declared response length",
                audit=self._audit(
                    request_digest=request_digest,
                    raw=partial,
                    truncated=True,
                ),
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise OllamaReactProviderError(
                "RuntimeTransportError",
                "Ollama transport failed",
                audit=self._audit(request_digest=request_digest),
            ) from exc
        audit = self._audit(
            request_digest=request_digest,
            raw=raw,
            http_status=http_status,
            truncated=len(raw) > self.max_response_bytes,
        )
        if len(raw) > self.max_response_bytes:
            raise OllamaReactProviderError(
                "ollama_response_truncated",
                "Ollama response exceeded the byte limit",
                audit=audit,
            )
        try:
            value = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise OllamaReactProviderError(
                "ollama_response_integrity_error",
                "Ollama returned invalid JSON",
                audit=audit,
            ) from exc
        if not isinstance(value, dict):
            raise OllamaReactProviderError(
                "ollama_response_integrity_error",
                "Ollama returned a non-object response",
                audit=audit,
            )
        return value, audit

    @staticmethod
    def _message_payload(message: ReactMessage) -> dict:
        payload: dict = {"role": message.role, "content": message.content}
        if message.role == "assistant" and message.tool_calls:
            payload["tool_calls"] = [
                {
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in message.tool_calls
            ]
        if message.role == "tool":
            payload["tool_name"] = message.name
            payload["content"] = json.dumps(
                message.content, ensure_ascii=False, separators=(",", ":")
            )
        return payload

    @staticmethod
    def _tool_payload(tool: ToolSpec) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.arguments_model.model_json_schema(),
            },
        }

    @staticmethod
    def _parse_turn(envelope: dict, *, audit: dict | None = None) -> ReactTurn:
        try:
            message = envelope["message"]
            if not isinstance(message, dict):
                raise TypeError("message is not an object")
            content = message.get("content")
            if content is None:
                content = ""
            if not isinstance(content, str):
                raise TypeError("message content is not a string")
            raw_calls = message.get("tool_calls") or []
            if not isinstance(raw_calls, list):
                raise TypeError("tool_calls is not a list")
            calls = [
                ReactToolCall(
                    name=raw["function"]["name"],
                    arguments=raw["function"].get("arguments") or {},
                )
                for raw in raw_calls
            ]
            return ReactTurn(
                assistant_text=content,
                tool_calls=calls,
                stop_reason=envelope.get("done_reason"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OllamaReactProviderError(
                "ollama_response_integrity_error",
                "Ollama returned an invalid tool-calling response",
                audit=audit,
            ) from exc

    @classmethod
    def _audit(
        cls,
        *,
        request_digest: str | None,
        raw: bytes = b"",
        http_status: int | None = None,
        truncated: bool = False,
    ) -> dict:
        return {
            "request_digest": request_digest,
            "response_digest": sha256_bytes(raw) if raw else None,
            "response_bytes": len(raw),
            "response_truncated": truncated,
            "http_status": http_status,
            "response_summary": cls._response_summary(raw.decode("utf-8", "replace")),
        }

    @staticmethod
    def _response_summary(content: str) -> str:
        normalized = " ".join(content.split())
        if len(normalized) <= 480:
            return normalized
        return normalized[:240] + " ... " + normalized[-240:]
