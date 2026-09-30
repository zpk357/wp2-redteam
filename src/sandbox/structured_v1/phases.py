"""Frozen two-stage conversation and receipts (SPEC E-03)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from sandbox.structured_v1.budget import RunBudgetEnvelope, RunUsage
from sandbox.structured_v1.models import StructuredContract

PhaseId = Literal["investigation", "action"]


class TwoPhaseProtocol(StructuredContract):
    version: Literal["investigate-act-v1", "investigate-act-v2"] = "investigate-act-v1"
    action_task_text: str = Field(min_length=1, max_length=2000)
    calls_per_phase: Literal[8] = 8
    #: `ZIE-11`: which tools the action phase offers.  Empty keeps the frozen v1 behaviour, where
    #: every tool stays available.  The action phase was measured spending 4.6 of its 8 calls on
    #: retrieval and delivering nothing (`ZIE` §1.5), so a run that wants a delivery-or-wrap-up
    #: phase names the delivery tools here.
    #:
    #: Narrowing the offered set is stronger than refusing calls one at a time: a model cannot
    #: select a tool it cannot see, so no call is wasted and no refusal result has to be invented.
    #: It also subsumes `ZIE-12`: with delivery tools the only options, every action call is itself
    #: a delivery attempt, so the last calls cannot be spent on anything else.
    action_tools: tuple[str, ...] = ()

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler: SerializerFunctionWrapHandler) -> dict:
        # An empty addition is dropped, so a fixture that does not use it keeps its exact digest --
        # the same rule `StructuredFixtureManifest` applies to its own later fields.
        payload = handler(self)
        if not self.action_tools:
            payload.pop("action_tools", None)
        return payload

    def budgets(self, total: RunBudgetEnvelope) -> tuple[RunBudgetEnvelope, ...]:
        if total.max_model_calls != 16 or total.max_tool_calls != 16:
            raise ValueError("two-stage episodes require exactly 16 model/tool call limits")
        fields = total.model_dump()
        shared = ("max_wall_clock_seconds", "max_input_tokens",
                  "max_output_tokens", "max_expense_units")
        if any(fields[key] < 2 for key in shared):
            raise ValueError("two-stage episodes must reserve resources for both phases")
        first = {key: value // 2 for key, value in fields.items()}
        second = {key: value - first[key] for key, value in fields.items()}
        return RunBudgetEnvelope(**first), RunBudgetEnvelope(**second)


class PhaseReceipt(StructuredContract):
    """Half-open decision interval; action IDs bind its real tool transactions."""

    phase_id: PhaseId
    task_text: str
    start_decision: int = Field(ge=0)
    end_decision: int = Field(ge=0)
    budget: RunBudgetEnvelope
    usage: RunUsage
    elapsed_seconds: int = Field(ge=0)
    end_reason: Literal["model-stopped", "phase-call-limit", "budget-exceeded"]
