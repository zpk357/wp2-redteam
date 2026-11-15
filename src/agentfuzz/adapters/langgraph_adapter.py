"""The real-model entry: a langchain chat model driven by the agentfuzz loop.

The adapter converts the provider-neutral conversation into langchain messages,
binds the M0 tool contracts as tool schemas, and reports token usage so the
scheduler cost term (SPEC 0.6 C) has a source from day one.
"""

from __future__ import annotations

import json
from typing import Any

from agentfuzz.adapters.provider import AgentTurn, ToolSpec


class LangGraphReactAdapter:
    """Drives a langchain chat model over the provider-neutral protocol."""

    name = "langgraph-react"
    version = "langgraph-react-v1"

    def __init__(self, chat_model: Any, *, model_label: str = "chat-model") -> None:
        self._chat_model = chat_model
        self.model = model_label
        self.last_token_usage: dict[str, int] | None = None
        self._bound_model: Any = None
        self._bound_specs: list[ToolSpec] | None = None

    async def generate(
        self,
        messages: list,
        tools: list[ToolSpec],
        *,
        seed: int,
    ) -> AgentTurn:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        converted = []
        for message in messages:
            if message.role == "system":
                converted.append(SystemMessage(content=str(message.content)))
            elif message.role == "user":
                converted.append(HumanMessage(content=str(message.content)))
            elif message.role == "assistant":
                converted.append(AIMessage(content=message.content or ""))
            elif message.role == "tool":
                converted.append(
                    ToolMessage(
                        content=json.dumps(message.content, ensure_ascii=False, sort_keys=True),
                        tool_call_id=message.call_id or message.name or "call",
                        name=message.name or "tool",
                    )
                )
        # Bind once per tool-set: without this the model never sees the tools and
        # answers in prose (measured: the first TASK-08 run emitted a bash block
        # instead of a tool call, gate FAIL).  bind_tools converts the OpenAI-style
        # schemas via convert_to_openai_tool, which ChatOllama passes onto the wire.
        if self._bound_model is None or self._bound_specs != tools:
            self._bound_model = bind_tools(self._chat_model, list(tools))
            self._bound_specs = list(tools)
        response = await self._bound_model.ainvoke(converted)
        usage = getattr(response, "usage_metadata", None)
        self.last_token_usage = (
            {key: int(value) for key, value in usage.items()} if isinstance(usage, dict) else None
        )
        tool_calls = [
            {"name": call["name"], "arguments": dict(call.get("args") or {})}
            for call in (getattr(response, "tool_calls", None) or [])
        ]
        content = response.content if isinstance(response.content, str) else json.dumps(
            response.content, ensure_ascii=False
        )
        return AgentTurn(
            assistant_text=content or None,
            tool_calls=tool_calls,
            stop_reason="tool_calls" if tool_calls else "stop",
            token_usage=self.last_token_usage,
        )

def _tool_schema(spec: ToolSpec) -> dict:
    """OpenAI-style function schema, the format bind_tools accepts."""

    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def bind_tools(chat_model, specs: list[ToolSpec]):
    """Bind the M0-derived tool specs onto a chat model."""

    return chat_model.bind_tools([_tool_schema(spec) for spec in specs])


def build_ollama_chat(endpoint: str, model: str, *, temperature: float = 0.0):
    """The fixed local stack chat model (SPEC 0.7: one fixed LLM API)."""

    from langchain_ollama import ChatOllama

    return ChatOllama(base_url=endpoint, model=model, temperature=temperature)
