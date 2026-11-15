"""Provider-neutral conversation shapes for the agentfuzz React loop.

A provider turns messages plus tool specs into one turn.  ``ScriptedProvider``
is the deterministic fake the local pipeline is validated with; the LangGraph
adapter (``langgraph_adapter.py``) is the real-model entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AgentMessage:
    """One message in the provider-neutral conversation."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: Any
    name: str | None = None
    call_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """One tool as the provider sees it (JSON-schema shape)."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class AgentTurn:
    """One provider turn."""

    assistant_text: str | None
    tool_calls: list[dict[str, Any]]  # [{"name": ..., "arguments": {...}}]
    stop_reason: str | None
    token_usage: dict[str, int] | None = None


class Provider(Protocol):
    """What a model adapter must provide (Ollama, LangGraph, scripted fake)."""

    name: str
    model: str

    async def generate(
        self,
        messages: list[AgentMessage],
        tools: list[ToolSpec],
        *,
        seed: int,
    ) -> AgentTurn: ...


class ScriptedProvider:
    """A deterministic provider: fixed turns, with ``$n`` argument substitution.

    ``$n`` substitutes the whole raw return of call *n*; ``$n.field`` reads one
    key out of a mapping return.  Enough to drive a real multi-step chain in
    tests without a model.
    """

    name = "scripted"
    model = "scripted-provider"

    def __init__(self, turns: list[AgentTurn]) -> None:
        self.turns = list(turns)
        self.index = 0

    async def generate(
        self,
        messages: list[AgentMessage],
        tools: list[ToolSpec],
        *,
        seed: int,
    ) -> AgentTurn:
        if self.index >= len(self.turns):
            return AgentTurn(None, [], "scripted-exhausted")
        turn = self.turns[self.index]
        self.index += 1
        return turn


def substitute(raw: str, results: list[Any]) -> Any:
    """Resolve ``$n`` / ``$n.field`` references against recorded returns.

    ``n`` is 1-based: ``$1`` is the first call's raw return.
    """

    if not raw.startswith("$"):
        return raw
    body = raw[1:]
    index_part, _, field_part = body.partition(".")
    value = results[int(index_part) - 1]
    if field_part:
        value = value[field_part]
    return value
