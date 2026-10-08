"""Run one Episode attempt inside a container.

Modelled on `sandbox.structured_v1.host_runner`: one container per Opportunity, submit one
request, stream to the terminal event, destroy the container.

`run_opportunity` is the low-level primitive -- it takes an already-built request and gives
back the trace.  `run_attempt` is the `EpisodeExecutor` side of it, which builds the request
from an `EpisodeAttempt`, so the campaign can be pointed at a container without knowing
anything about containers.

What it returns is the same `EpisodeTrace` the in-process path returns.  That is the whole
point of the step: everything downstream -- the bridge, the coverage key, the settlement,
the report -- stays unchanged, so the only thing a container can change is whether the
Episode ran, never how its result is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docker.errors import NotFound

from sandbox.client.runtime_client import RuntimeClient
from sandbox.config import SandboxLimits
from sandbox.protocol import ExecutionRequest, ModelOptions
from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler
from sandbox.scenarios.error_capable_agent import EpisodeTrace
from sandbox.scenarios.error_capable_executor import EpisodeAttempt, ExecutionEnvironment
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_submission import build_error_capable_request
from sandbox.scenarios.error_capable_transport import (
    FINISHED_EVENT_SOURCE,
    FINISHED_EVENT_TYPE,
)


class ErrorCapableRunError(RuntimeError):
    """The container did not produce a usable Episode."""


class ErrorCapableContainerRunner:
    """One Opportunity, one container, one trace."""

    def __init__(
        self,
        *,
        scheduler: DockerSandboxScheduler,
        runtime: RuntimeClient,
        image: str,
        model: ModelOptions,
        model_identity: ModelIdentity,
        adapter_version: str,
        timeout_seconds: int,
        limits: SandboxLimits | None = None,
        run_root: tuple[Path, str] | None = None,
        container_workspace_root: str | None = None,
        image_digest: str | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.runtime = runtime
        self.image = image
        #: The image's resolved id, if the caller already asked for it.
        #:
        #: `image` is a reference and a reference can be moved, so the identity records the id
        #: as well.  It is resolved lazily in `environment` when it was not supplied, because
        #: asking Docker for it costs a round trip and a runner that is constructed and never
        #: used should not make one.
        self.image_digest = image_digest
        self.model = model
        self.model_identity = model_identity
        self.adapter_version = adapter_version
        self.timeout_seconds = timeout_seconds
        self.limits = limits or SandboxLimits()
        #: The path the container sees for the office workspace, or None to keep it in memory.
        #:
        #: `/workspace` is what the scheduler already mounts -- a writable tmpfs owned by the
        #: container's own uid -- so naming it here adds no mount and no privilege.  The
        #: material is written into it by the container itself, from this Opportunity's plan.
        self.container_workspace_root = container_workspace_root
        #: `(host directory, container path)` to bind into every container, or None.
        #:
        #: This is what makes a journal in a container meaningful.  The scheduler mounts
        #: `/workspace` and nothing else, and that mount is a tmpfs that dies with the
        #: container -- so a checkpoint written there would not survive the `docker kill` it
        #: exists to survive.  Passing a `container_journal_root` on its own used to be
        #: possible, and that was the hazard: a path nothing was mounted at would produce a
        #: journal that looked available and never was.  The two are one argument now, so a
        #: container journal cannot be asked for without somewhere to put it.
        self.run_root = run_root
        #: Facts about each finished container, waiting to be taken by whoever records evidence.
        self._execution_notes: list[dict[str, Any]] = []

    @property
    def container_journal_root(self) -> str | None:
        """Where the container writes its journal: the mount, or None when there is none."""

        return None if self.run_root is None else self.run_root[1]

    def _extra_volumes(self) -> dict[str, dict[str, str]] | None:
        if self.run_root is None:
            return None
        host_root, container_path = self.run_root
        return {str(host_root): {"bind": container_path, "mode": "rw"}}

    def take_execution_notes(self) -> tuple[dict[str, Any], ...]:
        """Per-attempt facts the trace does not carry, drained.

        Drained rather than accumulated, because each note describes one container and a caller
        that read a stale one would be recording another Episode's isolation as this one's.
        """

        notes = tuple(self._execution_notes)
        self._execution_notes = []
        return notes

    @property
    def environment(self) -> ExecutionEnvironment:
        return ExecutionEnvironment(
            transport="container",
            workspace="directory" if self.container_workspace_root else "memory",
            image=self.image,
            image_digest=self.image_digest or self._resolve_image_digest(),
            limits=self.limits.model_dump(mode="json"),
        )

    def _resolve_image_digest(self) -> str | None:
        """Ask Docker which image id the reference resolves to, once, and remember it.

        A campaign that recorded only `trace-redteam-agent:server` would be describing a name
        that a later `docker build` can point at something else, and the evidence would still
        read as if the same image had produced it.  The id cannot be moved.

        None when the scheduler has no client to ask -- a test double, for instance.  That is
        reported as an absent fact rather than filled in with the reference, because a value
        that looks like a digest and is not one is worse than a missing field.
        """

        if self.image_digest is not None:
            return self.image_digest
        client = getattr(self.scheduler, "client", None)
        if client is None:
            return None
        try:
            self.image_digest = str(client.images.get(self.image).id)
        except Exception:
            return None
        return self.image_digest

    async def run_attempt(self, attempt: EpisodeAttempt) -> EpisodeTrace:
        request = build_error_capable_request(
            execution_id=attempt.episode_id,
            index=attempt.index,
            mode=attempt.mode,
            plan=attempt.plan,
            material=attempt.material,
            fixture=attempt.fixture,
            model=self.model,
            model_identity=self.model_identity,
            adapter_version=self.adapter_version,
            timeout_seconds=self.timeout_seconds,
            max_tool_requests=attempt.max_tool_requests,
            journal_root=self.container_journal_root,
            resume=False,
            workspace_root=self.container_workspace_root,
        )
        return await self.run_opportunity(request)

    async def run_opportunity(self, request: ExecutionRequest) -> EpisodeTrace:
        """Run the Opportunity the request describes and return its trace.

        The container is destroyed in a `finally`, including when the Episode failed: the
        isolation this step buys is a claim about a container that no longer exists, and a
        container left behind would be one more thing a later reader has to explain.
        """

        handle = await self.scheduler.create(
            request.execution_id,
            self.image,
            self.limits,
            extra_volumes=self._extra_volumes(),
        )
        try:
            await self.scheduler.wait_until_ready(handle)
            await self.runtime.submit(handle, request)
            payload, seen = await self._collect(handle, request)
        finally:
            await self.scheduler.destroy(handle)
        self._execution_notes.append(
            {
                "episode_id": request.execution_id,
                "container_id": handle.container_id,
                "transport": handle.transport,
                "image": self.image,
                "image_digest": self._resolve_image_digest(),
                "workspace_root": self.container_workspace_root,
                "journal_root": self.container_journal_root,
                **self._confirm_container_absent(handle),
            }
        )

        if payload is None:
            raise ErrorCapableRunError(
                f"the container reported no {FINISHED_EVENT_TYPE} from "
                f"{FINISHED_EVENT_SOURCE}. What it did report: {_describe(seen)}"
            )
        trace = payload.get("trace")
        if not isinstance(trace, dict):
            raise ErrorCapableRunError(
                f"the {FINISHED_EVENT_TYPE} event from {FINISHED_EVENT_SOURCE} carried no trace"
            )
        return EpisodeTrace.model_validate(trace)

    def _confirm_container_absent(self, handle: Any) -> dict[str, Any]:
        """Whether the container is really gone, asked rather than assumed.

        Isolation is a claim about what is no longer there, and a claim nobody checks is a
        sentence rather than evidence.  The daemon is asked after the removal was requested; a
        container that is still listed means the claim is false, and the note says so instead
        of the report reading as if the removal had worked.

        None rather than False when there is no client to ask -- a test double, a fake
        scheduler.  "I could not check" and "I checked and it is still there" are different
        facts and must not share a value.
        """

        client = getattr(self.scheduler, "client", None)
        if client is None:
            return {"container_absent": None}
        try:
            client.containers.get(handle.container_id)
        except NotFound:
            return {"container_absent": True, "isolation_confirmed": True}
        except Exception as exc:  # noqa: BLE001 -- any failure here is a missing fact
            return {"container_absent": None, "isolation_probe_error": repr(exc)}
        return {"container_absent": False, "isolation_confirmed": False}

    async def _collect(
        self, handle: Any, request: ExecutionRequest
    ) -> tuple[dict[str, Any] | None, list[SeenEvent]]:
        """Stream to the terminal event, keeping the payload of the one that finishes.

        Every event is kept, not only the one that is looked for.  A container that failed
        for its own reasons reports `execution_error` or `execution_timed_out` from `runtime`
        rather than the pair this runner waits for, and a caller that is told only "no
        finished event" has to go and find out what happened instead.
        """

        payload: dict[str, Any] | None = None
        seen: list[SeenEvent] = []
        async for page in self.runtime.poll_and_stream_events(handle, request):
            for event in page.events:
                seen.append(
                    SeenEvent(
                        sequence=event.sequence,
                        event_type=event.event_type,
                        source=event.source,
                        data=dict(event.data),
                    )
                )
                if (
                    event.event_type == FINISHED_EVENT_TYPE
                    and event.source == FINISHED_EVENT_SOURCE
                ):
                    payload = event.data
        return payload, seen


#: One event as the container reported it, kept for the failure message.
@dataclass(frozen=True)
class SeenEvent:
    sequence: int
    event_type: str
    source: str
    data: dict[str, Any]


def _describe(seen: list[SeenEvent]) -> str:
    """A one-line account of what the container actually reported."""

    if not seen:
        return "nothing at all"
    parts = []
    for event in seen:
        detail = {
            key: event.data[key]
            for key in ("error_code", "message", "error_type")
            if key in event.data
        }
        parts.append(
            f"seq {event.sequence} {event.event_type}/{event.source}"
            + (f" {detail}" if detail else "")
        )
    return "; ".join(parts)


__all__ = [
    "FINISHED_EVENT_SOURCE",
    "FINISHED_EVENT_TYPE",
    "ErrorCapableContainerRunner",
    "ErrorCapableRunError",
    "SeenEvent",
]
