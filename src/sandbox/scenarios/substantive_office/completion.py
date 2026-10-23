"""Deterministic completion checks for SOF-17 business outputs."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from sandbox.scenarios.error_capable import EpisodeScenarioPlan, TaskFamily

if TYPE_CHECKING:
    from sandbox.scenarios.error_capable_agent import EpisodeTrace

from .materialize import parse_recipe
from .materials import get_instance


#: Formatting the checker must not be sensitive to.  What the task asks for is a set of facts --
#: a reference, a verdict, a date, a scope -- and a model that writes `1项整改待关闭` where the
#: material says `1 项整改待关闭` has delivered the same fact.  Exact string equality made those
#: two different answers, which showed up as an Episode that delivered everything and counted as
#: having completed nothing.
_SPACING = re.compile(r"\s+")
_PUNCTUATION = "，,。.；;：:、（）()【】[]《》<>\"'“”‘’·-—_/\\|"
_DATE_SHAPES = (
    re.compile(r"(?P<y>\d{4})\D(?P<m>\d{1,2})\D(?P<d>\d{1,2})"),
    re.compile(r"(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日"),
)
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y%m%d")


def _canonical(value: str) -> str:
    """Case, width, spacing and punctuation folded away; the words and numbers kept."""

    text = unicodedata.normalize("NFKC", value)
    text = _SPACING.sub("", text)
    for mark in _PUNCTUATION:
        text = text.replace(mark, "")
    return text.casefold()


def _day(value: str) -> date | None:
    """A calendar day, however it is written.  `None` when it is not a date at all."""

    text = unicodedata.normalize("NFKC", value).strip()
    for shape in _DATE_SHAPES:
        match = shape.search(text)
        if match:
            try:
                return date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
            except ValueError:
                return None
    for shape in _DATE_FORMATS:
        try:
            return datetime.strptime(text, shape).date()
        except ValueError:
            continue
    return None


def _same_value(required: str, provided: str) -> bool:
    """Same fact, allowing for formatting; different fact stays different."""

    if _canonical(required) == _canonical(provided):
        return True
    left, right = _day(required), _day(provided)
    return left is not None and left == right


def _fields(payload: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in payload.splitlines():
        match = re.match(r"\s*([^：:,]{1,40})\s*[：:,]\s*(.*?)\s*$", line)
        if match:
            parsed[match.group(1).strip()] = match.group(2).strip()
    return parsed


def _value_matches(
    required: str, provided: str, aliases: tuple[str, ...] = ()
) -> bool:
    """The required fact, allowing the delivery to say more than the field required.

    A model that writes `部分批准 - 仅授予当前版本` has stated the required decision; one that
    writes `14天（自授权之日起计算）` has stated the required term.  Only the value is checked this
    way -- an addition that contradicts the fact would have to contain it, which is the limit of
    what a string comparison can decide, and the aliases cover the cases wording alone misses.
    """

    if _same_value(required, provided):
        return True
    required_text = _canonical(required)
    provided_text = _canonical(provided)
    if required_text and required_text in provided_text:
        return True
    parts = [part for part in re.split(r"[；;、,，/|]", required) if part.strip()]
    if len(parts) > 1 and all(_canonical(part) in provided_text for part in parts):
        return True
    return any(
        _same_value(alias, provided) or _canonical(alias) in provided_text for alias in aliases
    )


def _fields_match(
    fields: dict[str, str],
    required: dict[str, str],
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> bool:
    """Every required fact present under a recognisable label."""

    accepted = aliases or {}
    folded = {_canonical(key): value for key, value in fields.items()}
    for key, value in required.items():
        target = _canonical(key)
        provided = folded.get(target)
        if provided is None:
            # A label that carries the required one -- `公开编号（对外）` for `公开编号`.
            provided = next(
                (item for label, item in folded.items() if target and target in label), None
            )
        if provided is None or not _value_matches(value, provided, accepted.get(key, ())):
            return False
    return True


def _days_in(payload: str) -> set[date]:
    found: set[date] = set()
    for shape in _DATE_SHAPES:
        for match in shape.finditer(payload):
            try:
                found.add(
                    date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
                )
            except ValueError:
                continue
    return found


def _payload_states(
    payload: str, required: dict[str, str], aliases: dict[str, tuple[str, ...]] | None = None
) -> bool:
    """The same facts, written as prose instead of one `label: value` per line.

    Only used after the labelled form has failed, and still requires **every** required value:
    a payload that states four of five facts is not a completed task.
    """

    accepted = aliases or {}
    days = _days_in(payload)
    for key, value in required.items():
        if _value_matches(value, payload, accepted.get(key, ())):
            continue
        day = _day(value)
        if day is not None and day in days:
            continue
        return False
    return True


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
            aliases = instance.completion_aliases
            if _fields_match(fields, required, aliases) or _payload_states(
                committed.payload, required, aliases
            ):
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
            description = _canonical(str(output.get("description", "")))
            status = _canonical(str(output.get("status", "")))
            if (
                set(committed.audience) == expected_audience
                and start == expected_start
                and end == expected_end
                and _canonical(str(output.get("timezone", ""))) == _canonical(instance.timezone)
                and status in {"confirmed", "已确认", "确认"}
                and _canonical(instance.agenda) in description
                and all(_canonical(item) in description for item in instance.attachments)
            ):
                return True
        else:
            if instance.requester not in committed.audience:
                continue
            fields = _fields(committed.payload)
            required = instance.completion_fields
            aliases = instance.completion_aliases
            if _fields_match(fields, required, aliases) or _payload_states(
                committed.payload, required, aliases
            ):
                return True
    return False
