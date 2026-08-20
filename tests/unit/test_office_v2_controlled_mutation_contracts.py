from __future__ import annotations

import pytest

from sandbox.fuzzer.v2_campaign_store import V2CampaignStore
from sandbox.fuzzer.v2_frontier import FrontierKind
from sandbox.fuzzer.v2_mutation_identity import (
    V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST,
    build_v2_mutation_identity_lock,
)
from sandbox.fuzzer.v2_scheduler import (
    AllocationLane,
    ComparisonContext,
    GenerationAllocation,
    MutationGenerationAllocation,
    OperatorAllocation,
)
from sandbox.mutation.v2_brief import (
    V2_MUTATION_PROMPT_IDENTITY_DIGEST,
    V2_MUTATION_RESPONSE_SCHEMA_DIGEST,
    BriefFact,
    build_minimal_fact_brief,
)
from sandbox.mutation.v2_candidate import parse_candidate
from sandbox.mutation.v2_contracts import (
    MutationFieldAuthority,
    MutationFieldClass,
    MutationFieldRule,
    MutationIntent,
    MutationPlan,
    MutationProviderBudget,
    PayloadSlot,
    build_v2_mutation_field_registry,
    seal_contract,
    seal_field_registry,
)
from sandbox.mutation.v2_materializer import (
    SlotMaterializationTarget,
    TextMaterializationOperation,
    materialize_candidate,
    rematerialize_office_v2_scenario_text,
)
from sandbox.mutation.v2_ollama import (
    OllamaHTTPResponse,
    OllamaV2MutationProvider,
)
from sandbox.mutation.v2_policy import (
    FeedbackGapKind,
    FeedbackSignal,
    MutationCapability,
    OperatorFamily,
    OperatorSelectionStatus,
    select_operator,
)
from sandbox.mutation.v2_preparation import (
    MutationPreparationState,
    prepare_candidate,
)
from sandbox.mutation.v2_provider import (
    MutationProviderResult,
    ProviderFailureClass,
    RuleBasedV2MutationProvider,
    V2ProviderFailure,
    seal_failed_provider_attempt,
)
from sandbox.mutation.v2_validation import (
    CandidateValidationDisposition,
    validate_candidate,
)
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_cases import build_representative_scenario_fixtures
from sandbox.scenarios.office_v2.attack_models import AttackEntryKind, CompatibilityPurpose
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world


def digest(label: str) -> str:
    return sha256_digest({"label": label})


def allocation() -> MutationGenerationAllocation:
    context = seal_contract(
        ComparisonContext,
        {
            "actor_id": "actor-1",
            "task_id": "task-1",
            "resource_binding_digest": digest("binding"),
            "allocation_target_digest": digest("target"),
            "authorization_branch": "delegation-missing",
            "baseline_snapshot_digest": digest("coverage"),
        },
        "comparison_context_digest",
    )
    base = seal_contract(
        GenerationAllocation,
        {
            "generation_allocation_id": "allocation-1",
            "generation_index": 1,
            "frontier_kind": FrontierKind.RISK,
            "frontier_id": "frontier-1",
            "allocation_target_digest": digest("target"),
            "parent_seed_id": "seed-1",
            "supporting_execution_record_id": "execution-1",
            "binding_source_digest": digest("binding-source"),
            "allocation_lane": AllocationLane.RISK,
            "reason_codes": ("soft-ranking",),
            "coverage_snapshot_digest": digest("coverage"),
            "corpus_digest": digest("corpus"),
            "frontier_digest": digest("frontier"),
        },
        "allocation_digest",
    )
    operator = seal_contract(
        OperatorAllocation,
        {
            "operator_allocation_id": "operator-1",
            "frontier_id": "frontier-1",
            "supporting_execution_record_id": "execution-1",
            "feedback_digest": digest("feedback"),
            "selected_operator_families": ("expression_structure",),
            "reason_codes": ("feedback-observed-not-used",),
            "policy_digest": V2_FEEDBACK_TO_OPERATOR_POLICY_DIGEST,
        },
        "operator_allocation_digest",
    )
    return seal_contract(
        MutationGenerationAllocation,
        {
            "mutation_generation_allocation_id": "mutation-allocation-1",
            "base_allocation": base,
            "initial_context": context,
            "operator_allocation": operator,
            "final_context": context,
        },
        "mutation_allocation_digest",
    )


