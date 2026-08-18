from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path("scripts/build_weeks_1_5_validation.py").resolve()
SPEC = importlib.util.spec_from_file_location("weeks_1_5_validation", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _artifacts(root: Path) -> tuple[Path, str]:
    campaign_id = "real-smoke"
    digest = "sha256:" + "a" * 64
    result = root / "reports" / campaign_id
    _write(
        result / "agent-run.json",
        {"execution": {"status": "succeeded"}, "container_removed": True},
    )
    _write(
        result / "weeks-1-5" / "week2-validation.json",
        {"passed": True, "model_digest": digest},
    )
    manifest = {
        "campaign_id": campaign_id,
        "agent_model_name": "qwen3:8b",
        "agent_model_digest": digest,
        "mutation_model_name": "qwen3:8b",
        "mutation_model_digest": digest,
    }
    _write(
        result / "campaign-export.json",
        {
            "manifest": manifest,
            "corpus": [{"work_item_id": "work"}],
            "work_items": [
                {
                    "status": "committed",
                    "source": {"kind": "mutation"},
                }
            ],
            "seeds": [{"origin": "mutation", "mutation_depth": 1}],
        },
    )
    _write(
        result / "coverage-export.json",
        {
            "snapshot": {
                "campaign_id": campaign_id,
                "total_trajectories": 1,
                "total_risk_categories": 2,
                "risk_depths": {"risk-a": 1, "risk-b": 0},
            },
            "results": [{"trajectory_id": "trace"}],
            "profiles": [{"profile_hash": digest}],
            "pretty_heatmap": {"campaign_id": campaign_id},
        },
    )
    _write(
        result / "mutation-export.json",
        {
            "snapshot": {"campaign_id": campaign_id},
            "batches": [{"feedback": {"gaps": []}, "plan": {"items": []}}],
            "candidates": [{"provider": "ollama", "model_digest": digest}],
            "rejections": [],
            "provider_calls": [
                {
                    "provider": "ollama",
                    "model_name": "qwen3:8b",
                    "model_digest": digest,
                    "request_digest": digest,
                    "latency_ms": 10,
                }
            ],
        },
    )
    _write(result / "campaign-validation.json", {"passed": True})
    _write(
        result / "learning" / "learning-metrics.json",
        {"model_sources": [f"ollama:qwen3:8b@{digest}"]},
    )
    _write(
        result / "learning" / "golden-set-candidate-manifest.json",
        {
            "work_record_count": 25,
            "candidate_count": 25,
            "candidate_pool_size_gate_passed": False,
            "is_golden_set": False,
            "labels_generated_by_model": False,
            "human_label_required": True,
        },
    )
    return result, campaign_id


def _run(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    result: Path,
    campaign_id: str,
    *,
    data: bool,
) -> int:
    output = result / ("data-validation.json" if data else "smoke-validation.json")
    argv = [
        str(SCRIPT),
        "--campaign-id",
        campaign_id,
        "--result-dir",
        str(result),
        "--output",
        str(output),
    ]
    if data:
        argv.append("--require-golden-pool")
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", argv)
    return MODULE.main()


def test_smoke_does_not_require_second_generation_or_gold_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, campaign_id = _artifacts(tmp_path)

    assert _run(monkeypatch, tmp_path, result, campaign_id, data=False) == 0

    payload = json.loads((result / "smoke-validation.json").read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["golden_set_status"]["is_human_labeled_gold"] is False


def test_data_mode_requires_second_generation_and_100_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, campaign_id = _artifacts(tmp_path)

    with pytest.raises(SystemExit, match="Weeks 1-5 acceptance failed"):
        _run(monkeypatch, tmp_path, result, campaign_id, data=True)

    payload = json.loads((result / "data-validation.json").read_text(encoding="utf-8"))
    failed = payload["failed_checks"]["week5_automated_campaign_loop"]
    assert "second_generation_requirement_satisfied" in failed
    assert "real_trajectory_candidate_pool_at_least_100" in failed
