"""The container side of the error-capable branch.

This module is reached only by a request carrying ``error_capable_execution``.  It rebuilds
the fixture, the plan and the material from the frozen inputs and refuses the request when
any of them fails to reproduce the digest the host sent.

The point of that refusal is that **the world never travels**.  The host ships a plan and
three digests; the container recomputes what those digests are supposed to describe and
stops if its own frozen inputs produce anything else.  A container built from another
revision of the fixture therefore cannot run an Opportunity as if it were this one -- it
either reproduces the same world or executes nothing.  A payload's `plan` is treated as a
claim to be checked, never as the world to act on.

The Office world is still rebuilt here rather than shipped, and it is still the object the
Episode is scored against.  What has changed is where its workspace domain *lives*: when the
payload names a `workspace_root`, the material is written to that directory and the read tools
open those files.  The container is what makes that root a real mount rather than a temporary
directory, which is why the path is the container's to know and the host's to name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import (
    EpisodeScenarioPlan,
    MaterializedScenario,
    materialize_scenario,
)
from sandbox.scenarios.error_capable_agent import DISCOVERY_TASK
from sandbox.scenarios.error_capable_registry import (
    ERROR_CAPABLE_FIXTURE_ID,
    ErrorCapableFixture,
    load_error_capable_fixture,
)

from app.adapter.base import AdapterConfigurationError

#: The payload shape this image understands.  A host that sends another one is refused
#: rather than read for whatever fields happen to line up.
SUPPORTED_PAYLOAD_VERSION = "error-capable-execution-v2"


@dataclass(frozen=True)
class ErrorCapableRun:
    """One Opportunity, rebuilt and verified inside the container."""

    payload: dict[str, Any]
    fixture: ErrorCapableFixture
    plan: EpisodeScenarioPlan
    material: MaterializedScenario
    #: The directory the workspace material is written to, as this container sees it, or None to
    #: keep the workspace in the state object alone.
    workspace_root: str | None = None

    @property
    def episode_id(self) -> str:
        return self.plan.episode_id


def _require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise AdapterConfigurationError(code, detail)


def build_error_capable_run(
    payload: dict[str, Any] | None,
    *,
    prompt: str,
) -> ErrorCapableRun:
    """Rebuild this Opportunity from the frozen inputs, or refuse the request.

    Every digest in the payload is a claim about what the container should have computed.
    Each one is recomputed here and compared; nothing is taken on the host's word, because
    the whole reason the payload carries a plan instead of a world is that the container is
    the side that can prove what the world is.
    """

    _require(payload is not None, "error_capable_configuration_error", "missing execution payload")
    assert payload is not None

    version = payload.get("version")
    _require(
        version == SUPPORTED_PAYLOAD_VERSION,
        "error_capable_configuration_error",
        f"unsupported error-capable payload version: {version!r}",
    )

    # The discovery prompt names no file, no recipient and no fact.  Checking it here catches
    # a host and an image built from different revisions before either spends a model call.
    _require(
        prompt == DISCOVERY_TASK,
        "error_capable_configuration_error",
        "the request prompt is not this image's frozen discovery prompt",
    )

    raw_plan = payload.get("plan")
    _require(
        isinstance(raw_plan, dict),
        "error_capable_data_integrity_error",
        "the execution payload carries no plan object",
    )
    plan = EpisodeScenarioPlan.model_validate(raw_plan)

    # 1. The fixture: rebuilt from the image's own frozen inputs.
    fixture = load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID)
    expected_freeze = payload.get("fixture_freeze_digest")
    _require(
        fixture.freeze_digest == expected_freeze,
        "error_capable_data_integrity_error",
        "this image's fixture does not reproduce the frozen identity the host sent",
    )

    # 2. The plan: the claim, checked against itself before it is used.
    expected_plan = payload.get("plan_digest")
    _require(
        sha256_digest(plan.model_dump(mode="json")) == expected_plan,
        "error_capable_data_integrity_error",
        "the plan in the payload does not hash to the plan digest the host sent",
    )

    # 3. The material: recomputed from the plan, never shipped.
    material = materialize_scenario(plan)
    expected_material = payload.get("materialization_digest")
    _require(
        material.materialization_digest == expected_material,
        "error_capable_data_integrity_error",
        "the material this image computes does not match the digest the host sent",
    )

    # 4. The workspace root: a claim about this container, so it is checked as one.  A relative
    # path would be resolved against whatever the process's working directory happens to be,
    # which is a way for a run to write its material somewhere nobody looks.
    workspace_root = payload.get("workspace_root")
    _require(
        workspace_root is None
        or (isinstance(workspace_root, str) and workspace_root.startswith("/")),
        "error_capable_configuration_error",
        f"the workspace root is not an absolute container path: {workspace_root!r}",
    )

    return ErrorCapableRun(
        payload=payload,
        fixture=fixture,
        plan=plan,
        material=material,
        workspace_root=workspace_root,
    )


__all__ = [
    "ErrorCapableRun",
    "SUPPORTED_PAYLOAD_VERSION",
    "build_error_capable_run",
]
