"""Container tool bridge for one validated TRACE-G office episode."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import ValidationError

from app.tools.base import ToolResult
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_episode import (
    OFFICE_TOOL_STATE_VERSION,
    OfficeEpisodeInitialization,
    OfficeExecutedAction,
    OfficeInitializationConfigurationError,
    OfficeInitializationIntegrityError,
    OfficeToolRuntimeState,
    load_office_episode_initialization,
)
from sandbox.scenarios.office_runtime import (
    CAPABILITY_TOOL_NAMES,
    OfficeRuntime,
    OfficeRuntimeError,
)

OFFICE_TOOL_CAPABILITY_BY_NAME = {
    tool_name: capability_id
    for capability_id, tool_name in CAPABILITY_TOOL_NAMES.items()
}


class OfficeEpisodeRequestError(ValueError):
    error_code: ClassVar[str] = "trace_office_request_mismatch"


class OfficeEpisodeToolRuntime:
    """Expose office ToolSpecs while delegating every state change to OfficeRuntime."""

    def __init__(self, initialization: OfficeEpisodeInitialization) -> None:
        initialization.assert_integrity()
        self.initialization = initialization
        self.runtime = OfficeRuntime(initialization.test_case)
        if (
            self.runtime.initial_state != initialization.initial_state
            or self.runtime.state_digest() != initialization.initial_state_digest
        ):
            raise OfficeInitializationIntegrityError(
                "OfficeRuntime initial state does not match the initialization envelope"
            )

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        expected_digest: str | None = None,
    ) -> OfficeEpisodeToolRuntime:
        initialization = load_office_episode_initialization(
            payload,
            expected_digest=expected_digest,
        )
        return cls(initialization)

    @classmethod
    def restore(cls, payload: dict[str, Any]) -> OfficeEpisodeToolRuntime:
        if not isinstance(payload, dict):
            raise OfficeInitializationIntegrityError("office tool state must be an object")
        if "schema_version" not in payload:
            raise OfficeInitializationIntegrityError("office tool state version is missing")
        if payload["schema_version"] != OFFICE_TOOL_STATE_VERSION:
            raise OfficeInitializationConfigurationError(
                f"unsupported office tool state version: {payload['schema_version']}"
            )
        try:
            saved = OfficeToolRuntimeState.model_validate(payload)
        except ValidationError as exc:
            raise OfficeInitializationIntegrityError(
                "office tool state failed schema or integrity validation"
            ) from exc

        restored = cls(saved.initialization)
        for action in saved.actions:
            try:
                restored.runtime.execute(
                    action.capability_id,
                    action.arguments,
                    enforce_parameter_provenance=True,
                )
            except OfficeRuntimeError as exc:
                raise OfficeInitializationIntegrityError(
                    "office tool state contains an invalid recorded action"
                ) from exc
        if restored.runtime.state_digest() != saved.final_state_digest:
            raise OfficeInitializationIntegrityError(
                "replayed office actions do not match final_state_digest"
            )
        if restored._records_digest() != saved.records_digest:
            raise OfficeInitializationIntegrityError(
                "replayed office actions do not match records_digest"
            )
        return restored

    def validate_request(self, request) -> None:
        case = self.initialization.test_case
        expected = {
            "case_id": case.case_id,
            "scenario_id": case.scenario.template_id,
            "prompt": case.benign_task.instruction,
            "max_steps": case.budget.max_steps,
            "timeout_seconds": case.budget.timeout_seconds,
            "seed": case.seed,
        }
        actual = {
            "case_id": request.case_id,
            "scenario_id": request.scenario_id,
            "prompt": request.prompt,
            "max_steps": request.max_steps,
            "timeout_seconds": request.timeout_seconds,
            "seed": request.seed,
        }
        mismatches = sorted(name for name in expected if actual[name] != expected[name])

        configured_model = request.model
        if case.agent.provider == "ollama":
            if configured_model is None:
                mismatches.append("model")
            else:
                expected_model = {
                    "provider": case.agent.provider,
                    "model_name": case.agent.model_name,
                    "model_digest": case.agent.model_digest,
                    "endpoint": case.agent.endpoint,
                }
                actual_model = {
                    "provider": configured_model.provider.value,
                    "model_name": configured_model.model_name,
                    "model_digest": configured_model.model_digest,
                    "endpoint": configured_model.endpoint,
                }
                if actual_model != expected_model:
                    mismatches.append("model")
        elif configured_model is not None and (
            configured_model.provider.value != case.agent.provider
            or configured_model.model_name != case.agent.model_name
        ):
            mismatches.append("model")

        if mismatches:
            details = sorted(set(mismatches))
            raise OfficeEpisodeRequestError(
                f"execution request conflicts with frozen TestCase fields: {details}"
            )

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        capability_id = OFFICE_TOOL_CAPABILITY_BY_NAME.get(name)
        if capability_id is None:
            raise AssertionError(f"office episode has no registered tool: {name}")
        try:
            record = self.runtime.execute(
                capability_id,
                arguments,
                enforce_parameter_provenance=True,
            )
        except OfficeRuntimeError as exc:
            return ToolResult(
                allowed=False,
                outcome="rejected",
                error=str(exc),
            )

        risk_category = None
        case = self.initialization.test_case
        if (
            not record.authorized
            and record.outcome == "succeeded"
            and case.attack is not None
            and self.runtime.attack_success().passed
        ):
            risk_category = case.attack.objective.risk_category_ids[0]
        return ToolResult(
            allowed=record.outcome != "blocked",
            authorized=record.authorized,
            outcome=record.outcome,
            output=record.output,
            error=record.error,
            risk_category=risk_category,
            rejection_kind=record.rejection_kind,
        )

    def export_state(self) -> dict[str, Any]:
        state = OfficeToolRuntimeState(
            initialization=self.initialization,
            actions=tuple(
                OfficeExecutedAction(
                    capability_id=record.capability_id,
                    arguments=record.arguments,
                )
                for record in self.runtime.records
            ),
            records_digest=self._records_digest(),
            final_state_digest=self.runtime.state_digest(),
        )
        return state.model_dump(mode="json")

    def state_digest(self) -> str:
        return self.runtime.state_digest()

    def observe(self) -> dict[str, Any]:
        case = self.initialization.test_case
        attack_success = self.runtime.attack_success().passed if case.attack else None
        return {
            "case_id": case.case_id,
            "scenario_id": case.scenario.template_id,
            "normal_task_completed": self.runtime.benign_success().passed,
            "attack_side_effect_observed": attack_success,
            "action_count": len(self.runtime.records),
            "unauthorized_action_count": sum(
                not record.authorized for record in self.runtime.records
            ),
            "initial_state_digest": self.initialization.initial_state_digest,
            "final_state_digest": self.runtime.state_digest(),
        }

    def _records_digest(self) -> str:
        return sha256_digest(
            [record.model_dump(mode="json") for record in self.runtime.records]
        )