def plan(
    *,
    provider_id: str = "provider-rule-based-v2",
    model_identity_digest: str | None = None,
) -> MutationPlan:
    item = allocation()
    intent = seal_contract(
        MutationIntent,
        {
            "mutation_intent_id": "intent-1",
            "mutation_allocation_digest": item.mutation_allocation_digest,
            "frontier_id": item.base_allocation.frontier_id,
            "parent_seed_id": item.base_allocation.parent_seed_id,
            "supporting_execution_record_id": (
                item.base_allocation.supporting_execution_record_id
            ),
            "binding_source_digest": item.base_allocation.binding_source_digest,
            "comparison_context_digest": item.final_context.comparison_context_digest,
            "baseline_snapshot_digest": item.base_allocation.coverage_snapshot_digest,
            "feedback_digest": item.operator_allocation.feedback_digest,
            "operator_allocation_digest": (
                item.operator_allocation.operator_allocation_digest
            ),
        },
        "intent_digest",
    )
    registry = build_v2_mutation_field_registry()
    return seal_contract(
        MutationPlan,
        {
            "mutation_plan_id": "plan-1",
            "intent": intent,
            "allocation": item,
            "payload_slots": (
                PayloadSlot(
                    payload_slot_id="slot-1",
                    payload_spec_id="payload-1",
                    placement_ref="mail/message-1/body",
                    field_path="condition.payload_slots.generated_content",
                    content_constraints=("plain-text",),
                ),
            ),
            "field_registry_digest": registry.registry_digest,
            "changed_field_paths": (
                "condition.digests",
                "condition.payload_slots.generated_content",
            ),
            "preserved_field_paths": ("scenario.canonical_world",),
            "provider_id": provider_id,
            "model_identity_digest": model_identity_digest or digest("model"),
            "prompt_identity_digest": V2_MUTATION_PROMPT_IDENTITY_DIGEST,
            "response_schema_digest": V2_MUTATION_RESPONSE_SCHEMA_DIGEST,
            "budget": MutationProviderBudget(
                plan_total_token_budget=512,
                per_attempt_token_limit=256,
                reserved_total_cost_microunits=100,
            ),
            "mutation_identity_digest": build_v2_mutation_identity_lock().identity_digest,
        },
        "plan_digest",
    )


def test_field_registry_rejects_unknown_duplicate_and_authority_drift() -> None:
    registry = build_v2_mutation_field_registry()
    paths = tuple(item.field_path for item in registry.rules)
    registry.require_paths(paths)
    with pytest.raises(ValueError, match="unknown, duplicate, or missing"):
        registry.require_paths((*paths, "unknown"))
    with pytest.raises(ValueError, match="more than once"):
        seal_field_registry(
            registry_version="v1",
            object_shape="shape",
            rules=(registry.rules[0], registry.rules[0]),
        )
    with pytest.raises(ValueError, match="derived fields must be host-derived"):
        MutationFieldRule(
            field_path="digest",
            field_class=MutationFieldClass.DERIVED,
            authority=MutationFieldAuthority.PROVIDER_TEXT,
        )


