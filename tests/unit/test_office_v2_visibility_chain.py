"""The visibility chain: input evidence -> argument source -> pre-action knowledge.

Four shapes must stay distinguishable, using the real tool runtime and the same
inference the container session uses:

* shown and correctly cited  -> the source chain covers the resource;
* never shown                -> no input evidence, and no source is invented;
* shown but the source chain does not match (here: a different version)
                             -> must not be reported as "never shown";
* shown only by the call's own result -> invisible before that call, visible after.

Nothing here calls a model; the tool definitions only stand in for the runtime
plumbing the real tools use.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from sandbox.replay.decision_input import resource_mentions
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.models import (
    ActionKind,
    BindingResolutionStatus,
    OfficeV2Contract,
    ResolvedBinding,
    ResourceKind,
    ResourceRef,
    TaskContract,
    TaskFact,
    TaskGoal,
    TaskGoalGraph,
)
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.contracts import ToolResultStatus
from sandbox.scenarios.office_v2.tools.provenance import (
    infer_binding_argument_sources,
    infer_exact_argument_sources,
)
from sandbox.scenarios.office_v2.tools.runtime import (
    OfficeV2ToolRuntime,
    PreparedAction,
    ToolDefinition,
)
from sandbox.scenarios.office_v2.world import EpisodeWorld

FILE_ID = "drive.apollo.review-plan"
VERSION_2 = "version.apollo.review-plan.2"
VERSION_1 = "version.apollo.review-plan.1"
FILE_REF = ResourceRef(
    kind=ResourceKind.DRIVE_FILE,
    resource_id=FILE_ID,
    version_id=VERSION_2,
)


class FileIdArguments(OfficeV2Contract):
    file_id: str = Field(min_length=1)
    version_id: str | None = None


class SendArguments(OfficeV2Contract):
    related_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    subject: str = Field(default="Requested test attachment.")


def _task(actor_id: str) -> TaskContract:
    completed = TaskFact(
        fact_id="fact.visibility-complete",
        description="The visibility test operation completed",
    )
    return TaskContract(
        task_id="task.visibility-test",
        task_version="2.0",
        issuer_principal_id="user.maya.chen",
        issuer_authentication="authenticated",
        instruction="Exercise the visibility evidence chain.",
        actor_id=actor_id,
        goal_graph=TaskGoalGraph(
            goals=(
                TaskGoal(
                    goal_id="goal.visibility-test",
                    description="Complete the visibility test",
                    success_assertions=("fact.visibility-complete",),
                ),
            )
        ),
        required_response_facts=(completed,),
    )


def _runtime(*, with_task_binding: bool = False) -> OfficeV2ToolRuntime:
    canonical = load_canonical_world()
    actor = canonical.state.domain_graph.directory.derive_actor_context(
        actor_id="user.jordan.lee",
        authenticated_principal_id="user.maya.chen",
        session_capabilities=("drive.read", "mail.send"),
        logical_time=canonical.state.logical_clock.now,
    )

    def prepare_read(runtime: OfficeV2ToolRuntime, parsed: Any) -> PreparedAction:
        resource = ResourceRef(
            kind=ResourceKind.DRIVE_FILE, resource_id=parsed.file_id
        )
        runtime.visible_resource(resource)
        return PreparedAction(resources=(resource,))

    def execute_read(*_: object) -> dict[str, object]:
        return {
            "file_id": FILE_ID,
            "resource": FILE_REF.model_dump(mode="json"),
            "name": "Review Plan",
        }

    def prepare_send(runtime: OfficeV2ToolRuntime, parsed: Any) -> PreparedAction:
        resources = tuple(parsed.related_refs)
        for resource in resources:
            runtime.visible_resource(resource)
        return PreparedAction(resources=resources)

    def execute_send(*_: object) -> dict[str, object]:
        return {"delivered": True}

    definitions = {
        "read_test": ToolDefinition(
            name="read_test",
            arguments_model=FileIdArguments,
            action=ActionKind.READ,
            capability_id="drive.read",
            resource_kinds=(ResourceKind.DRIVE_FILE,),
            prepare=prepare_read,
            execute=execute_read,
        ),
        "send_test": ToolDefinition(
            name="send_test",
            arguments_model=SendArguments,
            action=ActionKind.SEND,
            capability_id="mail.send",
            resource_kinds=(ResourceKind.DRIVE_FILE,),
            prepare=prepare_send,
            execute=execute_send,
        ),
    }
    episode = EpisodeWorld(canonical, episode_id="visibility-chain")
    bindings = (
        (
            ResolvedBinding(
                query_id="query.task-support",
                binding_name="source_file",
                resource_refs=(FILE_REF,),
                matched_fact_refs=("fact.binding.0",),
                candidate_evidence_refs=("fact.binding.0",),
                resolution_status=BindingResolutionStatus.RESOLVED_UNIQUE,
                resolver_version="binding-resolver-v1",
                world_digest=episode.state.canonical_digest(),
                actor_view_digest="sha256:" + "0" * 64,
                resolution_digest="sha256:" + "0" * 64,
            ),
        )
        if with_task_binding
        else ()
    )
    return OfficeV2ToolRuntime(
        episode=episode,
        actor=actor,
        task=_task(actor.actor_id),
        definitions=definitions,
        bindings=bindings,
    )


def _session_sources(
    runtime: OfficeV2ToolRuntime,
    arguments: dict[str, Any],
):
    """Mirror the container session: prior tool results first, then frozen bindings."""

    tool_sources = infer_exact_argument_sources(arguments, runtime.results)
    binding_sources = infer_binding_argument_sources(
        arguments,
        runtime.binding_evidence,
        claimed_paths=tuple(source.argument_path for source in tool_sources),
    )
    return (*tool_sources, *binding_sources)


def _invoke_with_inferred_sources(
    runtime: OfficeV2ToolRuntime,
    tool_name: str,
    arguments: dict[str, Any],
):
    """Mirror the container session: infer sources, then invoke."""

    sources = _session_sources(runtime, arguments)
    return sources, runtime.invoke(tool_name, arguments, argument_sources=sources)


def _knowledge_entry(result, resource_id: str):
    return next(
        item
        for item in result.pre_action_knowledge.resources
        if item.resource.resource_id == resource_id
    )


def _audit(messages: list[dict[str, Any]], result) -> str:
    """Classify what the evidence allows an audit to claim about this resource."""

    entry = _knowledge_entry(result, FILE_ID)
    shown = bool(resource_mentions(messages, resource_id=FILE_ID))
    if entry.visible_before_action:
        return "shown_and_sourced"
    return "shown_but_unsourced" if shown else "never_shown"


def file_citation(**overrides: Any) -> dict[str, Any]:
    citation: dict[str, Any] = {"kind": "drive_file", "resource_id": FILE_ID}
    citation.update(overrides)
    return citation


def test_read_then_file_level_citation_yields_a_sourced_visible_resource() -> None:
    runtime = _runtime()

    read_result = runtime.invoke("read_test", {"file_id": FILE_ID})
    assert read_result.status is ToolResultStatus.SUCCEEDED
    read_evidence = {
        item.evidence_id: item for item in read_result.output_evidence
    }
    assert any(
        item.resource_ref is not None and item.resource_ref.version_id == VERSION_2
        for item in read_evidence.values()
    )

    arguments = {"related_refs": [file_citation()]}
    sources, send_result = _invoke_with_inferred_sources(runtime, "send_test", arguments)

    assert send_result.status is ToolResultStatus.SUCCEEDED
    assert [source.argument_path for source in sources] == [("related_refs", "0")]
    entry = _knowledge_entry(send_result, FILE_ID)
    assert entry.visible_before_action is True
    # The source chain, the knowledge evidence and the read output all point at
    # the same observed evidence record.
    assert entry.evidence_refs == sources[0].source_evidence_ids
    assert set(entry.evidence_refs).issubset(read_evidence)
    assert all(
        read_evidence[evidence_id].resource_ref.version_id == VERSION_2
        for evidence_id in entry.evidence_refs
    )


def test_citation_without_prior_evidence_stays_unsourced_and_invisible() -> None:
    runtime = _runtime()

    arguments = {"related_refs": [file_citation()]}
    sources, send_result = _invoke_with_inferred_sources(runtime, "send_test", arguments)

    assert sources == ()
    assert send_result.status is ToolResultStatus.SUCCEEDED
    entry = _knowledge_entry(send_result, FILE_ID)
    assert entry.visible_before_action is False
    assert entry.evidence_refs == ()
    assert _audit([], send_result) == "never_shown"


def test_wrong_version_citation_is_shown_but_unsourced() -> None:
    runtime = _runtime()
    read_result = runtime.invoke("read_test", {"file_id": FILE_ID})
    assert read_result.status is ToolResultStatus.SUCCEEDED

    arguments = {"related_refs": [file_citation(version_id=VERSION_1)]}
    sources, send_result = _invoke_with_inferred_sources(runtime, "send_test", arguments)

    assert sources == ()
    entry = _knowledge_entry(send_result, FILE_ID)
    assert entry.visible_before_action is False
    # The tool result did show the file: an audit must say the source chain, not
    # the input, is what failed to line up.
    messages = [{"role": "tool", "content": read_result.visible_output}]
    assert resource_mentions(messages, resource_id=FILE_ID) == (0,)
    assert _audit(messages, send_result) == "shown_but_unsourced"


def test_resource_is_invisible_before_its_own_result_and_visible_afterwards() -> None:
    runtime = _runtime()

    # With no binding and no prior tool result the id has no source yet: this read
    # is unsourced, and only its own result makes the resource visible to the call
    # that follows it.
    read_result = runtime.invoke("read_test", {"file_id": FILE_ID})
    read_entry = _knowledge_entry(read_result, FILE_ID)
    assert read_entry.visible_before_action is False
    assert read_entry.evidence_refs == ()

    arguments = {"related_refs": [file_citation()]}
    _, send_result = _invoke_with_inferred_sources(runtime, "send_test", arguments)
    assert _knowledge_entry(send_result, FILE_ID).visible_before_action is True


def test_a_cited_task_binding_makes_the_resource_visible() -> None:
    """A frozen binding rendered into the prompt is Agent-visible provenance.

    The task hands the Agent the file id as a concrete referent; citing it is not
    guessing, so the citation links to the seeded binding evidence.
    """

    runtime = _runtime(with_task_binding=True)

    arguments = {"file_id": FILE_ID}
    sources, read_result = _invoke_with_inferred_sources(runtime, "read_test", arguments)

    assert read_result.status is ToolResultStatus.SUCCEEDED
    assert [source.argument_path for source in sources] == [("file_id",)]
    entry = _knowledge_entry(read_result, FILE_ID)
    assert entry.visible_before_action is True
    assert entry.evidence_refs == sources[0].source_evidence_ids
    assert entry.evidence_refs[0].startswith("evidence.binding.")


def test_a_cited_binding_version_also_links() -> None:
    runtime = _runtime(with_task_binding=True)

    arguments = {"file_id": FILE_ID, "version_id": VERSION_2}
    sources, read_result = _invoke_with_inferred_sources(runtime, "read_test", arguments)

    assert read_result.status is ToolResultStatus.SUCCEEDED
    assert [source.argument_path for source in sources] == [
        ("file_id",),
        ("version_id",),
    ]
    assert _knowledge_entry(read_result, FILE_ID).visible_before_action is True


def test_a_citation_without_a_binding_stays_invisible() -> None:
    runtime = _runtime()

    arguments = {"file_id": FILE_ID}
    sources, read_result = _invoke_with_inferred_sources(runtime, "read_test", arguments)

    # No binding and no prior tool result: the id has no prior source at all.
    assert sources == ()
    assert _knowledge_entry(read_result, FILE_ID).visible_before_action is False


def test_a_citation_outside_the_bindings_stays_invisible() -> None:
    runtime = _runtime(with_task_binding=True)

    # This file exists and is readable, but no binding mentions it.
    arguments = {"file_id": "drive.apollo.decision-log"}
    sources, read_result = _invoke_with_inferred_sources(runtime, "read_test", arguments)

    assert read_result.status is ToolResultStatus.SUCCEEDED
    assert sources == ()
    assert (
        _knowledge_entry(read_result, "drive.apollo.decision-log").visible_before_action
        is False
    )


def test_definitions_cover_the_frozen_catalog() -> None:
    """Guard: the harness runs beside the real catalog, not instead of it."""

    assert set(office_v2_tool_definitions()) >= {"read_drive_file", "send_email"}
