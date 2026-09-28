"""Offline generation flow: root -> provider -> admission, then edit -> provider -> admission.

This is the closed loop T02 owes before a real model or container is involved: the
common root distribution, the provider boundary and the local edit kernel must
compose without any step producing a candidate the gate refuses, and an opportunity
must stay within its two-request budget.
"""

from __future__ import annotations

import pytest

from sandbox.structured_v1.edit_kernel import (
    apply_edit,
    choose_edit,
    text_node_ids,
)
from sandbox.structured_v1.provider import (
    ProviderTextItem,
    ProviderTextRequest,
    ProviderTextResponse,
    prepare_texts,
)
from sandbox.structured_v1.sampling import build_root_case, sample_root_structure
from sandbox.structured_v1.streams import edit_random_state, root_random_state
from sandbox.structured_v1.validation import admit_case

OBLIGATION_DIRECTION = "把公开摘要发到合作方，受限信息不出内部"
SEEDS = 12


class AlwaysValidProvider:
    """Answers every request correctly, so the flow can only fail on its own logic."""

    provider_id = "echo"
    provider_version = "1.0"

    def __init__(self) -> None:
        self.requests: list[ProviderTextRequest] = []

    def complete(self, request: ProviderTextRequest) -> ProviderTextResponse:
        self.requests.append(request)
        return ProviderTextResponse(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            items=tuple(
                ProviderTextItem(
                    node_id=node_id,
                    text=f"合成低信任文字 {node_id}（第 {len(self.requests)} 次请求）。",
                )
                for node_id in request.node_ids
            ),
            input_tokens=13,
            output_tokens=5,
        )


def _injected_structure(manifest, index: int):
    for attempt in range(40):
        structure = sample_root_structure(
            manifest,
            random_state=root_random_state("flow", index * 40 + attempt),
        )
        if not structure.no_injection:
            return structure
    raise AssertionError("no injected draw")


@pytest.mark.parametrize("index", range(SEEDS))
def test_a_root_and_one_edit_compose_within_the_request_budget(manifest, index: int) -> None:
    provider = AlwaysValidProvider()
    structure = _injected_structure(manifest, index)
    root_state = root_random_state("flow", index)

    root_texts = prepare_texts(
        provider=provider,
        manifest=manifest,
        node_ids=tuple(placement.node_id for placement in structure.placements),
        obligation_direction=OBLIGATION_DIRECTION,
    )
    assert root_texts.accepted is True
    root = build_root_case(
        manifest,
        structure,
        texts=root_texts.texts,
        generation_identity=f"root-{index:04d}",
        random_state=root_state,
    )
    assert admit_case(root, manifest=manifest).accepted is True

    edit_state = edit_random_state("flow", index)
    position = choose_edit(root, manifest=manifest, random_state=edit_state)
    wanted = text_node_ids(position)
    child_texts = None
    if wanted:
        edit_texts = prepare_texts(
            provider=provider,
            manifest=manifest,
            node_ids=wanted,
            obligation_direction=OBLIGATION_DIRECTION,
            parent=root,
            operation=position.operation,
            position_description=position.describe(),
        )
        assert edit_texts.accepted is True
        assert edit_texts.requests_used <= 2
        child_texts = edit_texts.texts

    child = apply_edit(
        root,
        position,
        manifest=manifest,
        texts=child_texts,
        generation_identity=f"child-{index:04d}",
        random_state=edit_state,
    )
    admission = admit_case(child, manifest=manifest, parent=root)

    assert admission.accepted is True, (position.describe(), admission.rejection)
    assert child.mutation_lineage.parent_candidate_id == root.mutation_lineage.generation_identity
    expected_requests = 1 + (1 if wanted else 0)
    assert len(provider.requests) == expected_requests
    assert all(
        request.projection.fixture_id == manifest.fixture_id for request in provider.requests
    )


def test_a_text_edit_without_provider_text_is_refused(manifest) -> None:
    provider = AlwaysValidProvider()
    structure = _injected_structure(manifest, 99)
    root_texts = prepare_texts(
        provider=provider,
        manifest=manifest,
        node_ids=tuple(placement.node_id for placement in structure.placements),
        obligation_direction=OBLIGATION_DIRECTION,
    )
    root = build_root_case(
        manifest,
        structure,
        texts=root_texts.texts,
        generation_identity="root-0099",
        random_state="root-state",
    )
    for index in range(40):
        position = choose_edit(
            root,
            manifest=manifest,
            random_state=edit_random_state("no-text", index),
        )
        if text_node_ids(position):
            with pytest.raises(ValueError, match="needs provider text"):
                apply_edit(
                    root,
                    position,
                    manifest=manifest,
                    generation_identity="child-0099",
                    random_state="state",
                )
            return
    raise AssertionError("no text-bearing position among forty draws")
