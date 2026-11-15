"""Coverage keys (SPEC 0.3): what one run added to the two feedback dimensions.

behavior_key = (source_tool, intermediate_tool, sink_tool, dependency_type,
                injection_position, execution_stage)   -- the walked path
risk_key     = (sink_type, source_trust_level, injection_position,
                effect_type, effect_severity)          -- the risk it produced
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ChainContext:
    """The scenario-level chain facts the keys are extracted against."""

    target_chain: tuple[str, ...]
    sink_tool: str
    sink_type: str  # cmdi / ssrf / sqli / file
    injection_position: str  # e.g. "fetch_document.url" / "workspace_file.doc.md"
    source_trust_level: str  # "tool_return" / "test_file"
    dependency_type: str = "direct"


def walked_chain(trace_dict: dict[str, Any], context: ChainContext) -> tuple[str, ...]:
    """The prefix of the target chain the run actually completed, in order."""

    names = [c["name"] for c in trace_dict.get("tool_calls", [])]
    walked: list[str] = []
    remaining = iter(names)
    for tool in context.target_chain:
        if tool in remaining:
            walked.append(tool)
        else:
            break
    return tuple(walked)


def behavior_key(
    trace_dict: dict[str, Any],
    stage_value: str,
    context: ChainContext,
) -> tuple[str, ...]:
    walked = walked_chain(trace_dict, context)
    source = walked[0] if walked else ""
    sink = walked[-1] if walked and walked[-1] == context.sink_tool else ""
    intermediate = "-".join(walked[1:-1]) if len(walked) > 2 else ""
    return (
        source,
        intermediate,
        sink,
        context.dependency_type,
        context.injection_position,
        stage_value,
    )


def risk_key(
    trace_dict: dict[str, Any],
    stage_value: str,
    context: ChainContext,
    effect_kind: str | None,
) -> tuple[str, ...]:
    severity = {
        "effect_observed": "confirmed",
        "params_affected": "params_affected",
    }.get(stage_value, "unreached")
    return (
        context.sink_type,
        context.source_trust_level,
        context.injection_position,
        effect_kind or "none",
        severity,
    )


class CoverageDB:
    """The seen-key sets plus per-case history the scheduler scores against."""

    def __init__(self) -> None:
        self.behavior_keys: set[tuple[str, ...]] = set()
        self.risk_keys: set[tuple[str, ...]] = set()
        self.cases: dict[str, dict[str, Any]] = {}

    def case(self, case_id: str) -> dict[str, Any]:
        return self.cases.setdefault(
            case_id,
            {
                "attempts": 0,
                "confirmed": 0,
                "stages_seen": set(),
                "tokens": 0,
                "repairs_since_progress": 0,
            },
        )

    def add_behavior(self, key: tuple[str, ...]) -> bool:
        new = key not in self.behavior_keys
        self.behavior_keys.add(key)
        return new

    def add_risk(self, key: tuple[str, ...]) -> bool:
        new = key not in self.risk_keys
        self.risk_keys.add(key)
        return new
