"""Bind the existing React provider contract to the structured episode model port."""

from __future__ import annotations

import asyncio
import time

from app.agent.react_contract import (
    SUBMIT_TOOL_SPEC,
    ReactMessage,
    ReactModelProvider,
    ReactToolCall,
)
from sandbox.agent_prompts import OFFICE_AGENT_SYSTEM_PROMPT
from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.session import ToolCallReport
from sandbox.tool_contracts import ToolSpec


class ReactProviderModelPort:
    """Stateful conversation adapter used by the structured session.

    The trusted task is the sole user message. Low-trust material reaches this port only
    through a real tool result passed to :meth:`observe` by the session.
    """

    def __init__(
        self,
        provider: ReactModelProvider,
        *,
        model_name: str,
        seed: int | None,
    ) -> None:
        self._provider = provider
        self._model_name = model_name
        self._seed = seed
        self._messages: list[ReactMessage] = []
        self._tools: tuple[ToolSpec, ...] = ()
        self._pending_call: ReactToolCall | None = None
        self._submitted_call: ReactToolCall | None = None

    @property
    def messages(self) -> tuple[ReactMessage, ...]:
        return tuple(self._messages)

    def bind(self, *, task_text: str, tools: tuple[object, ...]) -> None:
        if self._messages:
            raise RuntimeError("the model port is already bound")
        required = ("name", "description", "arguments_model", "public_contract")
        if not all(all(hasattr(tool, field) for field in required) for tool in tools):
            raise TypeError("the structured model port requires model-visible tool specs")
        self._tools = (*tools, SUBMIT_TOOL_SPEC)  # type: ignore[assignment]
        self._messages = [
            ReactMessage(role="system", content=OFFICE_AGENT_SYSTEM_PROMPT),
            ReactMessage(role="user", content=task_text),
        ]

    def decide(self, *, step: int) -> ModelDecision:
        if not self._messages or not self._tools:
            raise RuntimeError("the model port must be bound before deciding")
        input_messages = tuple(self._messages)
        started = time.monotonic()
        turn = asyncio.run(
            self._provider.generate(input_messages, self._tools, seed=self._seed)
        )
        elapsed = max(int(time.monotonic() - started), 0)
        selected = turn.tool_calls[0] if turn.tool_calls else None
        selected = (
            None
            if selected is None
            else selected.model_copy(
                update={"call_id": selected.call_id or f"model.{step + 1:03d}.tool.001"}
            )
        )
        assistant_calls = [] if selected is None else [selected]
        self._messages.append(
            ReactMessage(
                role="assistant",
                content=turn.assistant_text,
                tool_calls=assistant_calls,
            )
        )
        usage = getattr(self._provider, "last_token_usage", None)
        usage_reported = isinstance(usage, dict)
        input_tokens = int(usage.get("prompt_tokens", 0)) if usage_reported else 0
        output_tokens = int(usage.get("completion_tokens", 0)) if usage_reported else 0
        stopped = selected is None or selected.name == SUBMIT_TOOL_SPEC.name
        self._submitted_call = selected if stopped else None
        self._pending_call = None if stopped else selected
        return ModelDecision(
            call_id=f"model.{step + 1:03d}",
            action_request_id=(None if stopped else f"action.{step + 1:03d}"),
            tool_name=None if stopped else selected.name,
            arguments={} if stopped else selected.arguments,
            assistant_text=turn.assistant_text,
            provider_version=self._provider.version,
            model_name=self._model_name,
            input_digest=sha256_digest(
                [message.model_dump(mode="json") for message in input_messages]
            ),
            tool_catalogue_digest=sha256_digest(
                [tool.public_contract() for tool in self._tools]
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            wall_clock_seconds=elapsed,
            usage_reported=usage_reported,
        )

    def observe(self, decision: ModelDecision, report: ToolCallReport) -> None:
        call = self._pending_call
        if call is None or decision.action_request_id is None:
            raise RuntimeError("a tool result arrived without a pending model tool call")
        self._messages.append(
            ReactMessage(
                role="tool",
                call_id=call.call_id,
                name=call.name,
                content=report.model_visible_result,
            )
        )
        self._pending_call = None

    def continue_task(
        self, *, task_text: str, tools: tuple[ToolSpec, ...] | None = None
    ) -> None:
        """Append the frozen follow-up, and narrow the offered tools when a phase asks for it.

        `ZIE-11`: a run may give the action phase its own tool set.  The submit contract is appended
        here as well, so a narrowed phase still leaves the model its wrap-up -- which is what makes
        "every episode ends with a delivery or a submit" a structural property rather than a hope.
        """
        if not self._messages or self._pending_call is not None:
            raise RuntimeError("continuation requires a bound, resolved conversation")
        if self._submitted_call is not None:
            call = self._submitted_call
            self._messages.append(ReactMessage(
                role="tool", call_id=call.call_id, name=call.name,
                content={"status": "phase-response-received"},
            ))
            self._submitted_call = None
        if tools is not None:
            self._tools = (*tools, SUBMIT_TOOL_SPEC)  # type: ignore[assignment]
        self._messages.append(ReactMessage(role="user", content=task_text))
