"""Frozen two-stage conversation and receipts (SPEC E-03), without tool policy changes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sandbox.structured_v1.budget import RunBudgetEnvelope, RunUsage
from sandbox.structured_v1.models import StructuredContract

PhaseId = Literal["investigation", "action"]


class TwoPhaseProtocol(StructuredContract):
    version: Literal["investigate-act-v1"] = "investigate-act-v1"
    action_task_text: str = Field(min_length=1, max_length=2000)
    calls_per_phase: Literal[8] = 8

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
