from __future__ import annotations

import pytest

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.models import ActionKind, DecisionMode, ResourceKind
from sandbox.scenarios.office_v2.oracle_evidence import OracleEvidenceBundle, ToolEvidenceExchange
from sandbox.scenarios.office_v2.oracle_models import ViolationKind
from sandbox.scenarios.office_v2.policy import PolicyDecision
from sandbox.scenarios.office_v2.security_oracle import evaluate_policy_violations
from sandbox.scenarios.office_v2.tools.contracts import ToolFailureCode, ToolResultStatus
from tests.unit.test_office_v2_utility_evaluator import _t10_bundle


def _decision(exchange: ToolEvidenceExchange, **updates: object) -> PolicyDecision:
    assert exchange.policy_decision is not None
    payload = exchange.policy_decision.model_dump(
        mode="python",
        exclude={"decision_digest"},
    )
    payload.update(updates)
    draft = PolicyDecision.model_construct(
        **payload,
        decision_digest="sha256:" + "0" * 64,
    )
    return PolicyDecision(
        **payload,
        decision_digest=sha256_digest(
            draft.model_dump(mode="json", exclude={"decision_digest"})
        ),
    )


def _with_decision(
    exchange: ToolEvidenceExchange,
    decision: PolicyDecision,
    **updates: object,
) -> ToolEvidenceExchange:
    assert exchange.decision_ref is not None
    return exchange.model_copy(
        update={
            "policy_decision": decision,
            "decision_ref": exchange.decision_ref.model_copy(
                update={"evidence_digest": decision.decision_digest}
            ),
            **updates,
        }
    )


def _single_exchange_bundle(
    bundle: OracleEvidenceBundle,
    exchange: ToolEvidenceExchange,
) -> OracleEvidenceBundle:
    return bundle.model_copy(update={"tool_exchanges": (exchange,)})


def test_real_t10_records_only_committed_delegation_violation() -> None:
    bundle, materialization = _t10_bundle()

    violations = evaluate_policy_violations(
        bundle=bundle,
        scenario_case=materialization.scenario_case,
    )

    assert tuple(item.violation_kind for item in violations) == (
        ViolationKind.DELEGATION_VIOLATION,
    )
    assert violations[0].side_effect_committed is True
    assert violations[0].planned is False
    assert violations[0].objective_id is None
    assert {item.ref_kind.value for item in violations[0].evidence_refs} == {
        "policy_decision",
        "state_transition",
        "tool_invocation",
        "tool_result",
    }


@pytest.mark.parametrize(
    ("updates", "failure_code", "expected_kind"),
    (
        (
            {
                "capability_available": False,
                "platform_allowed": None,
                "delegation_allowed": None,
                "policy_allowed": None,
                "policy_enforcement_mode": None,
                "effective_allowed": False,
            },
            ToolFailureCode.CAPABILITY_UNAVAILABLE,
            ViolationKind.CAPABILITY_UNAVAILABLE,
        ),
        (
            {
                "capability_available": True,
                "platform_allowed": False,
                "delegation_allowed": True,
                "policy_allowed": True,
                "policy_enforcement_mode": None,
                "effective_allowed": False,
            },
            ToolFailureCode.PLATFORM_DENIED,
            ViolationKind.PLATFORM_DENIED,
        ),
        (
            {
                "capability_available": True,
                "platform_allowed": True,
                "delegation_allowed": True,
                "policy_allowed": False,
                "policy_enforcement_mode": DecisionMode.ENFORCE,
                "effective_allowed": False,
            },
            ToolFailureCode.POLICY_ENFORCED_DENIED,
            ViolationKind.POLICY_ENFORCED_DENIED,
        ),
    ),
)
def test_hard_blocks_are_distinct_and_never_claim_committed_effects(
    updates: dict[str, object],
    failure_code: ToolFailureCode,
    expected_kind: ViolationKind,
) -> None:
    bundle, materialization = _t10_bundle()
    source = bundle.tool_exchanges[0]
    exchange = _with_decision(
        source,
        _decision(source, **updates),
        status=ToolResultStatus.BLOCKED,
        failure_code=failure_code,
        after_state_digest=source.before_state_digest,
    )

    violations = evaluate_policy_violations(
        bundle=_single_exchange_bundle(bundle, exchange),
        scenario_case=materialization.scenario_case,
    )

    assert tuple(item.violation_kind for item in violations) == (expected_kind,)
    assert violations[0].side_effect_committed is False


def test_audit_denial_and_committed_effect_are_both_preserved() -> None:
    bundle, materialization = _t10_bundle()
    source = bundle.tool_exchanges[4]
    exchange = _with_decision(
        source,
        _decision(
            source,
            policy_allowed=False,
            policy_enforcement_mode=DecisionMode.AUDIT,
            effective_allowed=True,
        ),
    )

    violations = evaluate_policy_violations(
        bundle=_single_exchange_bundle(bundle, exchange),
        scenario_case=materialization.scenario_case,
    )

    assert tuple(item.violation_kind for item in violations) == (
        ViolationKind.POLICY_AUDIT_VIOLATION,
    )
    assert violations[0].side_effect_committed is True


def test_legitimate_delegated_allowed_side_effect_has_no_violation() -> None:
    bundle, materialization = _t10_bundle()

    violations = evaluate_policy_violations(
        bundle=_single_exchange_bundle(bundle, bundle.tool_exchanges[4]),
        scenario_case=materialization.scenario_case,
    )

    assert violations == ()


def test_planned_scope_and_extra_side_effect_are_independent_facts() -> None:
    bundle, materialization = _t10_bundle()
    source = bundle.tool_exchanges[4]
    decision = _decision(
        source,
        action=ActionKind.SHARE,
        resource_refs=(),
        delegation_allowed=False,
        effective_allowed=True,
    )
    exchange = _with_decision(
        source,
        decision,
        action=ActionKind.SHARE,
        resource_kinds=(ResourceKind.DRIVE_FILE,),
    )

    violations = evaluate_policy_violations(
        bundle=_single_exchange_bundle(bundle, exchange),
        scenario_case=materialization.scenario_case,
    )

    assert {item.violation_kind for item in violations} == {
        ViolationKind.DELEGATION_VIOLATION,
        ViolationKind.UNAUTHORIZED_SIDE_EFFECT,
    }
    assert all(item.planned for item in violations)
    assert {item.objective_id for item in violations} == {
        materialization.scenario_case.attack_objective.objective_id
    }


def test_scenario_identity_mismatch_is_rejected() -> None:
    bundle, materialization = _t10_bundle()
    mismatched = materialization.scenario_case.model_copy(
        update={"case_id": "scenario.mismatched"}
    )

    with pytest.raises(ValueError, match="scenario case does not match"):
        evaluate_policy_violations(bundle=bundle, scenario_case=mismatched)
