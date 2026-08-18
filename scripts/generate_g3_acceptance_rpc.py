"""Generate one bounded JSON-RPC request for the local 5.G3 Docker gate."""

from __future__ import annotations

import argparse
import base64
import json

from sandbox.protocol import ExecutionRequest, ModelOptions, ModelProvider
from sandbox.scenarios.models import AgentConfig, ExecutionBudget, TestCase
from sandbox.scenarios.office_episode import build_office_episode_initialization
from sandbox.scenarios.office_matrix import OFFICE_V1_TEST_MATRIX

MODEL_NAME = "qwen3:8b"
MODEL_DIGEST = "sha256:500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"
MODEL_ENDPOINT = "http://127.0.0.1:11434"


def formal_case(kind: str) -> TestCase:
    source = (
        OFFICE_V1_TEST_MATRIX.clean_cases[0]
        if kind == "clean"
        else OFFICE_V1_TEST_MATRIX.attack_cases[0]
    )
    payload = source.model_dump(mode="json")
    payload["agent"] = AgentConfig(
        provider="ollama",
        model_name=MODEL_NAME,
        model_digest=MODEL_DIGEST,
        endpoint=MODEL_ENDPOINT,
    ).model_dump(mode="json")
    payload["budget"] = ExecutionBudget(
        max_steps=source.budget.max_steps,
        timeout_seconds=600,
        max_output_tokens=source.budget.max_output_tokens,
    ).model_dump(mode="json")
    payload["content_digest"] = None
    return TestCase.model_validate(payload)


def execution_request(kind: str, execution_id: str) -> ExecutionRequest:
    case = formal_case(kind)
    initialization = build_office_episode_initialization(case)
    return ExecutionRequest(
        execution_id=execution_id,
        case_id=case.case_id,
        scenario_id=case.scenario.template_id,
        prompt=case.benign_task.instruction,
        max_steps=case.budget.max_steps,
        timeout_seconds=case.budget.timeout_seconds,
        seed=case.seed,
        model=ModelOptions(
            provider=ModelProvider.OLLAMA,
            model_name=MODEL_NAME,
            model_digest=MODEL_DIGEST,
            endpoint=MODEL_ENDPOINT,
            timeout_seconds=600,
        ),
        scenario_initialization=initialization.model_dump(mode="json"),
    )


def rpc_payload(method: str, execution_id: str, kind: str) -> dict:
    if method == "submit":
        params = execution_request(kind, execution_id).model_dump(mode="json")
        rpc_method = "execution.submit"
    elif method == "get":
        params = {"execution_id": execution_id}
        rpc_method = "execution.get"
    else:
        params = {"execution_id": execution_id, "after_sequence": -1, "limit": 100}
        rpc_method = "execution.events"
    return {"jsonrpc": "2.0", "id": method, "method": rpc_method, "params": params}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("clean", "injected"), required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--method", choices=("submit", "get", "events"), required=True)
    parser.add_argument("--base64", action="store_true")
    args = parser.parse_args()
    body = json.dumps(
        rpc_payload(args.method, args.execution_id, args.case),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if args.base64:
        print(base64.urlsafe_b64encode(body).decode("ascii"))
    else:
        print(body.decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
