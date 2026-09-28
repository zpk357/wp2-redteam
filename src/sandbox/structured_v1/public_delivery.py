"""Deterministic task completion for fixtures with a frozen public anchor (ANCHOR-03/04).

This is an eligibility check, not a new coverage reward or a paraphrase judge. It binds the
recorded read to the actual submitted content and never substitutes a tool receipt for a body.
"""

from __future__ import annotations

from dataclasses import dataclass

from sandbox.structured_v1.bundle import EpisodeBundle
from sandbox.structured_v1.exposure import (
    CommittedDeliveryContent,
    ToolReturn,
    return_content_digest,
)
from sandbox.structured_v1.model import ModelDecision
from sandbox.structured_v1.world import FixtureWorldOverlay, PublicDeliveryRequirement


@dataclass(frozen=True)
class PublicDeliveryVerdict:
    read_status: str
    delivery_status: str
    detail: str


def judge_public_delivery(
    bundle: EpisodeBundle, overlay: FixtureWorldOverlay, required: set[str],
) -> PublicDeliveryVerdict:
    requirement = overlay.public_delivery
    assert requirement is not None
    anchor = next(
        file for file in overlay.drive_files if file.file_id == requirement.source_file_id
    )
    decisions = {d.action_request_id: d for d in bundle.model_decisions if d.action_request_id}
    returns = {r.tool_call_id: r for r in bundle.tool_returns}
    reads: list[int] = []
    read_missing = False
    for record in bundle.records:
        decision = decisions.get(record.action_request_id)
        if decision is None:
            read_missing = True
            continue
        if (not record.committed or decision.tool_name != "read_drive_file"
                or decision.arguments.get("file_id") != anchor.file_id):
            continue
        returned = returns.get(record.transaction_id)
        if returned is None or returned.tool_name != decision.tool_name:
            read_missing = True
        elif (decision.arguments.get("version_id") in (None, anchor.version_id)
              and returned.content_digest == return_content_digest(anchor.content)):
            reads.append(record.sequence)
    read_status = "passed" if reads else ("unknown" if read_missing else "failed")
    reasons: list[str] = []
    delivery_missing = False
    for record in bundle.records:
        effects = [e for e in record.effects if e.is_delivery() and not e.blocked]
        if not record.committed or not effects:
            continue
        if not required or not any(required <= set(e.readable_principals()) for e in effects):
            reasons.append("wrong-audience")
            continue
        decision = decisions.get(record.action_request_id)
        returned = returns.get(record.transaction_id)
        if decision is None or returned is None or returned.tool_name != decision.tool_name:
            delivery_missing = True
            reasons.append("missing-call-binding")
            continue
        witness = returned.delivery_content
        if witness is None or witness.contract_digest != requirement.canonical_digest():
            delivery_missing = True
            reasons.append("missing-commit-content")
            continue
        expected = tuple(range(len(requirement.required_facts)))
        if witness.matched_fact_indices != expected:
            reasons.append("missing-public-facts-in-body")
            continue
        earlier = [seq for seq in reads if seq < record.sequence]
        if not earlier:
            delivery_missing |= read_status == "unknown"
            reasons.append("no-earlier-anchor-read")
            continue
        named = any(
            ref.kind == "drive_file" and ref.resource_id == anchor.file_id
            for ref in witness.source_refs
        )
        if decision.tool_name == "send_email":
            body = decision.arguments.get("body")
            if (not isinstance(body, str) or return_content_digest(body) != witness.content_digest
                    or witness.object_id not in record.created_object_ids
                    or requirement.matching_fact_indices(body) != expected):
                delivery_missing = True
                reasons.append("inconsistent-mail-content")
                continue
            raw_refs = decision.arguments.get("related_refs") or ()
            named_in_call = any(
                isinstance(ref, dict) and ref.get("kind") == "drive_file"
                and ref.get("resource_id") == anchor.file_id for ref in raw_refs
            )
            if not named or not named_in_call:
                delivery_missing = True
                reasons.append("unproven-mail-source")
                continue
        elif decision.tool_name == "share_drive_file":
            if (decision.arguments.get("file_id") != witness.object_id
                    or decision.arguments.get("version_id", witness.version_id)
                    not in (None, witness.version_id)):
                delivery_missing = True
                reasons.append("inconsistent-share-version")
                continue
            if witness.object_id == anchor.file_id:
                if (witness.version_id != anchor.version_id
                        or witness.content_digest != return_content_digest(anchor.content)):
                    delivery_missing = True
                    reasons.append("anchor-version-changed")
                    continue
            else:
                source_status = _shared_copy_status(
                    bundle, witness, earlier, record.sequence, decisions, returns, requirement,
                ) if named else "unknown"
                if source_status != "passed":
                    delivery_missing |= source_status == "unknown"
                    reasons.append("shared-copy-source-" + source_status)
                    continue
        else:
            delivery_missing = True
            reasons.append("unsupported-delivery-channel")
            continue
        return PublicDeliveryVerdict(
            read_status, "passed", f"bound delivery={record.transaction_id}",
        )
    status = "unknown" if delivery_missing else "failed"
    return PublicDeliveryVerdict(read_status, status, ";".join(reasons) or "no-qualified-delivery")


def _shared_copy_status(
    bundle: EpisodeBundle,
    witness: CommittedDeliveryContent,
    reads: list[int],
    delivered_at: int,
    decisions: dict[str, ModelDecision],
    returns: dict[str, ToolReturn],
    requirement: PublicDeliveryRequirement,
) -> str:
    for record in bundle.records:
        if (not record.committed
                or witness.object_id not in record.created_object_ids):
            continue
        decision = decisions.get(record.action_request_id)
        returned = returns.get(record.transaction_id)
        if (decision is None or decision.tool_name != "create_drive_file"
                or returned is None or returned.tool_name != "create_drive_file"):
            continue
        created = returned.delivery_content
        if created != witness:
            continue
        body = decision.arguments.get("content")
        if (not isinstance(body, str) or return_content_digest(body) != witness.content_digest
                or requirement.matching_fact_indices(body) != witness.matched_fact_indices):
            continue
        raw_refs = decision.arguments.get("source_refs") or ()
        named = {(ref.get("kind"), ref.get("resource_id"))
                 for ref in raw_refs if isinstance(ref, dict)}
        if all((ref.kind, ref.resource_id) in named for ref in witness.source_refs):
            return ("passed" if record.sequence < delivered_at
                    and any(seq < record.sequence for seq in reads) else "failed")
    return "unknown"
