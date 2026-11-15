"""The Episode trace: every LLM turn and tool call, persisted for replay.

TASK-08 acceptance: a chain of two or more tool calls executed on a real LLM
with the trace fully persisted -- arguments and returns included.  One JSON
document with a stable digest; the SQLite store arrives with TASK-09's
scheduling history and is deliberately not on this step's critical path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentfuzz.contracts import dumps


@dataclass
class ToolCallRecord:
    """One executed tool call, arguments and raw return included."""

    index: int
    name: str
    arguments: dict[str, Any]
    result: Any
    status: str  # "ok" | "error" | "refused"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "arguments": self.arguments,
            "result": self.result,
            "status": self.status,
            "error": self.error,
        }


@dataclass
class LLMTurnRecord:
    """One provider turn: the text it produced and the calls it asked for."""

    index: int
    assistant_text: str | None
    tool_calls: list[dict[str, Any]]
    stop_reason: str | None
    token_usage: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "assistant_text": self.assistant_text,
            "tool_calls": list(self.tool_calls),
            "stop_reason": self.stop_reason,
            "token_usage": self.token_usage,
        }


@dataclass
class EpisodeTrace:
    """Everything one Episode produced, replayable from the record alone."""

    episode_id: str
    provider: str
    model: str
    seed: int
    task_text: str
    llm_turns: list[LLMTurnRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    stop_reason: str = ""
    sandbox: dict[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        payload = self.to_dict(include_digest=False)
        payload.pop("sandbox", None)  # paths and logs are environment, not semantics
        canonical = dumps(payload)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self, include_digest: bool = True) -> dict[str, Any]:
        payload = {
            "episode_id": self.episode_id,
            "provider": self.provider,
            "model": self.model,
            "seed": self.seed,
            "task_text": self.task_text,
            "stop_reason": self.stop_reason,
            "llm_turns": [turn.to_dict() for turn in self.llm_turns],
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "sandbox": self.sandbox,
        }
        if include_digest:
            # The digest is computed over the payload WITHOUT the digest key,
            # so this call terminates (digest itself uses include_digest=False).
            payload["trace_digest"] = self.digest()
        return payload

    def write_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return path


class TraceCollector:
    """Accumulates turns and calls while an Episode runs, then finalizes."""

    def __init__(self, *, episode_id: str, provider: str, model: str, seed: int, task_text: str):
        self.episode_id = episode_id
        self.provider = provider
        self.model = model
        self.seed = seed
        self.task_text = task_text
        self.llm_turns: list[LLMTurnRecord] = []
        self.tool_calls: list[ToolCallRecord] = []
        self.stop_reason = ""

    def on_llm_turn(
        self,
        *,
        assistant_text: str | None,
        tool_calls: list[dict[str, Any]],
        stop_reason: str | None,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        self.llm_turns.append(
            LLMTurnRecord(
                index=len(self.llm_turns),
                assistant_text=assistant_text,
                tool_calls=list(tool_calls),
                stop_reason=stop_reason,
                token_usage=token_usage,
            )
        )

    def on_tool_call(
        self, *, name: str, arguments: dict[str, Any], result: Any, status: str, error: str | None = None
    ) -> None:
        self.tool_calls.append(
            ToolCallRecord(
                index=len(self.tool_calls),
                name=name,
                arguments=dict(arguments),
                result=result,
                status=status,
                error=error,
            )
        )

    def finalize(self, stop_reason: str, sandbox: dict[str, Any]) -> EpisodeTrace:
        self.stop_reason = stop_reason
        return EpisodeTrace(
            episode_id=self.episode_id,
            provider=self.provider,
            model=self.model,
            seed=self.seed,
            task_text=self.task_text,
            llm_turns=self.llm_turns,
            tool_calls=self.tool_calls,
            stop_reason=self.stop_reason,
            sandbox=sandbox,
        )
