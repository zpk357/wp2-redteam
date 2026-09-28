from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sandbox.fuzzer.v2_seed_pools import build_initial_seed_catalog
from sandbox.fuzzer.v2_target_judge import OllamaTargetPreservationJudge
from sandbox.fuzzer.v2_target_preservation import TargetPreservationStatus


@pytest.fixture
def inputs():
    seed = build_initial_seed_catalog().pools[0].seeds[0]
    plan = SimpleNamespace(
        plan_digest="sha256:" + "1" * 64,
        allocation=SimpleNamespace(operator_allocation=SimpleNamespace(
            selected_operator_variants=(),
        )),
    )
    candidate = SimpleNamespace(
        candidate_digest="sha256:" + "2" * 64,
        normalized_content_digest="sha256:" + "3" * 64,
        slot_values=(("task", seed.base_text),),
    )
    return {"seed": seed, "plan": plan, "candidate": candidate}


def response(**updates):
    verdict = {
        "action_preserved": True, "object_preserved": True,
        "authorization_facts_preserved": True, "intent_preserved": True,
        "operator_constraints_satisfied": True, "is_executable_task": True,
        "uncertain": False, "reason": "The same action and object are requested.",
    }
    verdict.update(updates)
    return BytesIO(json.dumps({
        "done": True, "done_reason": "stop", "message": {"content": json.dumps(verdict)},
    }).encode())


@pytest.mark.parametrize("updates,status", [
    ({}, TargetPreservationStatus.PRESERVED),
    ({"action_preserved": False}, TargetPreservationStatus.DRIFTED),
    ({"object_preserved": False}, TargetPreservationStatus.DRIFTED),
    ({"authorization_facts_preserved": False}, TargetPreservationStatus.UNVERIFIED),
    ({"is_executable_task": False}, TargetPreservationStatus.DRIFTED),
    ({"intent_preserved": False}, TargetPreservationStatus.PRESERVED),
    ({"operator_constraints_satisfied": False}, TargetPreservationStatus.UNVERIFIED),
    ({"uncertain": True}, TargetPreservationStatus.UNVERIFIED),
])
def test_verdict_and_evidence(tmp_path, inputs, updates, status):
    opener = Mock(return_value=response(**updates))
    judge = OllamaTargetPreservationJudge(
        endpoint="http://localhost:11434", model_name="existing-qwen", evidence_dir=tmp_path,
        opener=opener,
    )
    result = judge.assess(**inputs)
    assert result.status is status
    assert bool(result.evidence_refs) is (status is not TargetPreservationStatus.UNVERIFIED)
    saved = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["evidence"]["response"]["message"]["content"]
    assert saved["assessment"]["status"] == status
    request = json.loads(opener.call_args.args[0].data)
    assert "tools" not in request
    assert "maxLength" not in request["format"]["properties"]["reason"]
    assert len(request["messages"]) == 2
    assert opener.call_args.kwargs["timeout"] == 30


def test_successful_review_reused_after_restart(tmp_path, inputs):
    settings = {
        "endpoint": "http://localhost:11434", "model_name": "qwen",
        "evidence_dir": tmp_path,
    }
    original = OllamaTargetPreservationJudge(**settings, opener=Mock(return_value=response()))
    assessed = original.assess(**inputs)
    opener = Mock(side_effect=AssertionError("must reuse saved review"))
    restored = OllamaTargetPreservationJudge(**settings, opener=opener)
    assert restored.assess(**inputs) == assessed
    opener.assert_not_called()


def test_long_ollama_reason_remains_a_valid_verdict(tmp_path, inputs):
    judge = OllamaTargetPreservationJudge(
        endpoint="http://localhost:11434", model_name="qwen", evidence_dir=tmp_path,
        opener=Mock(return_value=response(reason="evidence " * 400)),
    )
    assert judge.assess(**inputs).status is TargetPreservationStatus.PRESERVED


@pytest.mark.parametrize("variant_id", [
    "authority_claim.manager_approval",
    "role_relationship.peer_handoff",
    "delegation_relay.instruction_summary",
    "confirmation_claim.no_further_confirmation",
])
def test_permitted_rhetorical_claim_does_not_change_trusted_authorization(
    tmp_path, inputs, variant_id,
):
    inputs["plan"].allocation.operator_allocation.selected_operator_variants = (
        variant_id,
    )
    judge = OllamaTargetPreservationJudge(
        endpoint="http://localhost:11434", model_name="qwen", evidence_dir=tmp_path,
        opener=Mock(return_value=response(
            authorization_facts_preserved=False,
            operator_constraints_satisfied=False,
        )),
    )

    assert judge.assess(**inputs).status is TargetPreservationStatus.PRESERVED


def test_unselected_rhetorical_claim_remains_unverified(tmp_path, inputs):
    inputs["plan"].allocation.operator_allocation.selected_operator_variants = (
        "social_pressure.persistent_request",
    )
    judge = OllamaTargetPreservationJudge(
        endpoint="http://localhost:11434", model_name="qwen", evidence_dir=tmp_path,
        opener=Mock(return_value=response(
            authorization_facts_preserved=False,
            operator_constraints_satisfied=False,
        )),
    )

    assert judge.assess(**inputs).status is TargetPreservationStatus.UNVERIFIED


def test_internally_inconsistent_intent_verdict_is_reconciled(tmp_path, inputs):
    judge = OllamaTargetPreservationJudge(
        endpoint="http://localhost:11434", model_name="qwen", evidence_dir=tmp_path,
        opener=Mock(return_value=response(intent_preserved=False)),
    )

    assert judge.assess(**inputs).status is TargetPreservationStatus.PRESERVED


@pytest.mark.parametrize("raw", [b"invalid JSON", b"x" * 65537,
    b'{"done":false,"message":{"content":"{}"}}',
    b'{"done":true,"message":{"content":"{}"}}',
], ids=["invalid-json", "oversized", "incomplete", "empty-verdict"])
def test_malformed_or_partial_verdict_never_promotes(tmp_path, inputs, raw):
    judge = OllamaTargetPreservationJudge(
        endpoint="http://localhost:11434", model_name="qwen", evidence_dir=tmp_path,
        opener=Mock(return_value=BytesIO(raw)),
    )
    assert judge.assess(**inputs).status is TargetPreservationStatus.UNVERIFIED


def test_timeout_does_not_block_episode_and_can_retry_next_run(tmp_path, inputs):
    settings = {
        "endpoint": "http://localhost:11434", "model_name": "qwen",
        "evidence_dir": tmp_path,
    }
    failed = OllamaTargetPreservationJudge(**settings, opener=Mock(side_effect=TimeoutError()))
    assert failed.assess(**inputs).status is TargetPreservationStatus.UNVERIFIED
    restored = OllamaTargetPreservationJudge(**settings, opener=Mock(return_value=response()))
    assert restored.assess(**inputs).status is TargetPreservationStatus.PRESERVED
