"""The host's side of a structured episode: submit, retrieve, finalize, feed the search (I1).

Everything here is **reuse**: `DockerSandboxScheduler` creates the isolated container,
`RuntimeClient` speaks the existing JSON-RPC (`execution.submit` / `execution.events`), the
container's `StructuredV1Adapter` runs the session, and `finalize_container_episode` adds the
two host-owned receipts.  This module only supplies the pieces that were missing between them:

* the **assembled submission** (`submission.build_envelope` / `build_execution_request`) for one
  candidate;
* the **bundle retrieval**: the container hands its bundle back in the terminal event, and the
  host re-verifies it from its own contents before trusting it (`verify_bundle`);
* the **host observations** the closure receipt needs, each one a probe result rather than an
  inference from a successful destroy;
* the **seam into the search**: `execute` and `artifacts` are what `campaign.run_opportunity`
  calls, so a real container Episode drives the same loop the offline rehearsal drives.

Nothing in this module decides a judgement: the Oracle reads the recorded evidence, and the
search reads the coverage derived from it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sandbox.config import SandboxLimits, TraceConfig
from sandbox.protocol import ExecutionRequest, ModelOptions
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.canonical_world import CanonicalOfficeWorld
from sandbox.scheduler.models import SandboxHandle
from sandbox.structured_v1.budget import RunBudgetEnvelope
from sandbox.structured_v1.bundle import (
    EpisodeBundle,
    FinalizedEpisodeBundle,
    verify_bundle,
    write_finalized_bundle,
)
from sandbox.structured_v1.diagnostics import (
    DEFAULT_DIAGNOSTICS_DIR,
    FAILURE_EVENT_TYPES,
    host_failure_record,
    write_record,
)
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.finalization import finalize_container_episode
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import StructuredCase
from sandbox.structured_v1.oracle_io import EpisodeArtifacts
from sandbox.structured_v1.submission import build_envelope, build_execution_request
from sandbox.structured_v1.world import FixtureWorldOverlay

if TYPE_CHECKING:  # host-only: the Docker SDK is not installed inside the runtime image.
    from sandbox.client.runtime_client import RuntimeClient
    from sandbox.scheduler.docker_scheduler import DockerSandboxScheduler

#: The container's own bundle directory (the image's `StructuredV1Adapter` default).
DEFAULT_BUNDLE_DIRECTORY = Path("/tmp/structured-v1")


class HostRunnerError(RuntimeError):
    """The host could not turn a container run into a usable episode."""


def _run_sync(coro: Any) -> Any:
    """Drive a coroutine from a synchronous caller, whether or not a loop is already running.

    The probes are synchronous by contract (`HostProbes`), but the finalization path that calls
    them is async.  A bare `asyncio.run` therefore depends on the caller: inside a running loop it
    raises "asyncio.run() cannot be called from a running event loop".  When a loop is running the
    coroutine is driven on a worker thread instead, so the same probe serves both callers.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


@dataclass(frozen=True)
class HostProbes:
    """The observations the closure receipt is built from, each one a real probe.

    They are callables on purpose: the runner must not be able to *assume* that a container was
    removed or that it stopped acting, and a caller may replace them with a test double.
    """

    container_absent: Callable[[str], bool]
    #: Asked with the sequence the host already consumed, because "no activity after the bundle"
    #: can only be answered about events that come *after* it.
    no_post_bundle_activity: Callable[[SandboxHandle, ExecutionRequest, int], bool]
    #: Only answerable once the container is gone, so the caller evaluates it after `destroy`.
    isolation_confirmed: Callable[[SandboxHandle], bool]
    inspect: Callable[[str], dict[str, Any]] | None = None
    #: Who answered: the container runtime, or a substitute used offline (`SOC-SAF-16`).
    source: str = "container-runtime"


def docker_probes(docker_client: Any) -> HostProbes:
    """The default probes, answered by the container runtime itself."""

    def container_absent(container_id: str) -> bool:
        import docker.errors

        try:
            docker_client.containers.get(container_id)
        except docker.errors.NotFound:
            return True
        return False

    def no_post_bundle_activity(
        handle: SandboxHandle, request: ExecutionRequest, after_sequence: int
    ) -> bool:
        """The runtime reports no further trace event after the one the host already consumed.

        The question is about what happened *after* the terminal event, so the poll starts there:
        asking from the beginning of the trace would return the trace itself and could never
        answer it.
        """

        from sandbox.client.runtime_client import RuntimeClient

        client = RuntimeClient(
            TraceConfig(), request_timeout=10.0, docker_client=docker_client
        )
        page = _run_sync(_silent_after(client, handle, request.execution_id, after_sequence))
        return page is not None and page.terminal and not page.events

    def isolation_confirmed(handle: SandboxHandle) -> bool:
        """Absent container and no leftover workspace volume."""

        import docker.errors

        if not container_absent(handle.container_id):
            return False
        if handle.workspace_volume_name is None:
            return True
        try:
            docker_client.volumes.get(handle.workspace_volume_name)
        except docker.errors.NotFound:
            return True
        return False

    return HostProbes(
        container_absent=container_absent,
        no_post_bundle_activity=no_post_bundle_activity,
        isolation_confirmed=isolation_confirmed,
        source="container-runtime",
    )


