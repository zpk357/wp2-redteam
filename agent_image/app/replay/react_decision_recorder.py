"""Record and deterministically replay TRACE-G React provider turns."""

from __future__ import annotations

from uuid import uuid4

from app.agent.react_contract import (
    ReactMessage,
    ReactModelProvider,
    ReactTurn,
)
from sandbox.replay.digests import sha256_digest
from sandbox.replay.exceptions import ReplayDivergenceError
from sandbox.replay.models import (
    RECORDED_MODEL_TOKEN_USAGE_KEY,
    RecordedModelDecision,
)
from sandbox.tool_contracts import ToolSpec


class ReactDecisionRecorder:
    def __init__(self, provider: ReactModelProvider) -> None:
        self.provider = provider
        self.version = provider.version
        self.decisions: list[RecordedModelDecision] = []
        self._sequence = 0
        self._before_checkpoint_id = "unbound"

    def set_context(self, *, sequence: int, before_checkpoint_id: str) -> None:
        self._sequence = sequence
        self._before_checkpoint_id = before_checkpoint_id

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        turn = await self.provider.generate(messages, tools, seed=seed)
        input_payload = [message.model_dump(mode="json") for message in messages]
        output_payload = turn.model_dump(mode="json")
        token_usage = getattr(self.provider, "last_token_usage", None)
        if token_usage is not None:
            output_payload[RECORDED_MODEL_TOKEN_USAGE_KEY] = token_usage
        self.decisions.append(
            RecordedModelDecision(
                decision_id=f"decision-{uuid4().hex}",
                sequence=self._sequence,
                decision_index=len(self.decisions),
                before_checkpoint_id=self._before_checkpoint_id,
                input_digest=sha256_digest(input_payload),
                output_digest=sha256_digest(output_payload),
                action=output_payload,
                model_name=type(self.provider).__name__,
                model_version=self.provider.version,
            )
        )
        return turn

    def attach_after_checkpoint(self, checkpoint_id: str) -> None:
        if not self.decisions:
            raise RuntimeError("no React model decision is available")
        self.decisions[-1] = self.decisions[-1].model_copy(
            update={"after_checkpoint_id": checkpoint_id}
        )


class RecordedReactProvider:
    def __init__(
        self,
        decisions: list[RecordedModelDecision],
        *,
        start_index: int = 0,
    ) -> None:
        self.decisions = decisions
        self.next_index = start_index
        self.version = (
            decisions[0].model_version if decisions else "recorded-react-provider-v1"
        )
        self.last_decision: RecordedModelDecision | None = None

    @property
    def next_before_checkpoint_id(self) -> str | None:
        if self.next_index >= len(self.decisions):
            return None
        return self.decisions[self.next_index].before_checkpoint_id

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del tools, seed
        if self.next_index >= len(self.decisions):
            raise ReplayDivergenceError(-32106, "recorded React decisions are exhausted")
        decision = self.decisions[self.next_index]
        if decision.decision_index != self.next_index:
            raise ReplayDivergenceError(-32102, "recorded decision index is not contiguous")
        input_payload = [message.model_dump(mode="json") for message in messages]
        if decision.input_digest != sha256_digest(input_payload):
            raise ReplayDivergenceError(-32106, "React model input digest diverged")
        try:
            action = dict(decision.action)
            action.pop(RECORDED_MODEL_TOKEN_USAGE_KEY, None)
            turn = ReactTurn.model_validate(action)
        except ValueError as exc:
            raise ReplayDivergenceError(-32108, "recorded React turn is invalid") from exc
        if decision.output_digest != sha256_digest(decision.action):
            raise ReplayDivergenceError(-32108, "recorded React turn digest is invalid")
        self.next_index += 1
        self.last_decision = decision
        return turn

    def assert_consumed(self) -> None:
        if self.next_index != len(self.decisions):
            raise ReplayDivergenceError(-32108, "recorded React decisions remain unconsumed")
