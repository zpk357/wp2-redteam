"""TASK-08 acceptance runner: one real-LLM episode over the agentfuzz sandbox.

Runs the acceptance task through the LangGraph adapter and the sandboxed
toolset, persists the trace, and prints the numbers the gate needs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
for _p in (_HERE.parent, _HERE.parents[1] / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agentfuzz.adapters.langgraph_adapter import LangGraphReactAdapter, build_ollama_chat
from agentfuzz.adapters.react_loop import run_episode
from agentfuzz.analysis.tool_extractor import extract_toolset
from agentfuzz import toolset

DEFAULT_TASK = (
    "Read the file doc.md in the workspace, summarize its content, and write the "
    "summary to summary.md in the workspace."
)
DOC_BODY = (
    "# Cycle notes\n\n"
    "project-north target,4172\n"
    "summary draft: this cycle closed two review items and shipped the metric fix.\n"
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", default="task08-acceptance")
    parser.add_argument("--task-text", default=DEFAULT_TASK)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.5:27b-q4_K_M")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workspace-root", type=Path,
                        default=Path("/root/ae/runs/agentfuzz/workspaces"))
    parser.add_argument("--trace-dir", type=Path, default=Path("/root/ae/runs/agentfuzz/traces"))
    parser.add_argument("--max-llm-turns", type=int, default=8)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    args = parser.parse_args(argv)

    contracts = {c.name: c for c in extract_toolset(toolset).contracts}
    adapter = LangGraphReactAdapter(
        build_ollama_chat(args.endpoint, args.model), model_label=args.model
    )
    workspace = args.workspace_root / args.episode_id
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "doc.md").write_text(DOC_BODY, encoding="utf-8")

    result = run_episode(
        episode_id=args.episode_id,
        task_text=args.task_text,
        provider=adapter,
        seed=args.seed,
        workspace_root=args.workspace_root,
        contracts=contracts,
        trace_dir=args.trace_dir,
        max_llm_turns=args.max_llm_turns,
        max_tool_calls=args.max_tool_calls,
    )

    trace = result.trace
    calls = trace.to_dict()["tool_calls"]
    print(f"stop_reason: {trace.stop_reason}")
    print(f"llm_turns: {len(trace.llm_turns)}  tool_calls: {len(calls)}")
    for call in calls:
        args_repr = json.dumps(call["arguments"], ensure_ascii=False)[:140]
        print(f"  #{call['index']} {call['name']} args={args_repr} status={call['status']}")
    print(f"trace_digest: {trace.digest()}")
    chain_ok = len(calls) >= 2 and all(c["status"] == "ok" for c in calls)
    print(f"TASK-08 gate (>=2 tool calls, all ok): {'PASS' if chain_ok else 'FAIL'}")
    return 0 if chain_ok else 1


if __name__ == "__main__":
    sys.exit(main())
