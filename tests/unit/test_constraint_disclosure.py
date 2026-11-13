"""End-to-end proof that a new run discloses the third constraint table.

``TASK.md`` TASK-02a says the protected-fact table and the delivery
authorisation must reach the artifact, and that tampering with either must be
detectable.  A unit test on the disclosure object would prove neither: it would
only show that a function the test itself called returns a digest.

So this file runs a real Episode with the scripted Agent (no model, no
container), reads the journal back off disk, and checks two things:

* the tables are there, with a digest that validates;
* editing one authorised recipient in the stored bytes makes the digest fail.

The second half is the load-bearing one.  A disclosure nobody can falsify is a
comment.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable import (
    ATTACK_SPECS,
    TASK_FAMILY_SPECS,
    ConstraintDisclosure,
    CoverageFeedback,
    EpisodeKind,
    ErrorCapableMode,
    MaterializedScenario,
    SelectorDecision,
    SelectorRequest,
    build_plan,
    disclose_constraints,
    materialize_scenario,
)
from sandbox.scenarios.error_capable_agent import (
    DiscoveryScriptedAgent,
    EpisodeJournal,
    run_agent_episode,
)
from sandbox.scenarios.error_capable_identity import ModelIdentity
from sandbox.scenarios.error_capable_journal import JournalStore
from sandbox.scenarios.error_capable_registry import (
    ERROR_CAPABLE_FIXTURE_ID,
    load_error_capable_fixture,
)

IDENTITY = ModelIdentity.capture(
    provider_id="local", raw_model_label="scripted-agent", provider_version="test"
)
EPISODE_ID = "test.constraints.0"


def _plan():
    family = TASK_FAMILY_SPECS[0]
    attack = ATTACK_SPECS[0]
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
        path_id=family.path_ids[0],
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


@pytest.fixture(scope="module")
def episode_journal(tmp_path_factory):
    root = tmp_path_factory.mktemp("constraint-disclosure")
    store = JournalStore(root, EPISODE_ID)
    store.claim()
    plan, material = _plan()
    asyncio.run(
        run_agent_episode(
            fixture=load_error_capable_fixture(ERROR_CAPABLE_FIXTURE_ID),
            plan=plan,
            material=material,
            adapter=DiscoveryScriptedAgent(),
            model_identity=IDENTITY,
            seed=1,
            max_tool_requests=24,
            journal=store,
        )
    )
    store.release()
    return plan, material, store.path


def test_material_really_carries_a_protected_fact():
    """Guard the fixture assumption: an empty table would make the test vacuous."""

    _, material = _plan()
    assert material.facts, "material declares no facts"
    assert any(fact.restricted for fact in material.facts)
    assert material.authorized_recipients


def test_disclosure_is_deterministic():
    _, material = _plan()
    first = disclose_constraints(material)
    second = disclose_constraints(material)
    assert first.disclosure_digest == second.disclosure_digest
    assert first.digest_is_valid()


def test_journal_on_disk_carries_the_third_table(episode_journal):
    _, material, path = episode_journal
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("constraints")
    assert isinstance(raw, dict), "journal carries no constraint disclosure"

    disclosure = ConstraintDisclosure.model_validate(raw)
    assert disclosure.digest_is_valid()
    assert disclosure.disclosure_digest == disclose_constraints(material).disclosure_digest
    assert tuple(fact.fact_id for fact in disclosure.facts) == tuple(
        fact.fact_id for fact in material.facts
    )
    assert disclosure.authorized_recipients == material.authorized_recipients


def test_journal_digest_covers_the_disclosure(episode_journal):
    """The disclosure is inside the sealed digest, not appended beside it."""

    _, _, path = episode_journal
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["constraints"] is not None

    sealed = payload.pop("journal_digest")
    assert sha256_digest(payload) == sealed

    payload["constraints"]["authorized_recipients"].append("attacker.example")
    assert sha256_digest(payload) != sealed


def test_a_journal_without_a_disclosure_keeps_its_old_digest_shape(episode_journal):
    """Removing the key must reproduce the pre-2026-11-12 payload, not a null field."""

    _, _, path = episode_journal
    payload = json.loads(path.read_text(encoding="utf-8"))
    legacy = {
        key: value for key, value in payload.items() if key not in {"constraints", "journal_digest"}
    }

    journal = EpisodeJournal.model_validate({**payload, "constraints": None})
    assert journal.constraints is None
    assert sha256_digest(journal.digest_payload()) == sha256_digest(legacy)


def test_editing_an_authorised_recipient_breaks_the_digest(episode_journal):
    _, _, path = episode_journal
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = dict(payload["constraints"])
    raw["authorized_recipients"] = [*raw["authorized_recipients"], "attacker.example"]

    tampered = ConstraintDisclosure.model_validate(raw)
    assert not tampered.digest_is_valid()


def test_editing_a_fact_breaks_the_digest(episode_journal):
    _, _, path = episode_journal
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = json.loads(json.dumps(payload["constraints"]))
    fact = dict(raw["facts"][0])
    fact["restricted"] = not fact["restricted"]
    raw["facts"][0] = fact

    tampered = ConstraintDisclosure.model_validate(raw)
    assert not tampered.digest_is_valid()


def test_materialized_scenario_is_not_needed_to_read_the_table(episode_journal):
    """The whole point: a reader with only the artifact can recover the tables."""

    _, _, path = episode_journal
    payload = json.loads(path.read_text(encoding="utf-8"))
    disclosure = ConstraintDisclosure.model_validate(payload["constraints"])
    restricted = [fact.fact_id for fact in disclosure.facts if fact.restricted]
    assert restricted
    readable = {
        fact.fact_id: fact.readable_by for fact in disclosure.facts if fact.readable_by
    }
    assert readable, "no fact declares who may read it; the audience table would be partial"


def test_materialized_scenario_type_is_what_the_disclosure_copies():
    archive = MaterializedScenario
    assert archive.model_fields["facts"] is not None
    assert archive.model_fields["authorized_recipients"] is not None
