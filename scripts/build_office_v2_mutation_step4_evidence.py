"""Build deterministic acceptance evidence for Office V2 mutation step 4."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sandbox.fuzzer.v2_mutation_identity import build_v2_mutation_identity_lock
from sandbox.mutation.v2_brief import (
    V2_MUTATION_PROMPT_IDENTITY_DIGEST,
    V2_MUTATION_RESPONSE_SCHEMA_DIGEST,
)
from sandbox.mutation.v2_contracts import build_v2_mutation_field_registry
from sandbox.mutation.v2_policy import FeedbackGapKind, OperatorFamily
from sandbox.mutation.v2_provider import ProviderFailureClass
from sandbox.replay.digests import sha256_digest

DEFAULT_OUTPUT = Path(
    "reports/local-acceptance/office-v2-mutation-step4/step4-evidence.json"
)


def build_evidence() -> dict[str, object]:
    identity = build_v2_mutation_identity_lock()
    registry = build_v2_mutation_field_registry()
    payload: dict[str, object] = {
        "evidence_version": "office-v2-mutation-step4-evidence-v1",
        "mutation_identity_digest": identity.identity_digest,
        "campaign_identity_digest": identity.campaign_identity_digest,
        "field_registry": {
            "registry_digest": registry.registry_digest,
            "classified_field_count": len(registry.rules),
            "field_classes": tuple(
                sorted({item.field_class.value for item in registry.rules})
            ),
            "provider_writable_paths": tuple(
                item.field_path
                for item in registry.rules
                if item.authority.value == "provider_text"
            ),
        },
        "provider_boundary": {
            "candidate_count": 1,
            "ordinary_slot_count": 1,
            "composition_may_use_multiple_slots": True,
            "prompt_identity_digest": V2_MUTATION_PROMPT_IDENTITY_DIGEST,
            "response_schema_digest": V2_MUTATION_RESPONSE_SCHEMA_DIGEST,
            "structural_objective_preserved": True,
            "semantic_preservation": "unverified",
        },
        "feedback_policy": {
            "gap_kind_count": len(FeedbackGapKind),
            "operator_family_count": len(OperatorFamily),
            "deterministic": True,
            "no_compatible_operator_is_explicit": True,
        },
        "validation": {
            "host_validation_layer_count": 14,
            "exact_duplicate": "reject",
            "near_duplicate": "audit-and-deprioritize",
            "coverage_prediction": False,
        },
        "persistence": {
            "store": "V2CampaignStore",
            "preparation_separate_from_candidate_work": True,
            "ready_reopens_with_same_digest": True,
            "step4_creates_episode_work": False,
        },
        "provider_errors": {
            "failure_classes": tuple(sorted(item.value for item in ProviderFailureClass)),
            "transient_only_bounded_retry": True,
            "ambiguous_or_unknown_pauses": True,
        },
        "evidence_limits": {
            "engineering_contract_only": True,
            "agent_executed": False,
            "semantic_quality_proven": False,
            "coverage_gain_proven": False,
        },
        "prohibited_runtime_used": {
            "docker": False,
            "real_ollama": False,
            "qwen": False,
            "judge": False,
            "full_pytest": False,
            "stage_2_to_8_rebuild": False,
        },
    }
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def _render(evidence: dict[str, object]) -> str:
    return json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = _render(build_evidence())
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Office V2 mutation step 4 evidence differs")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
