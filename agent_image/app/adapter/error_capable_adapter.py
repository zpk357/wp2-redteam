"""Execute one error-capable Episode inside the container.

This is the branch reached only by a request carrying ``error_capable_execution``.  It
rebuilds the Opportunity through :mod:`app.error_capable_runtime_surface` -- which refuses
anything that does not reproduce the host's digests -- builds the same provider the host
would have built, and runs the Episode loop here instead of on the host.

The loop is the host's own ``run_agent_episode``, unmodified: the container imports it from
the same package the image already ships.  What moves in this step is the process, not the
code that decides what the Agent does, so the trace that comes back is comparable to the
one the in-process path produces -- which is the acceptance test for this step.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from pathlib import Path

from app.adapter.base import AdapterConfigurationError, AgentAdapter
from app.error_capable_runtime_surface import ErrorCapableRun, build_error_capable_run
from app.protocol import ExecutionRequest, ModelProvider, TraceEvent

from sandbox.scenarios.error_capable_agent import DiscoveryScriptedAgent, run_agent_episode
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_journal import JournalStore

# The host is what waits for the terminal event, so it owns the name.  Importing it here
# means a container cannot announce an Episode as finished under a name no host waits for.
# It comes from the dependency-free transport module rather than from the runner: the runner
# imports the Docker client, which this image does not carry.
from sandbox.scenarios.error_capable_transport import (
    FINISHED_EVENT_SOURCE,
    FINISHED_EVENT_TYPE,
)


class ErrorCapableAdapter(AgentAdapter):
    """One error-capable Episode, in this container."""

    version = "error-capable-container-adapter-v1"

    def __init__(self, *, adapter: object | None = None) -> None:
        #: Supplied by acceptance tests so a container can be driven without a model.
        self._adapter = adapter
        self.last_checkpoint_digests: tuple = ()
        self.last_final_state_digest: str | None = None

    async def execute(self, request: ExecutionRequest) -> AsyncIterator[TraceEvent]:
        run = build_error_capable_run(
            request.error_capable_execution,
            prompt=request.prompt,
        )
        # The protocol carries the seed too, and it is the plan's.  Comparing them costs
        # nothing and catches a host that set one of the two and not the other.
        if request.seed is not None and request.seed != run.plan.seed:
            raise AdapterConfigurationError(
                "error_capable_data_integrity_error",
                "the request seed is not the plan's seed",
            )
        cancellation = threading.Event()
        try:
            adapter = self._adapter or self._build_adapter(request)
            self._require_matching_adapter_version(run, adapter)

            journal_root = run.payload.get("journal_root")
            store = None
            if journal_root is not None:
                store = JournalStore(Path(str(journal_root)), run.episode_id)
            resume = bool(run.payload.get("resume")) and store is not None and store.exists()

            trace = await run_agent_episode(
                fixture=run.fixture,
                plan=run.plan,
                material=run.material,
                adapter=adapter,  # type: ignore[arg-type]
                model_identity=ModelIdentity.model_validate(run.payload["model_identity"]),
                seed=run.plan.seed,
                max_tool_requests=int(run.payload["max_tool_requests"]),
                # The host's number, checked by the surface before the Episode is reached.  Not a
                # default here: the budget is part of what the run is.
                max_continuations=run.max_continuations,
                actor_case=str(run.payload["actor_case"]),
                drop_capabilities=tuple(str(item) for item in run.payload["drop_capabilities"]),
                journal=store,
                resume=resume,
                # The root the payload named is this container's mount.  The material is
                # materialised into it inside `run_agent_episode`, from this Opportunity's own
                # plan -- nothing is uploaded, so what the Agent reads is what this plan
                # produced rather than what a host said it produced.
                workspace_root=run.workspace_root,
            )
        except BaseException:
            cancellation.set()
            raise

        self.last_final_state_digest = trace.trace_digest
        yield TraceEvent(
            execution_id=request.execution_id,
            sequence=0,
            event_type=FINISHED_EVENT_TYPE,
            source=FINISHED_EVENT_SOURCE,
            data={
                "episode_id": run.episode_id,
                "index": run.payload.get("index"),
                "mode": run.payload.get("mode"),
                "trace": trace.model_dump(mode="json"),
                "trace_digest": trace.trace_digest,
                "stop_reason": trace.stop_reason,
            },
        )

    def _require_matching_adapter_version(self, run: ErrorCapableRun, adapter: object) -> None:
        """The provider that drove the Episode is part of the Episode's identity.

        The journal already refuses to resume across a provider change; checking it here as
        well means a fresh Episode cannot be recorded under one provider version and read
        later as if another had produced it.
        """

        expected = run.payload.get("adapter_version")
        observed = getattr(adapter, "version", None)
        if expected is not None and observed != expected:
            raise AdapterConfigurationError(
                "error_capable_configuration_error",
                f"provider version mismatch: host expected {expected!r}, this image has"
                f" {observed!r}",
            )

    @staticmethod
    def _build_adapter(request: ExecutionRequest) -> object:
        """The same provider boundary the host uses, built from the request's own options."""

        options = request.model
        if options is None or options.provider is ModelProvider.FAKE:
            return DiscoveryScriptedAgent()
        if options.provider is ModelProvider.OLLAMA:
            from app.agent.ollama_react_provider import OllamaReactProvider

            return OllamaReactProvider(options)
        raise AdapterConfigurationError(
            "error_capable_configuration_error",
            f"unsupported error-capable model provider: {options.provider}",
        )


__all__ = [
    "FINISHED_EVENT_SOURCE",
    "FINISHED_EVENT_TYPE",
    "ErrorCapableAdapter",
]
