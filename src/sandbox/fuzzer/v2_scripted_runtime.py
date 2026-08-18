"""Formal deterministic run/resume entry for the Office V2 engineering loop."""

from __future__ import annotations

from sandbox.coverage.v2_episode_coverage import V2CoverageDelta
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import OfficeV2Contract

from .v2_campaign import record_valid_episode
from .v2_campaign_state import V2CampaignStateSnapshot, build_campaign_state
from .v2_campaign_store import V2CampaignStore
from .v2_corpus import ExecutionRecord
from .v2_feedback import build_next_generation_feedback
from .v2_orchestrator import (
    GenerationClosureKind,
    build_generation_closure_receipt,
)
from .v2_runtime import (
    V2CampaignRunResult,
    V2GenerationAdvance,
    run_or_resume_campaign,
)


class ScriptedCampaignBootstrap(OfficeV2Contract):
    initial_state: V2CampaignStateSnapshot
    execution: ExecutionRecord
    delta: V2CoverageDelta


ScriptedCampaignRunResult = V2CampaignRunResult


class _ScriptedGenerationDriver:
    def __init__(self, bootstrap: ScriptedCampaignBootstrap) -> None:
        self.bootstrap = bootstrap

    def advance(self, *, campaign_id, decision, state, previous_feedback):
        generation = state.lifecycle.counters.generation_index
        next_state = build_campaign_state(
            coverage=state.coverage,
            corpus=state.corpus,
            frontiers=state.frontiers,
            exposure_ledger=state.exposure_ledger,
            budget=state.budget,
            lifecycle=record_valid_episode(state.lifecycle, coverage_gain=generation == 0),
        )
        feedback = build_next_generation_feedback(
            campaign_id=campaign_id,
            generation_index=generation + 1,
            execution=self.bootstrap.execution,
            delta=self.bootstrap.delta,
            previous_feedback_digest=(
                previous_feedback.feedback_digest if previous_feedback is not None else None
            ),
            consecutive_no_gain=generation > 0,
        )
        settlement_digest = sha256_digest(
            {"scripted_generation": generation, "state": next_state.state_digest}
        )
        closure = build_generation_closure_receipt(
            campaign_id=campaign_id,
            generation_index=generation,
            closure_kind=GenerationClosureKind.CANDIDATE_SETTLEMENT,
            settlement_id=f"settlement.scripted.{generation}",
            settlement_digest=settlement_digest,
            resulting_state_digest=next_state.state_digest,
        )
        return V2GenerationAdvance(
            next_state=next_state,
            closure=closure,
            feedback=feedback,
        )


def run_or_resume_scripted_campaign(
    *,
    store: V2CampaignStore,
    campaign_id: str,
    bootstrap: ScriptedCampaignBootstrap,
    generation_count: int = 3,
) -> ScriptedCampaignRunResult:
    return run_or_resume_campaign(
        store=store,
        campaign_id=campaign_id,
        initial_state=bootstrap.initial_state,
        generation_count=generation_count,
        driver=_ScriptedGenerationDriver(bootstrap),
    )


__all__ = [
    "ScriptedCampaignBootstrap",
    "ScriptedCampaignRunResult",
    "run_or_resume_scripted_campaign",
]