def test_operator_policy_is_deterministic_and_requires_scheduler_allocation() -> None:
    capability = MutationCapability(
        operator_family=OperatorFamily.AUTHORIZATION_BRANCH,
        supported_gap_kinds=(FeedbackGapKind.ATTEMPTED_BLOCKED,),
        changed_dimensions=("authorization_branch",),
        preserved_dimensions=("objective",),
        scheduler_allocation_required=True,
    )
    feedback = FeedbackSignal(
        gap_kind=FeedbackGapKind.ATTEMPTED_BLOCKED,
        feedback_digest=digest("blocked"),
    )
    rejected = select_operator(
        frontier_id="frontier-1",
        supporting_execution_record_id="execution-1",
        feedback=feedback,
        capabilities=(capability,),
    )
    assert rejected.status is OperatorSelectionStatus.NO_COMPATIBLE_OPERATOR

    allowed = feedback.model_copy(
        update={"authorized_scheduler_allocations": ("authorization_branch",)}
    )
    first = select_operator(
        frontier_id="frontier-1",
        supporting_execution_record_id="execution-1",
        feedback=allowed,
        capabilities=(capability,),
    )
    second = select_operator(
        frontier_id="frontier-1",
        supporting_execution_record_id="execution-1",
        feedback=allowed,
        capabilities=(capability,),
    )
    assert first == second
    assert first.allocation.selected_operator_families == ("authorization_branch",)


def test_plan_locks_single_slot_and_brief_contains_only_provider_surface() -> None:
    item = plan()
    brief = build_minimal_fact_brief(
        plan=item,
        frontier_description="Exercise an uncovered business-policy boundary.",
        operator_instructions=("Change only the expression structure.",),
        scenario_facts=(
            BriefFact(
                fact_kind="resource-label",
                public_label="Document sensitivity",
                public_value="restricted",
            ),
        ),
        parent_payload_texts=("Parent test instruction",),
    )
    assert tuple(slot.payload_slot_id for slot in brief.slots) == ("slot-1",)
    assert "oracle" not in brief.model_dump_json().lower()
    assert "canonical_world" in brief.forbidden_changes

    payload = {
        field_name: getattr(item, field_name)
        for field_name in type(item).model_fields
        if field_name != "plan_digest"
    }
    payload["payload_slots"] = (*item.payload_slots, item.payload_slots[0].model_copy(
        update={"payload_slot_id": "slot-2"}
    ))
    with pytest.raises(ValueError, match="ordinary mutation plan"):
        seal_contract(MutationPlan, payload, "plan_digest")


@pytest.mark.asyncio
async def test_rule_provider_parse_validate_and_materialize_are_deterministic() -> None:
    item = plan()
    registry = build_v2_mutation_field_registry()
    brief = build_minimal_fact_brief(
        plan=item,
        frontier_description="Exercise an uncovered business-policy boundary.",
        operator_instructions=("Change only the expression structure.",),
        scenario_facts=(),
        parent_payload_texts=("Parent test instruction",),
    )
    provider = RuleBasedV2MutationProvider()
    first = await provider.generate(plan=item, brief=brief, attempt_index=1)
    second = await provider.generate(plan=item, brief=brief, attempt_index=1)
    assert first == second

    raw = first.candidate.model_dump_json()
    parsed = parse_candidate(
        plan=item,
        raw_json=raw,
        parent_text_by_slot={"slot-1": "Parent test instruction"},
    )
    validation = validate_candidate(
        plan=item,
        registry=registry,
        candidate=parsed,
        cumulative_output_tokens=first.attempt.output_tokens,
    )
    assert validation.disposition is CandidateValidationDisposition.ACCEPTED
    assert len(validation.checks) == 14

    target = SlotMaterializationTarget(
        payload_slot_id="slot-1",
        resource_id="message-1",
        resource_version="v1",
        field_path="body",
        original_content="Original business content",
        operation=TextMaterializationOperation.APPEND,
    )
    materialized_first = materialize_candidate(
        plan=item,
        parsed=parsed,
        validation=validation,
        scenario_case_id="scenario-case-1",
        targets=(target,),
    )
    materialized_second = materialize_candidate(
        plan=item,
        parsed=parsed,
        validation=validation,
        scenario_case_id="scenario-case-1",
        targets=(target,),
    )
    assert materialized_first == materialized_second
    assert materialized_first.slot_values[0].visible_content.startswith(
        "Original business content\nControlled test variant"
    )
    assert materialized_first.candidate.delivered_payloads


