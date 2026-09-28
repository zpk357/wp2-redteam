"""Campaign strategy selection for exploratory Office V2 runs."""

from __future__ import annotations

from enum import StrEnum


class CampaignStrategy(StrEnum):
    COVERAGE_GUIDED = "coverage_guided"
    RANDOM_INDEPENDENT = "random_independent"


__all__ = ["CampaignStrategy"]
