"""Read-only phase windows using transaction -> action request -> decision index (E-06)."""

from __future__ import annotations

from sandbox.structured_v1.bundle import EpisodeBundle, verify_bundle
from sandbox.structured_v1.coverage import JointRelation, RiskEventKind, extract_coverage
from sandbox.structured_v1.fixture import StructuredFixtureManifest
from sandbox.structured_v1.models import StructuredCase
from sandbox.structured_v1.rendering import render_material


def phase_report(
    bundle: EpisodeBundle, *, manifest: StructuredFixtureManifest,
    parent: StructuredCase | None = None,
) -> dict:
    verify_bundle(bundle)
    if bundle.fixture_id != manifest.fixture_id or bundle.material.manifest_digest != (
        manifest.manifest_digest
    ):
        raise ValueError("phase report fixture identity mismatch")
    records = {r.transaction_id: r for r in bundle.records}
    decisions = {d.action_request_id: i for i, d in enumerate(bundle.model_decisions)
                 if d.action_request_id is not None}
    parent_slots = ({} if parent is None else
                    {s.slot_id: s for s in render_material(parent, manifest).slots})
    changed_slots = ({s.slot_id for s in bundle.material.slots
                      if parent_slots.get(s.slot_id) != s} if parent is not None else set())
    reads = []
    for fact in bundle.exposures:
        record = records.get(fact.tool_call_id)
        if record is None or record.action_request_id not in decisions:
            raise ValueError("read exposure is missing its model decision binding")
        index = decisions[record.action_request_id]
        later = bundle.model_decisions[index + 1:]
        reads.append({
            "tool_call_id": fact.tool_call_id, "action_request_id": record.action_request_id,
            "model_call_id": bundle.model_decisions[index].call_id, "decision_index": index,
            "phase_id": bundle.model_decisions[index].phase_id,
            "slot_id": fact.material.slot_id, "resource_id": fact.material.resource_id,
            "changed_slot": fact.material.slot_id in changed_slots,
            "later_decisions": [d.call_id for d in later],
            "later_action_decisions": [d.call_id for d in later if d.phase_id == "action"],
        })
    coverage = extract_coverage(bundle, manifest=manifest)
    restricted = [r.key for r in coverage.risk if r.event_kind is RiskEventKind.READ_RESTRICTED]
    joint = [j.key for j in coverage.joint if j.relation is JointRelation.SAME_EXCHANGE]
    return {
        "episode_id": bundle.episode_id, "bundle_digest": bundle.bundle_digest,
        "phases": [p.model_dump(mode="json") for p in bundle.phases], "reads": reads,
        "restricted_read_keys": restricted, "same_exchange_keys": joint,
        "restricted_read_with_joint": bool(restricted and joint),
        "changed_material_with_action_window": any(
            r["changed_slot"] and r["later_action_decisions"] for r in reads
        ),
        "phase_overruns": {p.phase_id: list(p.usage.exceeds(p.budget)) for p in bundle.phases
                           if p.usage.exceeds(p.budget)},
        "phase_time_overruns": [p.phase_id for p in bundle.phases
                                if p.elapsed_seconds > p.budget.max_wall_clock_seconds],
    }
