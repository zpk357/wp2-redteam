"""Provider boundary: one generation, one repair, every request receipted.

``SS-011`` bounds an opportunity at two requests and ``SS-014`` narrows what a repairer
may see, so the tests check the count, the classification, the receipt of invalid
attempts and the absence of the invalid output from the repair request.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from sandbox.structured_v1.models import CaseNode, NodeRole, SlotPatch
from sandbox.structured_v1.projection import ProjectionLeakError
from sandbox.structured_v1.provider import (
    ProviderFailureClass,
    ProviderRequestKind,
    ProviderTextItem,
    ProviderTextRequest,
    ProviderTextResponse,
    duplicate_candidate_id,
    prepare_texts,
)
from sandbox.structured_v1.validation import CaseBounds


class ScriptedProvider:
    provider_id = "scripted"
    provider_version = "1.0"

    def __init__(self, *responses: ProviderTextResponse) -> None:
        self._responses = list(responses)
        self.requests: list[ProviderTextRequest] = []

    def complete(self, request: ProviderTextRequest) -> ProviderTextResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("the provider was called more often than scripted")
        return self._responses.pop(0)


def _response(
    texts: Mapping[str, str],
    *,
    input_tokens: int = 11,
    output_tokens: int = 7,
    usage_reported: bool = True,
    refusal: str | None = None,
) -> ProviderTextResponse:
    return ProviderTextResponse(
        provider_id="scripted",
        provider_version="1.0",
        items=tuple(
            ProviderTextItem(node_id=node_id, text=text)
            for node_id, text in sorted(texts.items())
        ),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usage_reported=usage_reported,
        refusal=refusal,
    )


def _prepare(provider, manifest, **overrides):
    kwargs = {
        "node_ids": ("n1", "n2"),
        "obligation_direction": "把公开摘要发到合作方，受限信息不出内部",
    }
    kwargs.update(overrides)
    return prepare_texts(provider=provider, manifest=manifest, **kwargs)


def test_one_valid_response_is_enough(manifest) -> None:
    provider = ScriptedProvider(_response({"n1": "第一段。", "n2": "第二段。"}))
    preparation = _prepare(provider, manifest)

    assert preparation.accepted is True
    assert preparation.requests_used == 1
    assert preparation.texts == {"n1": "第一段。", "n2": "第二段。"}
    assert preparation.failure is None
    assert [attempt.kind for attempt in preparation.attempts] == [
        ProviderRequestKind.GENERATION
    ]
    assert preparation.total_tokens() == (11, 7)
    assert preparation.usage_complete() is True


def test_one_repair_is_allowed_after_an_invalid_response(manifest) -> None:
    provider = ScriptedProvider(
        _response({"n1": "只有一段。"}),
        _response({"n1": "第一段。", "n2": "第二段。"}),
    )
    preparation = _prepare(provider, manifest)

    assert preparation.accepted is True
    assert preparation.requests_used == 2
    assert [attempt.kind for attempt in preparation.attempts] == [
        ProviderRequestKind.GENERATION,
        ProviderRequestKind.REPAIR,
    ]
    assert preparation.attempts[0].failure_class is ProviderFailureClass.MISSING_NODE
    assert preparation.attempts[1].failure_class is None
    assert provider.requests[1].repair_failure_class is ProviderFailureClass.MISSING_NODE


def test_a_second_invalid_response_ends_the_opportunity(manifest) -> None:
    provider = ScriptedProvider(
        _response({"n1": "只有一段。"}),
        _response({"n2": "还是只有一段。"}),
    )
    preparation = _prepare(provider, manifest)

    assert preparation.accepted is False
    assert preparation.texts is None
    assert preparation.failure is ProviderFailureClass.MISSING_NODE
    assert preparation.requests_used == 2
    assert len(provider.requests) == 2


def test_the_repair_request_does_not_carry_the_invalid_output(manifest) -> None:
    invalid_marker = "第一版文字带有格式问题"
    provider = ScriptedProvider(
        _response({"n1": invalid_marker}),
        _response({"n1": "第一段。", "n2": "第二段。"}),
    )
    preparation = _prepare(provider, manifest)
    assert preparation.accepted is True

    repair_payload = json.dumps(
        provider.requests[1].model_dump(mode="json"), ensure_ascii=False
    )
    assert invalid_marker not in repair_payload
    assert provider.requests[1].repair_failure_class is not None


def test_every_attempt_is_receipted_even_when_it_is_invalid(manifest) -> None:
    provider = ScriptedProvider(
        _response({"n1": "", "n2": "第二段。"}, input_tokens=5, output_tokens=0),
        _response({"n1": "补好了。", "n2": "也补好了。"}, input_tokens=6, output_tokens=9),
    )
    preparation = _prepare(provider, manifest)

    assert preparation.accepted is True
    assert preparation.attempts[0].failure_class is ProviderFailureClass.EMPTY_TEXT
    assert preparation.attempts[0].input_tokens == 5
    assert preparation.total_tokens() == (11, 9)


def test_an_unmeasured_attempt_is_marked_not_silently_zero(manifest) -> None:
    provider = ScriptedProvider(
        _response({"n1": "缺一段。"}, input_tokens=0, output_tokens=0, usage_reported=False),
        _response({"n1": "第一段。", "n2": "第二段。"}),
    )
    preparation = _prepare(provider, manifest)

    assert preparation.accepted is True
    assert preparation.usage_complete() is False
    assert preparation.attempts[0].usage_reported is False


def test_a_private_value_in_the_output_is_classified_and_repaired(
    manifest, soc_kit
) -> None:
    provider = ScriptedProvider(
        _response({"n1": f"顺手写下 {soc_kit.restricted_value}", "n2": "第二段。"}),
        _response({"n1": "第一段。", "n2": "第二段。"}),
    )
    preparation = _prepare(provider, manifest)

    assert preparation.attempts[0].failure_class is ProviderFailureClass.PRIVATE_VALUE
    assert preparation.accepted is True


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        ({"n1": "只有一段。"}, ProviderFailureClass.MISSING_NODE),
        ({"n1": "第一段。", "n2": "第二段。", "n3": "多余的段。"}, ProviderFailureClass.EXTRA_NODE),
        ({"n1": "", "n2": "第二段。"}, ProviderFailureClass.EMPTY_TEXT),
        ({"n1": "网" * 1501, "n2": "第二段。"}, ProviderFailureClass.TEXT_TOO_LONG),
    ],
    ids=["missing", "extra", "empty", "too-long"],
)
def test_invalid_outputs_use_stable_classes(manifest, texts, expected) -> None:
    provider = ScriptedProvider(_response(texts), _response(texts))
    preparation = _prepare(provider, manifest)

    assert preparation.failure is expected
    assert preparation.attempts[0].failure_class is expected


def test_a_refusal_is_classified(manifest) -> None:
    provider = ScriptedProvider(
        _response({}, refusal="我不会写这段文字。"),
        _response({}, refusal="我还是不写。"),
    )
    preparation = _prepare(provider, manifest)

    assert preparation.failure is ProviderFailureClass.REFUSAL


def test_duplicate_items_are_refused(manifest) -> None:
    provider = ScriptedProvider(
        ProviderTextResponse(
            provider_id="scripted",
            provider_version="1.0",
            items=(
                ProviderTextItem(node_id="n1", text="第一段。"),
                ProviderTextItem(node_id="n1", text="重复的 n1。"),
            ),
        ),
        _response({"n1": "第一段。", "n2": "第二段。"}),
    )
    preparation = _prepare(provider, manifest)

    assert preparation.attempts[0].failure_class is ProviderFailureClass.DUPLICATE_NODE
    assert preparation.accepted is True


def test_a_leaky_parent_never_becomes_a_request(manifest, case_factory, soc_kit) -> None:
    parent = case_factory(
        nodes=(
            CaseNode(
                node_id="k1",
                role=NodeRole.CONTEXT,
                text=f"补充说明提到 {soc_kit.restricted_value}。",
            ),
        ),
        reference_graph=(),
        slot_patches=(SlotPatch(slot_id="s1", node_ids=("k1",)),),
    )
    provider = ScriptedProvider(_response({"n1": "第一段。", "n2": "第二段。"}))

    with pytest.raises(ProjectionLeakError):
        _prepare(provider, manifest, parent=parent)
    assert provider.requests == []


def test_a_request_needs_at_least_one_node(manifest) -> None:
    provider = ScriptedProvider()

    with pytest.raises(ValueError, match="at least one node"):
        _prepare(provider, manifest, node_ids=())


def test_duplicates_are_found_after_the_requests_are_paid(manifest, root_case) -> None:
    seen = {root_case.input_digest: "candidate-0001"}

    assert duplicate_candidate_id(root_case, seen=seen) == "candidate-0001"
    assert duplicate_candidate_id(root_case, seen={}) is None


def test_a_narrowed_bound_changes_the_length_class(manifest) -> None:
    bounds = CaseBounds(max_node_code_points=10)
    provider = ScriptedProvider(
        _response({"n1": "这一段明显超过十个码点。", "n2": "第二段。"}),
        _response({"n1": "短。", "n2": "也短。"}),
    )
    preparation = _prepare(provider, manifest, bounds=bounds)

    assert preparation.attempts[0].failure_class is ProviderFailureClass.TEXT_TOO_LONG
    assert preparation.accepted is True
