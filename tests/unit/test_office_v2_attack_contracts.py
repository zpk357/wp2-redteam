from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2.attack_models import (
    ADVERSARIAL_CONDITION_ADAPTER,
    ContentPlacement,
    DirectTaskCondition,
    ForgedAuthorizationCondition,
    IndirectContentCondition,
    ParameterSourceManipulationCondition,
    SemanticParameterKind,
)
from sandbox.scenarios.office_v2.attack_objectives import ATTACK_OBJECTIVES
from sandbox.scenarios.office_v2.attack_surface import REACHABLE_ATTACK_SURFACES
from sandbox.scenarios.office_v2.clean_cases import CLEAN_CASES
from sandbox.scenarios.office_v2.models import (
    ActionKind,
    ActionScope,
    ResourceKind,
    ResourceRef,
)

ZERO = "sha256:" + "0" * 64


def _finish(model: type, payload: dict[str, object]):
    draft = model.model_construct(**payload, content_digest=ZERO)
    return model(**payload, content_digest=sha256_digest(draft.digest_payload()))


def test_four_condition_branches_round_trip_through_the_discriminator() -> None:
    case = CLEAN_CASES[0]
    surface = REACHABLE_ATTACK_SURFACES[0]
    first, second = surface.fields[:2]
    first_placement = ContentPlacement(resource_ref=first.resource_ref, field_path=first.field_path)
    second_placement = ContentPlacement(
        resource_ref=second.resource_ref, field_path=second.field_path
    )
    common = {
        "condition_id": "condition.contract-test",
        "template_id": "template.contract-test",
        "objective_id": ATTACK_OBJECTIVES[0].objective_id,
        "parent_case_id": case.case_id,
        "expression_digest": ZERO,
    }
    direct = _finish(
        DirectTaskCondition,
        {
            **common,
            "instruction_variant": "Perform the requested operation.",
            "issuer_authentication": case.task.issuer_authentication,
            "inherited_task_digest": case.task.canonical_digest(),
            "derived_task_digest": ZERO,
        },
    )
    indirect = _finish(
        IndirectContentCondition,
        {
            **common,
            "placements": (first_placement,),
            "operation": first.compatible_operations[0],
            "adversarial_content": "Controlled evaluation instruction.",
            "original_value_digests": (ZERO,),
            "reachable_surface_digest": surface.surface_digest,
        },
    )
    forged = _finish(
        ForgedAuthorizationCondition,
        {
            **common,
            "claim_placements": (first_placement,),
            "claimed_action_scope": ActionScope(
                action=ActionKind.SHARE, resource_kinds=(ResourceKind.DRIVE_FILE,)
            ),
            "claimed_resource_refs": (first.resource_ref,),
            "authoritative_absence_assertions": ("authority.no-active-grant",),
            "original_value_digests": (ZERO,),
            "reachable_surface_digest": surface.surface_digest,
        },
    )
    parameter = _finish(
        ParameterSourceManipulationCondition,
        {
            **common,
            "source_placement": first_placement,
            "semantic_parameter": SemanticParameterKind.RESOURCE_REFERENCE,
            "original_value": first.resource_ref,
            "visible_value": ResourceRef(
                kind=ResourceKind.WORKSPACE_FILE,
                resource_id="/workspace/contract-test.txt",
            ),
            "original_value_digest": sha256_digest(first.resource_ref),
            "visible_value_digest": sha256_digest(
                ResourceRef(
                    kind=ResourceKind.WORKSPACE_FILE,
                    resource_id="/workspace/contract-test.txt",
                )
            ),
            "verification_sources": (second_placement,),
            "reachable_surface_digest": surface.surface_digest,
        },
    )

    for condition in (direct, indirect, forged, parameter):
        restored = ADVERSARIAL_CONDITION_ADAPTER.validate_json(condition.model_dump_json())
        assert type(restored) is type(condition)
        assert restored == condition


def test_contracts_reject_unknown_fields_cycles_and_tampering() -> None:
    payload = ATTACK_OBJECTIVES[0].model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        type(ATTACK_OBJECTIVES[0]).model_validate(payload)

    tampered = deepcopy(ATTACK_OBJECTIVES[0].model_dump(mode="json"))
    tampered["title"] = "Changed without re-signing"
    with pytest.raises(ValidationError, match="content_digest"):
        type(ATTACK_OBJECTIVES[0]).model_validate(tampered)

    cyclic = deepcopy(ATTACK_OBJECTIVES[0].model_dump(mode="json"))
    milestones = cyclic["milestone_graph"]["milestones"]
    milestones[0]["depends_on"] = [milestones[-1]["milestone_id"]]
    with pytest.raises(ValidationError, match="DAG"):
        type(ATTACK_OBJECTIVES[0]).model_validate(cyclic)
