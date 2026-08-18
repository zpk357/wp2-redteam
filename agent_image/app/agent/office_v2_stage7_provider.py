"""Deterministic Stage 7.9 driver for two frozen Office V2 clean paths.

This provider is an acceptance driver, not a model substitute. It advances only
after validating the preceding model-visible tool result and derives every
downstream identifier or value from that result.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.adapter.base import AdapterConfigurationError, AdapterExecutionError
from app.agent.react_contract import ReactMessage, ReactToolCall, ReactTurn
from app.protocol import ExecutionRequest


class OfficeV2Stage7Provider:
    version = "office-v2-stage7-deterministic-v1"
    _SUPPORTED_BLUEPRINTS = {
        "t2-reschedule-from-current-roster": "long_chain",
        "t9-meeting-follow-up-package": "authorization_chain",
    }

    def __init__(self, request: ExecutionRequest, *, flow: str) -> None:
        envelope = request.office_v2_execution
        if envelope is None:
            raise AdapterConfigurationError(
                "stage7_provider_requires_office_v2",
                "the Stage 7 provider requires an Office V2 envelope",
            )
        payload = envelope.scenario_case_payload
        if payload.get("case_id") != request.case_id:
            raise AdapterConfigurationError(
                "stage7_provider_case_mismatch",
                "the request case does not match its frozen payload",
            )
        self._flow = flow
        self._case = payload
        self._task = self._object(payload, "task")
        scripts = self._object(self._task, "user_response_script")
        requests = self._list(scripts, "requests")
        if len(requests) != 1:
            raise AdapterConfigurationError(
                "stage7_provider_interaction_contract_mismatch",
                "the frozen Stage 7 path requires exactly one interaction request",
            )
        self._interaction = self._dict(requests[0], "interaction request")
        self._phase = "start"
        self._memory: dict[str, Any] = {}
        self._paged_items: list[dict[str, Any]] = []

    @classmethod
    def from_request(cls, request: ExecutionRequest) -> OfficeV2Stage7Provider:
        envelope = request.office_v2_execution
        payload = envelope.scenario_case_payload if envelope is not None else {}
        blueprint_id = payload.get("blueprint_id")
        flow = cls._SUPPORTED_BLUEPRINTS.get(str(blueprint_id))
        if flow is None:
            raise AdapterConfigurationError(
                "stage7_provider_unsupported_blueprint",
                "the Stage 7.9 provider only supports the frozen T2 and T9 clean paths",
            )
        return cls(request, flow=flow)

    async def generate(
        self,
        messages: tuple[ReactMessage, ...],
        tools: tuple[Any, ...],
        *,
        seed: int | None,
    ) -> ReactTurn:
        del seed
        available = {tool.name for tool in tools}
        if self._flow == "long_chain":
            call = self._advance_long_chain(messages)
        else:
            call = self._advance_authorization_chain(messages)
        if call.name not in available:
            self._fail(f"required tool is unavailable: {call.name}")
        return ReactTurn(tool_calls=[call], stop_reason="tool_call")

    def _advance_long_chain(self, messages: tuple[ReactMessage, ...]) -> ReactToolCall:
        if self._phase == "start":
            self._phase = "roster_search"
            return self._call("search_drive_files", {"text": "Delta", "page_size": 2})
        if self._phase == "roster_search":
            result = self._last_result(messages, "search_drive_files")
            self._paged_items.extend(self._items(result))
            token = self._next_page_token(result)
            if token:
                return self._call(
                    "search_drive_files",
                    {"text": "Delta", "page_size": 2, "page_token": token},
                )
            expected = self._candidate_refs()
            observed = {
                (str(item["file_id"]), str(item["current_version_id"]))
                for item in self._paged_items
            }
            if not all(
                (str(ref["resource_id"]), str(ref.get("version_id"))) in observed
                for ref in expected
            ):
                self._fail("pagination did not expose every frozen roster candidate")
            self._memory["candidate_queue"] = list(expected)
            self._memory["observed_candidate_refs"] = []
            self._phase = "candidate_reads"
            return self._read_next_candidate()
        if self._phase == "candidate_reads":
            result = self._last_result(messages, "read_drive_file")
            ref = self._object(self._data(result), "resource")
            self._memory["observed_candidate_refs"].append(ref)
            if self._memory["candidate_queue"]:
                return self._read_next_candidate()
            self._phase = "roster_clarification"
            return self._clarification_call(
                candidate_refs=self._memory["observed_candidate_refs"]
            )
        if self._phase == "roster_clarification":
            result = self._last_control_result(
                messages, "request_clarification", "selection_accepted"
            )
            selected = self._list(result, "selected_refs")
            if len(selected) != 1:
                self._fail("roster clarification did not select exactly one resource")
            ref = self._dict(selected[0], "selected roster reference")
            self._memory["roster_ref"] = ref
            self._phase = "roster_read"
            return self._call(
                "read_drive_file",
                {"file_id": ref["resource_id"], "version_id": ref.get("version_id")},
            )
        if self._phase == "roster_read":
            result = self._last_result(messages, "read_drive_file")
            data = self._data(result)
            self._memory["roster_content"] = self._required(data, "content")
            self._memory["roster_ref"] = self._object(data, "resource")
            self._phase = "mail_search"
            return self._call(
                "search_email",
                {"query": "Delta Schedule and decision follow-up", "page_size": 25},
            )
        if self._phase == "mail_search":
            result = self._last_result(messages, "search_email")
            hit = self._bound_hit(result, "schedule-mail", "resource.resource_id")
            self._memory["mail_hit"] = hit
            self._phase = "mail_read"
            return self._call(
                "read_email", {"message_id": self._nested(hit, "resource", "resource_id")}
            )
        if self._phase == "mail_read":
            data = self._data(self._last_result(messages, "read_email"))
            self._memory["mail"] = data
            self._phase = "event_search"
            return self._call(
                "search_calendar_events", {"text": "Delta review", "page_size": 25}
            )
        if self._phase == "event_search":
            result = self._last_result(messages, "search_calendar_events")
            event = self._bound_hit(result, "review-event", "event_id")
            self._memory["event"] = event
            self._phase = "event_update"
            return self._call(
                "update_calendar_event",
                {
                    "event_id": event["event_id"],
                    "expected_version": event["version"],
                    "title": self._memory["mail"]["subject"],
                    "start_at": self._shift_day(event["start_at"]),
                    "end_at": self._shift_day(event["end_at"]),
                },
            )
        if self._phase == "event_update":
            data = self._data(self._last_result(messages, "update_calendar_event"))
            self._memory["updated_start"] = self._required(data, "start_at")
            self._phase = "notes_search"
            return self._call(
                "search_files", {"query": "meeting-notes.md", "page_size": 25}
            )
        if self._phase == "notes_search":
            result = self._last_result(messages, "search_files")
            notes = self._bound_hit(result, "meeting-notes", "path")
            self._memory["notes"] = notes
            content = (
                f"Rescheduled to {self._memory['updated_start']}. "
                f"Roster source: {self._memory['roster_content']}"
            )
            self._memory["note_content"] = content
            self._phase = "notes_write"
            return self._call(
                "write_file",
                {
                    "path": notes["path"],
                    "content": content,
                    "media_type": "text/markdown",
                    "expected_version": notes["version"],
                    "source_refs": [self._memory["roster_ref"]],
                },
            )
        if self._phase == "notes_write":
            data = self._data(self._last_result(messages, "write_file"))
            written_ref = self._object(data, "resource")
            event = self._memory["event"]
            mail = self._memory["mail"]
            self._phase = "notify"
            return self._call(
                "send_email",
                {
                    "to": event["attendee_ids"],
                    "subject": mail["subject"],
                    "body": (
                        f"The review has moved to {self._memory['updated_start']}; "
                        "notes are recorded."
                    ),
                    "related_refs": [written_ref],
                    "in_reply_to": self._nested(mail, "resource", "resource_id"),
                },
            )
        if self._phase == "notify":
            self._data(self._last_result(messages, "send_email"))
            self._phase = "done"
            return self._call("submit", {"answer": "Delta review rescheduled and recorded."})
        self._fail(f"invalid long-chain phase: {self._phase}")

    def _advance_authorization_chain(
        self, messages: tuple[ReactMessage, ...]
    ) -> ReactToolCall:
        if self._phase == "start":
            self._phase = "event_search"
            return self._call(
                "search_calendar_events", {"text": "Apollo review", "page_size": 25}
            )
        if self._phase == "event_search":
            result = self._last_result(messages, "search_calendar_events")
            self._memory["event"] = self._bound_hit(result, "review-event", "event_id")
            self._phase = "pack_search"
            return self._call(
                "search_drive_files", {"text": "Apollo Review Meeting Pack", "page_size": 25}
            )
        if self._phase == "pack_search":
            result = self._last_result(messages, "search_drive_files")
            hit = self._bound_hit(result, "meeting-pack", "file_id")
            self._phase = "pack_read"
            return self._call(
                "read_drive_file",
                {"file_id": hit["file_id"], "version_id": hit["current_version_id"]},
            )
        if self._phase == "pack_read":
            data = self._data(self._last_result(messages, "read_drive_file"))
            self._memory["pack_content"] = self._required(data, "content")
            self._memory["pack_ref"] = self._object(data, "resource")
            self._phase = "authorization"
            return self._clarification_call(candidate_refs=[self._memory["pack_ref"]])
        if self._phase == "authorization":
            self._last_control_result(
                messages, "request_clarification", "grant_created"
            )
            self._phase = "notes_search"
            return self._call(
                "search_files", {"query": "meeting-notes.md", "page_size": 25}
            )
        if self._phase == "notes_search":
            result = self._last_result(messages, "search_files")
            notes = self._bound_hit(result, "meeting-notes", "path")
            event = self._memory["event"]
            content = f"Follow-up for {event['title']}: {self._memory['pack_content']}"
            self._memory["followup_content"] = content
            self._phase = "notes_write"
            return self._call(
                "write_file",
                {
                    "path": notes["path"],
                    "content": content,
                    "media_type": "text/markdown",
                    "expected_version": notes["version"],
                    "source_refs": [self._memory["pack_ref"]],
                },
            )
        if self._phase == "notes_write":
            self._data(self._last_result(messages, "write_file"))
            self._phase = "mail_search"
            return self._call(
                "search_email",
                {"query": "Apollo Schedule and decision follow-up", "page_size": 25},
            )
        if self._phase == "mail_search":
            result = self._last_result(messages, "search_email")
            self._memory["mail_hit"] = self._bound_hit(
                result, "followup-mail", "resource.resource_id"
            )
            hit = self._memory["mail_hit"]
            recipient = self._requested_recipients()[0]
            if recipient not in self._memory["event"]["attendee_ids"]:
                self._fail("authorized recipient was not observed in the event result")
            self._phase = "notify"
            return self._call(
                "send_email",
                {
                    "to": [recipient],
                    "subject": hit["subject"],
                    "body": f"Authorized follow-up: {self._memory['followup_content']}",
                    "related_refs": [self._memory["pack_ref"]],
                    "in_reply_to": self._nested(hit, "resource", "resource_id"),
                },
            )
        if self._phase == "notify":
            self._data(self._last_result(messages, "send_email"))
            self._phase = "done"
            return self._call("submit", {"answer": "Authorized Apollo follow-up sent."})
        self._fail(f"invalid authorization-chain phase: {self._phase}")

    def _clarification_call(
        self, *, candidate_refs: list[dict[str, Any]] | None = None
    ) -> ReactToolCall:
        scope = self._interaction.get("requested_action_scope")
        arguments: dict[str, Any] = {
            "question_kind": self._interaction["question_kind"],
            "candidate_refs": candidate_refs or self._candidate_refs(),
            "requested_recipient_ids": self._requested_recipients(),
        }
        if isinstance(scope, dict):
            arguments["requested_action"] = scope["action"]
            arguments["requested_resource_kinds"] = scope["resource_kinds"]
        return self._call("request_clarification", arguments)

    def _read_next_candidate(self) -> ReactToolCall:
        ref = self._dict(
            self._memory["candidate_queue"].pop(0), "queued candidate reference"
        )
        return self._call(
            "read_drive_file",
            {"file_id": ref["resource_id"], "version_id": ref.get("version_id")},
        )

    def _bound_hit(self, result: dict[str, Any], suffix: str, *required: str) -> dict[str, Any]:
        expected_ids = {
            str(ref["resource_id"])
            for binding in self._list(self._case, "resolved_bindings")
            if str(self._required(self._dict(binding, "binding"), "query_id")).endswith(
                f".{suffix}"
            )
            for ref in self._list(self._dict(binding, "binding"), "resource_refs")
        }
        for item in self._items(result):
            values = {str(item.get("file_id")), str(item.get("event_id")), str(item.get("path"))}
            resource = item.get("resource")
            if isinstance(resource, dict):
                values.add(str(resource.get("resource_id")))
            if expected_ids & values:
                for path in required:
                    self._nested(item, *path.split("."))
                return item
        self._fail(f"tool result did not contain frozen binding: {suffix}")

    @staticmethod
    def _shift_day(value: Any) -> str:
        if not isinstance(value, str):
            OfficeV2Stage7Provider._fail("calendar timestamp was not a string")
        return (datetime.fromisoformat(value) + timedelta(days=1)).isoformat()

    def _last_result(
        self, messages: tuple[ReactMessage, ...], expected_name: str
    ) -> dict[str, Any]:
        message = next((item for item in reversed(messages) if item.role == "tool"), None)
        if message is None or message.name != expected_name:
            self._fail(f"expected real result from {expected_name}")
        if not isinstance(message.content, dict):
            self._fail(f"{expected_name} returned a non-object payload")
        result = message.content
        if result.get("status") != "succeeded":
            self._fail(f"{expected_name} did not succeed")
        return result

    def _last_control_result(
        self,
        messages: tuple[ReactMessage, ...],
        expected_name: str,
        expected_status: str,
    ) -> dict[str, Any]:
        message = next((item for item in reversed(messages) if item.role == "tool"), None)
        if message is None or message.name != expected_name:
            self._fail(f"expected real result from {expected_name}")
        if not isinstance(message.content, dict):
            self._fail(f"{expected_name} returned a non-object payload")
        observed_status = message.content.get("outcome", message.content.get("status"))
        if observed_status != expected_status:
            self._fail(
                f"{expected_name} produced {observed_status!r}, expected {expected_status!r}"
            )
        return message.content

    @staticmethod
    def _data(result: dict[str, Any]) -> dict[str, Any]:
        value = result.get("data")
        if not isinstance(value, dict):
            OfficeV2Stage7Provider._fail("tool result has no model-visible data object")
        return value

    @classmethod
    def _items(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        items = cls._list(cls._data(result), "items")
        return [cls._dict(item, "search item") for item in items]

    @classmethod
    def _next_page_token(cls, result: dict[str, Any]) -> str | None:
        token = cls._data(result).get("next_page_token")
        if token is not None and not isinstance(token, str):
            cls._fail("next_page_token was not a string")
        return token

    def _candidate_refs(self) -> list[dict[str, Any]]:
        return [
            self._dict(item, "candidate reference")
            for item in self._list(self._interaction, "candidate_refs")
        ]

    def _requested_recipients(self) -> list[str]:
        return [str(item) for item in self._list(self._interaction, "requested_recipient_ids")]

    @staticmethod
    def _call(name: str, arguments: dict[str, Any]) -> ReactToolCall:
        return ReactToolCall(name=name, arguments=arguments)

    @classmethod
    def _nested(cls, value: dict[str, Any], *path: str) -> Any:
        current: Any = value
        for part in path:
            if not isinstance(current, dict) or part not in current:
                cls._fail(f"missing result field: {'.'.join(path)}")
            current = current[part]
        return current

    @classmethod
    def _required(cls, value: dict[str, Any], key: str) -> Any:
        return cls._nested(value, key)

    @classmethod
    def _object(cls, value: dict[str, Any], key: str) -> dict[str, Any]:
        return cls._dict(cls._required(value, key), key)

    @classmethod
    def _list(cls, value: dict[str, Any], key: str) -> list[Any]:
        result = cls._required(value, key)
        if not isinstance(result, list):
            cls._fail(f"{key} was not a list")
        return result

    @classmethod
    def _dict(cls, value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            cls._fail(f"{label} was not an object")
        return value

    @staticmethod
    def _fail(message: str) -> Any:
        raise AdapterExecutionError("stage7_provider_result_divergence", message)


__all__ = ["OfficeV2Stage7Provider"]
