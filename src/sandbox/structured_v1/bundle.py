"""The episode bundle and its offline re-verification (R12; P6).

A bundle has to be judgeable without the container, the model or the world it ran in: that
is what makes a run reviewable after the fact. So it carries the **objects**, not only the
digests - the material it ran on, the initialization transition, the committed records,
the tool returns the exposures are bound to, the closure and the two tooling identities -
and it carries its own digest, recomputed on verification.

Two structural rules are checked here rather than trusted:

* the initialization transition is present but carries **no** effects (`SOC-ENV-76`), so
  materialisation can never be counted as a delivery;
* ``complete`` agrees with the closure: a bundle cannot claim full observation while a
  channel is still unresolved.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import SerializerFunctionWrapHandler, model_serializer, model_validator

from sandbox.replay.digests import sha256_digest
from sandbox.structured_v1.budget import RunUsage
from sandbox.structured_v1.closure import REASON_HOST_OBSERVATION_UNAVAILABLE, ClosureRecord
from sandbox.structured_v1.effects import expected_effects
from sandbox.structured_v1.envelope_codes import EnvelopeRefusal, FailureCode
from sandbox.structured_v1.evidence import ChannelClosure, RequestState
from sandbox.structured_v1.exposure import ExposureFact, ToolReturn, verify_exposures
from sandbox.structured_v1.host import HostClosureReceipt, RuntimeReceipt
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.models import Identifier, Sha256Digest, StructuredContract
from sandbox.structured_v1.oracle_io import EpisodeArtifacts
from sandbox.structured_v1.phases import PhaseReceipt
from sandbox.structured_v1.rendering import RenderedMaterial
from sandbox.structured_v1.tool_catalogue import ToolingVerification
from sandbox.structured_v1.transitions import (
    StructuredTransitionRecord,
    verify_chain,
)


class StopReason(StrEnum):
    """Why an episode's loop ended; both are ordinary ends, neither is a judgement."""

    MODEL_STOPPED = "model-stopped"
    BUDGET_EXCEEDED = "budget-exceeded"


class EpisodeBundle(StructuredContract):
    """Everything one episode produced, in the form it is judged and reviewed from."""

    episode_id: Identifier
    fixture_id: Identifier
    envelope_digest: Sha256Digest
    base_world_digest: Sha256Digest
    overlay_digest: Sha256Digest
    material: RenderedMaterial
    model_decisions: tuple[ModelDecision, ...] = ()
    phases: tuple[PhaseReceipt, ...] = ()
    initial_state_digest: Sha256Digest
    initialization_transition_digest: Sha256Digest
    final_state_digest: Sha256Digest
    initialization_transition: StructuredTransitionRecord
    records: tuple[StructuredTransitionRecord, ...] = ()
    exposures: tuple[ExposureFact, ...] = ()
    frozen_resource_digests: tuple[
        tuple[str, Sha256Digest, Sha256Digest, Sha256Digest], ...
    ] = ()
    tool_returns: tuple[ToolReturn, ...] = ()
    closure_record: ClosureRecord
    closure: tuple[ChannelClosure, ...] = ()
    complete: bool = False
    missing: tuple[str, ...] = ()
    tooling: ToolingVerification
    usage: RunUsage
    stop_reason: StopReason = StopReason.MODEL_STOPPED
    rejected_calls: tuple[Identifier, ...] = ()
    outcome_codes: tuple[FailureCode, ...] = ()
    bundle_digest: Sha256Digest

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler: SerializerFunctionWrapHandler) -> dict:
        payload = handler(self)
        if not self.frozen_resource_digests:
            payload.pop("frozen_resource_digests", None)
        if not self.phases:
            payload.pop("phases", None)
        return payload

    @model_validator(mode="after")
    def initialization_is_not_a_delivery(self) -> EpisodeBundle:
        if self.initialization_transition.effects:
            raise ValueError("the initialization transition cannot carry effects")
        if self.initialization_transition.transaction_id in {
            record.transaction_id for record in self.records
        }:
            raise ValueError("the initialization transaction cannot be an episode action")
        return self

    def effects(self) -> tuple[object, ...]:
        """The ledger: the projection of the committed records (`SOC-ENV-51`)."""

        return expected_effects(self.records)

    def artifacts(self) -> EpisodeArtifacts:
        return EpisodeArtifacts(
            episode_id=self.episode_id,
            fixture_id=self.fixture_id,
            records=self.records,
            exposures=self.exposures,
            closure=self.closure,
            complete=self.complete,
            missing=self.missing,
        )

    def digest_payload(self) -> dict[str, object]:
        """The bundle without its own digest, so the digest can be recomputed."""

        return self.model_dump(mode="json", exclude={"bundle_digest"}, exclude_none=False)


