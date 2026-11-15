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
    # The third injection channel (SPEC 0.2 注入点选择: 用户输入): when set, the
    # directive rides in the TASK text itself -- the baseline task is clean and
    # the injected task carries it.  task_text then stays the shared neutral
    # description used for record keeping.
    injected_task_text: str | None = None
    benign_task_text: str | None = None

    def endpoints(self, injected: bool) -> dict[str, str]:
        carrier = self.injected_body if injected else self.benign_body
        return {self.carrier_url: carrier, **self.extra_endpoints}

    def task_for(self, injected: bool) -> str:
        """The task text this group runs with, per the injection channel."""

        if self.injected_task_text is None:
            return self.task_text
        return self.injected_task_text if injected else (
            self.benign_task_text or self.task_text
        )
    @property
    def channel(self) -> str:
        return "user_input" if self.injected_task_text is not None else "tool_return"

