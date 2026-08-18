from __future__ import annotations

from pydantic import Field

from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
from sandbox.scenarios.office_v2.models import (
    ActionKind,
    OfficeV2Contract,
    ResourceKind,
    ResourceRef,
    TaskContract,
    TaskFact,
    TaskGoal,
    TaskGoalGraph,
)
from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
from sandbox.scenarios.office_v2.tools.contracts import (
    ToolFailureCode,
    ToolResultStatus,
)
from sandbox.scenarios.office_v2.tools.runtime import (
    OfficeV2ToolRuntime,
    PreparedAction,
    ToolDefinition,
)
from sandbox.scenarios.office_v2.world import EpisodeWorld


class EmptyArguments(OfficeV2Contract):
    marker: str = Field(default="ok", pattern="^ok$")


def _task(actor_id: str) -> TaskContract:
    completed = TaskFact(
        fact_id="fact.runtime-complete",
        description="The runtime test operation completed",
    )
    return TaskContract(
        task_id="task.runtime-test",
        task_version="2.0",
        issuer_principal_id="user.maya.chen",
        issuer_authentication="authenticated",
        instruction="Exercise the neutral Office V2 tool pipeline.",
        actor_id=actor_id,
        goal_graph=TaskGoalGraph(
            goals=(
                TaskGoal(
                    goal_id="goal.runtime-test",
                    description="Complete the test operation",
                    success_assertions=("fact.runtime-complete",),
                ),
            )
        ),
        required_response_facts=(completed,),
    )


def _runtime(*, capabilities: tuple[str, ...]) -> tuple[OfficeV2ToolRuntime, object]:
    canonical = load_canonical_world()
    episode = EpisodeWorld(canonical, episode_id="runtime-test")
    actor = canonical.state.domain_graph.directory.derive_actor_context(
        actor_id="user.jordan.lee",
        authenticated_principal_id="user.maya.chen",
        session_capabilities=capabilities,
        logical_time=canonical.state.logical_clock.now,
    )
    resource = ResourceRef(
        kind=ResourceKind.DRIVE_FILE,
        resource_id="drive.apollo.review-plan",
    )
    related_resource = ResourceRef(
        kind=ResourceKind.WORKSPACE_FILE,
        resource_id="/workspace/apollo/meeting-notes.md",
    )

    def prepare_read(runtime: OfficeV2ToolRuntime, _: object) -> PreparedAction:
        runtime.visible_resource(resource)
        return PreparedAction(resources=(resource,))

    def execute_read(*_: object) -> dict[str, object]:
        return {
            "resource": resource.model_dump(mode="json"),
            "name": "Review Plan",
            "related_refs": [related_resource.model_dump(mode="json")],
        }

    def execute_write(_: OfficeV2ToolRuntime, __: object, transaction: object) -> dict[str, object]:
        assert transaction is not None
        allocated = transaction.allocate_id("runtime.object")
        return {"allocated_id": allocated}

    def execute_failure(*_: object) -> dict[str, object]:
        raise ValueError("simulated validation failure")

    definitions = {
        "read_test": ToolDefinition(
            name="read_test",
            arguments_model=EmptyArguments,
            action=ActionKind.READ,
            capability_id="drive.read",
            resource_kinds=(ResourceKind.DRIVE_FILE,),
            prepare=prepare_read,
            execute=execute_read,
        ),
        "write_test": ToolDefinition(
            name="write_test",
            arguments_model=EmptyArguments,
            action=ActionKind.CREATE,
            capability_id="drive.write",
            resource_kinds=(ResourceKind.DRIVE_FILE,),
            prepare=lambda *_: PreparedAction(),
            execute=execute_write,
            writes_state=True,
        ),
        "failure_test": ToolDefinition(
            name="failure_test",
            arguments_model=EmptyArguments,
            action=ActionKind.CREATE,
            capability_id="drive.write",
            resource_kinds=(ResourceKind.DRIVE_FILE,),
            prepare=lambda *_: PreparedAction(),
            execute=execute_failure,
            writes_state=True,
        ),
    }
    return (
        OfficeV2ToolRuntime(
            episode=episode,
            actor=actor,
            task=_task(actor.actor_id),
            definitions=definitions,
        ),
        canonical,
    )


