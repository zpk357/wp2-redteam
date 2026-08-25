from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "serve_office_v2_coverage_ui.py"
SNAPSHOT_PATH = ROOT / "coverage_ui" / "data" / "latest.json"
APP_PATH = ROOT / "coverage_ui" / "app.js"
INDEX_PATH = ROOT / "coverage_ui" / "index.html"
SPEC = importlib.util.spec_from_file_location("coverage_ui_server", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _fixture() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _write_snapshot(tmp_path: Path, snapshot: dict[str, Any], name: str) -> Path:
    target = tmp_path / name
    target.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    return target


def test_snapshot_contains_verified_real_server_campaign() -> None:
    payload, digest = MODULE._load_snapshot(SNAPSHOT_PATH)
    parsed = json.loads(payload)

    assert digest.startswith("sha256:")
    assert parsed["schema_version"] == MODULE.SCHEMA_VERSION
    assert parsed["source"]["kind"] == "server_campaign_archive"
    assert parsed["source"]["integrity_status"] == "verified_archive"
    assert parsed["source"]["is_server_data"] is True
    assert parsed["source"]["source_revision"] == (
        "98478fd629d3004b84f5f5af83b20470efafb57c"
    )
    campaign = parsed["campaigns"][0]
    assert campaign["id"] == "office-v2-real-g3-98478fd"
    assert campaign["runtime"]["kind"] == "langgraph"
    assert campaign["model"]["name"] == "qwen3.5:27b-q4_K_M"
    assert campaign["completed_generations"] == 3
    assert campaign["valid_committed_episodes"] == 3
    assert campaign["generations"][-1]["coverage"]["cumulative"] == {
        "primary_behavior": 71,
        "risk_contexts": 1,
    }


def test_frontend_defaults_to_human_readable_view() -> None:
    html = INDEX_PATH.read_text(encoding="utf-8")
    app = APP_PATH.read_text(encoding="utf-8")

    assert 'id="technical-details" type="checkbox"' in html
    assert 'id="technical-details" type="checkbox" checked' not in html
    assert "showTechnical: false" in app
    assert "该方向尚未完成真实模型基线" in app
    assert "查看调度器原始字段" in app
    assert "按风险大类分组的初始种子" in html
    assert "选择风险大类" in app
    assert "锁定具体目标" in app
    assert "选中父种子" in app
    assert "本代补哪块空白" not in app


def test_risk_families_group_seeds_and_drive_generation_selection() -> None:
    campaign = _fixture()["campaigns"][0]
    baseline = campaign["baseline"]
    families = baseline["risk_catalog"]["families"]

    assert len(families) == 4
    assert {seed_id for family in families for seed_id in family["seed_ids"]} == {
        seed["id"] for seed in baseline["seed_pool"]
    }
    expected = (
        (
            "risk-family.destructive-operation",
            "objective.a04.unauthorized-drive-delete",
        ),
        (
            "risk-family.sensitive-information-disclosure",
            "objective.a02.undelegated-drive-share",
        ),
        (
            "risk-family.wrong-modification-propagation",
            "objective.a12.workspace-to-drive-propagation",
        ),
    )
    for generation, (family_id, objective_id) in zip(
        campaign["generations"], expected, strict=True
    ):
        selection = generation["decision"]["frontier_selection"]
        assert selection["primary_risk_family"] == family_id
        assert selection["objective_id"] == objective_id
        assert selection["selected_parent_seed_id"] in selection["family_seed_ids"]
        assert selection["selected_parent_seed_id"] in selection["candidate_seed_ids"]


def test_first_generation_uses_baseline_seed_without_prior_feedback() -> None:
    campaign = _fixture()["campaigns"][0]
    generation = campaign["generations"][0]

    assert generation["number"] == 1
    assert generation["internal_decision_index"] == 0
    assert generation["decision"]["input_feedback_digest"] is None
    assert generation["decision"]["input_feedback_kind"] == "initial_baseline"
    assert generation["mutation"]["parent_seed_id"] == campaign["baseline"]["g1_selection"][
        "parent_seed_id"
    ]
    assert (
        generation["agent_input"]["candidate_delivery"]["content"]
        == generation["mutation"]["candidate_content"]
    )


def test_real_generations_have_complete_episode_and_feedback_lineage() -> None:
    campaign = _fixture()["campaigns"][0]
    previous_feedback = None
    for number, generation in enumerate(campaign["generations"], start=1):
        assert generation["number"] == number
        assert generation["settlement_kind"] == "candidate_settlement"
        assert generation["decision"]["input_feedback_digest"] == previous_feedback
        assert generation["mutation"]["validation"]["status"] == "accepted"
        assert generation["agent_input"]["task_instruction"] == generation["mutation"][
            "candidate_content"
        ]
        assert generation["episode"]["events"][-1]["event_type"] == "execution_finished"
        assert generation["coverage"]["delta_digest"].startswith("sha256:")
        assert generation["seed_promotion"]["disposition"] == "risk_seed"
        previous_feedback = generation["feedback_output"]["digest"]


def test_snapshot_rejects_feedback_lineage_tampering(tmp_path: Path) -> None:
    parsed = _fixture()
    parsed["campaigns"][0]["generations"][1]["decision"][
        "input_feedback_digest"
    ] = "sha256:tampered"

    with pytest.raises(ValueError, match="feedback lineage mismatch"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "bad-lineage.json"))


