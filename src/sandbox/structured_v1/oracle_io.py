"""Assembling what the oracle may look at, from the episode's own artifacts (P4).

The oracle reads the **committed records** and the **bound exposure facts**, never a final
world state. That is what makes a revocation, a deletion or an episode cleanup unable to
erase what was readable when the content was committed (``SOC-SAF-15``), and it is why the
initialization transaction - which is retained in the bundle for reconstruction - never
contributes an effect (``SOC-ENV-76``).

``complete`` is not a convenience flag. With a positive fact the violation stands; without
one, incomplete observation must read ``UNKNOWN`` rather than clean (``SOC-SAF-14``), so an
episode whose channels were not fully observed cannot come out as "no violation".
"""

from __future__ import annotations

from pydantic import Field

from sandbox.structured_v1.effects import expected_effects
from sandbox.structured_v1.evidence import (
    ChannelClosure,
    DeliveryEffect,
    EpisodeEvidence,
)
from sandbox.structured_v1.exposure import ExposureFact, to_events
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import Identifier, StructuredContract
from sandbox.structured_v1.obligations import EpisodeJudgment, judge_episode
from sandbox.structured_v1.transitions import StructuredTransitionRecord


class EpisodeArtifacts(StructuredContract):
    """The episode as it was recorded: records, bound exposures, closure and completeness."""

    episode_id: Identifier
    fixture_id: Identifier
    records: tuple[StructuredTransitionRecord, ...] = ()
    exposures: tuple[ExposureFact, ...] = ()
    closure: tuple[ChannelClosure, ...] = ()
    complete: bool = False
    missing: tuple[str, ...] = Field(default=())

    def effects(self) -> tuple[DeliveryEffect, ...]:
        """The ledger, as the committed records define it (``SOC-ENV-51``)."""

        return expected_effects(self.records)

    def deliveries(self) -> tuple[DeliveryEffect, ...]:
        """The committed effects a real audience could read (``SOC-SAF-08``)."""

        return tuple(effect for effect in self.effects() if effect.is_delivery())


def episode_evidence(artifacts: EpisodeArtifacts) -> EpisodeEvidence:
    """Everything the oracle may look at, and nothing it may infer."""

    return EpisodeEvidence(
        episode_id=artifacts.episode_id,
        fixture_id=artifacts.fixture_id,
        effects=artifacts.effects(),
        exposures=to_events(artifacts.exposures),
        closure=artifacts.closure,
        complete=artifacts.complete,
        missing=artifacts.missing,
    )


def judge_artifacts(
    artifacts: EpisodeArtifacts,
    *,
    manifest: StructuredFixtureManifest,
) -> EpisodeJudgment:
    """Judge one episode from its artifacts alone."""

    return judge_episode(episode_evidence(artifacts), manifest=manifest)
