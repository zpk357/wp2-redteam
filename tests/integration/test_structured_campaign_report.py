"""Reports consume real offline campaign checkpoints, never manufactured Episodes."""

from pathlib import Path
from runpy import run_path

import pytest

from sandbox.scenarios.structured_v1.fixtures import load_fixture
from sandbox.structured_v1.campaign import load_checkpoint
from sandbox.structured_v1.campaign_report import checkpoint_report
from sandbox.structured_v1.search import ArmKind


@pytest.fixture(scope="module")
def checkpoint(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("campaign-report")
    script = run_path(str(Path(__file__).parents[2] / "scripts/dry_run_structured_arms.py"))
    fixture = load_fixture("summary-delivery-b")
    script["_drive_arm"](
        manifest=fixture.manifest, fixture=fixture, arm=ArmKind.RANDOM_EVOLUTION,
        opportunities=16, mode="frozen", seed="report-test", workspace=workspace,
        resume_at=4,
    )
    return load_checkpoint(workspace / "random_evolution.json")


def test_failures_repeats_and_costs_survive_report(checkpoint):
    report = checkpoint_report(checkpoint)
    assert report["complete"]
    assert len(report["curves"]) == 16
    assert report["generation_failures"] > 0
    assert report["episodes"] + report["generation_failures"] == 16
    assert report["repeated_coverage_episodes"] > 0
    assert report["usage_reconciled"]
    for row in report["curves"]:
        if row["generation_failed"]:
            assert row["episode_id"] is None
            assert not row["executed_local"]
            assert row["cost"]["mutator_calls"] > 0
    final = report["curves"][-1]
    assert final["cumulative_cost"]["mutator_calls"] == checkpoint.usage.mutator_calls
    assert final["cumulative"]["joint"] == len(checkpoint.search.ledger.global_seen.joint)


def test_unknown_cost_stays_unknown(checkpoint):
    reservations = dict(checkpoint.reservations)
    key = next(key for key, value in reservations.items() if value.actual is not None)
    reservation = reservations[key]
    reservations[key] = reservation.model_copy(update={
        "actual": reservation.actual.model_copy(update={"input_tokens": None}),
    })
    state = checkpoint.model_copy(update={
        "reservations": reservations,
        "usage": checkpoint.usage.model_copy(update={"input_tokens": None}),
    })
    report = checkpoint_report(state)
    assert not report["usage_complete"]
    assert report["curves"][-1]["cumulative_cost"]["input_tokens"] is None


def test_missing_execution_is_not_invented(checkpoint):
    state = checkpoint.model_copy(update={
        "search": checkpoint.search.model_copy(update={"parent_coverage": {}}),
    })
    with pytest.raises(ValueError, match="execution binding"):
        checkpoint_report(state)


def test_unsettled_opportunity_is_retained(checkpoint):
    state = checkpoint.model_copy(update={
        "limits": checkpoint.limits.model_copy(update={"opportunities": 17}),
    }).reserve("opportunity-16")
    report = checkpoint_report(state)
    assert not report["complete"]
    assert len(report["curves"]) == 17
    assert not report["curves"][-1]["settled"]
    assert report["curves"][-1]["cost"] is None
    assert report["usage_reconciled"]


def test_findings_and_unknown_are_distinct(checkpoint):
    coverages = {
        key: value.model_copy(update={
            "findings": ("one-finding",),
            "outcome_by_obligation": {"data-release": "unknown"},
        }) for key, value in checkpoint.search.parent_coverage.items()
    }
    state = checkpoint.model_copy(update={
        "search": checkpoint.search.model_copy(update={"parent_coverage": coverages}),
    })
    report = checkpoint_report(state)
    assert report["curves"][-1]["unique_findings"] == 1
    assert all(row["unknown_obligations"] == 1 for row in report["curves"]
               if row["episode_id"])


def test_tampered_cost_is_rejected(checkpoint):
    state = checkpoint.model_copy(update={
        "usage": checkpoint.usage.model_copy(update={
            "model_calls": checkpoint.usage.model_calls + 1,
        }),
    })
    with pytest.raises(ValueError, match="usage does not reconcile"):
        checkpoint_report(state)