#: Replaced by the bundle's own recomputable digest; never a valid digest.
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


def build_bundle(**fields: object) -> EpisodeBundle:
    """Build a bundle and stamp it with its own recomputable digest."""

    fields.setdefault("bundle_digest", PLACEHOLDER_DIGEST)
    bundle = EpisodeBundle(**fields)  # type: ignore[arg-type]
    return bundle.model_copy(
        update={"bundle_digest": sha256_digest(bundle.digest_payload())}
    )


def verify_bundle(
    bundle: EpisodeBundle,
    *,
    known_sources: frozenset[tuple[str, str]] | None = None,
) -> None:
    """Re-check a bundle from its own contents, with nothing else available."""

    if sha256_digest(bundle.digest_payload()) != bundle.bundle_digest:
        raise EnvelopeRefusal(
            FailureCode.PAYLOAD_MISSING, "the bundle digest does not match its content"
        )
    if sha256_digest(bundle.material.digest_payload()) != bundle.material.material_digest:
        raise EnvelopeRefusal(
            FailureCode.COVERAGE_MISMATCH, "the bundle's material is not the material it claims"
        )
    if len(bundle.model_decisions) != bundle.usage.model_calls:
        raise EnvelopeRefusal(
            FailureCode.MISSING,
            "the bundle's model-call usage does not match its recorded decisions",
        )
    _verify_phases(bundle)
    action_ids = tuple(
        decision.action_request_id
        for decision in bundle.model_decisions
        if decision.action_request_id is not None
    )
    if len(action_ids) != len(set(action_ids)):
        raise EnvelopeRefusal(
            FailureCode.CHAIN_BROKEN, "model action request ids are not unique"
        )
    recorded_action_ids = {record.action_request_id for record in bundle.records}
    if not recorded_action_ids.issubset(action_ids):
        raise EnvelopeRefusal(
            FailureCode.EVIDENCE_UNBOUND,
            "a recorded transition is not bound to a model decision",
        )
    verify_chain(
        bundle.records,
        expected_start_state_digest=bundle.initialization_transition.after_state_digest,
        expected_start_sequence=bundle.initialization_transition.sequence + 1,
    )
    ended_at = (
        max(bundle.records, key=lambda item: item.sequence).after_state_digest
        if bundle.records
        else bundle.initialization_transition.after_state_digest
    )
    if ended_at != bundle.final_state_digest:
        raise EnvelopeRefusal(
            FailureCode.CHAIN_BROKEN,
            "the recorded chain does not end where the bundle says the episode ended",
        )
    expected = bundle.initialization_transition.transaction_id
    if any(record.transaction_id == expected for record in bundle.records):
        raise EnvelopeRefusal(
            FailureCode.INITIALIZATION_CITED_AS_DELIVERY,
            "the initialization transaction appears among the episode's actions",
        )
    unresolved = [
        item.channel.value
        for item in bundle.closure
        if item.state is RequestState.UNRESOLVED
    ]
    if bundle.complete and unresolved:
        raise EnvelopeRefusal(
            FailureCode.MISSING,
            f"the bundle claims full observation with unresolved channels: {unresolved}",
        )
    if not bundle.complete and not bundle.missing:
        raise EnvelopeRefusal(
            FailureCode.MISSING, "an incomplete bundle must record what is missing"
        )
    verify_exposures(
        bundle.exposures,
        returns=bundle.tool_returns,
        material_digest=bundle.material.material_digest,
        slot_contents={
            slot.slot_id: "\n".join(slot.contents) for slot in bundle.material.slots
        },
        frozen_resource_digests={
            resource_id: carried for resource_id, carried, _value, _registered
            in bundle.frozen_resource_digests
        },
        frozen_value_digests={
            resource_id: value for resource_id, _carried, value, _registered
            in bundle.frozen_resource_digests
        },
        known_sources=known_sources,
    )


