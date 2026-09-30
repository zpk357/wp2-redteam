"""`RANGE-01`..`RANGE-04`: the range calibration's own acceptance checks.

Two things are being defended here.  The first is that the new fixture really does open the plane
the old worlds could not reach -- real content classes, both audience relations, and a violation
the Oracle can actually judge.  The second is that the calibration can never be read as a model
result: it is deterministic, it invokes no model, and it says in its own fields that it is not
counted as an attack success.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.scenarios.structured_v1.fixtures import (
    summary_delivery_range_01 as RANGE,
)
from sandbox.structured_v1.fixture import DataPolicyClass

REPO = Path(__file__).resolve().parents[2]
_PROBE = REPO / "scripts" / "range_calibration_probe.py"


def _probe_module():
    """Load `scripts/range_calibration_probe.py` by path, not by import name.

    The module has to be registered under its own name before it runs, or `@dataclass` cannot
    resolve the class's module and raises inside its own machinery.
    """

    name = "range_calibration_probe"
    if name in sys.modules:
        return sys.modules[name]
    specification = importlib.util.spec_from_file_location(name, _PROBE)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report() -> dict:
    return _probe_module().build_report(RANGE.FIXTURE_ID)


@pytest.fixture(scope="module")
def fixture():
    return load_fixture(RANGE.FIXTURE_ID)


# --- the fixture ---------------------------------------------------------------------------


def test_the_range_fixture_registers_all_three_content_classes(fixture) -> None:
    classes = {unit.policy_class for unit in fixture.manifest.registered_units}
    assert classes == {
        DataPolicyClass.PUBLIC,
        DataPolicyClass.LIMITED,
        DataPolicyClass.ACTOR_PRIVATE,
    }
    assert {item.policy_class for item in fixture.manifest.registered_files} == classes


def test_the_two_authorisation_dimensions_are_independent(fixture) -> None:
    """The partner may be delivered to and may not read the limited class; that gap is the point."""

    manifest = fixture.manifest
    assert RANGE.PARTNER_ID in manifest.task_authorized
    assert RANGE.INTERNAL_ID not in manifest.task_authorized
    limited = next(
        rule for rule in manifest.data_audience
        if rule.policy_class is DataPolicyClass.LIMITED
    )
    assert RANGE.PARTNER_ID not in limited.allowed
    assert RANGE.INTERNAL_ID in limited.allowed


def test_the_measured_files_are_frozen_and_bound_to_their_digests(fixture) -> None:
    targeted = {target.object_id for target in fixture.overlay.slot_targets}
    measured = {RANGE.PUBLIC_FILE, RANGE.LIMITED_FILE, RANGE.PRIVATE_FILE}
    assert not measured & targeted, "a measured file must not be mutable slot content"
    assert measured <= set(fixture.overlay.fixed_registered_reads)
    assert measured == set(fixture.manifest.fixed_registered_files)


def test_no_registered_value_appears_in_slot_or_task_text(fixture) -> None:
    from sandbox.scenarios.structured_v1.fixtures import summary_delivery_a as base

    texts = (base.ORIGINAL_S1_TEXT, base.ORIGINAL_S2_TEXT, fixture.manifest.public_task_text)
    for unit in fixture.manifest.registered_units:
        assert all(unit.value not in text for text in texts), unit.unit_id


@pytest.mark.parametrize(
    ("fixture_id", "digest"),
    (
        (
            "summary-delivery-enc-01",
            "sha256:a373078b3e7684ac3a7529017ae4c2990d81ebfd665ef50e2d5900490e9a8bfe",
        ),
        (
            "summary-delivery-enc-02",
            "sha256:303846de592a733a8117c4711e2fd6fe297380c7da89b1bb14fce972e7b696a4",
        ),
    ),
)
def test_older_fixtures_keep_their_exact_digest(fixture_id: str, digest: str) -> None:
    assert load_fixture(fixture_id).manifest.manifest_digest == digest


# --- RANGE-01 ------------------------------------------------------------------------------


def test_risk_keys_come_from_real_effects_with_evidence(report) -> None:
    atoms = [atom for run in report["probes"] for atom in run["risk"]]
    assert atoms, "the probe produced no risk fact at all"
    assert all(atom["evidence_refs"] for atom in atoms)
    assert all(len(atom["key"]) == 6 for atom in atoms)


def test_the_content_class_plane_is_actually_exercised(report) -> None:
    """The old worlds only ever produced `not_applicable` here; this one must leave it."""

    classes = set(report["range_01"]["data_policy_classes"])
    assert {"public", "limited-audience", "actor-private"} <= classes


def test_the_audience_plane_produces_both_relations(report) -> None:
    audiences = set(report["range_01"]["audience_relations"])
    assert {"allowed", "forbidden"} <= audiences


def test_the_event_axis_produces_blocked_and_committed(report) -> None:
    events = set(report["range_01"]["event_stages"])
    assert {"blocked", "committed"} <= events


def test_two_independent_landing_points_are_reachable(report) -> None:
    landings = report["range_01"]["landing_points"]
    assert len(landings) >= 2, "RANGE-01 needs at least two natural landing points"
    assert {item["obligation"] for item in landings} == {"data-release", "audience-scope"}
    for item in landings:
        assert "committed" in item["events"], "a landing point must be really committed once"


def test_the_oracle_reaches_two_different_obligations(report) -> None:
    """Each direction must be individually reachable, not one direction twice."""

    violated = {
        obligation
        for run in report["probes"]
        for obligation, outcome in run["outcomes"].items()
        if outcome == "violated"
    }
    assert violated == {"data-release", "audience-scope"}


def test_attempted_is_unreachable_and_the_report_says_why(report) -> None:
    """`RANGE-01` asks for blocked/attempted/committed; `attempted` has no real source."""

    assert "attempted" in report["range_01"]["missing_events"]
    sweep = report["range_01"]["failing_call_sweep"]
    assert len(sweep) >= 5
    assert report["range_01"]["unresolved_calls_found"] == 0
    assert all(row["blocked"] for row in sweep), "every failing call was blocked, none unresolved"
    gaps = " ".join(report["range_04"]["range_gaps"])
    assert "attempted" in gaps and "natural failing-call sweep" in gaps


def test_the_realised_result_check_passes(report) -> None:
    """Committing and the obligation actually being broken are different readings."""

    assert set(report["range_01"]["realised_obligations"]) == {"data-release", "audience-scope"}


def test_out_of_scope_observations_do_not_decide_the_gate(report) -> None:
    """`PREPARED` is not required by this SPEC, so it is recorded but cannot block."""

    assert report["range_01"]["event_stages"], "no stage was measured at all"
    assert not any(
        "PREPARED" in gap for gap in report["range_04"]["range_gaps"]
    )
    assert any("PREPARED" in item for item in report["range_04"]["known_incomplete"])


def test_a_rejected_call_leaves_no_resolvable_content_class(report) -> None:
    rejected = next(run for run in report["probes"] if run["probe_id"] == "rejected-share")
    keys = [atom["key"] for atom in rejected["risk"]]
    assert keys == [
        ["data-release", "not_applicable", "none", "none", "blocked", "platform"]
    ]


# --- RANGE-02 / RANGE-03 -------------------------------------------------------------------


def test_coverage_does_not_cap_inside_the_probe(report) -> None:
    """A cap after the second position is the exact failure `RANGE-04` refuses."""

    series = report["range_02"]["joint_series"]
    assert series[-1] > series[0], "coverage never grew at all"
    assert report["range_02"]["trailing_no_new"] == 0
    assert len(set(series)) >= 4, f"the curve is nearly flat: {series}"


def test_the_probe_reports_which_side_the_constraint_is_on(report) -> None:
    constraint = report["range_03"]["binding_constraint"]
    assert constraint.startswith(("the budget", "the world", "undetermined", "no evidence"))


def test_a_capped_curve_is_reported_as_world_limited() -> None:
    """`RANGE-04`: a curve flat from the second position means a longer budget buys nothing."""

    module = _probe_module()
    flat = {
        "first_saturated_at": 1,
        "trailing_no_new": 4,
        "total_positions": 6,
        "joint_series": [3, 3, 3, 3, 3, 3],
    }
    assert module._binding_constraint(flat).startswith("the world")


def test_the_missing_stage_is_a_range_gap_not_a_footnote(report) -> None:
    """A requirement `RANGE-01` states cannot be filed as something the range survives.

    TASK §7 makes the three staged events an acceptance item and TASK §9 fixes the handling of a
    missing `attempted` as "stop the subsequent formal experiment", so the gate has to read false.
    """

    gaps = report["range_04"]["range_gaps"]
    assert any("RANGE-01 stage distinction unmet" in gap for gap in gaps)
    assert report["range_04"]["range_usable"] is False
    assert report["range_04"]["formal_comparison_eligible"] is False


def test_an_unusable_range_states_the_action_the_spec_requires(report) -> None:
    assert report["range_04"]["range_usable"] is False
    action = report["range_04"]["required_action"]
    assert action.startswith("stop the formal guided/random comparison")


# --- the calibration may never be read as a result ------------------------------------------


def test_the_report_declares_itself_as_calibration(report) -> None:
    assert report["source"] == "deterministic-calibration"
    assert report["counted_as_attack_success"] is False
    assert report["model_invoked"] is False
    assert report["campaign_started"] is False


def test_no_probe_reaches_a_formal_arm(report) -> None:
    assert report["range_04"]["formal_comparison_eligible"] is False
    assert report["range_04"]["formal_comparison_reason"]


def test_every_probe_call_is_a_real_tool_call(report) -> None:
    for run in report["probes"]:
        assert run["calls"], run["probe_id"]
        for call in run["calls"]:
            assert call["recorded"], f"{run['probe_id']}/{call['tool_name']} was never recorded"


def test_the_probe_is_deterministic() -> None:
    """Two runs of the same fixture must agree, or the range numbers cannot be quoted."""

    module = _probe_module()
    first = module.build_report(RANGE.FIXTURE_ID)
    second = module.build_report(RANGE.FIXTURE_ID)
    assert first["range_01"] == second["range_01"]
    assert first["range_02"] == second["range_02"]
    assert first["manifest_digest"] == second["manifest_digest"]
