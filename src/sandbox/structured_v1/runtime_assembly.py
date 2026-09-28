"""Assemble the real tool runtime from a structured fixture (P6's remaining wiring).

The new protocol reuses the office_v2 tool definitions and the world state, but it must
build its **own** actor, task and bindings from the structured fixture instead of the old
scenario case. This module does that assembly, so a real `OfficeV2ToolRuntime.invoke()`
can run and its `policy_decision.recipient_ids` - the audience that actually got readable
access - can be captured as commit-time evidence.
"""

from __future__ import annotations

from sandbox.scenarios.office_v2.models import (
    TaskContract,
    TaskFact,
    TaskGoal,
    TaskGoalGraph,
)
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
from sandbox.scenarios.office_v2.world import EpisodeWorld
from sandbox.structured_v1.fixture import StructuredFixtureManifest

#: The capabilities the probe actor gets: read and write on drive/mail/workspace plus the
#: two delivery paths the inventory registers (`send_email`, `share_drive_file`), and nothing
#: that manages permissions or deletes.  Frozen for the first episode and the probe:
#: `SOC-SAF-02` fixes that the account can do its normal work, and the deliverable channels in
#: the fixture inventory are mail, shared storage and a persistent artifact.
PROBE_CAPABILITIES = (
    "drive.read",
    "drive.write",
    "drive.share",
    "mail.read",
    "mail.send",
    "workspace.read",
    "workspace.write",
)


def _task_contract(manifest: StructuredFixtureManifest, actor_id: str) -> TaskContract:
    completed = TaskFact(
        fact_id=f"fact.{manifest.fixture_id}.complete",
        description="The episode's delivery completed.",
    )
    return TaskContract(
        task_id=f"task.{manifest.fixture_id}",
        task_version="1.0",
        issuer_principal_id=actor_id,
        issuer_authentication="authenticated",
        instruction=manifest.public_task_text,
        actor_id=actor_id,
        goal_graph=TaskGoalGraph(
            goals=(
                TaskGoal(
                    goal_id=f"goal.{manifest.fixture_id}",
                    description=manifest.public_task_text,
                    success_assertions=(completed.fact_id,),
                ),
            )
        ),
        required_response_facts=(completed,),
    )


def assemble_tool_runtime(
    *,
    materialized: object,
    manifest: StructuredFixtureManifest,
    capabilities: tuple[str, ...] = PROBE_CAPABILITIES,
) -> OfficeV2ToolRuntime:
    """One real tool runtime over the materialised initial state, for this fixture's actor."""

    state = materialized.initial_state
    episode = EpisodeWorld.restore(
        episode_id=materialized.episode_id,
        base_world_digest=materialized.base_world_digest,
        state=state,
        initial_state_digest=materialized.initial_state_digest,
    )
    actor_id = manifest.actor_id or "maya.chen"
    actor = state.domain_graph.directory.derive_actor_context(
        actor_id=actor_id,
        authenticated_principal_id=actor_id,
        session_capabilities=capabilities,
        logical_time=state.logical_clock.now,
    )
    return OfficeV2ToolRuntime(
        episode=episode,
        actor=actor,
        task=_task_contract(manifest, actor_id),
        definitions=office_v2_tool_definitions(),
        bindings=(),
    )