def _verify_phases(bundle: EpisodeBundle) -> None:
    def refuse(detail: str) -> None:
        raise EnvelopeRefusal(FailureCode.CHAIN_BROKEN, detail)

    if not bundle.phases:
        if any(item.phase_id is not None for item in bundle.model_decisions):
            refuse("phase-tagged decisions require phase receipts")
        return
    if tuple(item.phase_id for item in bundle.phases) not in (
        ("investigation",), ("investigation", "action"),
    ):
        refuse("phases must form one investigation and at most one action phase")
    previous = 0
    if len({d.call_id for d in bundle.model_decisions}) != len(bundle.model_decisions):
        refuse("phase model call ids are not unique")
    for phase in bundle.phases:
        if phase.start_decision != previous or phase.end_decision < previous:
            refuse("phase decision ranges must be contiguous and ordered")
        selected = bundle.model_decisions[phase.start_decision:phase.end_decision]
        if any(item.phase_id != phase.phase_id for item in selected):
            refuse("a decision is bound to the wrong phase")
        if len(selected) != phase.usage.model_calls or len(selected) > 8:
            refuse("phase model usage does not match its decisions or call limit")
        if phase.usage.tool_calls > 8:
            refuse("phase tool usage exceeds its call limit")
        if phase.budget.max_model_calls != 8 or phase.budget.max_tool_calls != 8:
            refuse("phase call limits must remain 8/8")
        action_ids = {d.action_request_id for d in selected if d.action_request_id is not None}
        transactions = {r.transaction_id for r in bundle.records
                        if r.action_request_id in action_ids}
        observed = sum(r.tool_call_id in transactions for r in bundle.tool_returns)
        if observed != phase.usage.tool_calls:
            refuse("phase tool usage does not match its bound tool returns")
        for field in ("input_tokens", "output_tokens", "expense_units",
                      "wall_clock_seconds"):
            if sum(getattr(item, field) for item in selected) != getattr(phase.usage, field):
                refuse(f"phase {field} does not match its decisions")
        if phase.usage.usage_complete != all(item.usage_reported for item in selected):
            refuse("phase usage completeness does not match its decisions")
        previous = phase.end_decision
    if previous != len(bundle.model_decisions):
        refuse("phase ranges do not cover all decisions")
    for field in ("model_calls", "tool_calls", "input_tokens", "output_tokens",
                  "expense_units", "wall_clock_seconds"):
        if sum(getattr(phase.usage, field) for phase in bundle.phases) != getattr(
            bundle.usage, field,
        ):
            refuse(f"phase {field} does not reconcile with episode usage")
    if len(bundle.phases) == 2 and bundle.phases[0].end_reason == "budget-exceeded":
        refuse("a hard budget stop cannot continue into the action phase")


def write_bundle(bundle: EpisodeBundle, path: Path) -> Path:
    """Export a bundle to disk; nothing is dropped, so it can be re-verified as-is."""

    path.write_text(
        json.dumps(bundle.model_dump(mode="json", exclude_none=False), indent=2),
        encoding="utf-8",
    )
    return path


def load_bundle(path: Path) -> EpisodeBundle:
    """Read a bundle back and re-verify it from its own contents."""

    bundle = EpisodeBundle.model_validate(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )
    verify_bundle(bundle)
    return bundle


