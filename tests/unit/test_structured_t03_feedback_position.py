"""FDM-T03: the verified parent projection changes only guided position/intent draws."""

from __future__ import annotations

import sys
from contextlib import suppress
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from structured_coverage_helpers import record_coverage  # noqa: E402

from sandbox.replay.digests import sha256_digest  # noqa: E402
from sandbox.structured_v1.coverage import BehaviorAtom, CoverageResult  # noqa: E402
from sandbox.structured_v1.edit_kernel import (  # noqa: E402
    EditReferencePosition,
    MoveNodePosition,
    legal_positions,
    target_slot_ids,
)
from sandbox.structured_v1.experiment_inputs import prepare_inputs  # noqa: E402
from sandbox.structured_v1.feedback import (  # noqa: E402
    ParentEvidenceSource,
    ParentFeedbackEvidence,
    PublicFeedback,
    SlotExposureProof,
)
from sandbox.structured_v1.models import OperationKind  # noqa: E402
from sandbox.structured_v1.rendering import render_material  # noqa: E402
from sandbox.structured_v1.search import ArmKind, TwoArmSearch  # noqa: E402


def _search(manifest, *, seed: str) -> TwoArmSearch:
    return TwoArmSearch(
        {item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates},
        manifest,
        seed=seed,
    )


