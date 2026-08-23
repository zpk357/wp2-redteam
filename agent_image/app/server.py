"""FastAPI transport for the single-execution Runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.protocol import (
    EventsRequest,
    ExecutionIdRequest,
    ExecutionRequest,
    JsonRpcRequest,
    rpc_error,
    rpc_result,
)
from app.runtime import RuntimeRpcError, RuntimeState
from sandbox.replay.models import ReplayCheckpointsRequest, ReplayForkRequest, ReplayRequest

app = FastAPI(title="TRACE-G Agent Runtime", docs_url=None, redoc_url=None)
runtime = RuntimeState(expected_execution_id=os.environ.get("EXECUTION_ID"))
capability_token = os.environ.get("SANDBOX_TOKEN", "development-only-token")
MAX_RPC_REQUEST_BYTES = 1024 * 1024


@app.get("/health", response_model=None)
async def health() -> dict[str, str | bool] | JSONResponse:
    result: dict[str, str | bool] = {
        "status": "ok",
        "adapter": "trace_react_v2",
        "runtime_version": "0.3.0",
        "protocol_version": "1",
    }
    if os.environ.get("TRACE_G_FORMAL_AGENT") == "1":
        if os.environ.get("TRACE_G_AGENT_RUNTIME") == "deepseek_harness":
            result.update(
                {
                    "formal_agent": True,
                    "model_ready": False,
                    "runtime_mode": "deterministic_fixture",
                    "agent_framework": "deepseek_harness",
                    "agent_runtime_version": "deepseek-harness-h4-v1",
                }
            )
            return result
        if os.environ.get("TRACE_G_STAGE7_DETERMINISTIC_PROVIDER") == "1":
            expected = {
                "formal_agent": True,
                "model_name": os.environ.get("TRACE_G_MODEL_NAME"),
                "model_digest": os.environ.get("TRACE_G_MODEL_DIGEST"),
                "model_ready": False,
                "runtime_mode": os.environ.get("TRACE_G_RUNTIME_MODE", "live"),
                "agent_framework": "langgraph",
                "provider_profile": "stage7_deterministic",
            }
            result.update(expected)
            return result
        status_path = Path(
            os.environ.get("TRACE_G_STATUS_PATH", "/tmp/agent-qwen-status.json")
        )
        try:
            identity = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return JSONResponse(
                {**result, "status": "not_ready", "formal_agent": True},
                status_code=503,
            )
        expected = {
            "status": "ready",
            "formal_agent": True,
            "model_name": os.environ.get("TRACE_G_MODEL_NAME"),
            "model_digest": os.environ.get("TRACE_G_MODEL_DIGEST"),
            "ollama_endpoint": (
                "http://127.0.0.1:11434"
                if os.environ.get("TRACE_G_RUNTIME_MODE", "live") == "live"
                else None
            ),
            "model_ready": os.environ.get("TRACE_G_RUNTIME_MODE", "live") == "live",
            "runtime_mode": os.environ.get("TRACE_G_RUNTIME_MODE", "live"),
            "agent_framework": "langgraph",
        }
        if not isinstance(identity, dict) or any(
            identity.get(key) != value for key, value in expected.items()
        ):
            return JSONResponse(
                {**result, "status": "not_ready", "formal_agent": True},
                status_code=503,
            )
        result.update(expected)
    return result


@app.post("/rpc")
async def rpc(request: Request) -> JSONResponse:
    body = await request.body()
    if len(body) > MAX_RPC_REQUEST_BYTES:
        return JSONResponse(rpc_error(None, -32005, "request body too large"))
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(rpc_error(None, -32700, "parse error"))

    request_id = _request_id(payload)
    if request.headers.get("X-Protocol-Version") != "1":
        return JSONResponse(rpc_error(request_id, -32600, "unsupported protocol version"))
    if request.headers.get("X-Sandbox-Token") != capability_token:
        return JSONResponse(rpc_error(request_id, -32001, "unauthorized"))

    try:
        envelope = JsonRpcRequest.model_validate(payload)
        if envelope.jsonrpc != "2.0":
            return JSONResponse(rpc_error(envelope.id, -32600, "invalid JSON-RPC version"))
        result = await _dispatch(envelope.method, envelope.params)
        return JSONResponse(rpc_result(envelope.id, result))
    except ValidationError:
        return JSONResponse(rpc_error(request_id, -32602, "invalid params"))
    except RuntimeRpcError as exc:
        return JSONResponse(rpc_error(request_id, exc.code, exc.message))
    except Exception:
        return JSONResponse(rpc_error(request_id, -32603, "internal error"))


def _request_id(payload: object) -> str | int | None:
    if not isinstance(payload, dict):
        return None
    request_id = payload.get("id")
    if isinstance(request_id, bool):
        return None
    return request_id if isinstance(request_id, (str, int)) else None


async def _dispatch(method: str, params: dict):
    if method == "execution.submit":
        return await runtime.submit(ExecutionRequest.model_validate(params))
    if method == "execution.get":
        parsed = ExecutionIdRequest.model_validate(params)
        return (await runtime.get(parsed.execution_id)).model_dump(mode="json")
    if method == "execution.events":
        parsed = EventsRequest.model_validate(params)
        return await runtime.events(parsed.execution_id, parsed.after_sequence, parsed.limit)
    if method == "execution.cancel":
        parsed = ExecutionIdRequest.model_validate(params)
        return await runtime.cancel(parsed.execution_id)
    if method == "replay.submit":
        return await runtime.submit_replay(ReplayRequest.model_validate(params))
    if method == "replay.checkpoints":
        return await runtime.checkpoints(ReplayCheckpointsRequest.model_validate(params))
    if method == "replay.fork":
        return await runtime.submit_fork(ReplayForkRequest.model_validate(params))
    raise RuntimeRpcError(-32601, "method not found")
