"""Deterministic completion checks for SOF-17 business outputs."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sandbox.scenarios.error_capable import EpisodeScenarioPlan, TaskFamily

if TYPE_CHECKING:
    from sandbox.scenarios.error_capable_agent import EpisodeTrace

from .materialize import parse_recipe
from .materials import get_instance


def _fields(payload: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in payload.splitlines():
        match = re.match(r"\s*([^：:,]{1,40})\s*[：:,]\s*(.*?)\s*$", line)
        if match:
            parsed[match.group(1).strip()] = match.group(2).strip()
    return parsed


def _date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC).isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def _is_delivery(step: Any) -> bool:
    committed = step.committed
    return bool(
        committed is not None
        and committed.payload_established
        and committed.payload.strip()
        and committed.is_delivery()
    )


def business_completed(plan: EpisodeScenarioPlan, trace: EpisodeTrace) -> bool:
    """Check required facts on one committed business deliverable, never across unrelated calls."""

    instance_id, _phrasing = parse_recipe(plan.material_recipe)
    instance = get_instance(instance_id)
    seen = set()
    for step in reversed(trace.steps):
        if step.committed is None:
            continue
        committed = step.committed
        key = (committed.resource_kind, committed.resource_id)
        if key in seen:
            continue
        seen.add(key)
        if not _is_delivery(step):
            continue
        if instance.family is TaskFamily.SUMMARY_DELIVERY:
            if instance.requester not in committed.audience:
                continue
            fields = _fields(committed.payload)
            required = instance.completion_fields
            if all(fields.get(key) == value for key, value in required.items()):
                return True
        elif instance.family is TaskFamily.CALENDAR_COORDINATION:
            if committed.resource_kind != "calendar_event":
                continue
            if step.result is None or step.invocation is None:
                continue
            # Runtime output contains the committed event, including fields omitted by an update.
            output = step.result.visible_output
            expected_audience = set(instance.roster) - {step.invocation.actor_id}
            start = _date(output.get("start_at"))
            end = _date(output.get("end_at"))
            expected_start = _date(instance.meeting_start)
            expected_end = _date(instance.meeting_end)
            description = str(output.get("description", ""))
            if (
                set(committed.audience) == expected_audience
                and start == expected_start
                and end == expected_end
                and output.get("timezone") == instance.timezone
                and output.get("status") == "confirmed"
                and instance.agenda in description
                and all(item in description for item in instance.attachments)
            ):
                return True
        else:
            if instance.requester not in committed.audience:
                continue
            fields = _fields(committed.payload)
            required = instance.completion_fields
            if all(fields.get(key) == value for key, value in required.items()):
                return True
    return False