def _local_behavior(search: TwoArmSearch, seed: str) -> None:
    index = next(
        index
        for index in range(1, 80)
        if index % 4 == 1
        and index % 5 not in search._root_slots(f"{seed}:data-release", index // 5)
    )
    search.state = search.state.model_copy(update={
        "feedback_opportunity": 0,
        "direction_opportunities": {"data-release": index},
    })


def _evidence(search: TwoArmSearch, parent_id: str) -> ParentFeedbackEvidence:
    case = search.parents[parent_id]
    identity = search.state.parent_coverage[parent_id].execution
    assert identity is not None
    source = ParentEvidenceSource(
        candidate_id=parent_id,
        episode_id="episode-parent",
        input_digest=case.input_digest,
        material_digest=render_material(case, search.manifest).material_digest,
        fixture_id=search.manifest.fixture_id,
        manifest_digest=search.manifest.manifest_digest,
        execution_config_digest=identity.execution_config_digest,
        coverage_version=identity.coverage_version,
        bundle_digest=identity.bundle_digest,
        envelope_digest=identity.envelope_digest,
        final_bundle_digest=sha256_digest({"final": parent_id}),
    )
    proof = SlotExposureProof(
        slot_id="s1",
        fact_ids=("fact-1",),
        tool_call_ids=("tool-1",),
        action_request_ids=("request-1",),
        decision_call_ids=("decision-1",),
        later_action_request_ids=("request-2",),
        later_decision_call_ids=("decision-2",),
        tool_roles=("read",),
        action_window="observed",
    )
    return ParentFeedbackEvidence(
        source=source,
        public_feedback=PublicFeedback(
            observed_slot_ids=("s1",), observed_tool_roles=("read",)
        ),
        projection_digest=sha256_digest({"projection": parent_id}),
        slot_proofs=(proof,),
        exposure_count=1,
    )


def _seed_parent(search: TwoArmSearch) -> str:
    parent = sorted(search.parents)[0]
    record_coverage(
        search,
        CoverageResult(
            episode_id="episode-seed",
            fixture_id=search.manifest.fixture_id,
            behavior=(BehaviorAtom(key=("seed",)),),
        ),
        parent_id=parent,
    )
    return parent


def test_target_slot_mapping_uses_source_for_references_and_both_sides_for_moves(manifest):
    search = _search(manifest, seed="mapping")
    parent = search.parents[sorted(search.parents)[0]]
    positions = legal_positions(parent, manifest=manifest)

    moves = [
        item
        for item in positions[OperationKind.MOVE_NODE]
        if isinstance(item, MoveNodePosition)
    ]
    assert moves
    assert all(
        target_slot_ids(parent, item) == tuple(dict.fromkeys((item.source_slot_id, item.slot_id)))
        for item in moves
    )

    references = [
        item for item in positions[OperationKind.EDIT_REFERENCE]
        if isinstance(item, EditReferencePosition)
    ]
    assert references
    for item in references:
        source = item.source
        if source is None:
            edge = next(edge for edge in parent.reference_graph if edge.edge_id == item.edge_id)
            source = edge.source
        source_slot = next(
            patch.slot_id for patch in parent.slot_patches if source in patch.node_ids
        )
        assert target_slot_ids(parent, item) == (source_slot,)


def test_guided_receipt_records_priority_and_target_feedback(manifest):
    # Seed choice and direction are fixed; vary only the campaign seed until the frozen 3/4
    # branch selects a priority position.  This checks the algorithmic branch, not a frequency.
    for seed in ("t03-a", "t03-b", "t03-c", "t03-d", "t03-e"):
        search = _search(manifest, seed=seed)
        parent = _seed_parent(search)
        search.record_parent_feedback(parent, _evidence(search, parent))
        _local_behavior(search, seed)
        receipt = search.select(ArmKind.COVERAGE_GUIDED)
        if receipt.selection_branch == "priority":
            assert receipt.legal_positions_digest is not None
            assert receipt.priority_positions_digest is not None
            assert receipt.selected_position is not None
            assert "s1" in receipt.target_slot_ids
            assert receipt.public_feedback is not None
            assert receipt.public_feedback.action_window == "observed"
            assert receipt.probability_version == "structured-fdm-position-v2"
            # Feedback direction places *material*, so the priority branch must land on a position
            # that writes wording whenever the parent's priority set contains one.
            from sandbox.structured_v1.edit_kernel import needs_text, position_from_data

            assert needs_text(position_from_data(receipt.selected_position)) is True
            return
    pytest.fail("the deterministic seed set did not exercise the frozen priority branch")


def test_random_evolution_ignores_injected_guided_history(manifest):
    plain = _search(manifest, seed="t03-random")
    poisoned = _search(manifest, seed="t03-random")
    parent = sorted(plain.parents)[0]
    for search in (plain, poisoned):
        search.state = search.state.model_copy(update={
            "evolution_pool": (parent,),
            "direction_opportunities": {"data-release": 1},
        })
    _seed_parent(poisoned)
    poisoned.record_parent_feedback(parent, _evidence(poisoned, parent))

    left = plain.select(ArmKind.RANDOM_EVOLUTION)
    right = poisoned.select(ArmKind.RANDOM_EVOLUTION)
    assert left.selected_position == right.selected_position
    assert left.intent_id == right.intent_id
    assert left.public_feedback is None and right.public_feedback is None
    assert left.feedback_sources == right.feedback_sources == ()


def test_public_feedback_and_intent_reach_provider_only_for_text_edits(manifest):
    from sandbox.structured_v1.generation import GenerationBudget
    from sandbox.structured_v1.provider import ProviderTextItem, ProviderTextResponse

    class Provider:
        provider_id = "t03-provider"
        provider_version = "t03-provider-v1"

        def __init__(self):
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            return ProviderTextResponse(
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                items=tuple(
                    ProviderTextItem(node_id=node_id, text=f"改写-{node_id}")
                    for node_id in request.node_ids
                ),
            )

    provider = Provider()
    search = TwoArmSearch(
        {item.candidate_id: item.case for item in prepare_inputs(manifest, count=4).candidates},
        manifest,
        seed="t03-provider",
        provider=provider,
        generation_budget=GenerationBudget(
            max_input_tokens_per_request=4096, max_output_tokens_per_request=1024
        ),
    )
    parent = _seed_parent(search)
    search.record_parent_feedback(parent, _evidence(search, parent))
    _local_behavior(search, "t03-provider")
    for _ in range(50):
        receipt = search.select(ArmKind.COVERAGE_GUIDED)
        if receipt.intent_id is None:
            with suppress(Exception):
                search.generate(receipt)
            continue
        search.generate(receipt)
        assert provider.requests[-1].intent_id == receipt.intent_id
        assert provider.requests[-1].feedback == receipt.public_feedback
        return
    pytest.fail("did not draw a text-bearing local operation")
