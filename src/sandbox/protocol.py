"""Single source of truth for host/container JSON-RPC contracts.

The Docker image copies the host ``sandbox`` package into the runtime image.
``agent_image/app/protocol.py`` only re-exports these definitions and therefore
cannot silently drift from the host package.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RUNTIME_CHILD_CLEANUP_TIMEOUT_SECONDS = 2.0
RUNTIME_TERMINAL_GRACE_SECONDS = 5.0
RUNTIME_TERMINAL_TRANSPORT_MARGIN_SECONDS = 2.0


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def terminal(self) -> bool:
        return self in {
            self.SUCCEEDED,
            self.FAILED,
            self.CANCELLED,
            self.TIMED_OUT,
        }


class ExecutionBackend(StrEnum):
    TRACE_REACT_V2 = "trace_react_v2"


class AgentRuntimeKind(StrEnum):
    LANGGRAPH = "langgraph"
    DEEPSEEK_HARNESS = "deepseek_harness"


class ToolReplayMode(StrEnum):
    EXECUTE_AND_VERIFY = "execute_and_verify"
    STUB_RESPONSE = "stub_response"


class ModelProvider(StrEnum):
    FAKE = "fake"
    OLLAMA = "ollama"


_SHA256_PATTERN = re.compile(r"(?:sha256:)?([0-9a-fA-F]{64})")


def normalize_sha256_digest(value: str) -> str:
    """Normalize API-style bare SHA-256 values to the project's canonical form."""
    if not isinstance(value, str):
        raise ValueError("SHA-256 digest must be a string")
    match = _SHA256_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("digest must contain exactly 64 hexadecimal SHA-256 characters")
    return f"sha256:{match.group(1).lower()}"


class ModelOptions(ContractModel):
    provider: ModelProvider = ModelProvider.FAKE
    model_name: str = "llama3"
    model_digest: str | None = Field(default=None, min_length=1, max_length=256)
    endpoint: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=600)

    @model_validator(mode="after")
    def validate_provider_endpoint(self) -> ModelOptions:
        if self.provider == ModelProvider.OLLAMA:
            if not self.endpoint:
                raise ValueError("Ollama provider requires an endpoint")
            if not self.model_digest:
                raise ValueError("Ollama provider requires a locked model_digest")
            self.model_digest = normalize_sha256_digest(self.model_digest)
        return self


class RecordingOptions(ContractModel):
    enabled: bool = False
    checkpoint_policy: str = "stable_boundaries"
    default_tool_replay_mode: ToolReplayMode = ToolReplayMode.EXECUTE_AND_VERIFY
    normalization_version: str = "1.0"


OFFICE_V2_EXECUTION_ENVELOPE_VERSION = "office-v2-execution-envelope-v1"
OFFICE_V2_SCENARIO_ID = "office-workspace-v2"
OFFICE_V2_FROZEN_WORLD_DIGEST = (
    "sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106"
)
OFFICE_V2_FROZEN_TOOL_CATALOG_DIGEST = (
    "sha256:fe9fdcad58adb09859c92ceb5200901962da81a80a3941753a2edaff47365750"
)
OFFICE_V2_FROZEN_OBJECTIVE_CATALOG_DIGEST = (
    "sha256:b873a57e976204fdf754d97eb41c604f305f203d6ed8a0a8c0409e1733075215"
)


class V2ScenarioCaseKind(StrEnum):
    CLEAN = "clean"
    ATTACK = "attack"


class V2InteractionResponseDirective(BaseModel):
    """Transport-only trusted response selection for an Office V2 episode."""

    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str = Field(min_length=1, max_length=128)
    rule_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    responder_id: str = Field(min_length=1, max_length=128)
    authenticated_principal_id: str | None
    channel: Literal[
        "authenticated_task_session",
        "business_content",
        "unauthenticated_session",
    ] = "authenticated_task_session"


