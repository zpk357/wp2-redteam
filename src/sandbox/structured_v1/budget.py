"""The run budget envelope (`SOC-ENV-86`) and the usage it is measured against.

The limits are frozen in the envelope **before** a real run, so a run cannot grow its own
budget while it is happening. Reaching a cap ends the episode and records
``budget.exceeded`` (a controlled end, not a refusal); a provider that returns no usage is
marked ``usage.missing`` and the cost report is then labelled incomplete.

P1 delivers the contracts. Enforcement and per-call recording are P5 (TASK §14.1), and
P5 is where the two outcome codes are raised.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from sandbox.structured_v1.envelope_codes import FailureCode
from sandbox.structured_v1.models import Identifier, StructuredContract


class RunBudgetEnvelope(StructuredContract):
    """Hard limits for one episode, frozen before the run.

    Every limit is required: a default would let a run start against a cap nobody chose -
    a token cap of zero, for instance, would end the first call as ``budget.exceeded``.
    """

    max_model_calls: int = Field(ge=0)
    max_tool_calls: int = Field(ge=0)
    max_wall_clock_seconds: int = Field(ge=1)
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    max_expense_units: int = Field(ge=0)

    @model_validator(mode="after")
    def at_least_one_call_is_allowed(self) -> RunBudgetEnvelope:
        if self.max_model_calls == 0 and self.max_tool_calls == 0:
            raise ValueError("a budget that allows no call at all cannot run an episode")
        return self


class RunUsage(StructuredContract):
    """What an episode actually spent, call by call (`SOC-ENV-86`)."""

    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    expense_units: int = Field(default=0, ge=0)
    wall_clock_seconds: int = Field(default=0, ge=0)
    usage_complete: bool = True

    def exceeds(self, budget: RunBudgetEnvelope) -> tuple[str, ...]:
        """The limits this usage has passed, named for the record."""

        passed: list[str] = []
        if self.model_calls > budget.max_model_calls:
            passed.append("max_model_calls")
        if self.tool_calls > budget.max_tool_calls:
            passed.append("max_tool_calls")
        if self.wall_clock_seconds > budget.max_wall_clock_seconds:
            passed.append("max_wall_clock_seconds")
        if self.input_tokens > budget.max_input_tokens:
            passed.append("max_input_tokens")
        if self.output_tokens > budget.max_output_tokens:
            passed.append("max_output_tokens")
        if self.expense_units > budget.max_expense_units:
            passed.append("max_expense_units")
        return tuple(passed)

    def code(self, budget: RunBudgetEnvelope) -> FailureCode | None:
        """`budget.exceeded` when a limit was passed, `usage.missing` when incomplete."""

        if self.exceeds(budget):
            return FailureCode.BUDGET_EXCEEDED
        return None if self.usage_complete else FailureCode.USAGE_MISSING


class CallKind(StrEnum):
    """The two kinds of call an episode can make."""

    MODEL = "model"
    TOOL = "tool"


class UsageRecord(StructuredContract):
    """One call's usage, as it was recorded (`SOC-ENV-86`).

    ``usage_reported`` is false when the provider returned no usage: the call still
    happened, so it is counted, but the cost report becomes explicitly incomplete rather
    than quietly wrong.
    """

    record_id: Identifier
    sequence: int = Field(ge=0)
    kind: CallKind
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    expense_units: int = Field(default=0, ge=0)
    wall_clock_seconds: int = Field(default=0, ge=0)
    usage_reported: bool = True


class BudgetTracker(StructuredContract):
    """A frozen budget with every call recorded against it.

    ``can_call`` is what a runtime consults **before** making a call, because the count
    limits are knowable then; ``code`` is what it consults **after**, because tokens and
    expense are only knowable once the call returned. Reaching a cap ends the episode and
    records ``budget.exceeded`` - a controlled end, not a refusal and never a judgement.
    """

    budget: RunBudgetEnvelope
    records: tuple[UsageRecord, ...] = ()

    @property
    def usage(self) -> RunUsage:
        return RunUsage(
            model_calls=sum(1 for item in self.records if item.kind is CallKind.MODEL),
            tool_calls=sum(1 for item in self.records if item.kind is CallKind.TOOL),
            input_tokens=sum(item.input_tokens for item in self.records),
            output_tokens=sum(item.output_tokens for item in self.records),
            expense_units=sum(item.expense_units for item in self.records),
            wall_clock_seconds=sum(item.wall_clock_seconds for item in self.records),
            usage_complete=all(item.usage_reported for item in self.records),
        )

    def would_pass(self, kind: CallKind) -> str | None:
        """The limit that one more call of this kind would pass, if any."""

        usage = self.usage
        if kind is CallKind.MODEL and usage.model_calls >= self.budget.max_model_calls:
            return "max_model_calls"
        if kind is CallKind.TOOL and usage.tool_calls >= self.budget.max_tool_calls:
            return "max_tool_calls"
        if usage.wall_clock_seconds >= self.budget.max_wall_clock_seconds:
            return "max_wall_clock_seconds"
        return None

    def can_call(self, kind: CallKind) -> bool:
        return self.would_pass(kind) is None

    def record(
        self,
        kind: CallKind,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        expense_units: int = 0,
        wall_clock_seconds: int = 0,
        usage_reported: bool = True,
    ) -> BudgetTracker:
        """Record a call that a runtime already decided to make and has now observed."""

        record = UsageRecord(
            record_id=f"usage.{len(self.records):06d}",
            sequence=len(self.records),
            kind=kind,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            expense_units=expense_units,
            wall_clock_seconds=wall_clock_seconds,
            usage_reported=usage_reported,
        )
        return BudgetTracker(budget=self.budget, records=(*self.records, record))

    def code(self) -> FailureCode | None:
        """The outcome code the run must record, if its budget or its accounting says so."""

        return self.usage.code(self.budget)
