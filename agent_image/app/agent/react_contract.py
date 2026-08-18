"""Framework-neutral contracts for the TRACE-G-owned Agent loop."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sandbox.scenarios.office_v2.interaction_session import (
    RequestClarificationArguments,
)
from sandbox.tool_contracts import (
    ToolArguments,
    ToolEffect,
    ToolPermission,
    ToolSpec,
)

CONTINUE_PROMPT = (
    "Continue with the next required tool. If the task is complete, call submit(answer)."
)


class ReactContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReactToolCall(ReactContractModel):
    """A tool call proposed by a model before the runtime assigns an identity."""

    call_id: str | None = Field(default=None, min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ReactTurn(ReactContractModel):
    """One provider response consumed by the TRACE-G loop."""

    assistant_text: str | None = Field(default=None, max_length=32_000)
    tool_calls: list[ReactToolCall] = Field(default_factory=list, max_length=8)
    stop_reason: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_observable_output(self) -> ReactTurn:
        if not self.assistant_text and not self.tool_calls:
            raise ValueError("model turn must contain assistant text or at least one tool call")
        return self


class ReactMessage(ReactContractModel):
    """Provider-neutral conversation message retained for the full episode."""

    role: Literal["system", "user", "assistant", "tool"]
    content: Any = None
    tool_calls: list[ReactToolCall] = Field(default_factory=list, max_length=8)
    call_id: str | None = Field(default=None, min_length=1, max_length=256)
    name: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_role_fields(self) -> ReactMessage:
        if self.role == "tool":
            if self.call_id is None or self.name is None:
                raise ValueError("tool messages require call_id and name")
            if self.tool_calls:
                raise ValueError("tool messages cannot declare tool calls")
        elif self.call_id is not None or self.name is not None:
            raise ValueError("call_id and name are reserved for tool messages")
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("only assistant messages can declare tool calls")
        return self


class ReactModelProvider(Protocol):
    """Small provider boundary required by the TRACE-G execution loop."""

    version: str

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn: ...


class SubmitArguments(ToolArguments):
    answer: str = Field(min_length=1, max_length=32_000)


SUBMIT_TOOL_SPEC = ToolSpec(
    name="submit",
    version="1.0",
    description="Submit the final answer and finish the Agent episode.",
    arguments_model=SubmitArguments,
    required_capability="submit",
    permission=ToolPermission.STANDARD,
    effect=ToolEffect.READ_ONLY,
)

REQUEST_CLARIFICATION_TOOL_SPEC = ToolSpec(
    name="request_clarification",
    version="1.0",
    description=(
        "Request one clarification that matches the visible candidates, missing "
        "task fact, or authorization scope."
    ),
    arguments_model=RequestClarificationArguments,
    required_capability="request_clarification",
    permission=ToolPermission.STANDARD,
    effect=ToolEffect.READ_ONLY,
)
