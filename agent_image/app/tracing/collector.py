"""Assign an execution-local total order to normalized events."""

from __future__ import annotations

from typing import Any

from app.protocol import TraceEvent
from app.tracing.sanitizer import sanitize


class TraceCollector:
    def __init__(self, execution_id: str, *, schema_version: str | None = None) -> None:
        self.execution_id = execution_id
        self.schema_version = schema_version
        self._sequence = 0

    def emit(
        self,
        event_type: str,
        source: str,
        data: dict[str, Any] | None = None,
        **replay_fields: Any,
    ) -> TraceEvent:
        schema_version = self.schema_version or ("1.1" if replay_fields else "1.0")
        event = TraceEvent(
            schema_version=schema_version,
            execution_id=self.execution_id,
            sequence=self._sequence,
            event_type=event_type,
            source=source,
            data=sanitize(data or {}),
            **replay_fields,
        )
        self._sequence += 1
        return event
