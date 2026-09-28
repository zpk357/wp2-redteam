"""Host-owned second phase for a structured container Episode (P6/R10/R12)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from sandbox.scheduler.models import SandboxHandle
from sandbox.structured_v1.bundle import (
    EpisodeBundle,
    FinalizedEpisodeBundle,
    finalize_bundle,
)
from sandbox.structured_v1.host import (
    build_host_closure_receipt,
    query_runtime_receipt,
)


class ContainerDestroyer(Protocol):
    """The existing scheduler boundary needed by host finalization."""

    async def destroy(self, handle: SandboxHandle) -> None: ...


async def finalize_container_episode(
    container_bundle: EpisodeBundle,
    *,
    handle: SandboxHandle,
    scheduler: ContainerDestroyer,
    inspect: Callable[[str], dict[str, Any]] | None = None,
    container_absent: Callable[[str], bool],
    no_post_bundle_activity: bool,
    isolation_confirmed: Callable[[SandboxHandle], bool],
    observation_detail: str = "",
    observation_source: str = "container-runtime",
) -> FinalizedEpisodeBundle:
    """Bind evidence, stop its container, then append honest host observations.

    The observations happen at different moments and the order is not free. The runtime receipt is
    read while the container still exists, because it *is* the container's identity: inspecting it
    after the stop can only report that the object is gone. ``destroy`` comes next, and only then
    can absence and isolation be observed - so ``isolation_confirmed`` is asked as a callable after
    the stop instead of being accepted as an answer taken before it, which would state something
    about a container that had not been removed yet.

    None of these are consequences inferred from calling ``destroy``; a caller must only answer
    true when its runtime or scheduler evidence proves the fact, and ``observation_source`` records
    who answered (a container runtime, or an offline double).
    """

    runtime_receipt = query_runtime_receipt(
        handle.container_id,
        episode_id=container_bundle.episode_id,
        container_bundle_digest=container_bundle.bundle_digest,
        inspect=inspect,
        source=observation_source,
    )
    stop_requested = True
    details = [observation_detail] if observation_detail else []
    try:
        await scheduler.destroy(handle)
    except Exception as error:  # receipt must preserve failure without hiding evidence
        details.append(f"container destroy failed: {type(error).__name__}: {error}")

    try:
        absent = bool(container_absent(handle.container_id))
    except Exception as error:
        absent = False
        details.append(f"container absence check failed: {type(error).__name__}: {error}")

    try:
        isolated = bool(isolation_confirmed(handle))
    except Exception as error:
        isolated = False
        details.append(f"isolation observation failed: {type(error).__name__}: {error}")

    closure_receipt = build_host_closure_receipt(
        runtime_receipt=runtime_receipt,
        stop_requested=stop_requested,
        container_absent=absent,
        no_post_bundle_activity=no_post_bundle_activity,
        isolation_confirmed=isolated,
        detail="; ".join(details),
        source=observation_source,
    )
    return finalize_bundle(
        container_bundle,
        runtime_receipt=runtime_receipt,
        host_closure_receipt=closure_receipt,
    )
