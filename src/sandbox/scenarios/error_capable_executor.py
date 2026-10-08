"""Where one Episode attempt runs -- the part that does not name a container.

The campaign imports this module and nothing else of the runner's.  That is deliberate: a
container has to be able to import the scenario package, and the campaign must be able to
import an executor, but neither of them should drag in the Docker client.  The container
runner lives in `error_capable_runner` beside this file and depends on things the image
does not ship; keeping the interface and the in-process implementation here means the
dependency runs one way -- runner knows executor, executor knows nothing of the runner.

That is the same split as `error_capable_transport`, for the same reason, and the guard
test that blocks `docker` is what keeps it honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sandbox.scenarios.error_capable import EpisodeScenarioPlan, MaterializedScenario
from sandbox.scenarios.error_capable_agent import EpisodeTrace, run_agent_episode
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_registry import ErrorCapableFixture


@dataclass(frozen=True)
class ExecutionEnvironment:
    """Where an Episode's loop ran, as something a campaign's identity can be tied to.

    Before this existed, an in-process run and a container run were not distinguishable in the
    record at all: the campaign identity named the fixture, the model, the selector and the
    seed, and said nothing about the process.  Two sets of results that differed only in how
    they were executed would therefore have looked like one set -- the kind of sameness that
    cannot be taken apart afterwards.

    It is deliberately *not* a field of `EpisodeTrace`.  The trace is the Agent's record, and
    the acceptance test for putting the loop in a container is that the trace does not change
    when it does; naming the container there would have made the one property being checked
    the one property that could not be.  Where a run happened belongs to the campaign, the
    evidence and the report.
    """

    #: `in-process` or `container`.
    transport: str
    #: `memory` when the workspace was state only, `directory` when it was written to a disk.
    workspace: str
    #: The image reference the scheduler was asked for, for a container run.
    image: str | None = None
    #: The resolved image id.  A tag is a name somebody can move; the id is what ran.
    image_digest: str | None = None
    #: The limits the container was created under, as they were handed to the scheduler.
    limits: dict[str, Any] = field(default_factory=dict)

    def identity_payload(self) -> dict[str, Any]:
        """What goes into a campaign identity.  Optional-looking fields are all present.

        Written out rather than dumped from the dataclass so that a field being added does not
        silently leave the identity unchanged -- an identity that ignores a new fact is worse
        than one that is merely verbose.
        """

        return {
            "transport": self.transport,
            "workspace": self.workspace,
            "image": self.image,
            "image_digest": self.image_digest,
            "limits": dict(sorted(self.limits.items())),
        }


@dataclass(frozen=True)
class EpisodeAttempt:
    """One attempt of one Opportunity, described the same way for either executor.

    `journal_root` is a **host** path or None.  The container executor does not read it:
    where a container's own journal goes is a separate decision, because the run root is not
    mounted into the container yet.
    """

    episode_id: str
    index: int
    mode: str
    fixture: ErrorCapableFixture
    plan: EpisodeScenarioPlan
    material: MaterializedScenario
    max_tool_requests: int
    #: The Agent's silent-turn budget.  Measured before the formal run: every Episode of the
    #: first preflight used all of its three, because a model that ends by narrating gets
    #: nudged rather than believed.  It is a field here, and on the payload, so the number the
    #: run used is a number the run states.
    max_continuations: int = 3
    journal_root: Path | None = None
    #: Appended under the journal root.  Empty for a first attempt, `retries/attempt-N` for a
    #: repeat, so a discarded attempt is never resumed as if it were the first.
    journal_suffix: Path = Path("")
    #: A **host** directory to write the workspace material into, or None to keep it in memory.
    #:
    #: The container executor ignores it: a container has its own mount, and a host path handed
    #: to it would name a directory that does not exist there.  It is what the in-process
    #: executor uses so the two can be compared on the same terms -- the same plan, run once
    #: against a directory and once inside a container, has to produce the same trace.
    workspace_root: Path | None = None


class EpisodeExecutor(Protocol):
    """How one Episode attempt is run, and what that says about the run.

    `environment` is part of the interface rather than a detail of the implementation: a
    campaign identity that did not name it could not tell a container result from an
    in-process one, and the whole point of moving the loop is that the move is visible.
    """

    async def run_attempt(self, attempt: EpisodeAttempt) -> EpisodeTrace: ...

    @property
    def environment(self) -> ExecutionEnvironment: ...

    def take_execution_notes(self) -> tuple[dict[str, Any], ...]:
        """Per-attempt facts the trace does not carry, drained on read.

        The trace is the Agent's record and the environment is the run's; neither is the right
        place for "this container was confirmed gone".  Drained rather than accumulated so a
        caller cannot attribute one Episode's note to the next.
        """


class InProcessExecutor:
    """Run the Episode loop in this process.  The default, and the thing being moved."""

    def __init__(
        self,
        *,
        adapter: Any,
        model_identity: ModelIdentity,
        write_workspace: bool = False,
    ) -> None:
        self.adapter = adapter
        self.model_identity = model_identity
        #: Whether to write the workspace material out and read it back from a directory.
        #:
        #: Off by default because the in-memory path is what every result so far was produced
        #: by, and a default that silently changed how episodes are read would be a change
        #: disguised as a default.  Turned on to run a host Episode on the same terms a
        #: container runs one, which is what makes the two comparable.
        self.write_workspace = write_workspace

    @property
    def environment(self) -> ExecutionEnvironment:
        return ExecutionEnvironment(
            transport="in-process",
            workspace="directory" if self.write_workspace else "memory",
        )

    def take_execution_notes(self) -> tuple[dict[str, Any], ...]:
        """Nothing to report: this executor has no container to confirm the absence of."""

        return ()

    async def run_attempt(self, attempt: EpisodeAttempt) -> EpisodeTrace:
        store = None
        if attempt.journal_root is not None:
            root = (
                attempt.journal_root / attempt.journal_suffix
                if str(attempt.journal_suffix)
                else attempt.journal_root
            )
            store = _store_for(root, attempt.episode_id)
        return await run_agent_episode(
            fixture=attempt.fixture,
            plan=attempt.plan,
            material=attempt.material,
            adapter=self.adapter,
            model_identity=self.model_identity,
            seed=attempt.plan.seed,
            max_tool_requests=attempt.max_tool_requests,
            max_continuations=attempt.max_continuations,
            journal=store,
            resume=store is not None and store.exists(),
            workspace_root=attempt.workspace_root if self.write_workspace else None,
        )


def _store_for(root: Any, episode_id: str) -> Any:
    from sandbox.scenarios.error_capable_journal import JournalStore

    return JournalStore(root, episode_id)


__all__ = [
    "EpisodeAttempt",
    "EpisodeExecutor",
    "ExecutionEnvironment",
    "InProcessExecutor",
]
