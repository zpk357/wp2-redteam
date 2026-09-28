"""Concrete ports and the driving path for a deterministic, no-model local run (P6).

``run_episode`` needs a model, a tool port, a clock and a host probe. The tool port is the
real ``ToolRuntimePort`` over the fixture's slot resolver, so a read that returns the
material is bound to it as an exposure fact. The other three are the only ports a caller
still supplies; for a no-model local acceptance they are:

* ``ScriptedModelPort`` - a fixed decision list that issues the read-then-deliver sequence,
  so the "Agent" is a script and no model client, container or server is involved;
* ``MonotonicClock`` - wall-clock seconds, so closure is measured, not assumed;
* ``StaticHost`` - the host's view of container activity (none, locally).

The real model client is bound through the same ``ModelPort`` protocol, just with a
different implementation; nothing in this module changes for that swap.
"""

from __future__ import annotations

import time

from sandbox.structured_v1.assets import FrozenAssetStore
from sandbox.structured_v1.bundle import EpisodeBundle
from sandbox.structured_v1.envelope import StructuredEnvelope
from sandbox.structured_v1.evidence import DeliveryChannel
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.ports import RegisteredContent, SlotResolver, ToolRuntimePort
from sandbox.structured_v1.runtime_assembly import assemble_tool_runtime
from sandbox.structured_v1.session import (
    DEFAULT_CHANNELS,
    ClockPort,
    HostProbe,
    ModelPort,
    run_episode,
)
from sandbox.structured_v1.tool_catalogue import ToolingFacts
from sandbox.structured_v1.world import materialize_world
from sandbox.tool_contracts import OFFICE_V2_TOOL_SPECS, ToolSpec


class ScriptedModelPort:
    """A ``ModelPort`` that replays a fixed script; past the end it just stops."""

    def __init__(self, decisions: list[ModelDecision]) -> None:
        self._decisions = list(decisions)
        self.task_text: str | None = None
        self.tools: tuple[ToolSpec, ...] = ()
        self.observations: list[object] = []
        self.continuations: list[str] = []

    def bind(self, *, task_text: str, tools: tuple[ToolSpec, ...]) -> None:
        self.task_text = task_text
        self.tools = tools

    def decide(self, *, step: int) -> ModelDecision:
        if step >= len(self._decisions):
            return ModelDecision(
                call_id=f"model.{step + 1:03d}",
                provider_version="scripted-model-port-v1",
                model_name="scripted",
            )
        return self._decisions[step]

    def continue_task(self, *, task_text: str) -> None:
        self.continuations.append(task_text)

    def observe(self, decision: ModelDecision, report: object) -> None:
        del decision
        self.observations.append(report)


class MonotonicClock:
    """Wall-clock seconds; closure is measured from this, never assumed."""

    source = "runtime-monotonic"

    def now(self) -> int | None:
        return int(time.monotonic())

    def wait_until(self, target: int) -> None:
        remaining = target - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


class StaticHost:
    """The host's view of the container: for a local run there is no container activity."""

    source = "test-substitute"

    def __init__(self, activity: bool = False) -> None:
        self._activity = activity

    def saw_container_activity(self) -> bool:
        return self._activity


class UnavailableHost:
    """Conservative container-side probe when only the host can make the observation."""

    source = "unavailable-inside-container"

    def saw_container_activity(self) -> None:
        return None


def drive_structured_v1_episode(
    envelope: StructuredEnvelope,
    *,
    assets: FrozenAssetStore,
    tooling: ToolingFacts,
    model: ModelPort,
    clock: ClockPort | None = None,
    host: HostProbe | None = None,
    channels: tuple[DeliveryChannel, ...] = DEFAULT_CHANNELS,
) -> EpisodeBundle:
    """Drive one episode through the real tool runtime and the session.

    This is the container's driving path minus the model client: materialise inside the
    store, assemble the real tool runtime with the fixture's slot resolver, and run the
    session over the real tool port. The model and the clock/host are the only ports the
    caller still supplies - for a no-model local run they are a ``ScriptedModelPort`` and
    the default ``MonotonicClock``/``StaticHost``.
    """

    base_world = assets.resolve(envelope.base_locator)
    materialized = materialize_world(
        envelope.manifest,
        envelope.material,
        base_world=base_world,
        overlay=envelope.overlay,
        episode_id=envelope.schedule.episode_id,
        expected_base_world_digest=envelope.base_world_digest,
    )
    runtime = assemble_tool_runtime(materialized=materialized, manifest=envelope.manifest)
    tools = ToolRuntimePort(
        runtime,
        slots=SlotResolver.from_overlay(envelope.overlay),
        registered=RegisteredContent.from_manifest(envelope.manifest),
        public_delivery=envelope.overlay.public_delivery,
    )
    return run_episode(
        envelope,
        assets=assets,
        tooling=tooling,
        model=model,
        tools=tools,
        clock=clock or MonotonicClock(),
        host=host or StaticHost(),
        channels=channels,
        model_tools=tuple(OFFICE_V2_TOOL_SPECS),
    )
