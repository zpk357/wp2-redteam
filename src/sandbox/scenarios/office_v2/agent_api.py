"""Provider-neutral Office V2 model tools and visible result projection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from sandbox.scenarios.office_v2.agent_context import AgentRenderedSystemPrompt
from sandbox.scenarios.office_v2.models import OfficeV2Contract
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_EXCLUDED_TOOL_NAMES,
    OFFICE_V2_TOOL_NAMES,
    office_v2_tool_definitions,
)
from sandbox.scenarios.office_v2.tools.contracts import (
    ArgumentSource,
    OfficeToolResult,
    ToolFailureCode,
    ToolResultStatus,
)
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.tool_contracts import (
    OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
    OFFICE_V2_TOOL_SPECS,
    OfficeV2ToolSpec,
)

AGENT_VISIBLE_TOOL_RESULT_VERSION = "office-v2-agent-visible-tool-result-v1"


class AgentVisibleToolErrorCode(StrEnum):
    TOOL_UNAVAILABLE = "tool_unavailable"
    PLATFORM_DENIED = "platform_denied"
    POLICY_ENFORCED_DENIED = "policy_enforced_denied"
    RESOURCE_NOT_FOUND_OR_HIDDEN = "resource_not_found_or_hidden"
    INVALID_OR_STALE_PAGE_TOKEN = "invalid_or_stale_page_token"
    RESOURCE_VERSION_CONFLICT = "resource_version_conflict"
    BINDING_STALE = "binding_stale"
    INVALID_ARGUMENTS = "invalid_arguments"
    ARGUMENT_SOURCE_MISSING_OR_MISMATCH = "argument_source_missing_or_mismatch"
    TRANSACTION_FAILED = "transaction_failed"


_VISIBLE_ERROR_MESSAGES = {
    AgentVisibleToolErrorCode.TOOL_UNAVAILABLE: (
        "This business tool is not available in the current session."
    ),
    AgentVisibleToolErrorCode.PLATFORM_DENIED: (
        "Platform access does not allow this operation on the requested resource."
    ),
    AgentVisibleToolErrorCode.POLICY_ENFORCED_DENIED: (
        "Enterprise policy blocks this operation."
    ),
    AgentVisibleToolErrorCode.RESOURCE_NOT_FOUND_OR_HIDDEN: (
        "The requested resource was not found or is not visible."
    ),
    AgentVisibleToolErrorCode.INVALID_OR_STALE_PAGE_TOKEN: (
        "The page token is invalid or no longer current."
    ),
    AgentVisibleToolErrorCode.RESOURCE_VERSION_CONFLICT: (
        "The resource changed; refresh it and use the current version."
    ),
    AgentVisibleToolErrorCode.BINDING_STALE: (
        "The selected task resource is stale and must be resolved again."
    ),
    AgentVisibleToolErrorCode.INVALID_ARGUMENTS: (
        "The tool arguments do not match the required business schema."
    ),
    AgentVisibleToolErrorCode.ARGUMENT_SOURCE_MISSING_OR_MISMATCH: (
        "A required argument source is missing or does not match the supplied value."
    ),
    AgentVisibleToolErrorCode.TRANSACTION_FAILED: (
        "The operation could not be completed and no requested change was committed."
    ),
}

_FAILURE_CODE_PROJECTION = {
    ToolFailureCode.UNKNOWN_TOOL: AgentVisibleToolErrorCode.TOOL_UNAVAILABLE,
    ToolFailureCode.UNSUPPORTED_TOOL_CONTRACT_VERSION: (
        AgentVisibleToolErrorCode.TOOL_UNAVAILABLE
    ),
    ToolFailureCode.INVALID_ARGUMENTS: AgentVisibleToolErrorCode.INVALID_ARGUMENTS,
    ToolFailureCode.INVALID_OR_STALE_PAGE_TOKEN: (
        AgentVisibleToolErrorCode.INVALID_OR_STALE_PAGE_TOKEN
    ),
    ToolFailureCode.RESOURCE_NOT_FOUND_OR_HIDDEN: (
        AgentVisibleToolErrorCode.RESOURCE_NOT_FOUND_OR_HIDDEN
    ),
    ToolFailureCode.RESOURCE_VERSION_CONFLICT: (
        AgentVisibleToolErrorCode.RESOURCE_VERSION_CONFLICT
    ),
    ToolFailureCode.BINDING_STALE: AgentVisibleToolErrorCode.BINDING_STALE,
    ToolFailureCode.INVALID_RESOURCE_REFERENCE: (
        AgentVisibleToolErrorCode.RESOURCE_NOT_FOUND_OR_HIDDEN
    ),
    ToolFailureCode.RECIPIENT_NOT_RESOLVABLE: (
        AgentVisibleToolErrorCode.RESOURCE_NOT_FOUND_OR_HIDDEN
    ),
    ToolFailureCode.ARGUMENT_SOURCE_MISSING: (
        AgentVisibleToolErrorCode.ARGUMENT_SOURCE_MISSING_OR_MISMATCH
    ),
    ToolFailureCode.ARGUMENT_SOURCE_MISMATCH: (
        AgentVisibleToolErrorCode.ARGUMENT_SOURCE_MISSING_OR_MISMATCH
    ),
    ToolFailureCode.CAPABILITY_UNAVAILABLE: AgentVisibleToolErrorCode.TOOL_UNAVAILABLE,
    ToolFailureCode.PLATFORM_DENIED: AgentVisibleToolErrorCode.PLATFORM_DENIED,
    ToolFailureCode.POLICY_ENFORCED_DENIED: (
        AgentVisibleToolErrorCode.POLICY_ENFORCED_DENIED
    ),
    ToolFailureCode.TRANSACTION_VALIDATION_FAILED: (
        AgentVisibleToolErrorCode.TRANSACTION_FAILED
    ),
    ToolFailureCode.TRANSACTION_CONFLICT: AgentVisibleToolErrorCode.TRANSACTION_FAILED,
    ToolFailureCode.INTERNAL_INTEGRITY_ERROR: (
        AgentVisibleToolErrorCode.TRANSACTION_FAILED
    ),
}


class AgentVisibleToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: AgentVisibleToolErrorCode
    message: str = Field(min_length=1, max_length=512)
    retryable: Literal[False] = False

    @model_validator(mode="after")
    def message_matches_code(self) -> Self:
        if self.message != _VISIBLE_ERROR_MESSAGES[self.code]:
            raise ValueError("visible tool error message does not match its stable code")
        return self


class AgentVisibleToolResult(OfficeV2Contract):
    result_version: Literal["office-v2-agent-visible-tool-result-v1"] = (
        AGENT_VISIBLE_TOOL_RESULT_VERSION
    )
    status: ToolResultStatus
    data: dict[str, JsonValue] = Field(default_factory=dict)
    error: AgentVisibleToolError | None = None

    def model_visible_payload(self) -> dict[str, JsonValue]:
        return self.model_dump(
            mode="json",
            exclude={"schema_version", "result_version"},
            exclude_none=False,
        )

    @model_validator(mode="after")
    def status_and_error_are_consistent(self) -> Self:
        if self.status is ToolResultStatus.SUCCEEDED:
            if self.error is not None:
                raise ValueError("successful visible tool result cannot contain an error")
        else:
            if self.data:
                raise ValueError("unsuccessful visible tool result cannot contain data")
            if self.error is None:
                raise ValueError("unsuccessful visible tool result requires an error")
        return self


@dataclass(frozen=True, slots=True)
class AgentToolResultProjection:
    """Keep the complete execution fact trusted while exposing only its stable view."""

    trusted_result: OfficeToolResult
    visible_result: AgentVisibleToolResult

    def __post_init__(self) -> None:
        if self.visible_result != _project_visible_result(self.trusted_result):
            raise ValueError("visible tool result does not match trusted execution fact")

    def model_visible_payload(self) -> dict[str, JsonValue]:
        return self.visible_result.model_visible_payload()


@dataclass(frozen=True, slots=True)
class OfficeV2AgentSessionSurface:
    """Bind one rendered V2 prompt and one Episode runtime for model execution."""

    rendered_prompt: AgentRenderedSystemPrompt
    runtime: OfficeV2ToolRuntime
    control_tool_specs: tuple[Any, ...]
    control_handler: Callable[[str, dict[str, Any]], Any]
    business_result_observer: Callable[[OfficeToolResult], None] | None = None
    argument_source_resolver: (
        Callable[[str, dict[str, Any]], tuple[ArgumentSource, ...]] | None
    ) = None

    def __post_init__(self) -> None:
        business_names = tuple(spec.name for spec in self.business_tool_specs)
        control_names = tuple(spec.name for spec in self.control_tool_specs)
        if len(control_names) != len(set(control_names)):
            raise ValueError("Agent control tool names must be unique")
        if set(business_names).intersection(control_names):
            raise ValueError("business and control tool names must be disjoint")
        if (
            self.rendered_prompt.envelope.tool_spec_digest
            != office_v2_model_tool_contract_digest()
        ):
            raise ValueError("rendered prompt and V2 business tools do not match")

    @property
    def system_message(self) -> str:
        return self.rendered_prompt.system_message

    @property
    def prompt_version(self) -> str:
        return self.rendered_prompt.envelope.render_version

    @property
    def prompt_digest(self) -> str:
        return self.rendered_prompt.envelope.system_message_digest

    @property
    def business_tool_specs(self) -> tuple[OfficeV2ToolSpec, ...]:
        return office_v2_model_tool_specs()

    def execute_business_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> AgentToolResultProjection:
        argument_sources = (
            self.argument_source_resolver(name, arguments)
            if self.argument_source_resolver is not None
            else ()
        )
        projection = project_office_v2_tool_result(
            self.runtime.invoke(name, arguments, argument_sources=argument_sources)
        )
        if self.business_result_observer is not None:
            self.business_result_observer(projection.trusted_result)
        return projection

    def handle_control_call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in {spec.name for spec in self.control_tool_specs}:
            raise ValueError(f"unsupported Agent control tool: {name}")
        return self.control_handler(name, arguments)


def office_v2_model_tool_specs() -> tuple[OfficeV2ToolSpec, ...]:
    definitions = office_v2_tool_definitions()
    if tuple(spec.name for spec in OFFICE_V2_TOOL_SPECS) != OFFICE_V2_TOOL_NAMES:
        raise RuntimeError("Office V2 model tool surface is not the frozen 17-tool catalog")
    if set(OFFICE_V2_TOOL_NAMES).intersection(OFFICE_V2_EXCLUDED_TOOL_NAMES):
        raise RuntimeError("Office V2 model tool surface contains an excluded tool")
    if any(spec.definition is not definitions[spec.name] for spec in OFFICE_V2_TOOL_SPECS):
        raise RuntimeError("Office V2 model tool surface copied a handler definition")
    return OFFICE_V2_TOOL_SPECS


def office_v2_provider_tool_schemas() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.arguments_model.model_json_schema(),
            },
        }
        for spec in office_v2_model_tool_specs()
    )


def office_v2_model_tool_contract_digest() -> str:
    office_v2_model_tool_specs()
    return OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST


def project_office_v2_tool_result(
    result: OfficeToolResult,
) -> AgentToolResultProjection:
    return AgentToolResultProjection(
        trusted_result=result,
        visible_result=_project_visible_result(result),
    )


def _project_visible_result(result: OfficeToolResult) -> AgentVisibleToolResult:
    if result.status is ToolResultStatus.SUCCEEDED:
        return AgentVisibleToolResult(status=result.status, data=result.visible_output)
    if result.failure_code is None:
        raise ValueError("unsuccessful OfficeToolResult requires a failure code")
    code = _FAILURE_CODE_PROJECTION[result.failure_code]
    return AgentVisibleToolResult(
        status=result.status,
        error=AgentVisibleToolError(
            code=code,
            message=_VISIBLE_ERROR_MESSAGES[code],
        ),
    )


if set(_FAILURE_CODE_PROJECTION) != set(ToolFailureCode):
    raise RuntimeError("Office V2 visible error projection is not exhaustive")


__all__ = [
    "AGENT_VISIBLE_TOOL_RESULT_VERSION",
    "AgentToolResultProjection",
    "AgentVisibleToolError",
    "AgentVisibleToolErrorCode",
    "AgentVisibleToolResult",
    "OfficeV2AgentSessionSurface",
    "office_v2_model_tool_contract_digest",
    "office_v2_model_tool_specs",
    "office_v2_provider_tool_schemas",
    "project_office_v2_tool_result",
]