class FinalizedEpisodeBundle(StructuredContract):
    """Container evidence plus the two host-owned receipts that finalize it."""

    final_version: str = "structured-final-bundle-v1"
    container_bundle: EpisodeBundle
    runtime_receipt: RuntimeReceipt
    host_closure_receipt: HostClosureReceipt
    complete: bool
    missing: tuple[str, ...] = ()
    final_bundle_digest: Sha256Digest

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"final_bundle_digest"}, exclude_none=False
        )

    def artifacts(self) -> EpisodeArtifacts:
        closure = self.container_bundle.closure
        if self.host_closure_receipt.proven:
            closure = tuple(
                item.model_copy(
                    update={
                        "state": RequestState.COMPLETED,
                        "proof_digest": self.host_closure_receipt.receipt_digest,
                        "note": None,
                    }
                )
                for item in closure
            )
        return EpisodeArtifacts(
            episode_id=self.container_bundle.episode_id,
            fixture_id=self.container_bundle.fixture_id,
            records=self.container_bundle.records,
            exposures=self.container_bundle.exposures,
            closure=closure,
            complete=self.complete,
            missing=self.missing,
        )


def finalize_bundle(
    container_bundle: EpisodeBundle,
    *,
    runtime_receipt: RuntimeReceipt,
    host_closure_receipt: HostClosureReceipt,
) -> FinalizedEpisodeBundle:
    """Bind unchanged container evidence to host identity and cleanup observations."""

    verify_bundle(container_bundle)
    if runtime_receipt.episode_id != container_bundle.episode_id:
        raise EnvelopeRefusal(
            FailureCode.IDENTITY_MISMATCH,
            "the runtime receipt names a different episode",
        )
    if runtime_receipt.container_bundle_digest != container_bundle.bundle_digest:
        raise EnvelopeRefusal(
            FailureCode.UNBOUND,
            "the runtime receipt is not bound to this container bundle",
        )
    if host_closure_receipt.episode_id != container_bundle.episode_id:
        raise EnvelopeRefusal(
            FailureCode.IDENTITY_MISMATCH,
            "the host closure receipt names a different episode",
        )
    if host_closure_receipt.container_id != runtime_receipt.container_id:
        raise EnvelopeRefusal(
            FailureCode.IDENTITY_MISMATCH,
            "the host receipts name different container instances",
        )
    if host_closure_receipt.runtime_receipt_digest != runtime_receipt.receipt_digest:
        raise EnvelopeRefusal(
            FailureCode.UNBOUND,
            "the closure receipt is not bound to the runtime receipt",
        )

    residual = tuple(
        item
        for item in container_bundle.missing
        if item != REASON_HOST_OBSERVATION_UNAVAILABLE
    )
    missing = list(residual)
    if not host_closure_receipt.proven:
        missing.append("host-closure-unproven")
    complete = host_closure_receipt.proven and not residual
    fields = {
        "container_bundle": container_bundle,
        "runtime_receipt": runtime_receipt,
        "host_closure_receipt": host_closure_receipt,
        "complete": complete,
        "missing": tuple(missing),
        "final_bundle_digest": PLACEHOLDER_DIGEST,
    }
    draft = FinalizedEpisodeBundle(**fields)
    return FinalizedEpisodeBundle(
        **{
            **fields,
            "final_bundle_digest": sha256_digest(draft.digest_payload()),
        }
    )


def verify_finalized_bundle(bundle: FinalizedEpisodeBundle) -> None:
    """Recompute the final wrapper and both receipt bindings offline."""

    rebuilt = finalize_bundle(
        bundle.container_bundle,
        runtime_receipt=bundle.runtime_receipt,
        host_closure_receipt=bundle.host_closure_receipt,
    )
    if rebuilt != bundle:
        raise EnvelopeRefusal(
            FailureCode.PAYLOAD_MISSING,
            "the finalized bundle does not match its recomputed form",
        )


def write_finalized_bundle(bundle: FinalizedEpisodeBundle, path: Path) -> Path:
    verify_finalized_bundle(bundle)
    path.write_text(
        json.dumps(bundle.model_dump(mode="json", exclude_none=False), indent=2),
        encoding="utf-8",
    )
    return path


def load_finalized_bundle(path: Path) -> FinalizedEpisodeBundle:
    bundle = FinalizedEpisodeBundle.model_validate(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )
    verify_finalized_bundle(bundle)
    return bundle