def test_exact_duplicate_is_rejected_without_predicting_coverage() -> None:
    item = plan()
    raw = '{"slot_values":[{"payload_slot_id":"slot-1","generated_content":"x"}]}'
    parsed = parse_candidate(
        plan=item,
        raw_json=raw,
        parent_text_by_slot={"slot-1": "parent"},
    )
    result = validate_candidate(
        plan=item,
        registry=build_v2_mutation_field_registry(),
        candidate=parsed,
        known_candidate_digests=frozenset({parsed.candidate_digest}),
    )
    assert result.disposition is CandidateValidationDisposition.REJECTED
    assert result.exact_duplicate is True


def test_unchanged_parent_text_is_rejected_as_noop() -> None:
    item = plan()
    parsed = parse_candidate(
        plan=item,
        raw_json=(
            '{"slot_values":[{"payload_slot_id":"slot-1",'
            '"generated_content":"parent"}]}'
        ),
        parent_text_by_slot={"slot-1": "parent"},
    )
    result = validate_candidate(
        plan=item,
        registry=build_v2_mutation_field_registry(),
        candidate=parsed,
    )
    assert result.disposition is CandidateValidationDisposition.REJECTED
    assert any(
        check.reason_code == "candidate-noop" and not check.passed
        for check in result.checks
    )


class RetryThenRuleProvider(RuleBasedV2MutationProvider):
    async def generate(self, *, plan, brief, attempt_index):
        if attempt_index == 1:
            attempt = seal_failed_provider_attempt(
                plan=plan,
                attempt_index=attempt_index,
                request_digest=digest("retry-request"),
                failure_class=ProviderFailureClass.SERVER_TRANSIENT,
                input_tokens=7,
                output_tokens=3,
                actual_cost_microunits=11,
            )
            raise V2ProviderFailure("temporary", attempt=attempt)
        result: MutationProviderResult = await super().generate(
            plan=plan, brief=brief, attempt_index=attempt_index
        )
        return result


@pytest.mark.asyncio
async def test_preparation_accumulates_failed_and_successful_attempt_costs() -> None:
    item = plan()
    brief = build_minimal_fact_brief(
        plan=item,
        frontier_description="Exercise an uncovered business-policy boundary.",
        operator_instructions=("Change only the expression structure.",),
        scenario_facts=(),
        parent_payload_texts=("Parent test instruction",),
    )
    preparation, _ = await prepare_candidate(
        campaign_id="campaign-1",
        plan=item,
        brief=brief,
        registry=build_v2_mutation_field_registry(),
        provider=RetryThenRuleProvider(),
        parent_text_by_slot={"slot-1": "Parent test instruction"},
        scenario_case_id="scenario-case-1",
        targets=(
            SlotMaterializationTarget(
                payload_slot_id="slot-1",
                resource_id="message-1",
                resource_version="v1",
                field_path="body",
                original_content="Original business content",
                operation=TextMaterializationOperation.APPEND,
            ),
        ),
    )
    assert preparation.state is MutationPreparationState.READY
    assert len(preparation.provider_attempts) == 2
    assert preparation.outcome.actual_input_tokens == (
        7 + preparation.provider_attempts[1].input_tokens
    )
    assert preparation.outcome.actual_output_tokens == (
        3 + preparation.provider_attempts[1].output_tokens
    )
    assert preparation.outcome.actual_cost_microunits == 11


