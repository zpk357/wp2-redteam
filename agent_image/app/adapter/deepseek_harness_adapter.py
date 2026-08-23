"""DeepSeek Harness adapter for the complete Office V2 direct runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.adapter.base import AdapterConfigurationError, AdapterExecutionError, AgentAdapter
from app.agent.react_contract import (
    REQUEST_CLARIFICATION_TOOL_SPEC,
    SUBMIT_TOOL_SPEC,
    ReactMessage,
    ReactToolCall,
    ReactTurn,
)
from app.office_v2_session import (
    OfficeV2LiveOracleArtifact,
    OfficeV2RecordingState,
)
from app.protocol import ExecutionRequest, ModelOptions, ModelProvider, TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.agent_api import office_v2_model_tool_specs

HARNESS_RUNTIME_VERSION = "deepseek-harness-h4-v1"
HARNESS_MODEL_NAME = "office-v2-h4-deterministic"
HARNESS_MODEL_DIGEST = (
    "sha256:739ea53b14ef47e4bd82b50d3e53cb68f41e70d0519b8f81886529636b5d7ab1"
)
HARNESS_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
_DRIVER_SCHEMA = "deepseek-harness-h4-driver-v1"
_RECORD_SCHEMA = "deepseek-harness-h4-bridge-record-v1"
_SUMMARY_SCHEMA = "deepseek-harness-h4-bridge-summary-v1"
_FOLLOWUP_SCHEMA = "deepseek-harness-h4-followup-v1"
_MAPPING_SCHEMA = "deepseek-harness-h4-tool-mapping-v1"
_SOURCE_SCHEMA = "deepseek-harness-runtime-source-v1"
_MAX_STDOUT_BYTES = 2 * 1024 * 1024
_MAX_STDERR_BYTES = 64 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _DriverEvent(_StrictModel):
    schema_version: Literal["deepseek-harness-h4-driver-v1"]
    execution_id: str
    sequence: int = Field(ge=0)
    event_type: Literal[
        "driver_started",
        "model_decision",
        "harness_activity",
        "trusted_followup",
        "driver_finished",
        "driver_failed",
    ]
    data: dict[str, Any]


class _BridgeRecord(_StrictModel):
    schema_version: Literal["deepseek-harness-h4-bridge-record-v1"]
    execution_id: str
    session_nonce: str = Field(min_length=32, max_length=32)
    sequence: int = Field(ge=0)
    bridge_pid: int = Field(gt=0)
    kind: Literal["business_tool", "clarification", "submit"]
    tool_name: str
    arguments: dict[str, Any]
    arguments_digest: str
    before_state_digest: str
    after_state_digest: str
    trusted_result: dict[str, Any] | None = None
    trusted_result_digest: str | None = None
    visible_result: dict[str, Any] | None = None
    visible_result_digest: str | None = None
    model_payload_text_sha256: str | None = None
    interaction_events: list[dict[str, Any]] = Field(default_factory=list)
    followup_user_message: str | None = None
    followup_user_message_digest: str | None = None

    @model_validator(mode="after")
    def validate_kind_payload(self) -> Self:
        if self.arguments_digest != sha256_digest(self.arguments):
            raise ValueError("bridge arguments digest mismatch")
        if self.kind == "business_tool":
            if (
                self.trusted_result is None
                or self.visible_result is None
                or self.trusted_result_digest != sha256_digest(self.trusted_result)
                or self.visible_result_digest != sha256_digest(self.visible_result)
                or self.model_payload_text_sha256 is None
            ):
                raise ValueError("business bridge record is incomplete")
        elif self.kind == "clarification":
            if (
                self.tool_name != REQUEST_CLARIFICATION_TOOL_SPEC.name
                or self.visible_result is None
                or self.visible_result_digest != sha256_digest(self.visible_result)
                or not self.interaction_events
            ):
                raise ValueError("clarification bridge record is incomplete")
            if (self.followup_user_message is None) != (
                self.followup_user_message_digest is None
            ):
                raise ValueError("clarification followup identity is incomplete")
            if (
                self.followup_user_message is not None
                and self.followup_user_message_digest
                != "sha256:"
                + hashlib.sha256(self.followup_user_message.encode("utf-8")).hexdigest()
            ):
                raise ValueError("clarification followup digest mismatch")
        elif self.tool_name != SUBMIT_TOOL_SPEC.name:
            raise ValueError("submit bridge record uses the wrong tool")
        return self


class _BridgeSummary(_StrictModel):
    schema_version: Literal["deepseek-harness-h4-bridge-summary-v1"]
    execution_id: str
    session_nonce: str = Field(min_length=32, max_length=32)
    bridge_pid: int = Field(gt=0)
    record_count: int = Field(ge=0)
    records_digest: str
    initial_state_digest: str
    final_state_digest: str
    transaction_count: int = Field(ge=0)
    tool_invocation_count: int = Field(ge=0)
    followup_count: int = Field(ge=0)
    complete: bool
    reason: str
    submitted: bool
    final_answer: str | None
    trace_digest: str | None
    recording_state_digest: str | None
    oracle_artifact_digest: str | None


class _TrustedFollowup(_StrictModel):
    schema_version: Literal["deepseek-harness-h4-followup-v1"]
    execution_id: str
    record_sequence: int = Field(ge=0)
    followup_index: int = Field(ge=0)
    user_message: str
    user_message_digest: str
    directive_digest: str


class _DriverProgress(_StrictModel):
    schema_version: Literal["deepseek-harness-h5-progress-v1"]
    execution_id: str
    status: Literal["running", "completed", "cancelled", "failed"]
    activity_count: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    token_usage: dict[str, int]

    @model_validator(mode="after")
    def validate_token_usage(self) -> Self:
        if set(self.token_usage) != {"prompt_tokens", "completion_tokens"} or any(
            type(value) is not int or value < 0 for value in self.token_usage.values()
        ):
            raise ValueError("driver progress token usage is invalid")
        return self


class _HarnessRecordedProvider:
    """Expose verified Harness activities through the canonical React recorder."""

    version = HARNESS_RUNTIME_VERSION

    def __init__(
        self,
        driver_events: tuple[_DriverEvent, ...],
        *,
        token_usage: dict[str, int],
    ) -> None:
        decisions = [
            event.data
            for event in driver_events
            if event.event_type == "model_decision"
        ]
        actionable_indexes = [
            index
            for index, decision in enumerate(decisions)
            if decision.get("kind") in {"tool_call", "submit"}
        ]
        if not actionable_indexes:
            raise AdapterExecutionError(
                "harness_recording_decisions_missing",
                "the Harness recording contains no actionable decisions",
            )
        last_actionable = actionable_indexes[-1]
        turns: list[ReactTurn] = []
        actionable_index = 0
        terminal_text_count = 0
        for index, decision in enumerate(decisions):
            kind = decision.get("kind")
            if kind in {"tool_call", "submit"}:
                turns.append(
                    ReactTurn(
                        tool_calls=[
                            ReactToolCall(
                                call_id=f"harness-h4-{actionable_index}",
                                name=DeepSeekHarnessAdapter._canonical_tool_name(
                                    decision.get("tool_name")
                                ),
                                arguments=decision.get("arguments", {}),
                            )
                        ],
                        stop_reason="tool_calls",
                    )
                )
                actionable_index += 1
                continue
            if kind != "final_text" or not isinstance(decision.get("text"), str):
                raise AdapterExecutionError(
                    "harness_recording_decision_invalid",
                    "the Harness recording contains an unsupported model decision",
                )
            if index > last_actionable:
                terminal_text_count += 1
                continue
            turns.append(
                ReactTurn(
                    assistant_text=decision["text"],
                    stop_reason="stop",
                )
            )
        if terminal_text_count != 1:
            raise AdapterExecutionError(
                "harness_recording_terminal_invalid",
                "the Harness recording must end with one diagnostic final response",
            )
        self._turns = tuple(turns)
        self._turn_token_usage = tuple(
            {
                key: total // len(self._turns) + (index < total % len(self._turns))
                for key, total in token_usage.items()
            }
            for index in range(len(self._turns))
        )
        self._next_index = 0
        self.last_token_usage: dict[str, int] | None = None

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[Any, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del messages, tools, seed
        if self._next_index >= len(self._turns):
            raise AdapterExecutionError(
                "harness_recording_decisions_exhausted",
                "the canonical recorder requested an extra Harness decision",
            )
        turn = self._turns[self._next_index]
        self.last_token_usage = self._turn_token_usage[self._next_index]
        self._next_index += 1
        return turn

    def assert_consumed(self) -> None:
        if self._next_index != len(self._turns):
            raise AdapterExecutionError(
                "harness_recording_decisions_remaining",
                "the canonical recorder did not consume every Harness activity decision",
            )


class DeepSeekHarnessAdapter(AgentAdapter):
    """Run Harness and publish only correlated Office V2 trusted facts."""

    version = HARNESS_RUNTIME_VERSION

    @staticmethod
    def fixture_model_options(*, timeout_seconds: int) -> ModelOptions:
        """Return the only model identity supported by the H0-H6 runtime."""

        return ModelOptions(
            provider=ModelProvider.FAKE,
            model_name=HARNESS_MODEL_NAME,
            model_digest=HARNESS_MODEL_DIGEST,
            endpoint=None,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def ollama_model_options(
        *,
        model_name: str,
        model_digest: str,
        timeout_seconds: int,
    ) -> ModelOptions:
        """Return the locked in-container Ollama identity used by server runs."""

        return ModelOptions(
            provider=ModelProvider.OLLAMA,
            model_name=model_name,
            model_digest=model_digest,
            endpoint=HARNESS_OLLAMA_ENDPOINT,
            timeout_seconds=timeout_seconds,
        )

    def __init__(self, *, variant_root: Path | None = None) -> None:
        self.variant_root = variant_root or self._default_variant_root()
        self.last_checkpoint_digests: tuple = ()
        self.last_final_state_digest: str | None = None
        self.last_bridge_summary: dict[str, Any] | None = None
        self.last_driver_events: tuple[dict[str, Any], ...] = ()
        self.last_driver_diagnostic: str | None = None
        self.last_token_usage: dict[str, int] | None = None
        self.last_v2_recording_state: OfficeV2RecordingState | None = None
        self.last_v2_oracle_artifact: OfficeV2LiveOracleArtifact | None = None
        self.last_tool_mapping_manifest: dict[str, Any] | None = None
        self._validate_source_lock()

    @property
    def producer_runtime_identity(self) -> dict[str, str]:
        return {
            "producer_runtime_kind": "deepseek_harness",
            "producer_runtime_version": self.version,
            "producer_runtime_composition_digest": self._composition_digest,
        }

    async def execute(self, request: ExecutionRequest) -> AsyncIterator[TraceEvent]:
        self._validate_request(request)
        episode_parent = os.environ.get("TRACE_G_HARNESS_EPISODE_ROOT")
        with tempfile.TemporaryDirectory(
            prefix="trace-g-h4-",
            dir=episode_parent,
        ) as temporary:
            episode_dir = Path(temporary).resolve()
            request_path = episode_dir / "request.json"
            request_path.write_text(request.model_dump_json(), encoding="utf-8")
            tool_specs = (
                *office_v2_model_tool_specs(),
                REQUEST_CLARIFICATION_TOOL_SPEC,
                SUBMIT_TOOL_SPEC,
            )
            mapping_manifest = self.tool_mapping_manifest(
                request.office_v2_execution.tool_catalog_digest,
                tool_specs,
            )
            self.last_tool_mapping_manifest = mapping_manifest
            (episode_dir / "bridge-bootstrap.json").write_text(
                json.dumps(
                    {
                        "schema_version": "deepseek-harness-h4-bootstrap-v1",
                        "execution_id": request.execution_id,
                        "tools": [self._mcp_tool_schema(spec) for spec in tool_specs],
                        "mapping_manifest": mapping_manifest,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            try:
                driver_events = await self._run_driver(request, episode_dir)
            except asyncio.CancelledError:
                self._capture_incomplete_summary(episode_dir)
                self._write_incomplete_recording(request, episode_dir)
                raise
            except (AdapterConfigurationError, AdapterExecutionError):
                self._capture_incomplete_summary(episode_dir)
                self._write_incomplete_recording(request, episode_dir)
                raise
            (
                records,
                summary,
                trace_events,
                recording_state,
                oracle,
                followups,
            ) = self._load_bridge_evidence(request, episode_dir)
            self._correlate(
                request,
                driver_events,
                records,
                summary,
                trace_events,
                recording_state,
                oracle,
                followups,
            )
            self.last_final_state_digest = summary.final_state_digest
            self.last_bridge_summary = summary.model_dump(mode="json")
            self.last_v2_recording_state = recording_state
            self.last_v2_oracle_artifact = oracle
            self.last_driver_events = tuple(
                event.model_dump(mode="json") for event in driver_events
            )
            output_events = trace_events
            if request.recording is not None and request.recording.enabled:
                (
                    output_events,
                    recording_state,
                    oracle,
                ) = await self._write_canonical_recording(
                    request,
                    driver_events,
                    summary,
                    recording_state,
                    oracle,
                )
                self.last_v2_recording_state = recording_state
                self.last_v2_oracle_artifact = oracle
                self.last_final_state_digest = recording_state.session.state_digest
            for event in output_events:
                yield event

    async def _write_canonical_recording(
        self,
        request: ExecutionRequest,
        driver_events: tuple[_DriverEvent, ...],
        bridge_summary: _BridgeSummary,
        bridge_recording_state: OfficeV2RecordingState,
        bridge_oracle: OfficeV2LiveOracleArtifact,
    ) -> tuple[
        tuple[TraceEvent, ...],
        OfficeV2RecordingState,
        OfficeV2LiveOracleArtifact,
    ]:
        from app.adapter.langgraph_react_runtime import LangGraphReactRuntime

        if self.last_token_usage is None:
            raise AdapterExecutionError(
                "harness_recording_token_usage_missing",
                "the Harness recording has no verified Episode token usage",
            )
        provider = _HarnessRecordedProvider(
            driver_events,
            token_usage=self.last_token_usage,
        )
        audit_events = (
            {
                "event_type": "harness_activity_sequence_bound",
                "driver_events_digest": sha256_digest(
                    tuple(
                        event.model_dump(mode="json", exclude_none=False)
                        for event in driver_events
                    )
                ),
                "activity_count": sum(
                    event.event_type == "harness_activity" for event in driver_events
                ),
                "followup_count": bridge_summary.followup_count,
                "token_usage": self.last_token_usage,
            },
        )
        runtime = LangGraphReactRuntime(
            provider_factory=lambda _request: provider,
            producer_runtime_kind="deepseek_harness",
            producer_runtime_version=self.version,
            producer_runtime_composition_digest=self._composition_digest,
            defer_trusted_followup_until_idle=True,
            recording_audit_events=audit_events,
        )
        canonical_events = tuple([event async for event in runtime.execute(request)])
        provider.assert_consumed()
        if runtime.last_v2_session is None or runtime.last_v2_oracle_artifact is None:
            raise AdapterExecutionError(
                "harness_recording_facts_missing",
                "the canonical recorder produced no Office V2 facts",
            )
        recording_state = runtime.last_v2_session.export_recording_state()
        oracle = runtime.last_v2_oracle_artifact
        if (
            recording_state.recording_state_digest
            != bridge_recording_state.recording_state_digest
            or recording_state.session.state_digest != bridge_summary.final_state_digest
            or oracle.trusted_facts_digest != bridge_oracle.trusted_facts_digest
            or oracle.evidence_bundle.bundle_digest
            != bridge_oracle.evidence_bundle.bundle_digest
            or oracle.oracle_result.result_digest
            != bridge_oracle.oracle_result.result_digest
        ):
            raise AdapterExecutionError(
                "harness_recording_fact_divergence",
                "the canonical recording diverged from the trusted Harness bridge facts",
            )
        return canonical_events, recording_state, oracle

    async def _run_driver(
        self,
        request: ExecutionRequest,
        episode_dir: Path,
    ) -> tuple[_DriverEvent, ...]:
        driver = self.variant_root / "runtime" / "driver.mjs"
        node = os.environ.get("TRACE_G_HARNESS_NODE", "node")
        payload = json.dumps(
            {
                "schema_version": "deepseek-harness-h4-request-v1",
                "execution_request": request.model_dump(mode="json", exclude_none=False),
                "episode_dir": str(episode_dir),
                "python_executable": sys.executable,
                "timeout_ms": (request.timeout_seconds + 10) * 1000,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        env = {
            **os.environ,
            "DSH_PYTHON_RUNTIME_ROOT": str(self._python_runtime_root()),
            "PYTHONPATH": os.pathsep.join(str(path) for path in self._python_import_paths()),
        }
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            process = await asyncio.create_subprocess_exec(
                node,
                str(driver),
                cwd=str(self.variant_root),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs,
            )
        except OSError as exc:
            raise AdapterConfigurationError(
                "harness_driver_unavailable",
                "the locked DeepSeek Harness driver could not start",
            ) from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload),
                timeout=request.timeout_seconds + 15,
            )
        except (asyncio.CancelledError, TimeoutError) as exc:
            await self._terminate_process_tree(process, episode_dir)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise AdapterExecutionError(
                "harness_driver_timeout",
                "the DeepSeek Harness driver exceeded the episode deadline",
            ) from exc
        if len(stdout) > _MAX_STDOUT_BYTES or len(stderr) > _MAX_STDERR_BYTES:
            raise AdapterExecutionError(
                "harness_driver_output_limit",
                "the DeepSeek Harness driver exceeded its output limit",
            )
        events = self._parse_driver_events(request.execution_id, stdout)
        self.last_driver_diagnostic = stderr.decode("utf-8", errors="replace")[-4096:]
        if process.returncode != 0 or not events or events[-1].event_type != "driver_finished":
            diagnostic = self._driver_diagnostic_summary(self.last_driver_diagnostic)
            last_event = events[-1].event_type if events else "none"
            raise AdapterExecutionError(
                "harness_driver_failed",
                "the DeepSeek Harness driver did not complete successfully "
                f"(return_code={process.returncode}, last_event={last_event}, "
                f"diagnostic={diagnostic})",
            )
        return events

    @staticmethod
    def _driver_diagnostic_summary(diagnostic: str | None) -> str:
        if not diagnostic:
            return "unavailable"
        first_line = next(
            (line.strip() for line in diagnostic.splitlines() if line.strip()),
            "unavailable",
        )
        printable = "".join(character for character in first_line if character.isprintable())
        return printable[:512] or "unavailable"

    @staticmethod
    async def _terminate_process_tree(
        process: asyncio.subprocess.Process,
        episode_dir: Path,
    ) -> None:
        if process.returncode is not None:
            return
        (episode_dir / "cancel.requested").write_text("cancel\n", encoding="ascii")
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            else:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

    def _capture_incomplete_summary(self, episode_dir: Path) -> None:
        path = episode_dir / "bridge-summary.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.last_bridge_summary = None
            return
        self.last_bridge_summary = value if value.get("complete") is False else None

    def _write_incomplete_recording(
        self,
        request: ExecutionRequest,
        episode_dir: Path,
    ) -> None:
        if request.recording is None or not request.recording.enabled:
            return
        from app.agent.react_contract import ReactMessage
        from app.office_v2_runtime_surface import build_office_v2_runtime_surface
        from app.replay.checkpoint import RecordingSession
        from app.replay.state_codec import OfficeV2StateCodec
        from app.tools.base import ToolRegistry

        progress = None
        with suppress(OSError, ValueError):
            progress = _DriverProgress.model_validate_json(
                (episode_dir / "driver-progress.json").read_bytes()
            )
        if progress is not None:
            self.last_token_usage = dict(progress.token_usage)
        session, surface = build_office_v2_runtime_surface(request)

        class _IncompleteProvider:
            version = HARNESS_RUNTIME_VERSION

        recording = RecordingSession(
            request,
            _IncompleteProvider(),
            ToolRegistry(),
            runtime_id="trace-react-v2-office-v2",
            system_prompt_version=surface.prompt_version,
            system_prompt_digest=surface.prompt_digest,
            state_codec=OfficeV2StateCodec(session.export_recording_state),
            producer_runtime_kind="deepseek_harness",
            producer_runtime_version=self.version,
            producer_runtime_composition_digest=self._composition_digest,
        )
        recording.audit_events.append(
            {
                "event_type": "harness_execution_incomplete",
                "reason": (self.last_bridge_summary or {}).get(
                    "reason", "driver_interrupted"
                ),
                "bridge_record_count": (self.last_bridge_summary or {}).get(
                    "record_count", 0
                ),
                "activity_count": progress.activity_count if progress else 0,
                "decision_count": progress.decision_count if progress else 0,
                "token_usage": progress.token_usage if progress else None,
                "token_usage_status": "recorded" if progress else "unavailable",
            }
        )
        initial_state = {
            "prompt": request.prompt,
            "max_steps": request.max_steps,
            "turn": 0,
            "step_count": 0,
            "messages": [
                ReactMessage(
                    role="system", content=surface.system_message
                ).model_dump(mode="json"),
                ReactMessage(role="user", content=request.prompt).model_dump(
                    mode="json"
                ),
            ],
            "seen_call_ids": [],
            "submitted": False,
            "final_answer": None,
        }
        recording.start(initial_state)
        recording.finalize_incomplete([], reason="harness_execution_incomplete")

    @staticmethod
    def _parse_driver_events(execution_id: str, payload: bytes) -> tuple[_DriverEvent, ...]:
        events: list[_DriverEvent] = []
        try:
            for line in payload.splitlines():
                events.append(_DriverEvent.model_validate_json(line))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AdapterExecutionError(
                "harness_driver_protocol_error",
                "the DeepSeek Harness driver emitted an invalid protocol message",
            ) from exc
        if any(
            event.execution_id != execution_id or event.sequence != index
            for index, event in enumerate(events)
        ):
            raise AdapterExecutionError(
                "harness_driver_protocol_error",
                "the DeepSeek Harness driver event order is invalid",
            )
        return tuple(events)

    @staticmethod
    def _load_bridge_evidence(
        request: ExecutionRequest,
        episode_dir: Path,
    ) -> tuple[
        tuple[_BridgeRecord, ...],
        _BridgeSummary,
        tuple[TraceEvent, ...],
        OfficeV2RecordingState,
        OfficeV2LiveOracleArtifact,
        tuple[_TrustedFollowup, ...],
    ]:
        records_path = episode_dir / "bridge-records.ndjson"
        summary_path = episode_dir / "bridge-summary.json"
        try:
            records_bytes = records_path.read_bytes()
            records = tuple(
                _BridgeRecord.model_validate_json(line)
                for line in records_bytes.splitlines()
            )
            summary = _BridgeSummary.model_validate_json(
                summary_path.read_text(encoding="utf-8")
            )
            trace_path = episode_dir / "bridge-trace.json"
            trace_bytes = trace_path.read_bytes()
            trace_events = tuple(
                TraceEvent.model_validate(item)
                for item in json.loads(trace_bytes)
            )
            recording_path = episode_dir / "bridge-recording-state.json"
            recording_bytes = recording_path.read_bytes()
            recording_state = OfficeV2RecordingState.model_validate_json(
                recording_bytes
            )
            oracle_path = episode_dir / "bridge-oracle.json"
            oracle_bytes = oracle_path.read_bytes()
            oracle = OfficeV2LiveOracleArtifact.model_validate_json(oracle_bytes)
            followups = tuple(
                _TrustedFollowup.model_validate_json(line)
                for line in (episode_dir / "bridge-followups.ndjson")
                .read_bytes()
                .splitlines()
            )
        except (OSError, ValueError) as exc:
            raise AdapterExecutionError(
                "harness_bridge_evidence_invalid",
                "the Office bridge evidence is missing or invalid",
            ) from exc
        expected_digest = "sha256:" + hashlib.sha256(records_bytes).hexdigest()
        if (
            summary.execution_id != request.execution_id
            or summary.records_digest != expected_digest
            or summary.record_count != len(records)
            or summary.trace_digest
            != "sha256:" + hashlib.sha256(trace_bytes).hexdigest()
            or summary.recording_state_digest
            != "sha256:" + hashlib.sha256(recording_bytes).hexdigest()
            or summary.oracle_artifact_digest
            != "sha256:" + hashlib.sha256(oracle_bytes).hexdigest()
            or any(
                record.execution_id != request.execution_id
                or record.session_nonce != summary.session_nonce
                or record.sequence != index
                for index, record in enumerate(records)
            )
            or len(followups) != summary.followup_count
            or any(
                followup.execution_id != request.execution_id
                or followup.followup_index != index
                or followup.record_sequence >= len(records)
                for index, followup in enumerate(followups)
            )
        ):
            raise AdapterExecutionError(
                "harness_bridge_evidence_mismatch",
                "the Office bridge evidence does not describe one contiguous episode",
            )
        return (
            records,
            summary,
            trace_events,
            recording_state,
            oracle,
            followups,
        )

    @staticmethod
    def _canonical_tool_name(value: object) -> str:
        name = str(value)
        marker = "mcp__office_v2__"
        if not name.startswith(marker):
            raise AdapterExecutionError(
                "harness_tool_identity_mismatch",
                "the Harness selected a tool outside the locked Office bridge",
            )
        return name.removeprefix(marker)

    def _correlate(
        self,
        request: ExecutionRequest,
        driver_events: tuple[_DriverEvent, ...],
        records: tuple[_BridgeRecord, ...],
        summary: _BridgeSummary,
        trace_events: tuple[TraceEvent, ...],
        recording_state: OfficeV2RecordingState,
        oracle: OfficeV2LiveOracleArtifact,
        followups: tuple[_TrustedFollowup, ...],
    ) -> None:
        decisions = [event.data for event in driver_events if event.event_type == "model_decision"]
        actionable = [item for item in decisions if item.get("kind") in {"tool_call", "submit"}]
        finished = next(
            (event.data for event in driver_events if event.event_type == "driver_finished"),
            None,
        )
        activities = [
            event.data
            for event in driver_events
            if event.event_type == "harness_activity"
        ]
        injected_followups = [
            event.data
            for event in driver_events
            if event.event_type == "trusted_followup"
        ]
        token_usage = None if finished is None else finished.get("token_usage")
        if (
            not records
            or records[-1].kind != "submit"
            or any(record.kind == "submit" for record in records[:-1])
            or len(actionable) != len(records)
            or finished is None
            or not activities
            or not summary.complete
            or not summary.submitted
            or summary.reason != "submitted"
            or summary.final_answer is None
            or finished.get("final_response") != summary.final_answer
            or finished.get("activity_count") != len(activities)
            or finished.get("decision_count") != len(decisions)
            or len(injected_followups) != len(followups)
            or not isinstance(token_usage, dict)
            or set(token_usage) != {"prompt_tokens", "completion_tokens"}
            or any(type(value) is not int or value < 0 for value in token_usage.values())
            or token_usage["completion_tokens"] > 512 * len(decisions)
        ):
            raise AdapterExecutionError(
                "harness_episode_incomplete",
                "the DeepSeek Harness episode did not satisfy the H4 completion contract",
            )
        self.last_token_usage = dict(token_usage)
        tool_call_count = sum(
            activity.get("event_types", []).count("tool/call")
            for activity in activities
        )
        tool_result_count = sum(
            activity.get("event_types", []).count("tool/result")
            for activity in activities
        )
        if tool_call_count != len(records) or tool_result_count != len(records):
            raise AdapterExecutionError(
                "harness_activity_mismatch",
                "the official Harness activity does not match the trusted bridge facts",
            )
        for index, (decision, record) in enumerate(zip(actionable, records, strict=True)):
            if (
                self._canonical_tool_name(decision.get("tool_name")) != record.tool_name
                or decision.get("arguments") != record.arguments
            ):
                raise AdapterExecutionError(
                    "harness_tool_correlation_failed",
                    "a Harness decision does not match its trusted Office bridge record",
                )
            if index > 0 and (
                decision.get("prior_tool_result_sha256")
                != records[index - 1].model_payload_text_sha256
            ):
                raise AdapterExecutionError(
                    "harness_result_feedback_missing",
                    "a Harness decision did not consume the preceding Office result",
                )
        if actionable[-1]["arguments"].get("answer") != summary.final_answer:
            raise AdapterExecutionError(
                "harness_submit_mismatch",
                "the submitted answer differs from the trusted bridge summary",
            )
        for followup, injected, record in zip(
            followups,
            injected_followups,
            (records[item.record_sequence] for item in followups),
            strict=True,
        ):
            if (
                record.kind != "clarification"
                or record.followup_user_message != followup.user_message
                or record.followup_user_message_digest != followup.user_message_digest
                or injected.get("record_sequence") != followup.record_sequence
                or injected.get("followup_index") != followup.followup_index
                or injected.get("user_message_digest") != followup.user_message_digest
                or injected.get("directive_digest") != followup.directive_digest
                or injected.get("after_activity_index") != followup.followup_index
            ):
                raise AdapterExecutionError(
                    "harness_followup_correlation_failed",
                    "trusted user followup did not cross a verified idle boundary",
                )
        envelope = request.office_v2_execution
        assert envelope is not None
        if (
            summary.initial_state_digest != envelope.initial_state_digest
            or recording_state.session.execution_envelope_digest
            != envelope.canonical_digest()
            or recording_state.session.state_digest != summary.final_state_digest
            or len(recording_state.tool_invocations) != summary.tool_invocation_count
            or trace_events[-1].event_type != "execution_finished"
            or trace_events[-1].state_digest != summary.final_state_digest
            or oracle.execution_id != request.execution_id
            or oracle.trace_digest
            != sha256_digest(
                tuple(
                    event.model_dump(mode="json", exclude_none=False)
                    for event in trace_events
                )
            )
        ):
            raise AdapterExecutionError(
                "harness_state_identity_mismatch",
                "the Office bridge state, TRACE, and Oracle identities do not close",
            )

    def _validate_request(self, request: ExecutionRequest) -> None:
        model = request.model
        if request.office_v2_execution is None:
            raise AdapterConfigurationError(
                "harness_requires_office_v2",
                "the DeepSeek Harness H4 runtime requires an Office V2 envelope",
            )
        fixture_identity = (
            model is not None
            and model.provider is ModelProvider.FAKE
            and model.model_name == HARNESS_MODEL_NAME
            and model.model_digest == HARNESS_MODEL_DIGEST
            and model.endpoint is None
        )
        ollama_identity = (
            model is not None
            and model.provider is ModelProvider.OLLAMA
            and model.endpoint == HARNESS_OLLAMA_ENDPOINT
            and os.environ.get("TRACE_G_FORMAL_AGENT") == "1"
            and os.environ.get("TRACE_G_MODEL_NAME") == model.model_name
            and os.environ.get("TRACE_G_MODEL_DIGEST", "").lower()
            == model.model_digest.lower()
            and os.environ.get("TRACE_G_OLLAMA_ENDPOINT", HARNESS_OLLAMA_ENDPOINT)
            == HARNESS_OLLAMA_ENDPOINT
        )
        if not fixture_identity and not ollama_identity:
            raise AdapterConfigurationError(
                "harness_model_identity_mismatch",
                "the request does not match a locked Harness model identity",
            )

    @staticmethod
    def _mcp_tool_schema(spec: Any) -> dict[str, Any]:
        return {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.arguments_model.model_json_schema(),
        }

    @staticmethod
    def tool_mapping_manifest(
        source_tool_catalog_digest: str,
        specs: tuple[Any, ...],
    ) -> dict[str, Any]:
        payload = {
            "schema_version": _MAPPING_SCHEMA,
            "source_tool_catalog_digest": source_tool_catalog_digest,
            "mappings": [
                {
                    "canonical_name": spec.name,
                    "transport_name": f"mcp__office_v2__{spec.name}",
                    "arguments_schema_digest": sha256_digest(
                        spec.arguments_model.model_json_schema()
                    ),
                }
                for spec in specs
            ],
        }
        return {**payload, "mapping_digest": sha256_digest(payload)}

    def _validate_source_lock(self) -> None:
        lock_path = self.variant_root / "locks" / "runtime-source.json"
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AdapterConfigurationError(
                "harness_runtime_lock_missing",
                "the DeepSeek Harness runtime source lock is unavailable",
            ) from exc
        expected = {
            "schema_version": _SOURCE_SCHEMA,
            "runtime_kind": "deepseek_harness",
            "runtime_version": self.version,
            "upstream_commit": "528c682e061696f5a160f363f236ecbf53cbd006",
            "dependency_install_contract": "npm-ci-package-lock-v1",
        }
        if any(lock.get(key) != value for key, value in expected.items()):
            raise AdapterConfigurationError(
                "harness_runtime_lock_mismatch",
                "the DeepSeek Harness runtime source lock does not match the adapter",
            )
        files = {
            "package_lock_sha256": self.variant_root / "package-lock.json",
            "composition_sha256": self.variant_root / "office_v2.cordis.yml",
            "driver_sha256": self.variant_root / "runtime" / "driver.mjs",
            "model_runtime_sha256": (
                self.variant_root / "runtime" / "model_runtime.mjs"
            ),
            "deterministic_model_sha256": (
                self.variant_root / "runtime" / "deterministic_model.mjs"
            ),
            "mcp_launcher_sha256": self.variant_root / "runtime" / "mcp_launcher.mjs",
            "bridge_sha256": self.variant_root / "runtime" / "office_bridge.py",
            "container_probe_sha256": (
                self.variant_root / "runtime" / "container_probe.py"
            ),
        }
        if any(lock.get(key) != self._file_sha256(path) for key, path in files.items()):
            raise AdapterConfigurationError(
                "harness_runtime_digest_mismatch",
                "the DeepSeek Harness runtime files do not match their source lock",
            )
        self._composition_digest = sha256_digest(
            {
                key: lock[key]
                for key in (
                    *expected,
                    *files,
                )
            }
        )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise AdapterConfigurationError(
                "harness_runtime_file_missing",
                "a locked DeepSeek Harness runtime file is unavailable",
            ) from exc

    @staticmethod
    def _default_variant_root() -> Path:
        configured = os.environ.get("TRACE_G_HARNESS_VARIANT_ROOT")
        if configured:
            return Path(configured).resolve()
        repository = Path(__file__).resolve().parents[3]
        local = repository / "agent_variants" / "deepseek_harness"
        return local if local.is_dir() else Path("/opt/runtime/agent_variants/deepseek_harness")

    @staticmethod
    def _python_runtime_root() -> Path:
        configured = os.environ.get("TRACE_G_PYTHON_RUNTIME_ROOT")
        if configured:
            return Path(configured).resolve()
        repository = Path(__file__).resolve().parents[3]
        return repository if (repository / "src" / "sandbox").is_dir() else Path("/opt/runtime")

    @staticmethod
    def _python_import_paths() -> tuple[Path, Path]:
        repository = Path(__file__).resolve().parents[3]
        if (repository / "agent_image" / "app").is_dir():
            return repository / "agent_image", repository / "src"
        return Path("/opt/runtime"), Path("/opt/runtime/src")


__all__ = [
    "HARNESS_MODEL_DIGEST",
    "HARNESS_MODEL_NAME",
    "HARNESS_OLLAMA_ENDPOINT",
    "HARNESS_RUNTIME_VERSION",
    "DeepSeekHarnessAdapter",
]
