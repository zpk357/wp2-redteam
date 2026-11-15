"""Local pipeline tests for the agentfuzz runtime (TASK-08).

The scripted provider drives real multi-step chains over the sandboxed
toolset: no model, no network, no real subprocess.  What must hold: every
tool call is recorded with arguments and raw returns; the workspace is
contained; the trace is a stable JSON document.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentfuzz.adapters.provider import AgentTurn, ScriptedProvider
from agentfuzz.adapters.react_loop import run_episode
from agentfuzz.analysis.tool_extractor import extract_toolset
from agentfuzz import toolset

CONTRACTS = {c.name: c for c in extract_toolset(toolset).contracts}
DOC = "# Cycle notes\nproject-north target,4172\n"


def _seed(workspace_root: Path, episode_id: str) -> None:
    directory = workspace_root / episode_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "doc.md").write_text(DOC, encoding="utf-8")


def _provider() -> ScriptedProvider:
    return ScriptedProvider(
        [
            AgentTurn(None, [{"name": "read_workspace_file", "arguments": {"path": "doc.md"}}], "tool_calls"),
            AgentTurn(None, [{"name": "summarize", "arguments": {"text": "$1"}}], "tool_calls"),
            AgentTurn(
                None,
                [{"name": "write_workspace_file", "arguments": {"path": "out.md", "content": "$2"}}],
                "tool_calls",
            ),
        ]
    )

def test_three_step_chain_runs_with_a_complete_trace(tmp_path: Path):
    _seed(tmp_path, "ep1")
    result = run_episode(
        episode_id="ep1",
        task_text="Summarize the cycle notes.",
        provider=_provider(),
        seed=0,
        workspace_root=tmp_path,
        contracts=CONTRACTS,
        trace_dir=tmp_path,
    )
    assert result.stop_reason == "agent-stopped"
    assert [c.name for c in result.trace.tool_calls] == [
        "read_workspace_file", "summarize", "write_workspace_file"
    ]
    assert all(c.status == "ok" for c in result.trace.tool_calls)
    assert all(c.arguments for c in result.trace.tool_calls)
    # the summary landed in the sandbox workspace
    assert (tmp_path / "ep1" / "out.md").exists()
    # the trace is a complete, stable document
    trace_file = tmp_path / "ep1.trace.json"
    assert trace_file.exists()
    payload = json.loads(trace_file.read_text(encoding="utf-8"))
    assert payload["trace_digest"] == result.trace.digest()
    again = result.trace.digest()
    assert again == result.trace.digest()


def test_data_propagates_through_substitution(tmp_path: Path):
    _seed(tmp_path, "ep2")
    provider = ScriptedProvider(
        [
            AgentTurn(None, [{"name": "build_query", "arguments": {"metric": "m1"}}], "tool_calls"),
            AgentTurn(None, [{"name": "query_metrics", "arguments": {"sql": "$1"}}], "tool_calls"),
        ]
    )
    result = run_episode(
        episode_id="ep2",
        task_text="Report the m1 metric.",
        provider=provider,
        seed=0,
        workspace_root=tmp_path,
        contracts=CONTRACTS,
        metrics_rows=[{"name": "m1", "value": 42.0}],
    )
    call = result.trace.tool_calls[1]
    assert call.arguments["sql"] == "SELECT value FROM metrics WHERE name = 'm1'"
    assert call.result == [{"value": 42.0}]

def test_shell_shim_serves_and_records(tmp_path: Path):
    _seed(tmp_path, "ep3")
    provider = ScriptedProvider(
        [
            AgentTurn(None, [{"name": "run_shell", "arguments": {"command": "cat doc.md"}}], "tool_calls"),
            AgentTurn(None, [{"name": "run_shell", "arguments": {"command": "rm -rf /"}}], "tool_calls"),
        ]
    )
    result = run_episode(
        episode_id="ep3",
        task_text="Inspect the notes.",
        provider=provider,
        seed=0,
        workspace_root=tmp_path,
        contracts=CONTRACTS,
    )
    first, second = result.trace.tool_calls
    assert first.result == DOC
    assert second.result == ""  # unsupported commands answer with nothing
    assert len(result.trace.sandbox["shell_log"]) == 2


def test_path_escape_is_refused(tmp_path: Path):
    _seed(tmp_path, "ep4")
    provider = ScriptedProvider(
        [
            AgentTurn(None, [{"name": "read_workspace_file", "arguments": {"path": "../secret"}}], "tool_calls"),
        ]
    )
    result = run_episode(
        episode_id="ep4",
        task_text="Try to escape.",
        provider=provider,
        seed=0,
        workspace_root=tmp_path,
        contracts=CONTRACTS,
    )
    call = result.trace.tool_calls[0]
    assert call.status == "refused"
    assert "escapes the workspace" in call.error


def test_controlled_fetch_serves_registered_and_refuses_unknown(tmp_path: Path):
    provider = ScriptedProvider(
        [
            AgentTurn(None, [{"name": "fetch_document", "arguments": {"url": "http://controlled.test/a"}}], "tool_calls"),
            AgentTurn(None, [{"name": "fetch_document", "arguments": {"url": "http://evil.test/x"}}], "tool_calls"),
        ]
    )
    result = run_episode(
        episode_id="ep5",
        task_text="Fetch the documents.",
        provider=provider,
        seed=0,
        workspace_root=tmp_path,
        contracts=CONTRACTS,
        endpoints={"http://controlled.test/a": "hello"},
    )
    served, refused = result.trace.tool_calls
    assert served.status == "ok" and served.result == "hello"
    assert refused.status == "error" and "refused" in refused.error
    assert [f["served"] for f in result.trace.sandbox["fetches"]] == [True, False]

async def test_langgraph_adapter_converts_and_parses():
    from agentfuzz.adapters.langgraph_adapter import LangGraphReactAdapter
    from langchain_core.messages import AIMessage

    class _StubChatModel:
        def bind_tools(self, schemas):
            self.bound = schemas
            return self

        async def ainvoke(self, messages):
            self.seen = messages
            return AIMessage(
                content="working on it",
                tool_calls=[
                    {"name": "read_workspace_file", "args": {"path": "doc.md"}, "id": "c1"}
                ],
                usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
            )

    from agentfuzz.adapters.provider import AgentMessage, ToolSpec

    specs = [ToolSpec(name="read_workspace_file", description="read", parameters={})]
    adapter = LangGraphReactAdapter(_StubChatModel(), model_label="qwen-test")
    messages = [
        AgentMessage(role="system", content="be careful"),
        AgentMessage(role="user", content="start"),
        AgentMessage(role="tool", content={"ok": 1}, name="read_workspace_file", call_id="c0"),
    ]
    turn = await adapter.generate(messages, specs, seed=0)
    assert turn.tool_calls == [
        {"name": "read_workspace_file", "arguments": {"path": "doc.md"}}
    ]
    assert turn.assistant_text == "working on it"
    assert turn.token_usage == {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}
    # the tool result was serialized into a ToolMessage with its call id
    assert adapter._chat_model.seen[-1].tool_call_id == "c0"
    assert adapter.last_token_usage["total_tokens"] == 14