class FakeOllamaTransport:
    def __init__(self, generated_content: str) -> None:
        self.generated_content = generated_content
        self.calls: list[dict[str, object]] = []

    async def post_json(
        self, *, endpoint: str, payload: dict[str, object], timeout_ms: int
    ) -> OllamaHTTPResponse:
        self.calls.append(
            {"endpoint": endpoint, "payload": payload, "timeout_ms": timeout_ms}
        )
        candidate = {
            "slot_values": [
                {
                    "payload_slot_id": "slot-1",
                    "generated_content": self.generated_content,
                }
            ]
        }
        envelope = {
            "response": __import__("json").dumps(candidate),
            "prompt_eval_count": 20,
            "eval_count": 8,
        }
        return OllamaHTTPResponse(
            status=200,
            body=__import__("json").dumps(envelope).encode("utf-8"),
        )


class FixedHTTPTransport:
    def __init__(self, response: OllamaHTTPResponse | Exception) -> None:
        self.response = response

    async def post_json(self, **_kwargs) -> OllamaHTTPResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.asyncio
async def test_fake_http_ollama_protocol_uses_schema_seed_and_locked_model() -> None:
    model_digest = digest("ollama-model")
    item = plan(
        provider_id="provider-ollama-v2",
        model_identity_digest=model_digest,
    )
    brief = build_minimal_fact_brief(
        plan=item,
        frontier_description="Exercise an uncovered business-policy boundary.",
        operator_instructions=("Change only the expression structure.",),
        scenario_facts=(),
        parent_payload_texts=("Parent test instruction",),
    )
    transport = FakeOllamaTransport("Offline generated variant")
    provider = OllamaV2MutationProvider(
        endpoint="http://fake-ollama/api/generate",
        model_name="qwen-test",
        model_identity_digest=model_digest,
        transport=transport,
    )
    result = await provider.generate(plan=item, brief=brief, attempt_index=1)
    call = transport.calls[0]
    assert call["payload"]["stream"] is False
    assert call["payload"]["format"]
    assert isinstance(call["payload"]["options"]["seed"], int)
    assert result.candidate.slot_values[0].generated_content == "Offline generated variant"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (429, ProviderFailureClass.RATE_LIMIT_TRANSIENT),
        (503, ProviderFailureClass.SERVER_TRANSIENT),
        (400, ProviderFailureClass.CONFIGURATION_PERMANENT),
    ),
)
async def test_fake_http_ollama_classifies_http_failures(status, expected) -> None:
    model_digest = digest("ollama-model")
    item = plan(
        provider_id="provider-ollama-v2",
        model_identity_digest=model_digest,
    )
    brief = build_minimal_fact_brief(
        plan=item,
        frontier_description="Exercise an uncovered business-policy boundary.",
        operator_instructions=("Change only the expression structure.",),
        scenario_facts=(),
        parent_payload_texts=("Parent test instruction",),
    )
    provider = OllamaV2MutationProvider(
        endpoint="http://fake-ollama/api/generate",
        model_name="qwen-test",
        model_identity_digest=model_digest,
        transport=FixedHTTPTransport(OllamaHTTPResponse(status=status, body=b"failure")),
    )
    with pytest.raises(V2ProviderFailure) as raised:
        await provider.generate(plan=item, brief=brief, attempt_index=1)
    assert raised.value.attempt.failure_class is expected
    assert raised.value.attempt.http_status == status


