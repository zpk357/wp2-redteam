from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "serve_office_v2_coverage_ui.py"
SNAPSHOT_PATH = ROOT / "coverage_ui" / "data" / "latest.json"
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


def test_v2_fixture_contains_harness_and_langgraph_campaigns() -> None:
    payload, digest = MODULE._load_snapshot(SNAPSHOT_PATH)
    parsed = json.loads(payload)

    assert digest.startswith("sha256:")
    assert parsed["schema_version"] == MODULE.SCHEMA_VERSION
    assert parsed["source"]["kind"] == "deterministic_fixture"
    assert parsed["source"]["is_server_data"] is False
    assert [campaign["runtime"]["kind"] for campaign in parsed["campaigns"]] == [
        "deepseek_harness",
        "langgraph",
    ]
    assert [campaign["completed_generations"] for campaign in parsed["campaigns"]] == [3, 2]


def test_first_generation_uses_baseline_seed_without_prior_feedback() -> None:
    harness = _fixture()["campaigns"][0]
    generation = harness["generations"][0]

    assert generation["number"] == 1
    assert generation["internal_decision_index"] == 0
    assert generation["decision"]["input_feedback_digest"] is None
    assert generation["decision"]["input_feedback_kind"] == "initial_baseline"
    assert generation["mutation"]["parent_seed_id"] == harness["baseline"]["g1_selection"][
        "parent_seed_id"
    ]
    assert (
        generation["agent_input"]["candidate_delivery"]["content"]
        == generation["mutation"]["candidate_content"]
    )


def test_snapshot_rejects_feedback_lineage_tampering(tmp_path: Path) -> None:
    parsed = _fixture()
    parsed["campaigns"][0]["generations"][1]["decision"]["input_feedback_digest"] = (
        "sha256:tampered"
    )

    with pytest.raises(ValueError, match="feedback lineage mismatch"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "bad-lineage.json"))


def test_snapshot_rejects_fake_episode_on_non_episode_settlement(tmp_path: Path) -> None:
    parsed = _fixture()
    harness = parsed["campaigns"][0]
    rejected = harness["generations"][2]
    accepted = harness["generations"][1]
    rejected["agent_input"] = copy.deepcopy(accepted["agent_input"])
    rejected["episode"] = copy.deepcopy(accepted["episode"])

    with pytest.raises(ValueError, match="must not contain an episode"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "fake-episode.json"))


def test_snapshot_rejects_coverage_on_non_episode_settlement(tmp_path: Path) -> None:
    parsed = _fixture()
    rejected_coverage = parsed["campaigns"][0]["generations"][2]["coverage"]
    rejected_coverage["delta"]["primary_behavior"] = 1
    rejected_coverage["behavior_features"].append(
        {"kind": "tool_unigram", "value": "files.read", "is_new": True}
    )

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
    parsed["campaigns"][1]["completed_generations"] = 10

    with pytest.raises(ValueError, match="completed generation count differs from rows"):
        MODULE._load_snapshot(_write_snapshot(tmp_path, parsed, "bad-count.json"))
