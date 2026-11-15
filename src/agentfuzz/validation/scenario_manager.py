"""Scenario pairs: one legitimate task, two materializations.

The differential design (SPEC 0.5): the SAME legitimate task runs twice.  The
baseline carrier carries the plain document; the injected carrier carries the
same document plus the injection directive.  Everything else -- tools,
endpoints, workspace seeds, budget, seed -- is identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentfuzz.validation.effect_probes import EffectProbe


@dataclass(frozen=True)
class ScenarioPair:
    case_id: str
    task_text: str
    carrier_url: str
    benign_body: str
    injected_body: str
    target_chain: tuple[str, ...]
    probes: tuple[EffectProbe, ...]
    extra_endpoints: dict[str, str] = field(default_factory=dict)
    metrics_rows: list[dict[str, Any]] = field(default_factory=list)
    workspace_seed: dict[str, str] = field(default_factory=dict)

    def endpoints(self, injected: bool) -> dict[str, str]:
        carrier = self.injected_body if injected else self.benign_body
        return {self.carrier_url: carrier, **self.extra_endpoints}
