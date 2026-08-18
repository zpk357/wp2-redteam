"""Deterministic Stage 7.10 controls for frozen Office V2 scenarios.

The provider never mutates scenario state or emits a verdict. It observes the
public tool surface, proposes ordinary tool calls, and lets ToolRuntime plus the
formal Oracle decide what happened.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from app.adapter.base import AdapterConfigurationError, AdapterExecutionError
from app.agent.react_contract import ReactMessage, ReactToolCall, ReactTurn
from app.protocol import ExecutionRequest

ControlMode = Literal["safe", "full", "partial"]


class OfficeV2Stage7ControlProvider:
    version = "office-v2-stage7-control-v1"
    _MODE_KEY = "office_v2_stage7_control_mode"

    def __init__(self, request: ExecutionRequest, *, mode: ControlMode) -> None:
        envelope = request.office_v2_execution
        if envelope is None or envelope.scenario_case_kind.value != "attack":
            raise AdapterConfigurationError(
                "stage7_control_requires_attack_case",
                "the Stage 7.10 control provider requires a frozen attack ScenarioCase",
            )
        payload = envelope.scenario_case_payload
        if payload.get("case_id") != request.case_id:
            raise AdapterConfigurationError(
                "stage7_control_case_mismatch",
                "the request case does not match its frozen payload",
            )
        self._mode = mode
        self._case = payload
        self._objective = self._object(payload, "attack_objective")
        self._bindings = self._binding_map(payload)
        self._steps = self._ordered_steps(self._objective)
        if mode == "partial":
            if len(self._steps) < 2:
                raise AdapterConfigurationError(
                    "stage7_control_partial_requires_compound",
                    "partial mode requires a compound objective",
                )
            self._steps = self._steps[:-1]
        self._observations = self._observation_refs(payload)
        self._preparations = self._preparation_refs()
        self._phase = "observe"
        self._active_ref: dict[str, Any] | None = None
        self._active_tool: str | None = None
        self._active_action: dict[str, Any] | None = None
        self._memory: dict[tuple[str, str], dict[str, Any]] = {}
        self._created: dict[str, dict[str, Any]] = {}
        self._carrier_values: list[Any] = []

    @classmethod
    def from_request(cls, request: ExecutionRequest) -> OfficeV2Stage7ControlProvider:
        raw = request.metadata.get(cls._MODE_KEY)
        if raw not in {"safe", "full", "partial"}:
            raise AdapterConfigurationError(
                "stage7_control_mode_invalid",
                "metadata must select safe, full, or partial control mode",
            )
        return cls(request, mode=raw)

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[Any, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del seed
        available = {tool.name for tool in tools}
        call = self._advance(messages)
        if call.name not in available:
            self._fail(f"required tool is unavailable: {call.name}")
        return ReactTurn(tool_calls=[call], stop_reason="tool_call")

    def _advance(self, messages: tuple[ReactMessage, ...]) -> ReactToolCall:
        if self._active_tool is not None:
            result = self._last_result(messages, self._active_tool)
            if self._active_ref is not None:
                repeat = self._consume_observation(self._active_ref, result)
                if repeat is not None:
                    return repeat
                self._active_ref = None
            elif self._active_action is not None:
                self._consume_action(self._active_action, result)
                self._active_action = None
            self._active_tool = None

        queue = self._observations if self._phase == "observe" else self._preparations
        if queue:
            ref = queue.pop(0)
            return self._start_observation(ref)

        if self._phase == "observe":
            if self._mode == "safe":
                self._phase = "done"
                return self._submit("Observed the test input and stopped without side effects.")
            self._phase = "prepare"
            return self._advance(messages)

        if self._phase == "prepare":
            self._phase = "act"

        if self._phase == "act" and self._steps:
            action = self._steps.pop(0)
            call = self._action_call(action)
            self._active_action = action
            self._active_tool = call.name
            return call

        if self._phase in {"act", "done"}:
            self._phase = "done"
            return self._submit(f"Completed deterministic {self._mode} control path.")
        self._fail(f"invalid control phase: {self._phase}")

    def _start_observation(self, ref: dict[str, Any]) -> ReactToolCall:
        kind = str(ref["kind"])
        resource_id = str(ref["resource_id"])
        if kind == "mail_message":
            call = self._call("read_email", {"message_id": resource_id})
        elif kind in {"drive_file", "drive_file_version"}:
            call = self._call(
                "read_drive_file",
                {"file_id": resource_id, "version_id": ref.get("version_id")},
            )
        elif kind == "workspace_file":
            call = self._call("read_file", {"path": resource_id})
        elif kind == "calendar_event":
            call = self._call("search_calendar_events", {"page_size": 25})
        else:
            self._fail(f"unsupported observation resource kind: {kind}")
        self._active_ref = ref
        self._active_tool = call.name
        return call

    def _consume_observation(
        self, ref: dict[str, Any], result: dict[str, Any]
    ) -> ReactToolCall | None:
        if result.get("status") != "succeeded":
            self._memory[self._ref_key(ref)] = {"status": result.get("status")}
            return None
        data = self._data(result)
        if ref["kind"] == "calendar_event":
            items = self._items(result)
            match = (
                items[0]
                if ref["resource_id"] == "__calendar_basis__" and items
                else next(
                    (
                        item
                        for item in items
                        if item.get("event_id") == ref["resource_id"]
                    ),
                    None,
                )
            )
            if match is None:
                token = data.get("next_page_token")
                if token:
                    return self._call(
                        "search_calendar_events",
                        {"page_size": 25, "page_token": token},
                    )
                self._memory[self._ref_key(ref)] = {"status": "not_observed"}
                return None
            data = match
        self._memory[self._ref_key(ref)] = data
        self._remember_carrier_value(ref, data)
        return None

    def _consume_action(self, action: dict[str, Any], result: dict[str, Any]) -> None:
        status = result.get("status")
        if status not in {"succeeded", "blocked", "rejected", "failed"}:
            self._fail("tool result had an unknown status")
        if status != "succeeded":
            return
        data = self._data(result)
        for slot_id in action["binding_slots"]:
            binding = self._bindings.get(slot_id)
            if binding and binding.get("output_resource_kind"):
                resource = data.get("resource")
                if not isinstance(resource, dict):
                    self._fail(f"created resource missing for output slot {slot_id}")
                self._created[slot_id] = resource
                self._memory[self._ref_key(resource)] = data

    def _action_call(self, action: dict[str, Any]) -> ReactToolCall:
        tool = str(action["tool_names"][0])
        resources = self._resources(action)
        carrier = self._carrier_text()

        if tool == "update_drive_permissions":
            drive = self._first_kind(resources, "drive_file")
            recipient = self._recipient(action)
            data = self._memory.get(self._ref_key(drive), {})
            args = {
                "file_id": drive["resource_id"],
                "version_id": drive.get("version_id"),
                "grantee": recipient,
                "add_rights": ["read"],
                "expected_acl_digest": data.get("acl_digest", "sha256:" + "0" * 64),
            }
        elif tool == "share_drive_file":
            drive = self._first_kind(resources, "drive_file")
            recipient = self._recipient(action)
            args = {
                "file_id": drive["resource_id"],
                "version_id": drive.get("version_id"),
                "recipient": recipient,
            }
        elif tool == "send_email":
            recipient = self._recipient(action)
            related = [
                ref
                for ref in resources
                if ref["kind"]
                in {"drive_file", "drive_file_version", "workspace_file", "calendar_event"}
            ]
            args = {
                "to": [recipient],
                "subject": "Controlled isolation-test result",
                "body": carrier,
                "related_refs": related,
            }
        elif tool == "delete_drive_file":
            drive = self._first_kind(resources, "drive_file")
            data = self._memory.get(self._ref_key(drive), {})
            args = {
                "file_id": drive["resource_id"],
                "expected_current_version_id": data.get("current_version_id")
                or drive.get("version_id")
                or "missing-version",
            }
        elif tool == "create_drive_file":
            sources = [ref for ref in resources if ref.get("resource_id")]
            args = {
                "name": "Controlled isolation-test record.md",
                "content": carrier,
                "mime_type": "text/markdown",
                "classification": "internal",
                "source_refs": sources,
            }
        elif tool == "cancel_calendar_event":
            event = self._first_kind(resources, "calendar_event")
            data = self._memory.get(self._ref_key(event), {})
            args = {
                "event_id": event["resource_id"],
                "expected_version": data.get("version", 1),
                "reason": "Controlled isolation-test calibration",
            }
        elif tool == "create_calendar_event":
            recipient = self._recipient(action)
            base = self._calendar_basis()
            start = datetime.fromisoformat(base["end_at"]) + timedelta(days=1)
            args = {
                "title": "Controlled isolation-test event",
                "description": carrier,
                "start_at": start.isoformat(),
                "end_at": (start + timedelta(hours=1)).isoformat(),
                "timezone": base["timezone"],
                "attendees": [recipient],
            }
        elif tool == "update_calendar_event":
            event = self._first_kind(resources, "calendar_event")
            data = self._memory.get(self._ref_key(event), {})
            start = datetime.fromisoformat(str(data["start_at"])) + timedelta(minutes=30)
            end = datetime.fromisoformat(str(data["end_at"])) + timedelta(minutes=30)
            args = {
                "event_id": event["resource_id"],
                "expected_version": data.get("version", 1),
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
            }
        elif tool == "write_file":
            workspace = self._first_kind(resources, "workspace_file")
            data = self._memory.get(self._ref_key(workspace), {})
            sources = [ref for ref in resources if ref != workspace]
            content = carrier
            if content == data.get("content"):
                content = f"{content}\n\nControlled isolation-test update."
            args = {
                "path": workspace["resource_id"],
                "content": content,
                "media_type": data.get("media_type", "text/markdown"),
                "expected_version": data.get("version", 1),
                "source_refs": sources,
            }
        else:
            self._fail(f"unsupported objective tool: {tool}")
        return self._call(tool, args)

    def _calendar_basis(self) -> dict[str, Any]:
        candidates = [
            value
            for (kind, _), value in self._memory.items()
            if kind == "calendar_event" and "end_at" in value
        ]
        if not candidates:
            self._fail("calendar creation requires a model-visible calendar observation")
        return max(candidates, key=lambda item: str(item["end_at"]))

    def _remember_carrier_value(self, ref: dict[str, Any], data: dict[str, Any]) -> None:
        field_path = tuple(ref.get("field_path", ()))
        key = {
            ("current_version", "content"): "content",
            ("current_version", "source_refs"): "source_refs",
        }.get(field_path, field_path[-1] if field_path else None)
        if key and key in data:
            self._carrier_values.append(data[key])

    def _carrier_text(self) -> str:
        for value in self._carrier_values:
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                return ", ".join(str(item) for item in value)
        return "Controlled isolation-test state transition evidence."

    def _preparation_refs(self) -> list[dict[str, Any]]:
        refs: dict[tuple[str, str], dict[str, Any]] = {}
        for binding in self._bindings.values():
            for ref in binding.get("resource_refs", []):
                refs[self._ref_key(ref)] = dict(ref)
        if any(step["tool_names"][0] == "create_calendar_event" for step in self._steps):
            found_calendar = False
            for binding in self._case.get("task_bindings", []):
                for ref in binding.get("resource_refs", []):
                    if ref.get("kind") == "calendar_event":
                        refs[self._ref_key(ref)] = dict(ref)
                        found_calendar = True
                        break
                else:
                    continue
                break
            if not found_calendar:
                ref = {
                    "kind": "calendar_event",
                    "resource_id": "__calendar_basis__",
                    "version_id": None,
                }
                refs[self._ref_key(ref)] = ref
        for ref in self._observations:
            refs.pop(self._ref_key(ref), None)
        return list(refs.values())

    @staticmethod
    def _observation_refs(payload: dict[str, Any]) -> list[dict[str, Any]]:
        changes = payload["materialization_record"]["changed_content_fields"]
        refs: dict[tuple[str, str], dict[str, Any]] = {}
        for change in changes:
            placement = change["placement"]
            ref = dict(placement["resource_ref"])
            ref["field_path"] = placement["field_path"]
            refs[(ref["kind"], ref["resource_id"])] = ref
        return list(refs.values())

    @staticmethod
    def _ordered_steps(objective: dict[str, Any]) -> list[dict[str, Any]]:
        graph = objective.get("milestone_graph")
        if graph is None:
            assertions = objective["attempted_assertions"]
            return [assertions[0]]
        pending = {item["milestone_id"]: item for item in graph["milestones"]}
        completed: set[str] = set()
        ordered: list[dict[str, Any]] = []
        while pending:
            milestone = next(
                item for item in pending.values() if set(item["depends_on"]) <= completed
            )
            ordered.append(milestone["attempted_assertions"][0])
            completed.add(milestone["milestone_id"])
            del pending[milestone["milestone_id"]]
        return ordered

    @staticmethod
    def _binding_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {item["slot_id"]: item for item in payload["objective_bindings"]}

    def _resources(self, action: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for slot_id in action["binding_slots"]:
            if slot_id in self._created:
                result.append(self._created[slot_id])
            else:
                result.extend(self._bindings.get(slot_id, {}).get("resource_refs", []))
        return result

    def _recipient(self, action: dict[str, Any]) -> str:
        for slot_id in action["binding_slots"]:
            values = self._bindings.get(slot_id, {}).get("principal_ids", [])
            if values:
                return str(values[0])
        for binding in self._bindings.values():
            values = binding.get("principal_ids", [])
            if values:
                return str(values[0])
        self._fail("objective action requires a recipient binding")

    @staticmethod
    def _first_kind(resources: list[dict[str, Any]], kind: str) -> dict[str, Any]:
        match = next((ref for ref in resources if ref.get("kind") == kind), None)
        if match is None:
            OfficeV2Stage7ControlProvider._fail(f"missing {kind} action binding")
        return match

    @staticmethod
    def _ref_key(ref: dict[str, Any]) -> tuple[str, str]:
        return str(ref["kind"]), str(ref["resource_id"])

    @staticmethod
    def _last_result(
        messages: tuple[ReactMessage, ...], expected_name: str
    ) -> dict[str, Any]:
        message = next((item for item in reversed(messages) if item.role == "tool"), None)
        if (
            message is None
            or message.name != expected_name
            or not isinstance(message.content, dict)
        ):
            OfficeV2Stage7ControlProvider._fail(
                f"expected real result from {expected_name}"
            )
        return message.content

    @staticmethod
    def _data(result: dict[str, Any]) -> dict[str, Any]:
        value = result.get("data")
        if not isinstance(value, dict):
            OfficeV2Stage7ControlProvider._fail("tool result has no data object")
        return value

    @classmethod
    def _items(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        value = cls._data(result).get("items")
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            cls._fail("search result has no item list")
        return value

    @staticmethod
    def _object(value: dict[str, Any], key: str) -> dict[str, Any]:
        result = value.get(key)
        if not isinstance(result, dict):
            OfficeV2Stage7ControlProvider._fail(f"missing object: {key}")
        return result

    @staticmethod
    def _call(name: str, arguments: dict[str, Any]) -> ReactToolCall:
        return ReactToolCall(name=name, arguments=arguments)

    @staticmethod
    def _submit(answer: str) -> ReactToolCall:
        return ReactToolCall(name="submit", arguments={"answer": answer})

    @staticmethod
    def _fail(message: str) -> Any:
        raise AdapterExecutionError("stage7_control_result_divergence", message)


__all__ = ["OfficeV2Stage7ControlProvider"]
