"""Rebuild the exact Recorder input of a recorded model decision.

Every model decision is tied to the ``before_model`` checkpoint that was taken
immediately before it, and that checkpoint's state artifact contains the full
``agent_state.messages`` list -- the same list the Recorder hashed into
``input_digest``. The input a decision actually saw can therefore be
reconstructed word for word and checked against the recorded digest.

This module is the read side of that contract: callers point at a decision and
receive either the exact messages or an explicit statement of what is missing
(no checkpoint, no artifact, an unreadable artifact, or a digest mismatch).
Nothing here writes recordings, invents text, or relaxes a check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from sandbox.replay.artifact_store import ArtifactStore
from sandbox.replay.digests import sha256_digest
from sandbox.replay.exceptions import ArtifactIntegrityError
from sandbox.replay.models import (
    RecordedModelDecision,
    ReplayManifest,
    StateCheckpoint,
)

DecisionInputStatus = Literal[
    "exact",
    "digest_mismatch",
    "checkpoint_missing",
    "artifact_missing",
    "artifact_invalid",
    "messages_unavailable",
]


@dataclass(frozen=True, slots=True)
class DecisionInput:
    """One decision's reconstructed input, or the reason it cannot be rebuilt."""

    decision_id: str
    decision_index: int
    before_checkpoint_id: str
    status: DecisionInputStatus
    recorded_digest: str
    messages: tuple[dict[str, Any], ...] = ()
    recomputed_digest: str | None = None
    reason: str | None = None

    @property
    def exact(self) -> bool:
        return self.status == "exact"

    def digest_payload(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "decision_index": self.decision_index,
            "before_checkpoint_id": self.before_checkpoint_id,
            "status": self.status,
            "recorded_digest": self.recorded_digest,
            "recomputed_digest": self.recomputed_digest,
            "reason": self.reason,
        }


def resource_mentions(
    messages: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    resource_id: str,
    version_id: str | None = None,
) -> tuple[int, ...]:
    """Message indices whose content cites a resource, checked semantically.

    Structured content is searched by resource id (and version when one is
    given); plain text is searched as a fallback. This is the input-side half of
    an audit: "the model input showed this resource" and "the argument source
    chain covers it" are separate questions, and a citation that does not match
    the source chain must not be reported as "never shown".
    """

    return tuple(
        index
        for index, message in enumerate(messages)
        if _contains_resource(message.get("content"), resource_id, version_id)
    )


def _contains_resource(value: Any, resource_id: str, version_id: str | None) -> bool:
    if isinstance(value, dict):
        if value.get("resource_id") == resource_id and (
            version_id is None or value.get("version_id") == version_id
        ):
            return True
        return any(
            _contains_resource(item, resource_id, version_id) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_resource(item, resource_id, version_id) for item in value)
    if isinstance(value, str):
        return resource_id in value
    return False


def load_recorded_decisions(
    store: ArtifactStore,
    manifest: ReplayManifest,
) -> tuple[RecordedModelDecision, ...]:
    return _read_ndjson(store, manifest.model_decisions, RecordedModelDecision)


def load_checkpoints(
    store: ArtifactStore,
    manifest: ReplayManifest,
) -> tuple[StateCheckpoint, ...]:
    return _read_ndjson(store, manifest.checkpoints, StateCheckpoint)


def load_decision_inputs(
    store: ArtifactStore,
    manifest: ReplayManifest,
    *,
    decisions: tuple[RecordedModelDecision, ...] | None = None,
    checkpoints: tuple[StateCheckpoint, ...] | None = None,
) -> tuple[DecisionInput, ...]:
    """Rebuild every decision input in recording order."""

    if checkpoints is None:
        checkpoints = load_checkpoints(store, manifest)
    if decisions is None:
        decisions = load_recorded_decisions(store, manifest)
    by_id = {checkpoint.checkpoint_id: checkpoint for checkpoint in checkpoints}
    return tuple(
        _rebuild(store, decision, by_id) for decision in decisions
    )


def load_decision_input(
    store: ArtifactStore,
    manifest: ReplayManifest,
    decision: RecordedModelDecision,
    *,
    checkpoints: tuple[StateCheckpoint, ...] | None = None,
) -> DecisionInput:
    if checkpoints is None:
        checkpoints = load_checkpoints(store, manifest)
    by_id = {checkpoint.checkpoint_id: checkpoint for checkpoint in checkpoints}
    return _rebuild(store, decision, by_id)


def _rebuild(
    store: ArtifactStore,
    decision: RecordedModelDecision,
    checkpoints: dict[str, StateCheckpoint],
) -> DecisionInput:
    def failed(
        status: DecisionInputStatus, reason: str
    ) -> DecisionInput:
        return DecisionInput(
            decision_id=decision.decision_id,
            decision_index=decision.decision_index,
            before_checkpoint_id=decision.before_checkpoint_id,
            status=status,
            recorded_digest=decision.input_digest,
            reason=reason,
        )

    checkpoint = checkpoints.get(decision.before_checkpoint_id)
    if checkpoint is None:
        return failed(
            "checkpoint_missing",
            f"before checkpoint {decision.before_checkpoint_id} is absent",
        )
    if checkpoint.state_artifact is None:
        reasons = "; ".join(checkpoint.non_recoverable_reasons) or "not recoverable"
        return failed("artifact_missing", f"checkpoint carries no state artifact: {reasons}")
    try:
        payload = store.read_bytes(checkpoint.state_artifact)
    except ArtifactIntegrityError as exc:
        return failed("artifact_missing", str(exc))
    try:
        envelope = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        return failed("artifact_invalid", f"state artifact is not readable JSON: {exc}")
    agent_state = envelope.get("agent_state") if isinstance(envelope, dict) else None
    messages = agent_state.get("messages") if isinstance(agent_state, dict) else None
    if not isinstance(messages, list) or not all(
        isinstance(message, dict) for message in messages
    ):
        return failed(
            "messages_unavailable",
            "state artifact has no usable agent_state.messages list",
        )
    recomputed = sha256_digest(messages)
    status: DecisionInputStatus = (
        "exact" if recomputed == decision.input_digest else "digest_mismatch"
    )
    return DecisionInput(
        decision_id=decision.decision_id,
        decision_index=decision.decision_index,
        before_checkpoint_id=decision.before_checkpoint_id,
        status=status,
        recorded_digest=decision.input_digest,
        messages=tuple(messages),
        recomputed_digest=recomputed,
        reason=None if status == "exact" else "recomputed digest differs from the recording",
    )


def _read_ndjson(store: ArtifactStore, reference: Any, model: Any) -> tuple[Any, ...]:
    payload = store.read_bytes(reference)
    rows = []
    for line in payload.splitlines():
        if not line.strip():
            continue
        rows.append(model.model_validate_json(line))
    return tuple(rows)


__all__ = [
    "DecisionInput",
    "DecisionInputStatus",
    "load_checkpoints",
    "load_decision_input",
    "load_decision_inputs",
    "load_recorded_decisions",
    "resource_mentions",
]
