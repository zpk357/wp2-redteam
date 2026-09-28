"""Structured-only budget settlement and crash recovery for the two-arm trial."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from sandbox.structured_v1.bundle import EpisodeBundle
from sandbox.structured_v1.coverage import bind_coverage_execution, extract_coverage
from sandbox.structured_v1.feedback import (
    ParentBundleIncomplete,
    ParentBundleNotFound,
    ParentEvidenceError,
)
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.generation import MutationPlan, TextGenerationFailed
from sandbox.structured_v1.models import Identifier, StructuredCase, StructuredContract
from sandbox.structured_v1.oracle_io import EpisodeArtifacts
from sandbox.structured_v1.search import (
    CURRENT_SEARCH_PROTOCOL_IDENTITY,
    ArmKind,
    CandidateRefused,
    ParentFeedbackUnavailable,
    SearchProtocolIdentity,
    SearchState,
    SelectionReceipt,
    TwoArmSearch,
)


class CampaignLimits(StructuredContract):
    opportunities: int = Field(ge=1)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    wall_clock_seconds: int = Field(ge=1)
    expense_units: int = Field(ge=0)
    #: The Mutator side of the same envelope (`SOC-FBK-15` records it separately).
    mutator_calls: int = Field(default=0, ge=0)
    mutator_input_tokens: int = Field(default=0, ge=0)
    mutator_output_tokens: int = Field(default=0, ge=0)


class CampaignUsage(StructuredContract):
    """What one opportunity cost, with the agent and the mutator kept apart (`SOC-FBK-15`)."""

    opportunities: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=0, ge=0)
    output_tokens: int | None = Field(default=0, ge=0)
    wall_clock_seconds: int = Field(default=0, ge=0)
    expense_units: int | None = Field(default=0, ge=0)
    mutator_calls: int = Field(default=0, ge=0)
    mutator_input_tokens: int | None = Field(default=0, ge=0)
    mutator_output_tokens: int | None = Field(default=0, ge=0)

    @property
    def complete(self) -> bool:
        return all(
            value is not None
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.expense_units,
                self.mutator_input_tokens,
                self.mutator_output_tokens,
            )
        )


class Reservation(StructuredContract):
    receipt_id: Identifier
    opportunity: int
    submitted: bool = False
    settled: bool = False
    planned: CampaignUsage = CampaignUsage()
    actual: CampaignUsage | None = None


# Alias kept at the campaign boundary so reports and callers do not need to import the search
# implementation to inspect protocol compatibility.
CampaignProtocolIdentity = SearchProtocolIdentity
CURRENT_CAMPAIGN_PROTOCOL_IDENTITY = CURRENT_SEARCH_PROTOCOL_IDENTITY


class CampaignCheckpoint(StructuredContract):
    limits: CampaignLimits
    #: ``None`` is the explicit legacy identity for old checkpoint JSON.  It remains readable,
    #: but a non-empty legacy checkpoint cannot silently resume under the FDM protocol.
    protocol_identity: CampaignProtocolIdentity | None = None
    usage: CampaignUsage = CampaignUsage()
    reservations: dict[str, Reservation] = Field(default_factory=dict)
    search: SearchState = SearchState()
    selections: tuple[SelectionReceipt, ...] = ()
    #: The mutation plans this campaign asked for, accepted or failed (`SS-010`).
    generations: tuple[MutationPlan, ...] = ()
    lineage: dict[str, str] = Field(default_factory=dict)
    stopped: bool = False
    isolated: bool = False
    stop_reason: str | None = None
    search_candidates: dict[str, StructuredCase] = Field(default_factory=dict)
    search_seed: str | None = None
    pending_search_opportunity: int | None = None
    pending_selection: SelectionReceipt | None = None
    pending_case: StructuredCase | None = None
    pending_generation: MutationPlan | None = None
    failed_parent_evidence: tuple[dict[str, str | int], ...] = ()

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler: SerializerFunctionWrapHandler) -> dict:
        payload = handler(self)
        if not self.search_candidates:
            payload.pop("search_candidates", None)
        if self.search_seed is None:
            payload.pop("search_seed", None)
        if self.pending_search_opportunity is None:
            payload.pop("pending_search_opportunity", None)
        if self.protocol_identity is None:
            payload.pop("protocol_identity", None)
        if self.pending_selection is None:
            payload.pop("pending_selection", None)
        if self.pending_case is None:
            payload.pop("pending_case", None)
        if self.pending_generation is None:
            payload.pop("pending_generation", None)
        return payload

    def bind_protocol(
        self,
        identity: CampaignProtocolIdentity = CURRENT_CAMPAIGN_PROTOCOL_IDENTITY,
    ) -> CampaignCheckpoint:
        """Bind a new checkpoint or reject a legacy/incompatible resume explicitly."""

        if self.protocol_identity is None:
            if self.search.protocol_identity == identity:
                return self.model_copy(update={"protocol_identity": identity})
            has_work = bool(
                self.usage.opportunities
                or self.reservations
                or self.selections
                or self.generations
                or self.search.opportunity
                or self.search.plans
                or self.search.parent_coverage
                or self.search.parent_feedback
            )
            if has_work:
                raise ValueError("legacy checkpoint cannot resume under FDM protocol")
            return self.model_copy(update={"protocol_identity": identity})
        if self.protocol_identity != identity:
            raise ValueError("campaign protocol identity mismatch")
        return self

    def record_selection(
        self, receipt: SelectionReceipt, *, child_id: str
    ) -> CampaignCheckpoint:
        return self.model_copy(
            update={
                "selections": (*self.selections, receipt),
                "lineage": {**self.lineage, child_id: receipt.parent_id},
            }
        )

    def record_generation(self, plan: MutationPlan) -> CampaignCheckpoint:
        return self.model_copy(update={"generations": (*self.generations, plan)})

    def with_search(self, search: SearchState) -> CampaignCheckpoint:
        return self.model_copy(update={"search": search})

    def record_pending_selection(self, receipt: SelectionReceipt) -> CampaignCheckpoint:
        return self.model_copy(update={"pending_selection": receipt})

    def record_pending_generation(
        self, case: StructuredCase, plan: MutationPlan
    ) -> CampaignCheckpoint:
        return self.model_copy(update={
            "pending_case": case,
            "pending_generation": plan,
        })

    def clear_pending(self) -> CampaignCheckpoint:
        return self.model_copy(update={
            "pending_selection": None,
            "pending_case": None,
            "pending_generation": None,
            "pending_search_opportunity": None,
        })

    def reserve(
        self, receipt_id: str, planned: CampaignUsage | None = None
    ) -> CampaignCheckpoint:
        planned = planned or CampaignUsage()
        if self.stopped or self.usage.opportunities >= self.limits.opportunities:
            raise RuntimeError("campaign budget does not permit another opportunity")
        outstanding = tuple(item.planned for item in self.reservations.values()
                            if not item.settled)
        reserved_model = sum(item.model_calls for item in outstanding)
        reserved_tool = sum(item.tool_calls for item in outstanding)
        reserved_wall = sum(item.wall_clock_seconds for item in outstanding)
        reserved_input = sum(item.input_tokens or 0 for item in outstanding)
        reserved_output = sum(item.output_tokens or 0 for item in outstanding)
        reserved_expense = sum(item.expense_units or 0 for item in outstanding)
        reserved_mutator_calls = sum(item.mutator_calls for item in outstanding)
        reserved_mutator_input = sum(item.mutator_input_tokens or 0 for item in outstanding)
        reserved_mutator_output = sum(item.mutator_output_tokens or 0 for item in outstanding)
        if (
            self.usage.model_calls + reserved_model + planned.model_calls
            > self.limits.model_calls
            or self.usage.tool_calls + reserved_tool + planned.tool_calls
            > self.limits.tool_calls
            or self.usage.wall_clock_seconds + reserved_wall + planned.wall_clock_seconds
            > self.limits.wall_clock_seconds
            or (
                planned.input_tokens is not None
                and self.usage.input_tokens is not None
                and self.usage.input_tokens + reserved_input + planned.input_tokens
                > self.limits.input_tokens
            )
            or (
                planned.output_tokens is not None
                and self.usage.output_tokens is not None
                and self.usage.output_tokens + reserved_output + planned.output_tokens
                > self.limits.output_tokens
            )
            or (
                planned.expense_units is not None
                and self.usage.expense_units is not None
                and self.usage.expense_units + reserved_expense + planned.expense_units
                > self.limits.expense_units
            )
            or (
                self.usage.mutator_calls + reserved_mutator_calls + planned.mutator_calls
                > self.limits.mutator_calls
            )
            or (
                planned.mutator_input_tokens is not None
                and self.usage.mutator_input_tokens is not None
                and self.usage.mutator_input_tokens
                + reserved_mutator_input
                + planned.mutator_input_tokens
                > self.limits.mutator_input_tokens
            )
            or (
                planned.mutator_output_tokens is not None
                and self.usage.mutator_output_tokens is not None
                and self.usage.mutator_output_tokens
                + reserved_mutator_output
                + planned.mutator_output_tokens
                > self.limits.mutator_output_tokens
            )
        ):
            raise RuntimeError("campaign budget does not cover the reservation")
        if receipt_id in self.reservations:
            return self
        reservations = dict(self.reservations)
        reservations[receipt_id] = Reservation(
            receipt_id=receipt_id,
            opportunity=self.usage.opportunities,
            planned=planned,
        )
        return self.model_copy(update={"reservations": reservations})

    def mark_submitted(self, receipt_id: str) -> CampaignCheckpoint:
        reservation = self.reservations[receipt_id]
        reservations = dict(self.reservations)
        reservations[receipt_id] = reservation.model_copy(update={"submitted": True})
        return self.model_copy(update={"reservations": reservations})

    def settle(self, receipt_id: str, actual: CampaignUsage) -> CampaignCheckpoint:
        reservation = self.reservations[receipt_id]
        if reservation.settled:
            return self
        values = {}
        for field in (
            "input_tokens",
            "output_tokens",
            "expense_units",
            "mutator_input_tokens",
            "mutator_output_tokens",
        ):
            old, new = getattr(self.usage, field), getattr(actual, field)
            values[field] = None if old is None or new is None else old + new
        usage = CampaignUsage(
            opportunities=self.usage.opportunities + 1,
            model_calls=self.usage.model_calls + actual.model_calls,
            tool_calls=self.usage.tool_calls + actual.tool_calls,
            wall_clock_seconds=self.usage.wall_clock_seconds + actual.wall_clock_seconds,
            mutator_calls=self.usage.mutator_calls + actual.mutator_calls,
            **values,
        )
        reservations = dict(self.reservations)
        reservations[receipt_id] = reservation.model_copy(update={"settled": True})
        exceeded = (
            usage.model_calls > self.limits.model_calls
            or usage.tool_calls > self.limits.tool_calls
            or usage.wall_clock_seconds > self.limits.wall_clock_seconds
            or (usage.input_tokens is not None and usage.input_tokens > self.limits.input_tokens)
            or (usage.output_tokens is not None and usage.output_tokens > self.limits.output_tokens)
            or (usage.expense_units is not None and usage.expense_units > self.limits.expense_units)
            or usage.mutator_calls > self.limits.mutator_calls
            or (
                usage.mutator_input_tokens is not None
                and usage.mutator_input_tokens > self.limits.mutator_input_tokens
            )
            or (
                usage.mutator_output_tokens is not None
                and usage.mutator_output_tokens > self.limits.mutator_output_tokens
            )
        )
        return self.model_copy(
            update={
                "usage": usage,
                "reservations": reservations,
                "stopped": exceeded,
                "stop_reason": "budget-exceeded" if exceeded else self.stop_reason,
            }
        )

    def record_receipt(
        self, receipt_id: str, actual: CampaignUsage
    ) -> CampaignCheckpoint:
        reservation = self.reservations[receipt_id]
        reservations = dict(self.reservations)
        reservations[receipt_id] = reservation.model_copy(update={"actual": actual})
        return self.model_copy(update={"reservations": reservations})

    def recover(self, *, closure_proven: bool) -> CampaignCheckpoint:
        recovered = self
        had_unsettled_receipt = any(
            item.actual is not None and not item.settled
            for item in self.reservations.values()
        )
        for receipt_id, reservation in self.reservations.items():
            if reservation.actual is not None and not reservation.settled:
                recovered = recovered.settle(receipt_id, reservation.actual)
        if recovered is not self:
            recovered = recovered.recover(closure_proven=closure_proven)
            if had_unsettled_receipt:
                return recovered.model_copy(update={
                    "stopped": True,
                    "isolated": True,
                    "stop_reason": "settlement-unfinalized",
                })
            return recovered
        unknown = any(
            item.submitted and not item.settled
            for item in recovered.reservations.values()
        )
        if unknown or not closure_proven:
            reason = "unknown-effect" if unknown else "closure-unproven"
            return self.model_copy(
                update={"stopped": True, "isolated": True, "stop_reason": reason}
            )
        return self


def mutator_usage(plan: MutationPlan) -> CampaignUsage:
    """The Mutator side of one opportunity's cost, kept separate from the agent's (`SOC-FBK-15`).

    Usage the provider never reported stays ``None``: an unreported call must not be recorded as
    a free one, and the settlement then reports the cost as incomplete.
    """

    reported = plan.usage_complete
    return CampaignUsage(
        mutator_calls=plan.requests,
        mutator_input_tokens=(plan.input_tokens if reported else None),
        mutator_output_tokens=(plan.output_tokens if reported else None),
    )


def run_opportunity(
    path: Path,
    checkpoint: CampaignCheckpoint,
    search: TwoArmSearch,
    *,
    arm: ArmKind,
    manifest: StructuredFixtureManifest,
    execute: Callable[[StructuredCase], EpisodeBundle],
    planned: CampaignUsage,
    artifacts: Callable[[EpisodeBundle], EpisodeArtifacts] | None = None,
    execution_config: Callable[[EpisodeBundle], str] | None = None,
    parent_bundle_directory: Path | None = None,
    fault_after: str | None = None,
) -> tuple[CampaignCheckpoint, EpisodeBundle | None]:
    """Persist each crash boundary around one formal Episode execution.

    A generation that never produced a candidate ends the opportunity without an Episode: its
    requests are still billed (`SS-011`), the plan is kept, and the caller gets ``None`` instead
    of a bundle it would have to pretend came from a run.
    """

    search.ensure_protocol_identity()
    checkpoint = checkpoint.bind_protocol(search.protocol_identity)
    phased = manifest.session_protocol is not None
    receipt_id = f"opportunity-{checkpoint.usage.opportunities}"
    pending = checkpoint.pending_selection
    can_resume_pre_submission = (
        pending is not None
        and receipt_id in checkpoint.reservations
        and not checkpoint.reservations[receipt_id].submitted
        and not checkpoint.reservations[receipt_id].settled
        and checkpoint.reservations[receipt_id].actual is None
    )
    if phased and checkpoint.reservations:
        restore_phase_search(checkpoint, search, allow_pending=can_resume_pre_submission)

    def snapshot(current: CampaignCheckpoint) -> CampaignCheckpoint:
        current = current.with_search(search.state)
        if phased:
            current = current.model_copy(update={
                "search_candidates": dict(search.parents), "search_seed": search.seed,
            })
        return current

    state = checkpoint.reserve(receipt_id, planned)
    if phased:
        state = snapshot(state)
    save_checkpoint(path, state)
    if fault_after == "reservation":
        raise RuntimeError("fault after reservation")
    def settle_missing_parent(error: ParentFeedbackUnavailable) -> tuple[CampaignCheckpoint, None]:
        nonlocal state
        search.consume_failed_parent(error.receipt)
        failure = {
            "opportunity": error.receipt.opportunity,
            "parent_id": error.receipt.parent_id,
            "reason": error.reason,
        }
        state = state.record_receipt(receipt_id, CampaignUsage()).settle(
            receipt_id, CampaignUsage()
        ).model_copy(update={
            "failed_parent_evidence": (*state.failed_parent_evidence, failure),
        })
        state = snapshot(state.clear_pending())
        save_checkpoint(path, state)
        return state, None

    def stop_invalid_parent(error: ParentEvidenceError) -> None:
        nonlocal state
        state = snapshot(state).model_copy(update={
            "stopped": True,
            "isolated": True,
            "stop_reason": "parent-evidence-identity-invalid",
        })
        save_checkpoint(path, state)
        raise error

    if (
        pending is not None and state.pending_case is None
        and arm is ArmKind.COVERAGE_GUIDED and not pending.root_restart
        and parent_bundle_directory is not None
    ):
        try:
            evidence = search.load_parent_feedback(
                pending.parent_id, str(parent_bundle_directory)
            )
        except (ParentBundleNotFound, ParentBundleIncomplete) as error:
            return settle_missing_parent(
                ParentFeedbackUnavailable(pending, type(error).__name__)
            )
        except ParentEvidenceError as error:
            stop_invalid_parent(error)
        if evidence.source.final_bundle_digest != pending.feedback_source_digest:
            raise ValueError("pending parent feedback digest mismatch")
    try:
        selection = pending if pending is not None else search.select(
            arm,
            parent_bundle_directory=(
                str(parent_bundle_directory)
                if arm is ArmKind.COVERAGE_GUIDED and parent_bundle_directory is not None
                else None
            ),
        )
    except ParentFeedbackUnavailable as error:
        return settle_missing_parent(error)
    except ParentEvidenceError as error:
        stop_invalid_parent(error)
    if pending is None:
        state = snapshot(state.record_pending_selection(selection))
        save_checkpoint(path, state)
        if fault_after == "selection":
            raise RuntimeError("fault after selection")
    elif selection.opportunity != state.usage.opportunities:
        raise RuntimeError("pending selection opportunity does not match reservation")
    try:
        if state.pending_case is not None:
            case = state.pending_case
            if state.pending_generation is None:
                raise RuntimeError("pending candidate has no generation plan")
            search.last_plan = state.pending_generation
        else:
            case = search.generate(selection)
    except (TextGenerationFailed, CandidateRefused) as failed:
        # Both are billed, opportunity-consuming failures that must not fabricate an Episode: a
        # provider that could not return usable wording, and a drawn position whose child the
        # shared admission gate refused (`SS-011`, `SS-014`).  The plan is kept either way, and a
        # refusal without one still settles its opportunity so the books stay complete.
        plan = failed.plan
        if plan is not None and not any(
            existing.canonical_digest() == plan.canonical_digest()
            for existing in state.generations
        ):
            state = state.record_generation(plan)
        state = state.settle(
            receipt_id, mutator_usage(plan) if plan is not None else CampaignUsage()
        )
        state = snapshot(state.clear_pending())
        save_checkpoint(path, state)
        return state, None
    if search.last_plan is not None and not any(
        existing.canonical_digest() == search.last_plan.canonical_digest()
        for existing in state.generations
    ):
        state = state.record_generation(search.last_plan)
    if state.pending_case is None:
        state = state.record_pending_generation(case, search.last_plan)
        state = snapshot(state)
        save_checkpoint(path, state)
        if fault_after == "generation":
            raise RuntimeError("fault after generation")
    state = state.mark_submitted(receipt_id)
    if phased:
        state = snapshot(state).model_copy(update={
            "pending_search_opportunity": selection.opportunity,
        })
    save_checkpoint(path, state)
    if fault_after == "submission":
        raise RuntimeError("fault after submission")
    bundle = execute(case)
    mutator = mutator_usage(search.last_plan) if search.last_plan is not None else CampaignUsage()
    usage = CampaignUsage(
        model_calls=bundle.usage.model_calls,
        tool_calls=bundle.usage.tool_calls,
        input_tokens=bundle.usage.input_tokens if bundle.usage.usage_complete else None,
        output_tokens=bundle.usage.output_tokens if bundle.usage.usage_complete else None,
        expense_units=bundle.usage.expense_units if bundle.usage.usage_complete else None,
        wall_clock_seconds=bundle.usage.wall_clock_seconds,
        mutator_calls=mutator.mutator_calls,
        mutator_input_tokens=mutator.mutator_input_tokens,
        mutator_output_tokens=mutator.mutator_output_tokens,
    )
    state = state.record_receipt(receipt_id, usage)
    save_checkpoint(path, state)
    if fault_after == "receipt":
        raise RuntimeError("fault after receipt")
    finalized_artifacts = None if artifacts is None else artifacts(bundle)
    coverage = extract_coverage(
        bundle,
        manifest=manifest,
        artifacts=finalized_artifacts,
    )
    # ``EpisodeBundle.complete`` may be false while a verified host finalization closes the
    # remaining observation channel.  The callback is therefore the authoritative finalized
    # completeness signal when supplied; without it, an incomplete container cannot earn a
    # no-new count.
    evidence_complete = (
        finalized_artifacts.complete
        if finalized_artifacts is not None
        else bundle.complete and bundle.artifacts().complete
    )
    try:
        if execution_config is not None:
            coverage = bind_coverage_execution(
                coverage, bundle=bundle, case=case, manifest=manifest,
                execution_config_digest=execution_config(bundle),
            )
        search.record(
            coverage,
            parent_id=case.mutation_lineage.generation_identity,
            parent_baseline=selection.parent_baseline,
            source_parent_id=selection.source_parent_id,
            selected_unit=selection.selected_unit,
            evidence_complete=evidence_complete,
            selection_cooldown=selection.cooldown,
        )
    except ValueError:
        # A mismatch cannot award coverage; it still costs the real execution already made.
        state = state.settle(receipt_id, usage).model_copy(update={
            "stopped": True, "stop_reason": "execution-identity-mismatch",
        })
        save_checkpoint(path, state)
        raise
    state = state.settle(receipt_id, usage)
    state = snapshot(state.record_selection(
        selection, child_id=case.mutation_lineage.generation_identity
    ).clear_pending())
    save_checkpoint(path, state)
    if fault_after in {"transaction", "settlement"}:
        raise RuntimeError(f"fault after {fault_after}")
    return state, bundle


def restore_phase_search(
    checkpoint: CampaignCheckpoint,
    search: TwoArmSearch,
    *,
    allow_pending: bool = False,
) -> None:
    """Restore persisted search state, allowing only a pre-submission pending draw to resume."""
    from sandbox.structured_v1.validation import admit_case

    if checkpoint.protocol_identity != search.protocol_identity:
        raise ValueError("campaign protocol identity mismatch")
    pending_receipt = checkpoint.pending_selection
    pending_id = None if pending_receipt is None else f"opportunity-{pending_receipt.opportunity}"
    pending_reservation = (
        checkpoint.reservations.get(pending_id) if pending_id is not None else None
    )
    expected_pending_opportunity = checkpoint.usage.opportunities + int(
        checkpoint.pending_case is not None
    )
    resumable_pending = bool(
        allow_pending
        and pending_receipt is not None
        and pending_reservation is not None
        and not pending_reservation.submitted
        and not pending_reservation.settled
        and pending_reservation.actual is None
        and checkpoint.pending_search_opportunity is None
        and checkpoint.search.opportunity == expected_pending_opportunity
    )
    if (
        checkpoint.stopped
        or checkpoint.isolated
        or checkpoint.pending_search_opportunity is not None
        or (
            any(not item.settled for item in checkpoint.reservations.values())
            and not resumable_pending
        )
        or (
            checkpoint.usage.opportunities != checkpoint.search.opportunity
            and not resumable_pending
        )
    ):
        raise RuntimeError("cannot resume an incomplete or isolated phase campaign")
    if checkpoint.search.protocol_identity != search.protocol_identity:
        raise ValueError("search protocol identity mismatch")
    if checkpoint.search_seed != search.seed or not checkpoint.search_candidates:
        raise ValueError("phase checkpoint lacks matching seed and candidate material")
    for candidate in checkpoint.search_candidates.values():
        if not admit_case(candidate, manifest=search.manifest).accepted:
            raise ValueError("phase checkpoint candidate identity or material is invalid")
    needed = set(checkpoint.search.evolution_pool) | set(checkpoint.search.parent_coverage)
    if checkpoint.pending_case is not None:
        needed.add(checkpoint.pending_case.mutation_lineage.generation_identity)
    if not needed.issubset(checkpoint.search_candidates):
        raise ValueError("phase checkpoint is missing a referenced parent")
    search.parents = dict(checkpoint.search_candidates)
    search.state = checkpoint.search
    if checkpoint.pending_case is not None:
        search.parents[checkpoint.pending_case.mutation_lineage.generation_identity] = (
            checkpoint.pending_case
        )


def save_checkpoint(path: Path, checkpoint: CampaignCheckpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError as error:
            # Windows may temporarily deny replacing a recently closed/read file. Retry
            # the SAME atomic write only; persistent denial preserves both files and fails.
            if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 4:
                raise
            time.sleep(0.05 * (2 ** attempt))


def load_checkpoint(path: Path) -> CampaignCheckpoint:
    return CampaignCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = [
    "CampaignProtocolIdentity",
    "CampaignCheckpoint",
    "CampaignLimits",
    "CampaignUsage",
    "CURRENT_CAMPAIGN_PROTOCOL_IDENTITY",
    "load_checkpoint",
    "mutator_usage",
    "run_opportunity",
    "save_checkpoint",
]
