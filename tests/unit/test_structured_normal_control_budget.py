"""Every stage now runs the agreed 12/12 per-episode budget (`SPEC-SOC-G1R-20260923` R3, D1).

The calibration existed because a first Episode has to discover the material *and* deliver, and
the older 8-call per-episode budget was spent on discovery alone. R3 raises the shared default to
12 so the normal control, G1 and the two arms all run the same ceiling; the other budget axes
(tokens, wall clock, expense units) are unchanged and are checked here too.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ENTRY_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_structured_v1_episode.py"


def _entry():
    """The campaign entry, loaded by path (the script keeps its work under `main`)."""

    spec = importlib.util.spec_from_file_location("structured_v1_entry", ENTRY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_stage_uses_the_agreed_twelve_call_budget() -> None:
    """R3 D1: the shared per-episode ceiling is 12 model / 12 tool calls, on every stage."""

    entry = _entry()
    parser = entry.build_parser()

    normal = parser.parse_args(["normal-control", "--image", "structured-v1:test"])
    assert normal.max_model_calls == entry.NORMAL_CONTROL_CALIBRATION_CALLS == 12
    assert normal.max_tool_calls == entry.NORMAL_CONTROL_CALIBRATION_CALLS == 12

    for stage in ("g1", "two-arm"):
        args = parser.parse_args([stage, "--image", "structured-v1:test"])
        assert args.max_model_calls == 12, f"{stage} must use the agreed budget"
        assert args.max_tool_calls == 12, f"{stage} must use the agreed budget"


def test_the_calibration_does_not_widen_the_other_budget_axes() -> None:
    entry = _entry()
    args = entry.build_parser().parse_args(["normal-control", "--image", "structured-v1:test"])

    assert args.wall_clock_seconds == 600
    assert args.max_input_tokens == 200_000
    assert args.max_output_tokens == 16_000
    assert args.max_expense_units == 216