def test_snapshot_rejects_fake_episode_on_non_episode_settlement(tmp_path: Path) -> None:
    parsed = _fixture()
    campaign = parsed["campaigns"][0]
    rejected = campaign["generations"][2]
    rejected["settlement_kind"] = "non_episode_settlement"
    previous = campaign["generations"][1]["coverage"]["cumulative"]
    rejected["coverage"] = {
        "cumulative": previous,
        "delta": {"primary_behavior": 0, "risk_contexts": 0},
        "tool_path": [],
        "behavior_features": [],
        "risk_contexts": [],
    }

    with pytest.raises(ValueError, match="must not contain an episode"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "fake-episode.json"))


def test_snapshot_rejects_coverage_on_non_episode_settlement(tmp_path: Path) -> None:
    parsed = _fixture()
    rejected = parsed["campaigns"][0]["generations"][2]
    rejected["settlement_kind"] = "non_episode_settlement"

    with pytest.raises(ValueError, match="has coverage contribution"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "fake-coverage.json"))


def test_snapshot_rejects_event_sequence_tampering(tmp_path: Path) -> None:
    parsed = _fixture()
    events = parsed["campaigns"][0]["generations"][0]["episode"]["events"]
    events[5]["sequence"] = 99

    with pytest.raises(ValueError, match="event sequence is not contiguous"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "bad-sequence.json"))


def test_snapshot_rejects_coverage_summary_without_complete_detail(tmp_path: Path) -> None:
    parsed = _fixture()
    coverage = parsed["campaigns"][0]["generations"][0]["coverage"]
    coverage["delta"]["primary_behavior"] += 1

    with pytest.raises(ValueError, match="behavior coverage total differs from detail rows"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "partial-detail.json"))


def test_snapshot_rejects_completed_generation_count_mismatch(tmp_path: Path) -> None:
    parsed = _fixture()
    parsed["campaigns"][0]["completed_generations"] = 10

    with pytest.raises(ValueError, match="completed generation count differs from rows"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "bad-count.json"))


def test_snapshot_rejects_unverified_server_source(tmp_path: Path) -> None:
    parsed = _fixture()
    parsed["source"]["integrity_status"] = "pending"

    with pytest.raises(ValueError, match="must be verified"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "unverified.json"))


def test_snapshot_rejects_risk_family_seed_group_tampering(tmp_path: Path) -> None:
    parsed = _fixture()
    parsed["campaigns"][0]["baseline"]["risk_catalog"]["families"][0][
        "seed_ids"
    ].clear()

    with pytest.raises(ValueError, match="risk family seed grouping is inconsistent"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "bad-risk-group.json"))


def test_snapshot_rejects_frontier_parent_mismatch(tmp_path: Path) -> None:
    parsed = _fixture()
    selection = parsed["campaigns"][0]["generations"][0]["decision"][
        "frontier_selection"
    ]
    selection["candidate_seed_ids"].clear()
    selection["candidate_seeds"].clear()

    with pytest.raises(ValueError, match="selected parent is not a frontier candidate"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "bad-frontier-parent.json"))
