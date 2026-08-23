"""Container-only H3 success and cancellation acceptance probe."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path

from app.adapter.deepseek_harness_adapter import (
    HARNESS_MODEL_DIGEST,
    HARNESS_MODEL_NAME,
    DeepSeekHarnessAdapter,
)
from app.protocol import ExecutionRequest, ModelOptions, ModelProvider

from sandbox.protocol import OFFICE_V2_SCENARIO_ID
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.execution_contracts import build_v2_execution_envelope


def request() -> ExecutionRequest:
    case = CLEAN_CASE_BY_ID["clean.t10.borealis"]
    model = ModelOptions(
        provider=ModelProvider.FAKE,
        model_name=HARNESS_MODEL_NAME,
        model_digest=HARNESS_MODEL_DIGEST,
    )
    envelope = build_v2_execution_envelope(
        case,
        initial_state=load_canonical_world().state,
        model_identity=model,
    )
    return ExecutionRequest(
        execution_id="episode.h3.container",
        case_id=case.case_id,
        scenario_id=OFFICE_V2_SCENARIO_ID,
        prompt=case.task.instruction,
        max_steps=4,
        timeout_seconds=30,
        model=model,
        office_v2_execution=envelope,
    )


async def success() -> dict[str, object]:
    adapter = DeepSeekHarnessAdapter()
    events = [event async for event in adapter.execute(request())]
    return {
        "schema_version": "deepseek-harness-h3-container-probe-v1",
        "mode": "success",
        "status": "passed",
        "event_types": [event.event_type for event in events],
        "tool_names": [
            event.data["name"] for event in events if event.event_type == "tool_call"
        ],
        "final_answer": events[-1].data["final_answer"],
        "state_unchanged": (
            adapter.last_bridge_summary["initial_state_digest"]
            == adapter.last_bridge_summary["final_state_digest"]
        ),
    }


async def cancel() -> dict[str, object]:
    root = Path("/tmp/h3-cancel")
    root.mkdir(parents=True, exist_ok=True)
    os.environ["TRACE_G_HARNESS_EPISODE_ROOT"] = str(root)
    adapter = DeepSeekHarnessAdapter()

    async def consume() -> None:
        async for _ in adapter.execute(request()):
            pass

    task = asyncio.create_task(consume())
    for _ in range(200):
        if list(root.glob("trace-g-h3-*/bridge-records.ndjson")):
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    summary = adapter.last_bridge_summary
    return {
        "schema_version": "deepseek-harness-h3-container-probe-v1",
        "mode": "cancel",
        "status": "passed" if summary and summary["complete"] is False else "failed",
        "incomplete": bool(summary and summary["complete"] is False),
        "episode_residue_count": len(tuple(root.iterdir())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("success", "cancel"))
    args = parser.parse_args()
    result = asyncio.run(success() if args.mode == "success" else cancel())
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