@pytest.mark.asyncio
async def test_fake_http_ollama_pauses_on_truncation_schema_drift_and_unknown() -> None:
    model_digest = digest("ollama-model")
    item = plan(
        provider_id="provider-ollama-v2",
        model_identity_digest=model_digest,
    )
    brief = build_minimal_fact_brief(
        plan=item,
        frontier_description="Exercise an uncovered business-policy boundary.",
        operator_instructions=("Change only the expression structure.",),
        scenario_facts=(),
        parent_payload_texts=("Parent test instruction",),
    )
    cases = (
        (
            b'{"done":false,"response":""}',
            ProviderFailureClass.TRUNCATED_TRANSIENT,
        ),
        (
            b'{"done":true,"response":"{\\"unexpected\\":true}"}',
            ProviderFailureClass.PROTOCOL_INTEGRITY_PERMANENT,
        ),
    )
    for body, expected in cases:
        provider = OllamaV2MutationProvider(
            endpoint="http://fake-ollama/api/generate",
            model_name="qwen-test",
            model_identity_digest=model_digest,
            transport=FixedHTTPTransport(OllamaHTTPResponse(status=200, body=body)),
        )
        with pytest.raises(V2ProviderFailure) as raised:
            await provider.generate(plan=item, brief=brief, attempt_index=1)
        assert raised.value.attempt.failure_class is expected

    provider = OllamaV2MutationProvider(
        endpoint="http://fake-ollama/api/generate",
        model_name="qwen-test",
        model_identity_digest=model_digest,
        transport=FixedHTTPTransport(OSError("unknown transport state")),
    )
    with pytest.raises(V2ProviderFailure) as raised:
        await provider.generate(plan=item, brief=brief, attempt_index=1)
    assert raised.value.attempt.failure_class is ProviderFailureClass.AMBIGUOUS


@pytest.mark.asyncio
async def test_no_model_preparation_persists_and_reopens_ready_state(tmp_path) -> None:
    item = plan()
    brief = build_minimal_fact_brief(
        plan=item,
        frontier_description="Exercise an uncovered business-policy boundary.",
        operator_instructions=("Change only the expression structure.",),
        scenario_facts=(),
        parent_payload_texts=("Parent test instruction",),
    )
    target = SlotMaterializationTarget(
        payload_slot_id="slot-1",
        resource_id="message-1",
        resource_version="v1",
        field_path="body",
        original_content="Original business content",
        operation=TextMaterializationOperation.APPEND,
    )
    preparation, materialized = await prepare_candidate(
        campaign_id="campaign-1",
        plan=item,
        brief=brief,
        registry=build_v2_mutation_field_registry(),
        provider=RuleBasedV2MutationProvider(),
        parent_text_by_slot={"slot-1": "Parent test instruction"},
        scenario_case_id="scenario-case-1",
        targets=(target,),
    )
    assert preparation.state is MutationPreparationState.READY
    assert materialized is not None

    path = tmp_path / "mutation-preparation.db"
    with V2CampaignStore(path) as store:
        store._db.execute(
            "INSERT INTO campaign VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "campaign-1",
                "{}",
                digest("identity"),
                "{}",
                digest("coverage"),
                digest("state"),
                0,
            ),
        )
        store._db.commit()
        store.put_mutation_preparation(preparation)
        assert store.load_mutation_preparation(preparation.preparation_id) == preparation
    with V2CampaignStore(path) as reopened:
        restored = reopened.load_mutation_preparation(preparation.preparation_id)
        assert restored == preparation
        assert restored.materialized_candidate.materialization_digest == (
            materialized.candidate.materialization_digest
        )


def test_stage5_materializer_is_reused_for_a_new_immutable_scenario_case() -> None:
    source = next(
        item.scenario_case
        for item in build_representative_scenario_fixtures()
        if item.scenario_case.adversarial_condition.entry_kind
        is AttackEntryKind.INDIRECT_CONTENT
        and item.purpose is CompatibilityPurpose.REALIZED_WITNESS
    )
    canonical = load_canonical_world()
    result = rematerialize_office_v2_scenario_text(
        source_case=source,
        canonical_world=canonical,
        generated_content="Controlled replacement text for isolated validation.",
        purpose=CompatibilityPurpose.REALIZED_WITNESS,
        seed=404,
    )
    assert result.scenario_case.case_id != source.case_id
    assert result.scenario_case.parent_case_digest == source.parent_case_digest
    assert result.scenario_case.base_world_digest == canonical.world_digest
    assert result.scenario_case.materialization_record.changed_content_fields
