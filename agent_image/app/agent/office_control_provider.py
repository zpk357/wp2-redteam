"""Deterministic safe and vulnerable controls for office Docker calibration."""

from __future__ import annotations

from typing import Any

from app.agent.react_contract import ReactMessage, ReactToolCall, ReactTurn
from sandbox.scenarios.models import TestCase
from sandbox.scenarios.office_controls import OfficeSafeControl, OfficeVulnerableControl
from sandbox.scenarios.office_runtime import CAPABILITY_TOOL_NAMES, OfficeActionRecord
from sandbox.tool_contracts import ToolSpec


class OfficeControlProviderError(RuntimeError):
    error_code = "trace_office_control_error"


class OfficeControlProvider:
    """Drive one office TestCase while requiring each real tool result in order."""

    def __init__(self, control: str, case: TestCase) -> None:
        if control == "safe":
            result = OfficeSafeControl().run(case)
        elif control == "vulnerable":
            result = OfficeVulnerableControl().run(case)
        else:
            raise ValueError(f"unsupported office control: {control}")
        if not result.passed:
            failure = result.failure
            details = failure.message if failure is not None else "unknown control failure"
            raise OfficeControlProviderError(
                f"{control} office control cannot calibrate {case.case_id}: {details}"
            )

        self.control = control
        self.case = case
        self.actions = result.actions
        self.version = f"trace-office-{control}-control-v1"

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[ToolSpec, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del seed
        self._validate_tools(tools)
        tool_messages = tuple(message for message in messages if message.role == "tool")
        if len(tool_messages) > len(self.actions):
            raise OfficeControlProviderError(
                "office control received more tool results than its frozen action plan"
            )
        for index, message in enumerate(tool_messages):
            self._validate_result(message, self.actions[index])

        if len(tool_messages) == len(self.actions):
            return self._call(
                "submit",
                {
                    "answer": (
                        f"{self.control} office control completed {self.case.case_id}."
                    )
                },
            )

        action = self.actions[len(tool_messages)]
        return self._call(
            CAPABILITY_TOOL_NAMES[action.capability_id],
            action.arguments,
        )

    @staticmethod
    def _validate_tools(tools: tuple[ToolSpec, ...]) -> None:
        actual = {tool.name for tool in tools}
        expected = {*CAPABILITY_TOOL_NAMES.values(), "submit"}
        if actual != expected:
            raise OfficeControlProviderError(
                "office control did not receive the exact office tool surface"
            )

    @staticmethod
    def _validate_result(message: ReactMessage, action: OfficeActionRecord) -> None:
        expected_name = CAPABILITY_TOOL_NAMES[action.capability_id]
        if message.name != expected_name:
            raise OfficeControlProviderError(
                f"office control expected {expected_name}, received {message.name}"
            )
        content = message.content
        if not isinstance(content, dict):
            raise OfficeControlProviderError(
                f"office tool {expected_name} returned a non-object result"
            )
        expected_fields: dict[str, Any] = {
            "allowed": True,
            "authorized": action.authorized,
            "outcome": action.outcome,
            "output": action.output,
            "error": action.error,
        }
        mismatches = sorted(
            field for field, expected in expected_fields.items() if content.get(field) != expected
        )
        if mismatches:
            raise OfficeControlProviderError(
                f"office tool {expected_name} result conflicts with frozen control: {mismatches}"
            )

    @staticmethod
    def _call(name: str, arguments: dict[str, Any]) -> ReactTurn:
        return ReactTurn(
            assistant_text=f"Calling {name}.",
            tool_calls=[ReactToolCall(name=name, arguments=arguments)],
            stop_reason="tool_calls",
        )
