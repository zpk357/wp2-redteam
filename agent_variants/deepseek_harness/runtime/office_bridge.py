"""Trusted MCP bridge from DeepSeek Harness to one Office V2 session."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import sys
from pathlib import Path
from typing import Any

MCP_PROTOCOL_VERSION = "2025-06-18"
RECORD_SCHEMA = "deepseek-harness-h4-bridge-record-v1"
SUMMARY_SCHEMA = "deepseek-harness-h4-bridge-summary-v1"
FOLLOWUP_SCHEMA = "deepseek-harness-h4-followup-v1"
ACK_SCHEMA = "deepseek-harness-h4-followup-ack-v1"
MAX_MESSAGE_BYTES = 10 * 1024 * 1024


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _text_digest(value: str) -> str:
    return _bytes_digest(value.encode("utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(f".tmp-{secrets.token_hex(8)}")
    temporary.write_text(_canonical_json(value), encoding="utf-8")
    os.replace(temporary, path)


class Bridge:
    def __init__(self) -> None:
        request_path = Path(os.environ["DSH_H4_REQUEST_PATH"])
        bootstrap_path = Path(os.environ["DSH_H4_BOOTSTRAP_PATH"])
        self.records_path = Path(os.environ["DSH_H4_RECORDS_PATH"])
        self.summary_path = Path(os.environ["DSH_H4_SUMMARY_PATH"])
        self.followups_path = Path(os.environ["DSH_H4_FOLLOWUPS_PATH"])
        self.followup_ack_path = Path(os.environ["DSH_H4_FOLLOWUP_ACK_PATH"])
        self.trace_path = Path(os.environ["DSH_H4_TRACE_PATH"])
        self.recording_state_path = Path(os.environ["DSH_H4_RECORDING_STATE_PATH"])
        self.oracle_path = Path(os.environ["DSH_H4_ORACLE_PATH"])

        self.request_payload = json.loads(request_path.read_text(encoding="utf-8"))
        bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        self.execution_id = self.request_payload.get("execution_id")
        envelope = self.request_payload.get("office_v2_execution")
        if not isinstance(self.execution_id, str) or not isinstance(envelope, dict):
            raise ValueError("H4 bridge requires an Office V2 envelope")
        if bootstrap.get("execution_id") != self.execution_id:
            raise ValueError("H4 bridge bootstrap identity mismatch")
        tools = bootstrap.get("tools")
        manifest = bootstrap.get("mapping_manifest")
        if not isinstance(tools, list) or not isinstance(manifest, dict):
            raise ValueError("H4 bridge bootstrap tool surface is missing")
        names = [item.get("name") for item in tools]
        if len(names) != len(set(names)) or set(names[-2:]) != {
            "request_clarification",
            "submit",
        }:
            raise ValueError("H4 bridge bootstrap tool surface is invalid")
        mappings = manifest.get("mappings")
        manifest_payload = {
            key: value for key, value in manifest.items() if key != "mapping_digest"
        }
        if (
            manifest.get("source_tool_catalog_digest")
            != envelope.get("tool_catalog_digest")
            or manifest.get("mapping_digest")
            != _text_digest(_canonical_json(manifest_payload))
            or not isinstance(mappings, list)
            or [item.get("canonical_name") for item in mappings] != names
        ):
            raise ValueError("H4 bridge mapping manifest is invalid")

        self._tool_schemas = tools
        self._business_names = frozenset(names[:-2])
        self.request = None
        self.session = None
        self.surface = None
        self.sha256_digest = None
        self.final_answer: str | None = None
        self.nonce = secrets.token_hex(16)
        self.sequence = 0
        self.finalized = False
        self.fatal_reason: str | None = None
        self.pending_followup: dict[str, Any] | None = None
        self.followup_count = 0
        self.initial_state_digest = envelope.get("initial_state_digest")
        if not isinstance(self.initial_state_digest, str):
            raise ValueError("H4 bridge initial state identity is missing")
        self.records_path.parent.mkdir(parents=True, exist_ok=True)
        self.records_path.touch(exist_ok=False)
        self.followups_path.touch(exist_ok=False)

    def _ensure_session(self) -> None:
        if self.session is not None:
            return
        from app.office_v2_runtime_surface import build_office_v2_runtime_surface
        from app.protocol import ExecutionRequest

        from sandbox.replay.digests import sha256_digest

        request = ExecutionRequest.model_validate(self.request_payload)
        session, surface = build_office_v2_runtime_surface(request)
        expected_names = [
            *(spec.name for spec in surface.business_tool_specs),
            *(spec.name for spec in surface.control_tool_specs),
        ]
        actual_names = [item["name"] for item in self._tool_schemas]
        if actual_names != expected_names:
            raise ValueError("H4 bridge bootstrap differs from the formal Agent surface")
        self.request = request
        self.session = session
        self.surface = surface
        self.sha256_digest = sha256_digest

    def tool_schemas(self) -> list[dict[str, Any]]:
        return self._tool_schemas

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.fatal_reason is not None:
            raise RuntimeError("bridge is already failed")
        self._ensure_session()
        assert self.request is not None
        assert self.session is not None
        assert self.surface is not None
        assert self.sha256_digest is not None
        if self.sequence >= self.request.max_steps:
            self.fail("agent_step_budget_exhausted")
            raise RuntimeError("Agent step budget exhausted")
        self._consume_followup_ack()

        before = self.session.episode.state_digest
        if name in self._business_names:
            projection = self.surface.execute_business_tool(name, arguments)
            visible = projection.model_visible_payload()
            trusted = projection.trusted_result.model_dump(mode="json", exclude_none=False)
            after = self.session.episode.state_digest
            model_text = _canonical_json(visible)
            self._append(
                self._record_payload(
                    kind="business_tool",
                    name=name,
                    arguments=arguments,
                    before=before,
                    after=after,
                    visible=visible,
                    trusted=trusted,
                    model_text=model_text,
                )
            )
            return self._mcp_result(visible, model_text)

        if name == "request_clarification":
            execution = self.surface.handle_control_call(name, arguments)
            visible = execution.model_visible_payload()
            if not isinstance(visible, dict):
                raise RuntimeError("clarification did not produce a visible result")
            after = self.session.episode.state_digest
            neutral = [
                {
                    "event_type": event.event_type,
                    "data": event.data,
                    "logical_time": event.logical_time,
                    "input_digest": event.input_digest,
                    "output_digest": event.output_digest,
                    "state_digest": event.state_digest,
                }
                for event in execution.neutral_trace_events()
            ]
            model_text = _canonical_json(visible)
            followup = execution.follow_up_user_message
            followup_digest = _text_digest(followup) if followup is not None else None
            record_sequence = self.sequence
            payload = self._record_payload(
                kind="clarification",
                name=name,
                arguments=arguments,
                before=before,
                after=after,
                visible=visible,
                trusted=None,
                model_text=model_text,
            )
            payload.update(
                {
                    "interaction_events": neutral,
                    "followup_user_message": followup,
                    "followup_user_message_digest": followup_digest,
                }
            )
            self._append(payload)
            if followup is not None:
                directive_digest = self.sha256_digest(
                    {
                        "record_sequence": record_sequence,
                        "interaction_events": neutral,
                        "followup_user_message_digest": followup_digest,
                    }
                )
                pending = {
                    "schema_version": FOLLOWUP_SCHEMA,
                    "execution_id": self.execution_id,
                    "record_sequence": record_sequence,
                    "followup_index": self.followup_count,
                    "user_message": followup,
                    "user_message_digest": followup_digest,
                    "directive_digest": directive_digest,
                }
                self._append_followup(pending)
                self.pending_followup = pending
                self.followup_count += 1
            return self._mcp_result(visible, model_text)

        if name == "submit":
            execution = self.surface.handle_control_call(name, arguments)
            self.final_answer = execution.final_answer
            if not isinstance(self.final_answer, str) or not self.final_answer:
                raise RuntimeError("submit did not produce a final answer")
            after = self.session.episode.state_digest
            self._append(
                self._record_payload(
                    kind="submit",
                    name=name,
                    arguments=arguments,
                    before=before,
                    after=after,
                    visible=None,
                    trusted=None,
                    model_text=None,
                )
            )
            accepted = {"accepted": True}
            return {
                "content": [{"type": "text", "text": _canonical_json(accepted)}],
                "structuredContent": accepted,
                "isError": False,
            }
        raise ValueError("unknown H4 tool")

    def _record_payload(
        self,
        *,
        kind: str,
        name: str,
        arguments: dict[str, Any],
        before: str,
        after: str,
        visible: dict[str, Any] | None,
        trusted: dict[str, Any] | None,
        model_text: str | None,
    ) -> dict[str, Any]:
        assert self.sha256_digest is not None
        return {
            "kind": kind,
            "tool_name": name,
            "arguments": arguments,
            "arguments_digest": self.sha256_digest(arguments),
            "trusted_result": trusted,
            "trusted_result_digest": (
                self.sha256_digest(trusted) if trusted is not None else None
            ),
            "visible_result": visible,
            "visible_result_digest": (
                self.sha256_digest(visible) if visible is not None else None
            ),
            "model_payload_text_sha256": (
                _text_digest(model_text) if model_text is not None else None
            ),
            "interaction_events": [],
            "followup_user_message": None,
            "followup_user_message_digest": None,
            "before_state_digest": before,
            "after_state_digest": after,
        }

    @staticmethod
    def _mcp_result(visible: dict[str, Any], model_text: str) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": model_text}],
            "structuredContent": visible,
            # Policy and validation refusals are ordinary model-visible Office
            # results. Only infrastructure failure is an MCP transport error.
            "isError": visible.get("status") == "failed",
        }

    def _append(self, payload: dict[str, Any]) -> None:
        record = {
            "schema_version": RECORD_SCHEMA,
            "execution_id": self.execution_id,
            "session_nonce": self.nonce,
            "sequence": self.sequence,
            "bridge_pid": os.getpid(),
            **payload,
        }
        encoded = (_canonical_json(record) + "\n").encode("utf-8")
        with self.records_path.open("ab", buffering=0) as stream:
            stream.write(encoded)
            os.fsync(stream.fileno())
        self.sequence += 1

    def _append_followup(self, payload: dict[str, Any]) -> None:
        encoded = (_canonical_json(payload) + "\n").encode("utf-8")
        with self.followups_path.open("ab", buffering=0) as stream:
            stream.write(encoded)
            os.fsync(stream.fileno())

    def _consume_followup_ack(self) -> None:
        if self.pending_followup is None:
            return
        try:
            ack = json.loads(self.followup_ack_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.fail("interaction_protocol_violation")
            raise RuntimeError("trusted followup was not injected at an idle boundary") from exc
        pending = self.pending_followup
        if (
            ack.get("schema_version") != ACK_SCHEMA
            or ack.get("execution_id") != self.execution_id
            or ack.get("record_sequence") != pending["record_sequence"]
            or ack.get("followup_index") != pending["followup_index"]
            or ack.get("user_message_digest") != pending["user_message_digest"]
            or ack.get("directive_digest") != pending["directive_digest"]
        ):
            self.fail("interaction_protocol_violation")
            raise RuntimeError("trusted followup acknowledgement is invalid")
        self.pending_followup = None
        self.followup_ack_path.unlink(missing_ok=True)

    def fail(self, reason: str) -> None:
        if self.fatal_reason is None:
            self.fatal_reason = reason

    def finalize(self, *, reason: str) -> None:
        if self.finalized:
            return
        self.finalized = True
        records_bytes = self.records_path.read_bytes()
        complete = (
            reason == "stdio_closed"
            and self.fatal_reason is None
            and self.final_answer is not None
            and self.pending_followup is None
            and self.session is not None
        )
        artifact_digests: dict[str, str | None] = {
            "trace_digest": None,
            "recording_state_digest": None,
            "oracle_artifact_digest": None,
        }
        if complete:
            try:
                trace_events = self._build_trace()
                assert self.session is not None
                assert self.final_answer is not None
                recording_state = self.session.export_recording_state()
                oracle = self.session.build_live_oracle_artifact(
                    trace_events=trace_events,
                    final_answer=self.final_answer,
                )
                _atomic_json(
                    self.trace_path,
                    [event.model_dump(mode="json", exclude_none=False) for event in trace_events],
                )
                _atomic_json(
                    self.recording_state_path,
                    recording_state.model_dump(mode="json", exclude_none=False),
                )
                _atomic_json(
                    self.oracle_path,
                    oracle.model_dump(mode="json", exclude_none=False),
                )
                artifact_digests = {
                    "trace_digest": _bytes_digest(self.trace_path.read_bytes()),
                    "recording_state_digest": _bytes_digest(
                        self.recording_state_path.read_bytes()
                    ),
                    "oracle_artifact_digest": _bytes_digest(self.oracle_path.read_bytes()),
                }
            except Exception:
                complete = False
                self.fail("final_artifact_build_failed")

        final_state_digest = (
            self.session.episode.state_digest
            if self.session is not None
            else self.initial_state_digest
        )
        summary = {
            "schema_version": SUMMARY_SCHEMA,
            "execution_id": self.execution_id,
            "session_nonce": self.nonce,
            "bridge_pid": os.getpid(),
            "record_count": self.sequence,
            "records_digest": _bytes_digest(records_bytes),
            "initial_state_digest": self.initial_state_digest,
            "final_state_digest": final_state_digest,
            "transaction_count": (
                len(self.session.episode.history) if self.session is not None else 0
            ),
            "tool_invocation_count": (
                len(self.session.runtime.invocations) if self.session is not None else 0
            ),
            "followup_count": self.followup_count,
            "complete": complete,
            "reason": "submitted" if complete else (self.fatal_reason or reason),
            "submitted": self.final_answer is not None,
            "final_answer": self.final_answer,
            **artifact_digests,
        }
        _atomic_json(self.summary_path, summary)

    def _build_trace(self) -> tuple[Any, ...]:
        from app.tracing.collector import TraceCollector

        from sandbox.replay.digests import sha256_digest

        assert self.request is not None
        assert self.session is not None
        assert self.final_answer is not None
        collector = TraceCollector(self.execution_id, schema_version="1.2")
        envelope = self.request.office_v2_execution
        assert envelope is not None
        records = [
            json.loads(line)
            for line in self.records_path.read_text(encoding="utf-8").splitlines()
        ]
        events = [
            collector.emit(
                "execution_started",
                "runtime",
                {
                    **self.request.metadata,
                    "case_id": self.request.case_id,
                    "scenario_id": self.request.scenario_id,
                    "execution_backend": "trace_react_v2",
                    "agent_runtime": "deepseek_harness",
                    "agent_runtime_version": "deepseek-harness-h4-v1",
                },
            ),
            collector.emit(
                "scenario_initialized",
                "trace.office.v2",
                {
                    "scenario_id": self.request.scenario_id,
                    "case_id": self.request.case_id,
                    "execution_envelope_digest": envelope.canonical_digest(),
                    "office_state_digest": envelope.initial_state_digest,
                    "scenario_case_kind": envelope.scenario_case_kind.value,
                },
                state_digest=self.initial_state_digest,
            ),
        ]
        available_tools = [item["name"] for item in self._tool_schemas]
        for record in records:
            turn = record["sequence"] + 1
            call_id = f"harness-h4-{record['sequence']}"
            events.append(
                collector.emit(
                    "model_start",
                    "deepseek-harness-h4-v1",
                    {"turn": turn, "available_tools": available_tools},
                    logical_time=turn,
                )
            )
            decision = {
                "assistant_text": None,
                "stop_reason": "tool_calls",
                "tool_calls": [
                    {
                        "call_id": call_id,
                        "name": record["tool_name"],
                        "arguments": record["arguments"],
                    }
                ],
            }
            events.append(
                collector.emit(
                    "model_end",
                    "deepseek-harness-h4-v1",
                    {"turn": turn, "decision": decision},
                    logical_time=turn,
                    output_digest=sha256_digest(decision),
                )
            )
            if record["kind"] == "submit":
                events.append(
                    collector.emit(
                        "agent_submit",
                        "trace.react",
                        {
                            "call_id": call_id,
                            "call_index": 0,
                            "accepted": True,
                            "answer_digest": sha256_digest(self.final_answer),
                        },
                        logical_time=turn,
                        state_digest=record["after_state_digest"],
                    )
                )
                continue
            source = (
                "agent_control"
                if record["kind"] == "clarification"
                else "controlled_tools"
            )
            events.append(
                collector.emit(
                    "tool_call",
                    source,
                    {
                        "call_id": call_id,
                        "call_index": 0,
                        "name": record["tool_name"],
                        "arguments": record["arguments"],
                    },
                    logical_time=turn,
                    input_digest=record["arguments_digest"],
                    state_digest=record["before_state_digest"],
                )
            )
            for fact in record["interaction_events"]:
                events.append(
                    collector.emit(
                        fact["event_type"],
                        "trace.office.interaction",
                        fact["data"],
                        logical_time=fact["logical_time"],
                        input_digest=fact["input_digest"],
                        output_digest=fact["output_digest"],
                        state_digest=fact["state_digest"],
                    )
                )
            events.append(
                collector.emit(
                    "tool_result",
                    source,
                    {
                        **record["visible_result"],
                        "call_id": call_id,
                        "call_index": 0,
                        "name": record["tool_name"],
                    },
                    logical_time=turn,
                    output_digest=record["visible_result_digest"],
                    state_digest=record["after_state_digest"],
                )
            )
        events.extend(
            [
                collector.emit(
                    "scenario_state_observed",
                    "trace.office.v2",
                    {
                        "transaction_count": len(self.session.episode.history),
                        "tool_invocation_count": len(self.session.runtime.invocations),
                    },
                    state_digest=self.session.episode.state_digest,
                ),
                collector.emit(
                    "execution_finished",
                    "runtime",
                    {
                        "final_answer": self.final_answer,
                        "restricted_data_exposed": False,
                        "scenario_case_kind": envelope.scenario_case_kind.value,
                    },
                    state_digest=self.session.episode.state_digest,
                ),
            ]
        )
        return tuple(events)


def _response(request_id: object, result: object) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main() -> int:
    bridge = Bridge()

    def stop(_signum: int, _frame: object) -> None:
        bridge.finalize(reason="terminated")
        raise SystemExit(143)

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    try:
        for raw in sys.stdin.buffer:
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError("MCP message exceeds size limit")
            message = json.loads(raw)
            request_id = message.get("id")
            method = message.get("method")
            if request_id is None:
                continue
            try:
                if method == "initialize":
                    params = message.get("params", {})
                    result = {
                        "protocolVersion": params.get("protocolVersion", MCP_PROTOCOL_VERSION),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "office-v2-h4", "version": "1.0.0"},
                        "instructions": (
                            "Operate only on this synthetic Office workspace and use "
                            "the provided tools before submitting an answer."
                        ),
                    }
                elif method == "ping":
                    result = {}
                elif method == "tools/list":
                    result = {"tools": bridge.tool_schemas()}
                elif method == "tools/call":
                    params = message.get("params", {})
                    arguments = params.get("arguments", {})
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be an object")
                    result = bridge.call(str(params.get("name", "")), arguments)
                else:
                    sys.stdout.write(
                        _canonical_json(_error(request_id, -32601, "method not found")) + "\n"
                    )
                    sys.stdout.flush()
                    continue
                sys.stdout.write(_canonical_json(_response(request_id, result)) + "\n")
            except ValueError:
                bridge.fail("bridge_invalid_request")
                sys.stdout.write(
                    _canonical_json(_error(request_id, -32602, "invalid request")) + "\n"
                )
            except Exception:
                bridge.fail("bridge_execution_failed")
                sys.stdout.write(
                    _canonical_json(_error(request_id, -32603, "bridge execution failed"))
                    + "\n"
                )
            sys.stdout.flush()
    finally:
        bridge.finalize(reason="stdio_closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
