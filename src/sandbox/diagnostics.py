"""Durable failure and tool-result records, for the runs that never produce a bundle.

A bundle is digest-first: the right shape for auditing a finished episode and no help at all
when the session dies before it is written, because then everything a reviewer needs lives only
in memory.  These records keep the same event shape and the same redaction rule the trace
already uses, and add nothing else - a failure stays reviewable without widening what may be
stored.  Credentials never enter a record, and every writer is fail-open: diagnostics must not
change what an episode does.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_DIAGNOSTICS_DIR = Path("/tmp/trace-g-diagnostics")
DIAGNOSTICS_DIR_ENV = "TRACE_G_DIAGNOSTICS_DIR"
MAX_RECORD_BYTES = 65_536

#: Terminal event types that end an execution without a bundle.
FAILURE_EVENT_TYPES = ("execution_error", "execution_timed_out", "execution_cancelled")

#: Keys a tool result uses to say why it refused.  Read generically, because the tool runtime
#: owns the vocabulary; the record carries whatever it said rather than a guess about it.
REFUSAL_KEYS = (
    "allowed",
    "outcome",
    "error",
    "error_code",
    "failure_code",
    "refusal_code",
    "reason",
    "status",
    "message",
)

#: Mirrors ``app.tracing.sanitizer.SENSITIVE_KEYS``; a test keeps the two lists equal so this
#: fallback (used when the agent image is not importable) cannot drift from the trace's rule.
SENSITIVE_KEYS = {
    "api_key",
    "token",
    "authorization",
    "cookie",
    "secret",
    "password",
    "x-sandbox-token",
}


def diagnostics_directory() -> Path:
    """Where diagnostics go, overridable so the host and the container can agree."""

    return Path(os.environ.get(DIAGNOSTICS_DIR_ENV, str(DEFAULT_DIAGNOSTICS_DIR)))


def redact(value: Any) -> Any:
    """The trace's own redaction rule, reused rather than reinvented."""

    try:
        from app.tracing.sanitizer import sanitize

        return sanitize(value)
    except Exception:  # pragma: no cover - only when the image's app package is absent
        return _redact_locally(value)


def _redact_locally(value: Any, *, max_string_length: int = 8_192) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if isinstance(key, str) and key.casefold() in SENSITIVE_KEYS
            else _redact_locally(item, max_string_length=max_string_length)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_locally(item, max_string_length=max_string_length) for item in value]
    if isinstance(value, str) and len(value) > max_string_length:
        return {
            "value": value[:max_string_length],
            "truncated": True,
            "original_length": len(value),
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)[:max_string_length]


def _bounded(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep a record small enough to write: replace oversized detail with its digest."""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) <= MAX_RECORD_BYTES:
        return payload
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    bulky = {"events", "model_visible_result", "received_events"}
    trimmed = {key: value for key, value in payload.items() if key not in bulky}
    trimmed["truncated"] = True
    trimmed["full_record_digest"] = f"sha256:{digest}"
    trimmed["full_record_bytes"] = len(encoded.encode("utf-8"))
    return trimmed


def write_record(
    kind: str,
    execution_id: str,
    name: str,
    payload: dict[str, Any],
    *,
    directory: Path | str | None = None,
) -> str | None:
    """Persist one redacted record; never raise, because diagnostics must not break a run."""

    try:
        target_dir = Path(directory) if directory is not None else diagnostics_directory()
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{execution_id}.{kind}.{name}.json"
        record = _bounded(redact(payload))
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return str(path)
    except Exception:
        return None


def append_tool_result(
    execution_id: str,
    payload: dict[str, Any],
    *,
    directory: Path | str | None = None,
) -> str | None:
    """Append one tool-result record as a JSON line; never raise."""

    try:
        target_dir = Path(directory) if directory is not None else diagnostics_directory()
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{execution_id}.tool-results.jsonl"
        line = json.dumps(_bounded(redact(payload)), ensure_ascii=False, sort_keys=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return str(path)
    except Exception:
        return None


def failure_record(
    event_type: str,
    *,
    execution_id: str,
    sequence: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    """The terminal failure in the trace's own event shape, so the two stay comparable."""

    return {
        "schema_version": "1.2",
        "execution_id": execution_id,
        "sequence": sequence,
        "event_type": event_type,
        "source": "runtime",
        "data": data,
    }


def host_failure_record(
    execution_id: str,
    *,
    received_events: list[dict[str, Any]],
    terminal_sequence: int,
) -> dict[str, Any]:
    """What the host actually saw: every event it received, and where the stream stopped."""

    failure = next(
        (
            item
            for item in received_events
            if item.get("event_type") in FAILURE_EVENT_TYPES
        ),
        None,
    )
    return {
        "record": "host-observation",
        "execution_id": execution_id,
        "terminal_sequence": terminal_sequence,
        "received_event_count": len(received_events),
        "container_failure": failure,
        "received_events": received_events,
    }


def tool_result_record(report: Any) -> dict[str, Any]:
    """Status, refusal rationale and source correlation for one official tool result."""

    visible = getattr(report, "model_visible_result", None)
    refusal: dict[str, Any] = {}
    if isinstance(visible, dict):
        refusal = {key: visible[key] for key in REFUSAL_KEYS if key in visible}
    channel = getattr(report, "channel", None)
    return {
        "record": "tool-result",
        "tool_call_id": report.tool_call_id,
        "tool_name": report.tool_name,
        "action_request_id": report.action_request_id,
        "ordinal": getattr(report, "ordinal", 0),
        "status": (
            "committed"
            if report.committed
            else "blocked"
            if report.blocked
            else "observed"
        ),
        "channel": getattr(channel, "value", str(channel)),
        "committed": report.committed,
        "blocked": report.blocked,
        "post_submit": getattr(report, "post_submit", False),
        "content_digest": report.content_digest,
        "source": {
            "object_id": getattr(report, "source_object_id", None),
            "field": getattr(report, "source_field", None),
            "slot_id": getattr(report, "slot_id", None),
        },
        "registered_units": list(getattr(report, "registered_units", ())),
        "registered_files": list(getattr(report, "registered_files", ())),
        "refusal": refusal or None,
        "model_visible_result": visible,
    }


__all__ = [
    "DEFAULT_DIAGNOSTICS_DIR",
    "DIAGNOSTICS_DIR_ENV",
    "FAILURE_EVENT_TYPES",
    "SENSITIVE_KEYS",
    "append_tool_result",
    "diagnostics_directory",
    "failure_record",
    "host_failure_record",
    "redact",
    "tool_result_record",
    "write_record",
]
