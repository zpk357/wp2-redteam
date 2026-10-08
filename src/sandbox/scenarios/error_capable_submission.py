"""The host's request for one error-capable Episode, addressed to a container.

Modelled on `sandbox.structured_v1.submission`: the host emits one `ExecutionRequest`
carrying an opaque payload, and the scenario package -- not `protocol.py` -- is what
validates and consumes it.

What travels is the **plan and the digests, not the world**.  The fixture and the
material are pure functions of the frozen inputs, so the container rebuilds both and
refuses the request when either fails to reproduce what the host computed.  That is the
same "recompute and compare, refuse on drift" rule the in-process path already applies
to a resumed Opportunity, moved across the container boundary rather than re-invented.

Only what varies per Opportunity travels.  `max_continuations` is left out on purpose:
the container's own default applies, so there is one source of truth for it instead of
two values that could drift.
"""

from __future__ import annotations

from typing import Any

from sandbox.protocol import ExecutionRequest, ModelOptions
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import EpisodeScenarioPlan, MaterializedScenario
from sandbox.scenarios.error_capable_agent import DEFAULT_ACTOR_CASE, DISCOVERY_TASK
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_registry import (
    ERROR_CAPABLE_FIXTURE_ID,
    ErrorCapableFixture,
)

#: Bumped when the payload's shape changes in a way a running container must not ignore.
#:
#: v2 added `workspace_root`.  It is an optional key, so a v1 reader would not fail on it -- it
#: would run the Episode in memory and report no workspace tree, which is exactly the wrong
#: answer delivered successfully.  An image that predates the real workspace must refuse the
#: request instead.
#:
#: v3 added `max_continuations`, for the same reason in the other direction: a v2 reader would
#: not fail on it either, and would silently run every Episode with its own default of three
#: while the host believed it had set a budget.  A budget the host did not set and the container
#: did not report is worse than a refusal.
ERROR_CAPABLE_EXECUTION_VERSION = "error-capable-execution-v3"


def error_capable_payload(
    *,
    index: int,
    mode: str,
    plan: EpisodeScenarioPlan,
    material: MaterializedScenario,
    fixture: ErrorCapableFixture,
    model_identity: ModelIdentity,
    adapter_version: str,
    max_tool_requests: int,
    max_continuations: int,
    actor_case: str = DEFAULT_ACTOR_CASE,
    drop_capabilities: tuple[str, ...] = (),
    journal_root: str | None = None,
    resume: bool = False,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """The container-facing payload for one Opportunity.

    Both budgets travel.  `max_continuations` used to be neither here nor on the command line: it
    was a default on `run_agent_episode`, so a container always ran with three whether or not the
    host had decided three, and the run's configuration could not say what it was.  A budget the
    specification requires to be frozen has to be a value somebody chose and a reader can find.

    `journal_root` is the **container's** view of the run root, not the host's: the
    directory is mounted at the same path on both sides so the journal a container writes
    is the journal a host reads, and a killed container can be replaced by another one
    holding the same volume.

    `workspace_root` is likewise the **container's** path -- the mount the image is given for
    the office workspace.  The host does not choose what to put there and does not ship it: the
    container writes its own materialisation into that directory, so what the Agent reads is
    what this Opportunity's plan produced.  None keeps the workspace in memory, which is what
    an in-process run does.
    """

    if fixture.fixture_id != ERROR_CAPABLE_FIXTURE_ID:
        raise ValueError("new material cannot execute under a historical fixture identity")
    if plan.episode_id != material.plan.episode_id:
        raise ValueError("plan and material describe different Episodes")
    if plan.seed < 0:
        raise ValueError("plan seed must be non-negative")

    return {
        "version": ERROR_CAPABLE_EXECUTION_VERSION,
        "index": index,
        "mode": mode,
        # The seed is the plan's, and the plan is digested: sending it again would create a
        # second copy that could disagree with the one the container verifies.
        "max_tool_requests": max_tool_requests,
        "max_continuations": max_continuations,
        "actor_case": actor_case,
        "drop_capabilities": list(drop_capabilities),
        "journal_root": journal_root,
        "resume": resume,
        "workspace_root": workspace_root,
        "adapter_version": adapter_version,
        "model_identity": model_identity.model_dump(mode="json"),
        "fixture_freeze_digest": fixture.freeze_digest,
        "plan_digest": sha256_digest(plan.model_dump(mode="json")),
        "materialization_digest": material.materialization_digest,
        "plan": plan.model_dump(mode="json"),
    }


def build_error_capable_request(
    *,
    execution_id: str,
    index: int,
    mode: str,
    plan: EpisodeScenarioPlan,
    material: MaterializedScenario,
    fixture: ErrorCapableFixture,
    model: ModelOptions,
    model_identity: ModelIdentity,
    adapter_version: str,
    timeout_seconds: int,
    max_tool_requests: int,
    max_continuations: int,
    actor_case: str = DEFAULT_ACTOR_CASE,
    drop_capabilities: tuple[str, ...] = (),
    journal_root: str | None = None,
    resume: bool = False,
    workspace_root: str | None = None,
) -> ExecutionRequest:
    """One Opportunity, addressed to a container.

    The prompt carried here is the frozen discovery prompt, unchanged and naming no file:
    the Agent still has to find the work request itself.  The container checks this string
    against its own copy, so a host and an image built from different revisions are refused
    rather than run.
    """

    return ExecutionRequest(
        execution_id=execution_id,
        case_id=execution_id,
        prompt=DISCOVERY_TASK,
        seed=plan.seed,
        max_tool_calls=max_tool_requests,
        timeout_seconds=timeout_seconds,
        model=model,
        error_capable_execution=error_capable_payload(
            index=index,
            mode=mode,
            plan=plan,
            material=material,
            fixture=fixture,
            model_identity=model_identity,
            adapter_version=adapter_version,
            max_tool_requests=max_tool_requests,
            max_continuations=max_continuations,
            actor_case=actor_case,
            drop_capabilities=drop_capabilities,
            journal_root=journal_root,
            resume=resume,
            workspace_root=workspace_root,
        ),
    )


__all__ = [
    "ERROR_CAPABLE_EXECUTION_VERSION",
    "build_error_capable_request",
    "error_capable_payload",
]