class V2ExecutionEnvelope(ContractModel):
    """Frozen Office V2 input carried by the existing execution RPC."""

    schema_version: Literal["office-v2-execution-envelope-v1"] = (
        OFFICE_V2_EXECUTION_ENVELOPE_VERSION
    )
    scenario_id: Literal["office-workspace-v2"] = OFFICE_V2_SCENARIO_ID
    scenario_case_kind: V2ScenarioCaseKind
    scenario_case_id: str = Field(min_length=1, max_length=128)
    scenario_case_digest: str
    scenario_case_payload: dict[str, Any]
    actor_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    task_digest: str
    base_world_digest: str
    initial_state_digest: str
    initial_state_payload: dict[str, Any]
    initialization_transition_payload: dict[str, Any] | None = None
    materialization_digest: str
    interaction_contract_digest: str
    interaction_response_directives: tuple[V2InteractionResponseDirective, ...] = ()
    interaction_response_directives_digest: str
    tool_catalog_digest: str
    objective_catalog_digest: str
    oracle_contract_version: Literal["office-v2-oracle-contract-v1"] = (
        "office-v2-oracle-contract-v1"
    )
    oracle_evidence_version: Literal["office-v2-oracle-evidence-v1"] = (
        "office-v2-oracle-evidence-v1"
    )
    model_identity: ModelOptions

    @field_validator(
        "scenario_case_digest",
        "task_digest",
        "base_world_digest",
        "initial_state_digest",
        "materialization_digest",
        "interaction_contract_digest",
        "interaction_response_directives_digest",
        "tool_catalog_digest",
        "objective_catalog_digest",
    )
    @classmethod
    def normalize_digest_fields(cls, value: str) -> str:
        return normalize_sha256_digest(value)

    @model_validator(mode="after")
    def identities_and_payloads_match(self) -> V2ExecutionEnvelope:
        if self.base_world_digest != OFFICE_V2_FROZEN_WORLD_DIGEST:
            raise ValueError("v2_data_integrity_error: base world digest mismatch")
        if self.tool_catalog_digest != OFFICE_V2_FROZEN_TOOL_CATALOG_DIGEST:
            raise ValueError("v2_data_integrity_error: tool catalog digest mismatch")
        if self.objective_catalog_digest != OFFICE_V2_FROZEN_OBJECTIVE_CATALOG_DIGEST:
            raise ValueError("v2_data_integrity_error: objective catalog digest mismatch")

        case = self.scenario_case_payload
        task = case.get("task")
        actor = case.get("actor")
        if not isinstance(task, dict) or not isinstance(actor, dict):
            raise ValueError("v2_protocol_error: case requires actor and task objects")
        if case.get("case_id") != self.scenario_case_id:
            raise ValueError("v2_data_integrity_error: case id mismatch")
        if actor.get("actor_id") != self.actor_id or task.get("actor_id") != self.actor_id:
            raise ValueError("v2_data_integrity_error: actor identity mismatch")
        if task.get("task_id") != self.task_id:
            raise ValueError("v2_data_integrity_error: task identity mismatch")
        if _protocol_digest(task) != self.task_digest:
            raise ValueError("v2_data_integrity_error: task digest mismatch")

        digest_field = (
            "case_digest"
            if self.scenario_case_kind is V2ScenarioCaseKind.CLEAN
            else "content_digest"
        )
        if case.get(digest_field) != self.scenario_case_digest:
            raise ValueError("v2_data_integrity_error: case recorded digest mismatch")
        case_payload = {key: value for key, value in case.items() if key != digest_field}
        if _protocol_digest(case_payload) != self.scenario_case_digest:
            raise ValueError("v2_data_integrity_error: case payload digest mismatch")
        if case.get("base_world_digest") != self.base_world_digest:
            raise ValueError("v2_data_integrity_error: case base world mismatch")
        if _protocol_digest(self.initial_state_payload) != self.initial_state_digest:
            raise ValueError("v2_data_integrity_error: initial state digest mismatch")

        if self.scenario_case_kind is V2ScenarioCaseKind.CLEAN:
            interaction = task.get("user_response_script")
            if self.materialization_digest != self.scenario_case_digest:
                raise ValueError("v2_data_integrity_error: clean materialization mismatch")
            if self.initialization_transition_payload is not None:
                raise ValueError(
                    "v2_protocol_error: clean case cannot have initialization transition"
                )
        else:
            interaction = case.get("interaction_contract")
            record = case.get("materialization_record")
            if not isinstance(record, dict):
                raise ValueError("v2_protocol_error: attack case requires materialization record")
            if record.get("materialization_digest") != self.materialization_digest:
                raise ValueError("v2_data_integrity_error: materialization digest mismatch")
            if case.get("initial_world_digest") != self.initial_state_digest:
                raise ValueError("v2_data_integrity_error: attack initial state mismatch")
            expected_transition = record.get("initialization_transition_digest")
            actual_transition = self.initialization_transition_payload
            if (expected_transition is None) != (actual_transition is None):
                raise ValueError("v2_data_integrity_error: initialization transition missing")
            if actual_transition is not None:
                recorded = actual_transition.get("transition_digest")
                transition_payload = {
                    key: value
                    for key, value in actual_transition.items()
                    if key != "transition_digest"
                }
                if (
                    recorded != expected_transition
                    or _protocol_digest(transition_payload) != recorded
                ):
                    raise ValueError("v2_data_integrity_error: transition digest mismatch")
                if actual_transition.get("after_state_digest") != self.initial_state_digest:
                    raise ValueError("v2_data_integrity_error: transition final state mismatch")

        if not isinstance(interaction, dict):
            raise ValueError("v2_protocol_error: case requires interaction contract")
        if _protocol_digest(interaction) != self.interaction_contract_digest:
            raise ValueError("v2_data_integrity_error: interaction contract digest mismatch")
        normalized_directives = tuple(
            item.model_dump(mode="json", exclude_none=False)
            for item in self.interaction_response_directives
        )
        if (
            _protocol_digest(normalized_directives)
            != self.interaction_response_directives_digest
        ):
            raise ValueError("v2_data_integrity_error: response directive digest mismatch")
        request_ids = {item["request_id"] for item in interaction["requests"]}
        rules = {
            item["rule_id"]: item["match"]["request_id"]
            for item in interaction["response_rules"]
        }
        directive_requests: set[str] = set()
        directive_turns: set[str] = set()
        for directive in self.interaction_response_directives:
            if directive.request_id in directive_requests:
                raise ValueError(
                    "v2_configuration_error: response directives repeat a request"
                )
            if directive.turn_id in directive_turns:
                raise ValueError(
                    "v2_configuration_error: response directives repeat a turn"
                )
            if (
                directive.request_id not in request_ids
                or rules.get(directive.rule_id) != directive.request_id
            ):
                raise ValueError(
                    "v2_data_integrity_error: response directive is outside frozen contract"
                )
            directive_requests.add(directive.request_id)
            directive_turns.add(directive.turn_id)
        return self

    def canonical_digest(self) -> str:
        return _protocol_digest(self.model_dump(mode="json", exclude_none=False))


