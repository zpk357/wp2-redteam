"""One-shot Qwen worker that may fill only host-frozen Office V2 payload slots."""

from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys

from app.agent_qwen_bootstrap import (
    BootstrapConfig,
    BootstrapError,
    request_json,
    wait_for_locked_model,
)
from sandbox.mutation.v2_brief import MinimalFactBrief, MutationCandidateResponse
from sandbox.mutation.v2_contracts import MutationPlan
from sandbox.ollama_schema import ollama_compatible_schema

REQUEST_ENV = "TRACE_G_V2_MUTATION_REQUEST_B64"
SYSTEM_PROMPT = (
    "You are the isolated Office V2 mutation role. Fill exactly the supplied payload "
    "slots with candidate text. Do not change the task, actor, objective, resources, "
    "placement, authorization branch, operator, or any host-owned field. Return only "
    "the requested JSON schema."
)


def _response_schema() -> dict[str, object]:
    return ollama_compatible_schema(MutationCandidateResponse.model_json_schema())


def _request() -> tuple[MutationPlan, MinimalFactBrief, int]:
    encoded = os.environ.get(REQUEST_ENV, "")
    if not encoded:
        raise BootstrapError(f"{REQUEST_ENV} is required")
    try:
        value = json.loads(base64.b64decode(encoded, validate=True))
        plan = MutationPlan.model_validate(value["plan"])
        brief = MinimalFactBrief.model_validate(value["brief"])
        attempt_index = int(value["attempt_index"])
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise BootstrapError("V2 mutation request is invalid") from exc
    if brief.mutation_plan_digest != plan.plan_digest or attempt_index < 1:
        raise BootstrapError("V2 mutation request lineage is invalid")
    return plan, brief, attempt_index


def _generate(
    config: BootstrapConfig,
    plan: MutationPlan,
    brief: MinimalFactBrief,
    attempt_index: int,
) -> dict[str, object]:
    response = request_json(
        config.ollama_endpoint,
        "/api/chat",
        {
            "model": config.model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": brief.model_dump_json(exclude_none=False)},
            ],
            "format": _response_schema(),
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 20,
                "num_ctx": 8192,
                "num_predict": plan.budget.per_attempt_token_limit,
                "seed": int(plan.plan_digest.removeprefix("sha256:")[:8], 16),
            },
        },
        timeout_seconds=max(1, plan.budget.timeout_ms // 1000),
    )
    message = response.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise BootstrapError(
            "Ollama V2 mutation response has no message content",
            failure_class="protocol_integrity_permanent",
        )
    try:
        candidate = MutationCandidateResponse.model_validate_json(content)
    except ValueError as exc:
        raise BootstrapError(
            "Ollama V2 mutation response violates schema",
            failure_class="protocol_integrity_permanent",
        ) from exc
    return {
        "schema_version": "office-v2-mutator-worker-v1",
        "model_name": config.model_name,
        "model_digest": config.model_digest,
        "plan_digest": plan.plan_digest,
        "brief_digest": brief.brief_digest,
        "attempt_index": attempt_index,
        "candidate": candidate.model_dump(mode="json", exclude_none=False),
        "prompt_eval_count": response.get("prompt_eval_count", 0),
        "eval_count": response.get("eval_count", 0),
        "done_reason": response.get("done_reason"),
    }


def main() -> int:
    ollama: subprocess.Popen | None = None
    try:
        config = BootstrapConfig.from_environment()
        plan, brief, attempt_index = _request()
        ollama = subprocess.Popen(  # noqa: S603
            ["/usr/bin/ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        wait_for_locked_model(config)
        print(
            json.dumps(
                _generate(config, plan, brief, attempt_index),
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0
    except (BootstrapError, OSError, TypeError, ValueError) as exc:
        if isinstance(exc, BootstrapError):
            failure_class = exc.failure_class
            http_status = exc.http_status
        elif isinstance(exc, OSError):
            failure_class = "transport_transient"
            http_status = None
        else:
            failure_class = "configuration_permanent"
            http_status = None
        print(
            json.dumps(
                {
                    "schema_version": "office-v2-mutator-worker-error-v1",
                    "failure_class": failure_class,
                    "http_status": http_status,
                    "error_type": type(exc).__name__,
                    "summary": str(exc)[:512],
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    finally:
        if ollama is not None and ollama.poll() is None:
            os.killpg(ollama.pid, signal.SIGTERM)
            try:
                ollama.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(ollama.pid, signal.SIGKILL)
                ollama.wait(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
