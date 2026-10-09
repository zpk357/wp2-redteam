"""The behaviour chain: what it must see, and what it must not.

Both halves are assertions of the same weight.  Seeing too little is what made the last pilot's key
put ten Episodes in one cell.  Seeing too much is worse in a way that is easy to miss: with the
material's own wording counted, every Episode is a new behaviour, so "this Episode added nothing"
never holds, the score's penalty never fires, and the selector goes back to having its violation rate
as the only signal it can act on.

The Episodes here are scripted, so each one differs from its neighbour in exactly one thing and the
assertion is about that thing rather than about a fixture.
"""

from __future__ import annotations

from test_error_capable_agent import _plan, _run, _SequenceAgent

from sandbox.scenarios.error_capable_bridge import bridge_trace
from sandbox.scenarios.error_capable_coverage import (
    ObservedKey,
    behaviour_chain,
    chain_tool_names,
)


def _reads(paths: list[str]) -> _SequenceAgent:
    return _SequenceAgent([("read_file", {"path": path}) for path in paths])


def _mail(body: str) -> _SequenceAgent:
    return _SequenceAgent(
        [("send_email", {"to": ["partner.contact"], "subject": "Brief", "body": body})]
    )


def _names(chain: list[list[str]]) -> list[str]:
    return [entry[0] for entry in chain]


def test_the_order_two_records_are_read_in_is_part_of_the_behaviour() -> None:
    """The difference the chain exists to catch.

    At the resolution the old key used, both of these are `read_file, read_file` -- which is how ten
    Episodes of the last pilot ended up in one cell.  They are not the same run: they opened the same
    two records in the opposite order, and on a task whose whole difficulty is knowing what to read
    before acting, that is a different way of working.
    """

    plan, material = _plan(family_index=0, path_index=0)
    paths = [material.plan.file_paths[item.file_id] for item in material.files]

    forward = _run(plan, material, adapter=_reads(paths[:2]))
    backward = _run(plan, material, adapter=_reads(list(reversed(paths[:2]))))

    ahead = behaviour_chain(forward, material)
    behind = behaviour_chain(backward, material)
    assert ahead != behind
    # ... and indistinguishable without the object of each call.
    assert _names(ahead) == _names(behind)


def test_which_record_was_read_is_part_of_the_behaviour() -> None:
    """Two runs of the same tool on different records are two behaviours."""

    plan, material = _plan(family_index=0, path_index=0)
    paths = [material.plan.file_paths[item.file_id] for item in material.files]

    one = behaviour_chain(_run(plan, material, adapter=_reads(paths[:1])), material)
    other = behaviour_chain(_run(plan, material, adapter=_reads(paths[3:4])), material)
    assert one != other
    assert _names(one) == _names(other)


def test_the_records_own_wording_is_not_part_of_the_behaviour() -> None:
    """The other half, and the one that keeps the penalty alive.

    A body copied out of the material differs between Episodes because the material differs.  Counted,
    it gives every Episode a distinct behaviour -- thirty-two out of thirty-two in the last pilot --
    and a penalty for repetition that can never fire is not a penalty.
    """

    plan, material = _plan(family_index=0, path_index=0)
    first = _run(plan, material, adapter=_mail("A summary of the current work."))
    second = _run(
        plan, material, adapter=_mail("An entirely different sentence, written another way.")
    )
    assert behaviour_chain(first, material) == behaviour_chain(second, material)


def test_records_are_named_by_what_they_are_for_not_by_id() -> None:
    """The ids are redrawn every Episode, so keying on them is the wording trap in another shape:
    every run would be new, and nothing would have been said."""

    plan, material = _plan(family_index=0, path_index=0)
    paths = [material.plan.file_paths[item.file_id] for item in material.files]
    chain = behaviour_chain(_run(plan, material, adapter=_reads(paths[:1])), material)

    joined = " ".join(" ".join(entry) for entry in chain)
    for item in material.files:
        assert str(item.file_id) not in joined, "a per-Episode id reached the chain"
    name = paths[0].rsplit("/", 1)[-1].split(".")[0]
    assert name in joined


def test_the_demoted_fields_are_reported_and_not_keyed() -> None:
    """How much was read and whether the inducement's file was opened are recorded, not keyed.

    Both are constant today -- the Agent reads all nine files every Episode -- so keying on them would
    swap one constant for another.  `carrier_opened` would additionally manufacture increments: a
    policy of reading only the first few files opens the carrier or not depending on where the
    material put it, so the same behaviour would give two keys and changing the layout would look like
    new behaviour coverage.

    Reported, because a run that reads selectively is exactly what a more careful Agent would do, and
    these two values move before anything else does.
    """

    plan, material = _plan(family_index=0, path_index=0)
    trace = _run(plan, material)
    bridged = bridge_trace(trace, material=material, plan=plan)
    key = ObservedKey.from_evidence(
        episode_id=plan.episode_id,
        family="summary_delivery",
        attack="cross_file_splice",
        kind="attack",
        bridge=bridged,
        trace=trace,
        material=material,
    )

    assert "scan" in key.behaviour_notes
    assert "carrier_opened" in key.behaviour_notes
    # The key is the digest of `behaviour_detail`, so anything stored there is a coefficient by
    # accident.  Neither of these is there.
    assert "scan" not in key.behaviour_detail
    assert "carrier_opened" not in key.behaviour_detail
    assert "chain" in key.behaviour_detail


def test_a_record_written_before_the_chain_existed_is_still_readable() -> None:
    """Archived evidence outlives the descriptor that wrote it.

    A readout over an old run should say what that run did rather than refuse to load -- the same
    reason `target_of` returns nothing for a mechanism that has left the set instead of raising.
    """

    assert chain_tool_names({"path": ["read_file", "send_email"]}) == ("read_file", "send_email")
    assert chain_tool_names(
        {"chain": [["read_file", "succeeded", "path=work-request"], ["send_email", "succeeded"]]}
    ) == ("read_file", "send_email")
    assert chain_tool_names({}) == ()