def _protocol_digest(value: object) -> str:
    # Lazy import avoids a protocol <-> replay import cycle during module loading.
    from sandbox.replay.digests import sha256_digest

    return sha256_digest(value)


class ExecutionRequest(ContractModel):
    execution_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=32_000)
    max_steps: int = Field(default=20, ge=1, le=100)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    metadata: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    scenario_id: str | None = None
    agent_version: str | None = None
    image_digest: str | None = None
    recording: RecordingOptions | None = None
    model: ModelOptions | None = None
    scenario_initialization: dict[str, Any] | None = None
    office_v2_execution: V2ExecutionEnvelope | None = None
    execution_backend: ExecutionBackend = ExecutionBackend.TRACE_REACT_V2

    @model_validator(mode="after")
    def validate_office_v2_binding(self) -> ExecutionRequest:
        envelope = self.office_v2_execution
        if envelope is None:
            if self.scenario_id == OFFICE_V2_SCENARIO_ID:
                raise ValueError("v2_configuration_error: Office V2 requires an execution envelope")
            return self
        if self.scenario_initialization is not None:
            raise ValueError(
                "v2_protocol_error: Office V2 cannot use legacy scenario initialization"
            )
        if self.scenario_id != envelope.scenario_id:
            raise ValueError("v2_configuration_error: scenario id does not match envelope")
        if self.case_id != envelope.scenario_case_id:
            raise ValueError("v2_data_integrity_error: request case id does not match envelope")
        task = envelope.scenario_case_payload["task"]
        if self.prompt != task["instruction"]:
            raise ValueError("v2_data_integrity_error: prompt does not match frozen task")
        if self.model is None:
            raise ValueError("v2_configuration_error: Office V2 requires model options")
        if self.model.model_digest != envelope.model_identity.model_digest:
            raise ValueError("v2_model_digest_mismatch: request model digest differs")
        if self.model.model_dump(mode="json") != envelope.model_identity.model_dump(mode="json"):
            raise ValueError("v2_configuration_error: request model options differ from envelope")
        return self


class TraceEvent(ContractModel):
    schema_version: Literal["1.2"] = "1.2"
    execution_id: str
    sequence: int = Field(ge=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    data: dict[str, Any] = Field(default_factory=dict)
    logical_time: int | None = Field(default=None, ge=0)
    input_digest: str | None = None
    output_digest: str | None = None
    state_digest: str | None = None
    checkpoint_id: str | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class CheckpointDigestRecord(ContractModel):
    checkpoint_index: int = Field(ge=0)
    kind: str
    state_digest: str


class ExecutionResult(ContractModel):
    execution_id: str
    status: ExecutionStatus
    final_answer: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    trace_count: int = Field(default=0, ge=0)
    final_sequence: int | None = Field(default=None, ge=0)
    final_state_digest: str | None = None
    checkpoint_digests: list[CheckpointDigestRecord] = Field(default_factory=list)


class TracePage(ContractModel):
    events: list[TraceEvent] = Field(default_factory=list)
    next_after_sequence: int = -1
    terminal: bool = False
    final_sequence: int | None = None


class EventsRequest(BaseModel):
    execution_id: str
    after_sequence: int = Field(default=-1, ge=-1)
    limit: int = Field(default=100, ge=1, le=100)


class ExecutionIdRequest(BaseModel):
    execution_id: str


class JsonRpcRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jsonrpc: str
    id: str | int | None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


def rpc_result(request_id: str | int | None, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(
    request_id: str | int | None,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}
