from __future__ import annotations

import json
from pathlib import Path

from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_cli import main
from sandbox.fuzzer.v2_identity import build_v2_campaign_identity_lock
from sandbox.fuzzer.v2_report import build_v2_campaign_report
from sandbox.fuzzer.v2_scripted_runtime import ScriptedCampaignBootstrap
from tests.unit.test_office_v2_feedback_loop_batch_c import CAMPAIGN_ID, loop_fixture


def campaign_db(path: Path) -> Path:
    _, state = loop_fixture()
    with V2CampaignStore(path) as store:
        store.create_campaign(
            campaign_id=CAMPAIGN_ID,
            identity=build_v2_campaign_identity_lock(),
            initial_state=state,
        )
    return path


def test_inspect_and_report_are_deterministic(tmp_path: Path, capsys) -> None:
    path = campaign_db(tmp_path / "campaign.db")
    assert main(["inspect", "--db", str(path), "--campaign-id", CAMPAIGN_ID]) == 0
    displayed = json.loads(capsys.readouterr().out)
    output = tmp_path / "report.json"
    assert main(
        [
            "report",
            "--db",
            str(path),
            "--campaign-id",
            CAMPAIGN_ID,
            "--output",
            str(output),
        ]
    ) == 0
    capsys.readouterr()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == displayed

    with V2CampaignStore(path) as store:
        assert build_v2_campaign_report(store=store, campaign_id=CAMPAIGN_ID) == written


def test_plan_next_persists_the_single_generation_decision(
    tmp_path: Path, capsys
) -> None:
    path = campaign_db(tmp_path / "campaign.db")
    args = ["plan-next", "--db", str(path), "--campaign-id", CAMPAIGN_ID]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["allocation"]["candidate_count"] == 1
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second == first


def test_run_and_resume_share_the_formal_scripted_runtime(tmp_path: Path, capsys) -> None:
    promoted, state = loop_fixture()
    bootstrap = ScriptedCampaignBootstrap(
        initial_state=state,
        execution=promoted.execution,
        delta=promoted.delta,
    )
    bootstrap_path = tmp_path / "bootstrap.json"
    bootstrap_path.write_text(bootstrap.model_dump_json(indent=2), encoding="utf-8")
    db = tmp_path / "run-resume.db"
    common = [
        "--db",
        str(db),
        "--campaign-id",
        CAMPAIGN_ID,
        "--bootstrap",
        str(bootstrap_path),
    ]

    assert main(["run", *common, "--generations", "1"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["completed_generation_count"] == 1
    assert first["resumed"] is False
    assert main(["resume", *common, "--generations", "3"]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["completed_generation_count"] == 3
    assert resumed["resumed"] is True
    assert len(resumed["decision_digests"]) == 3
    assert len(resumed["feedback_digests"]) == 3