async def _silent_after(
    client: RuntimeClient, handle: SandboxHandle, execution_id: str, after_sequence: int
):
    """Ask for the events after `after_sequence`; an empty terminal page means "nothing new"."""

    try:
        return await client.events(handle, execution_id, after_sequence=after_sequence)
    except Exception:
        return None


@dataclass
class StructuredHostRunner:
    """One host runner per campaign: it owns the image, the model and the frozen fixture."""

    scheduler: DockerSandboxScheduler
    runtime: RuntimeClient
    manifest: StructuredFixtureManifest
    base_world: CanonicalOfficeWorld
    overlay: FixtureWorldOverlay
    image: str
    model: ModelOptions
    default_budget: RunBudgetEnvelope
    bundle_directory: Path = DEFAULT_BUNDLE_DIRECTORY
    #: Where a failed episode's own record goes; the entry points it at the run's data root.
    failure_directory: Path = DEFAULT_DIAGNOSTICS_DIR
    #: The request's own deadline; the protocol allows at most 600 seconds, so the envelope's
    #: wall-clock budget must fit inside it.
    execution_timeout_seconds: int = 600
    sandbox_limits: SandboxLimits = field(default_factory=SandboxLimits)
    probes: HostProbes | None = None
    arm_id: str = "structured-v1"
    episode_counter: Iterator[int] = field(default_factory=lambda: iter(range(1, 10_000)))
    _execution_configs: dict[str, str] = field(default_factory=dict)
    _handles: dict[str, SandboxHandle] = field(default_factory=dict)
    _finalized: dict[str, FinalizedEpisodeBundle] = field(default_factory=dict)

    # --- the seam the two-arm loop calls -------------------------------------------------

    def execute(self, case: StructuredCase) -> EpisodeBundle:
        """Run one candidate in a real container and return its verified bundle."""

        episode_id = f"episode-{next(self.episode_counter):04d}"
        return self.run_case(case, episode_id=episode_id).container_bundle

    def artifacts(self, bundle: EpisodeBundle) -> EpisodeArtifacts:
        """The finalized view of an episode: host receipts turn proven channels into closed ones."""

        return self.finalized_for(bundle.episode_id).artifacts()

    def execution_config_digest(self, bundle: EpisodeBundle) -> str:
        """The request configuration and actual image bound to this returned bundle."""

        finalized = self.finalized_for(bundle.episode_id)
        if finalized.container_bundle.bundle_digest != bundle.bundle_digest:
            raise ValueError("execution configuration belongs to a different bundle")
        return self._execution_configs[bundle.episode_id]

    def finalized_for(self, episode_id: str) -> FinalizedEpisodeBundle:
        """The finalized bundle of an episode this runner executed."""

        finalized = self._finalized.get(episode_id)
        if finalized is None:
            raise HostRunnerError(f"episode {episode_id} was not finalized by this runner")
        return finalized

    # --- one episode, step by step -------------------------------------------------------

    def run_case(
        self,
        case: StructuredCase,
        *,
        episode_id: str,
        generation: int = 0,
        budget: RunBudgetEnvelope | None = None,
        attempt: int = 0,
        seed: int | None = None,
    ) -> FinalizedEpisodeBundle:
        return asyncio.run(
            self._run_case(
                case,
                episode_id=episode_id,
                generation=generation,
                budget=budget or self.default_budget,
                attempt=attempt,
                seed=seed,
            )
        )

    async def _run_case(
        self,
        case: StructuredCase,
        *,
        episode_id: str,
        generation: int,
        budget: RunBudgetEnvelope,
        attempt: int,
        seed: int | None,
    ) -> FinalizedEpisodeBundle:
        prepared = build_envelope(
            case,
            manifest=self.manifest,
            base_world=self.base_world,
            overlay=self.overlay,
            budget=budget,
            episode_id=episode_id,
            arm_id=self.arm_id,
            generation=generation,
            attempt=attempt,
        )
        request = build_execution_request(
            prepared.envelope,
            execution_id=episode_id,
            model=self.model,
            timeout_seconds=self.execution_timeout_seconds,
            seed=seed,
        )
        handle = await self.scheduler.create(
            episode_id, self.image, self.sandbox_limits
        )
        self._handles[episode_id] = handle
        try:
            await self.scheduler.wait_until_ready(handle)
            bundle, terminal_sequence = await self._drive(handle, request)
            finalized = await self._finalize(handle, request, bundle, terminal_sequence)
        except BaseException:
            self._handles.pop(episode_id, None)
            raise
        self._execution_configs[episode_id] = sha256_digest({
            "manifest": prepared.envelope.manifest_digest,
            "base": prepared.envelope.base_world_digest,
            "overlay": prepared.envelope.overlay.canonical_digest(),
            "tooling": prepared.envelope.tooling.model_dump(mode="json"),
            "image": finalized.runtime_receipt.image_digest,
            "model": request.model.model_dump(mode="json"),
            "seed": request.seed,
            "budget": budget.model_dump(mode="json"),
            "timeout": self.execution_timeout_seconds,
            "limits": self.sandbox_limits.model_dump(mode="json"),
        })
        self._finalized[episode_id] = finalized
        return finalized

    def _no_bundle_message(
        self,
        request: ExecutionRequest,
        received: list[dict[str, Any]],
        terminal_sequence: int,
    ) -> str:
        """Say what the container said, and where the record of it is.

        The events are already in hand when the bundle is missing; keeping only the exception
        text is what made the first G1 failure unlocatable once the container was gone.
        """

        path = write_record(
            "host-failure",
            request.execution_id,
            "no-structured-bundle",
            host_failure_record(
                request.execution_id,
                received_events=received,
                terminal_sequence=terminal_sequence,
            ),
            directory=self.failure_directory,
        )
        failure = next(
            (
                item
                for item in received
                if item.get("event_type") in FAILURE_EVENT_TYPES
            ),
            None,
        )
        detail = ""
        if failure is not None:
            data = failure.get("data") or {}
            detail = (
                f"; the container reported {failure['event_type']}"
                f" {data.get('error_code')}: {data.get('message')}"
            )
        return (
            f"the container produced no structured bundle event{detail}; host record: {path}"
        )

    async def _drive(
        self, handle: SandboxHandle, request: ExecutionRequest
    ) -> tuple[EpisodeBundle, int]:
        """Submit, stream to the terminal event, then re-verify what came back.

        The sequence it stopped at is returned with the bundle: the host activity question is
        about the events *after* it, so the terminal sequence is part of the evidence, not
        something to be guessed later from the beginning of the trace.
        """

        await self.runtime.submit(handle, request)
        payload: dict[str, Any] | None = None
        terminal_sequence = -1
        received: list[dict[str, Any]] = []
        async for page in self.runtime.poll_and_stream_events(handle, request):
            for event in page.events:
                terminal_sequence = max(terminal_sequence, event.sequence)
                received.append(
                    {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "source": event.source,
                        "data": event.data,
                    }
                )
                if event.event_type == "execution_finished" and event.source == "structured_v1":
                    payload = event.data
        if payload is None:
            raise HostRunnerError(
                self._no_bundle_message(request, received, terminal_sequence)
            )
        raw = payload.get("bundle")
        if not isinstance(raw, dict):
            raise HostRunnerError("the structured bundle event carried no bundle")
        try:
            bundle = EpisodeBundle.model_validate(raw)
            verify_bundle(bundle)
        except EnvelopeRefusal as refusal:
            raise HostRunnerError(
                f"the container bundle was refused: {refusal.code.value}: {refusal.detail}"
            ) from refusal
        if payload.get("bundle_digest") != bundle.bundle_digest:
            raise EnvelopeRefusal(
                FailureCode.PAYLOAD_MISSING,
                "the container's bundle digest does not match the retrieved bundle",
            )
        return bundle, terminal_sequence

    async def _finalize(
        self,
        handle: SandboxHandle,
        request: ExecutionRequest,
        bundle: EpisodeBundle,
        terminal_sequence: int,
    ) -> FinalizedEpisodeBundle:
        """Stop the container and append the host observations the bundle cannot carry itself.

        "No activity after the bundle" can only be asked while the runtime still answers, so it is
        probed here, before the stop. Isolation is a statement about a container that no longer
        exists, so it is handed to the finalizer as a callable: the finalizer reads the runtime
        receipt first (the container's identity still has to be inspectable), destroys it, and only
        then asks. Observing it here instead would answer a question about a container still
        running.
        """

        probes = self.probes or docker_probes(self.runtime.docker_client)
        activity_observed = probes.no_post_bundle_activity(handle, request, terminal_sequence)
        finalized = await finalize_container_episode(
            bundle,
            handle=handle,
            scheduler=self.scheduler,
            inspect=probes.inspect,
            container_absent=probes.container_absent,
            no_post_bundle_activity=activity_observed,
            isolation_confirmed=probes.isolation_confirmed,
            observation_detail=f"host probes answered after {request.execution_id}",
            observation_source=probes.source,
        )
        self._handles.pop(bundle.episode_id, None)
        return finalized

    def write(self, finalized: FinalizedEpisodeBundle, directory: Path) -> Path:
        """Persist the finalized bundle where the run's artifacts are collected."""

        directory.mkdir(parents=True, exist_ok=True)
        return write_finalized_bundle(
            finalized, directory / f"{finalized.container_bundle.episode_id}.final.json"
        )


__all__ = [
    "DEFAULT_BUNDLE_DIRECTORY",
    "HostProbes",
    "HostRunnerError",
    "StructuredHostRunner",
    "docker_probes",
]
