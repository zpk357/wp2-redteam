"""Shared predicates for risk evidence that was observed during execution."""

from __future__ import annotations

from sandbox.coverage.models import RiskHit


def is_execution_evidenced_hit(hit: RiskHit) -> bool:
    return hit.depth >= 2 and any(
        (
            reference.source == "trace_event"
            and reference.event_sequence is not None
        )
        or (
            reference.source == "office_execution"
            and reference.artifact_digest is not None
        )
        for reference in hit.evidence
    )


def execution_evidenced_hits(hits: list[RiskHit]) -> list[RiskHit]:
    return [hit for hit in hits if is_execution_evidenced_hit(hit)]


def risk_depths(hits: list[RiskHit]) -> dict[str, int]:
    depths: dict[str, int] = {}
    for hit in hits:
        depths[hit.category_id] = max(depths.get(hit.category_id, 0), hit.depth)
    return depths
