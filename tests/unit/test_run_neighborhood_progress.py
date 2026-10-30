"""The progress line is read while a run is happening, so it must not lie about the verdict."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_neighborhood_campaign.py"


def _progress_module():
    spec = importlib.util.spec_from_file_location(
        "run_neighborhood_campaign_under_test", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _record(*, observed_types: tuple[str, ...], risk_class: str, target: str = "content_release"):
    return SimpleNamespace(
        index=3,
        episode_id="campaign.guided.003",
        mode=SimpleNamespace(value="coverage_guided"),
        stop_reason="model-stopped-after-action",
        observed=SimpleNamespace(
            target_type=target,
            observed_types=observed_types,
            risk_class=risk_class,
        ),
    )


def test_the_progress_line_names_the_types_the_episode_reached(capsys) -> None:
    module = _progress_module()
    module._progress(_record(observed_types=("audience_mismatch",), risk_class="violation"))
    line = capsys.readouterr().out
    assert "audience_mismatch" in line
    assert "clean" not in line


def test_an_unassessed_episode_is_not_printed_as_clean(capsys) -> None:
    module = _progress_module()
    module._progress(_record(observed_types=(), risk_class="unassessed"))
    line = capsys.readouterr().out
    assert "unassessed" in line
    assert "clean" not in line


def test_a_genuinely_clean_episode_still_reads_clean(capsys) -> None:
    module = _progress_module()
    module._progress(_record(observed_types=(), risk_class="clean"))
    assert "clean" in capsys.readouterr().out
