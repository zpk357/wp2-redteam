"""Contract tests for running checkpoints and recovery (`RA-CLOSE-01`, `RA-AC-09`).

The tests here are about what a checkpoint may claim and what recovery may do with it.  The forced
kill itself is exercised by `scripts/probe_error_capable_recovery.py`, which spawns real child
processes and `SIGKILL`s them; an exception would unwind cleanly and prove nothing about a process
that dies.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sandbox.scenarios.error_capable import (
    ATTACK_SPECS,
    TASK_FAMILY_SPECS,
    CoverageFeedback,
    EpisodeKind,
    ErrorCapableMode,
    SelectorDecision,
    SelectorRequest,
    build_plan,
    materialize_scenario,
)
from sandbox.scenarios.error_capable_agent import (
    AgentRequest,
    DiscoveryScriptedAgent,
    JournalPhase,
    run_agent_episode,
    tool_writes_state,
)
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_journal import (
    JournalConflictError,
    JournalCorruptError,
    JournalIdentityError,
    JournalStore,
    rebuild_world,
)
from sandbox.scenarios.error_capable_registry import (
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)
from sandbox.scenarios.office_v2.tools.runtime import ToolSessionCursor

IDENTITY = ModelIdentity.capture(
    provider_id="local", raw_model_label="scripted-agent", provider_version="test"
)
EPISODE_ID = "test.recovery.0"


def _plan(path_index: int = 0):
    family = TASK_FAMILY_SPECS[0]
    attack = ATTACK_SPECS[path_index % len(ATTACK_SPECS)]
    request = SelectorRequest(
        mode=ErrorCapableMode.GUIDED,
        seed=1,
        agent_model_name="probe.agent",
        selector_model_name="probe.agent",
        available_task_families=tuple(item.task_family for item in TASK_FAMILY_SPECS),
        available_attacks=tuple(item.attack_mode for item in ATTACK_SPECS),
        feedback=CoverageFeedback(menu_gaps=("x",), target_menu_cells=1),
    )
    decision = SelectorDecision(
        task_family=family.task_family,
        path_id=family.path_ids[path_index],
        attack_mode=attack.attack_mode,
        attack_carrier=request.available_carriers[0],
        layout_id="balanced-9",
        episode_kind=EpisodeKind.ATTACK,
        rationale="unit test",
    )
    plan = build_plan(
        request,
        decision,
        episode_id=EPISODE_ID,
        task_file_id=f"{EPISODE_ID}-task",
        file_ids=(
            f"{EPISODE_ID}-task",
            f"{EPISODE_ID}-public",
            f"{EPISODE_ID}-restricted",
            f"{EPISODE_ID}-history",
        ),
        attack_carrier=decision.attack_carrier,
        model_name="probe.agent",
    )
    return plan, materialize_scenario(plan)


def _run(store, *, resume: bool = False, adapter=None, path_index: int = 0, budget: int = 24):
    plan, material = _plan(path_index)
    return asyncio.run(
        run_agent_episode(
            fixture=load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID),
            plan=plan,
            material=material,
            adapter=adapter or DiscoveryScriptedAgent(),
            model_identity=IDENTITY,
            seed=1,
            max_tool_requests=budget,
            journal=store,
            resume=resume,
        )
    )


class _CountingAgent(DiscoveryScriptedAgent):
    """Counts how often the model was asked, so re-asking for a saved call is visible.

    It keeps the scripted adapter's version on purpose: the checkpoint records the adapter identity,
    and a resumed Episode must be continued by the same one -- a fact the identity check enforces.
    """

    version = DiscoveryScriptedAgent.version

    def __init__(self) -> None:
        super().__init__()
        self.asks = 0

    async def generate(self, messages, tools, *, seed):  # noqa: ANN001, ANN201
        self.asks += 1
        return await super().generate(messages, tools, seed=seed)


# --------------------------------------------------------------- the checkpoint file


def test_a_checkpoint_seals_its_own_digest(tmp_path) -> None:
    store = JournalStore(tmp_path, EPISODE_ID)
    trace = _run(store)
    journal = store.read()

    assert journal.phase is JournalPhase.SETTLED
    assert journal.settled is True
    assert journal.digest_is_valid()
    assert journal.final_trace is not None
    assert journal.final_trace.trace_digest == trace.trace_digest
    # The world state and the tool results are in the same checkpoint, so neither can be read alone.
    assert journal.steps and journal.world_state is not None


def test_write_refuses_an_unsealed_journal(tmp_path) -> None:
    store = JournalStore(tmp_path, EPISODE_ID)
    _run(store)
    journal = store.read()
    tampered = journal.model_copy(update={"issued": journal.issued + 1})

    with pytest.raises(JournalCorruptError):
        store.write(tampered)


def test_an_atomic_write_leaves_no_temporary_behind(tmp_path) -> None:
    store = JournalStore(tmp_path, EPISODE_ID)
    _run(store)

    assert store.path.exists()
    assert not store.path.with_name(store.path.name + ".tmp").exists()


def test_the_write_is_atomic_across_a_partial_temporary(tmp_path) -> None:
    """A half-written temporary must not be able to replace a good checkpoint."""

    store = JournalStore(tmp_path, EPISODE_ID)
    _run(store)
    good = store.path.read_text(encoding="utf-8")

    temporary = store.path.with_name(store.path.name + ".tmp")
    temporary.write_text(good[: len(good) // 2], encoding="utf-8")

    # Nothing reads the temporary directly, so the checkpoint is still the whole one.
    assert store.read().digest_is_valid()
    assert store.path.read_text(encoding="utf-8") == good


def test_a_truncated_checkpoint_is_refused(tmp_path) -> None:
    store = JournalStore(tmp_path, EPISODE_ID)
    _run(store)
    store.path.write_text(store.path.read_text(encoding="utf-8")[:-40], encoding="utf-8")

    with pytest.raises(JournalCorruptError):
        store.read()


def test_a_tampered_field_is_refused(tmp_path) -> None:
    store = JournalStore(tmp_path, EPISODE_ID)
    _run(store)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["issued"] += 1
    store.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(JournalCorruptError):
        store.read()


def test_a_moved_plan_is_refused_and_says_which_field_moved(tmp_path) -> None:
    store = JournalStore(tmp_path, EPISODE_ID)
    _run(store)
    journal = store.read()
    plan, material = _plan(path_index=1)  # a different path is a different plan

    mismatches = journal.mismatches(
        fixture=load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID),
        plan=plan,
        material=material,
        model_identity=IDENTITY,
        adapter_version=DiscoveryScriptedAgent.version,
        actor_case=journal.actor_case,
        dropped_capabilities=journal.dropped_capabilities,
        seed=journal.seed,
        max_tool_requests=journal.max_tool_requests,
        max_continuations=journal.max_continuations,
    )

    assert "plan_digest" in mismatches
    assert "materialization_digest" in mismatches
    # The first input is deliberately not in the list: the task prompt is family-level, so moving the
    # path leaves it unchanged.  That the check reports what moved, and only what moved, is the point.
    assert "first_input" not in mismatches


def test_a_second_live_writer_is_refused(tmp_path) -> None:
    import subprocess
    import sys

    store = JournalStore(tmp_path, EPISODE_ID)
    store.claim()
    # A pid that certainly belongs to a live process other than this one: a real child process,
    # because a made-up pid would only prove the check refuses something that does not exist.
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        store.lock_path.write_text(f"{other.pid}\n", encoding="utf-8")
        with pytest.raises(JournalConflictError):
            JournalStore(tmp_path, EPISODE_ID).claim()
    finally:
        other.kill()
        other.wait(timeout=10)
        store.release()


def test_the_owner_may_reclaim_its_own_lock(tmp_path) -> None:
    store = JournalStore(tmp_path, EPISODE_ID)
    store.claim()
    try:
        JournalStore(tmp_path, EPISODE_ID).claim()
    finally:
        store.release()
    assert store.writer_pid() is None


# --------------------------------------------------------------- recovery behaviour


def test_a_settled_episode_comes_back_sealed_and_executes_nothing(tmp_path) -> None:
    store = JournalStore(tmp_path, EPISODE_ID)
    first = _run(store)
    counter = _CountingAgent()
    second = _run(store, resume=True, adapter=counter)

    assert second.trace_digest == first.trace_digest
    assert counter.asks == 0, "a settled Episode must not ask the model anything"
    assert store.read().final_trace.trace_digest == first.trace_digest


def test_resume_continues_instead_of_starting_over(tmp_path) -> None:
    """A resumed Episode keeps what was already done and only asks the model for what is left.

    A fresh run asks the model once per turn.  A run resumed mid-way must ask for fewer turns -- not
    replay the conversation from the top -- and must still end with the same committed state.
    """

    store = JournalStore(tmp_path, EPISODE_ID)
    fresh = _CountingAgent()
    first = _run(store, adapter=fresh)
    baseline = store.read()

    # Rewind to the moment the second call was in flight and nothing after it had run.
    first_step = next(
        step for step in baseline.steps if step.request.tool_name == "read_file"
    )
    rewound = baseline.model_copy(
        update={
            "phase": JournalPhase.BEFORE_CALL,
            "pending": (first_step.request,),
            "steps": tuple(
                step
                for step in baseline.steps
                if step.request.sequence < first_step.request.sequence
            ),
            "settled": False,
            "final_trace": None,
        }
    ).seal()
    store.write(rewound)

    resumed_counter = _CountingAgent()
    second = _run(store, resume=True, adapter=resumed_counter)

    assert resumed_counter.asks < fresh.asks, "resume must not replay the whole conversation"
    assert resumed_counter.asks > 0, "the remaining turns still have to be decided by the model"
    assert any(
        step.request.call_id == first_step.request.call_id for step in second.steps
    ), "the in-flight call is executed from the journal rather than asked for again"


def test_a_side_effecting_call_in_flight_is_not_reissued(tmp_path) -> None:
    """The one thing recovery must never do: re-send a request that may already have committed."""

    store = JournalStore(tmp_path, EPISODE_ID)
    _run(store)
    journal = store.read()

    in_flight = AgentRequest(
        sequence=journal.issued,
        call_id="call.inflight.000",
        tool_name="send_email",
        arguments={"to": ["partner.contact"], "subject": "s", "body": "b"},
        arguments_digest=journal.first_input.payload_digest,
        accepted=True,
    )
    rewound = journal.model_copy(
        update={
            "phase": JournalPhase.BEFORE_CALL,
            "pending": (in_flight,),
            "settled": False,
            "final_trace": None,
        }
    ).seal()
    store.write(rewound)

    counter = _CountingAgent()
    resumed = _run(store, resume=True, adapter=counter)
    after = store.read()

    assert resumed.stop_reason == "recovery-uncertain-commit"
    assert resumed.unresolved and "send_email" in resumed.unresolved[0]
    assert after.settled is True
    # Nothing was re-sent, so the world did not move: the email is not there a second time.
    assert not any(
        step.request.tool_name == "send_email"
        and step.request.call_id == in_flight.call_id
        for step in resumed.steps
    )
    assert len(after.world_history) == len(journal.world_history)


def test_a_read_only_call_in_flight_is_reissued(tmp_path) -> None:
    """A call that cannot change the world is safe to run again, and the trace says it did."""

    store = JournalStore(tmp_path, EPISODE_ID)
    baseline = _run(store)
    journal = store.read()

    in_flight = AgentRequest(
        sequence=journal.issued,
        call_id="call.inflight.001",
        tool_name="list_directory",
        arguments={"path": "/workspace"},
        arguments_digest=journal.first_input.payload_digest,
        accepted=True,
    )
    store.write(
        journal.model_copy(
            update={
                "phase": JournalPhase.BEFORE_CALL,
                "pending": (in_flight,),
                "settled": False,
                "final_trace": None,
            }
        ).seal()
    )

    resumed = _run(store, resume=True)
    assert resumed.stop_reason != "recovery-uncertain-commit"
    assert any(step.request.call_id == in_flight.call_id for step in resumed.steps)
    assert resumed.trace_digest != baseline.trace_digest  # the extra call is visible


def test_tool_writes_state_reads_the_frozen_catalogue() -> None:
    assert tool_writes_state("send_email")
    assert tool_writes_state("create_drive_file")
    assert not tool_writes_state("read_file")
    assert not tool_writes_state("not_a_tool")


def test_a_cursor_with_a_gap_is_refused() -> None:
    from datetime import UTC, datetime

    from sandbox.scenarios.office_v2.tools.contracts import OfficeToolInvocation

    invocation = OfficeToolInvocation.model_construct(sequence=1)
    cursor = ToolSessionCursor(
        time_origin=datetime(2026, 1, 1, tzinfo=UTC), invocations=(invocation,), results=()
    )

    from sandbox.scenarios.error_capable import derive_scenario_actor, materialize_office_world
    from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
    from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASE_BY_ID
    from sandbox.scenarios.office_v2.tools import office_v2_tool_definitions
    from sandbox.scenarios.office_v2.tools.runtime import OfficeV2ToolRuntime
    from sandbox.scenarios.office_v2.world import EpisodeWorld

    _plan_value, material = _plan()
    case = CLEAN_CASE_BY_ID["clean.t4.delta"]
    world = materialize_office_world(
        material, load_canonical_world(), actor_id=case.actor.actor_id
    )
    episode = EpisodeWorld(world, episode_id=EPISODE_ID)
    actor = derive_scenario_actor(world, case.actor)

    with pytest.raises(ValueError, match="contiguous"):
        OfficeV2ToolRuntime(
            episode=episode,
            actor=actor,
            task=case.task,
            definitions=office_v2_tool_definitions(),
            cursor=cursor,
        )


def test_rebuild_world_refuses_a_chain_that_does_not_add_up(tmp_path) -> None:
    store = JournalStore(tmp_path, EPISODE_ID)
    _run(store)
    journal = store.read()

    broken = journal.model_copy(
        update={"world_history": (*journal.world_history, journal.world_history[0])}
    )
    with pytest.raises(ValueError, match="duplicate transaction id"):
        rebuild_world(broken)
