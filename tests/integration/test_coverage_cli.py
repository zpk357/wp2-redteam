from __future__ import annotations

import pytest

from sandbox.cli import build_parser


def test_legacy_coverage_command_is_not_publicly_routable() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["coverage", "snapshot"])
