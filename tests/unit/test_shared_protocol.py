from __future__ import annotations

import pytest
from pydantic import ValidationError

from sandbox.protocol import TraceEvent


def test_trace_event_rejects_retired_schema() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            schema_version="1.1",
            execution_id="exec-old",
            sequence=0,
            event_type="execution_started",
            source="runtime",
        )