def test_runtime_rejects_protocol_errors_before_policy() -> None:
    runtime, _ = _runtime(capabilities=("drive.read",))

    unknown = runtime.invoke("unknown_tool", {})
    invalid = runtime.invoke("read_test", {"marker": "wrong"})
    version = runtime.invoke("read_test", {}, tool_contract_version="office-v2-tools-0.0")

    assert [item.status for item in (unknown, invalid, version)] == [
        ToolResultStatus.REJECTED,
        ToolResultStatus.REJECTED,
        ToolResultStatus.REJECTED,
    ]
    assert [item.failure_code for item in (unknown, invalid, version)] == [
        ToolFailureCode.UNKNOWN_TOOL,
        ToolFailureCode.INVALID_ARGUMENTS,
        ToolFailureCode.UNSUPPORTED_TOOL_CONTRACT_VERSION,
    ]
    assert all(item.policy_decision is None for item in (unknown, invalid, version))


def test_runtime_blocks_missing_capability_without_state_change() -> None:
    runtime, _ = _runtime(capabilities=())
    before = runtime.state.canonical_digest()

    result = runtime.invoke("read_test", {})

    assert result.status is ToolResultStatus.BLOCKED
    assert result.failure_code is ToolFailureCode.CAPABILITY_UNAVAILABLE
    assert result.policy_decision is not None
    assert result.policy_decision.capability_available is False
    assert runtime.state.canonical_digest() == before


def test_runtime_allows_delegation_missing_and_records_visible_evidence() -> None:
    runtime, _ = _runtime(capabilities=("drive.read",))

    result = runtime.invoke("read_test", {})

    assert result.status is ToolResultStatus.SUCCEEDED
    assert result.policy_decision is not None
    assert result.policy_decision.platform_allowed is True
    assert result.policy_decision.delegation_allowed is False
    assert result.policy_decision.effective_allowed is True
    assert result.output_evidence
    resource = ResourceRef.model_validate(result.visible_output["resource"])
    assert (
        next(
            item for item in result.output_evidence if item.field_path == ("resource",)
        ).resource_ref
        == resource
    )
    assert (
        next(
            item
            for item in result.output_evidence
            if item.field_path == ("related_refs", "0")
        ).resource_ref
        == ResourceRef(
            kind=ResourceKind.WORKSPACE_FILE,
            resource_id="/workspace/apollo/meeting-notes.md",
        )
    )
    assert runtime.evidence.items == result.output_evidence


def test_runtime_can_read_a_public_drive_resource_without_an_acl_entry() -> None:
    canonical = load_canonical_world()
    case = CLEAN_CASE_BY_ID["clean.t2.evergreen"]
    runtime = OfficeV2ToolRuntime(
        episode=EpisodeWorld(canonical, episode_id="runtime-public-drive-read"),
        actor=case.actor,
        task=case.task,
        definitions=office_v2_tool_definitions(),
        bindings=case.resolved_bindings,
    )

    result = runtime.invoke(
        "read_drive_file",
        {
            "file_id": "drive.evergreen.public-brief",
            "version_id": "version.evergreen.public-brief.1",
        },
    )

    assert result.status is ToolResultStatus.SUCCEEDED
    assert result.policy_decision is not None
    assert result.policy_decision.platform_allowed is True


def test_runtime_commits_metadata_delta_and_rolls_back_failures() -> None:
    runtime, canonical = _runtime(capabilities=("drive.write",))
    before = runtime.state.canonical_digest()

    success = runtime.invoke("write_test", {})
    after_success = runtime.state.canonical_digest()
    failed = runtime.invoke("failure_test", {})

    assert success.status is ToolResultStatus.SUCCEEDED
    assert success.state_transition is not None
    assert success.state_transition.committed is True
    assert success.state_transition.state_delta.changed_fields
    assert before != after_success
    assert failed.status is ToolResultStatus.FAILED
    assert failed.state_transition is not None
    assert failed.state_transition.committed is False
    assert failed.state_transition.state_delta.is_empty()
    assert runtime.state.canonical_digest() == after_success
    assert load_canonical_world().world_digest == canonical.world_digest
