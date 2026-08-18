from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_ORACLE_CONTRACT_VERSION,
    OFFICE_V2_ORACLE_EVIDENCE_VERSION,
)

ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "src" / "sandbox" / "scenarios" / "office_v2"
EVIDENCE_PATHS = {
    stage: ROOT
    / "reports"
    / "local-acceptance"
    / f"office-v2-stage{stage}"
    / f"stage{stage}-evidence.json"
    for stage in range(2, 6)
}
FROZEN_EVIDENCE_DIGESTS = {
    2: "sha256:fce39b28f536bd7d538e08d12c0b8796894b8959ae62ee5c6cd9a076f517b291",
    3: "sha256:7840411d116cffb17206708332edc7a57af9bf3f28b23f993d4a169e7d03b06c",
    4: "sha256:022763e692d764ff0e9045da242bf1272074142e9f498afd5917e83a68788077",
    5: "sha256:b44931cdd4e7e2173771f2b356860dc937fb253ff4a0fce8cf9a2f77bb50fe04",
}
FROZEN_STAGE3_IDENTITIES = {
    "clean_case_catalog_digest": (
        "sha256:fd06d46b562433f8a513192c5dc7299838c57205e131cf43643c9cc450f0ae06"
    ),
    "task_blueprint_catalog_digest": (
        "sha256:865b1a1d42d9485d3bbf5f990dcfbf0bf6a5ad196ddad414a32354fe5c4a3f00"
    ),
    "tool_spec_digest": "sha256:fe9fdcad58adb09859c92ceb5200901962da81a80a3941753a2edaff47365750",
    "world_digest": "sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106",
}
FROZEN_STAGE4_IDENTITIES = {
    "agent_context_version": "office-v2-agent-context-v1",
    "agent_surface_version": "office-v2-agent-surface-v1",
    "interaction_session_version": "office-v2-interaction-session-v1",
    "prompt_base_digest": "sha256:8c476f491dfd7fc1aeddf4d75c008c4222e0f5af2dd8682ab7586d347d6b2382",
    "stage3_evidence_digest": FROZEN_EVIDENCE_DIGESTS[3],
    "tool_spec_digest": FROZEN_STAGE3_IDENTITIES["tool_spec_digest"],
    "trace_schema_version": "1.2",
    "world_digest": FROZEN_STAGE3_IDENTITIES["world_digest"],
}
FROZEN_STAGE5_IDENTITIES = {
    "attack_contract_version": "office-v2-attack-contract-v1",
    "case_catalog_version": "office-v2-attack-cases-v1",
    "field_registry_digest": (
        "sha256:6d0b767539f6ddff8325e6c3e3de27dcbd8d0c07b9c74c4ca002c8e0183e2f3c"
    ),
    "field_registry_version": "office-v2-attack-fields-v1",
    "materializer_version": "office-v2-attack-materializer-v1",
    "objective_catalog_digest": (
        "sha256:b873a57e976204fdf754d97eb41c604f305f203d6ed8a0a8c0409e1733075215"
    ),
    "objective_catalog_version": "office-v2-attack-objectives-v1.1",
    "reachability_version": "office-v2-reachability-v1",
    "stage4_evidence_digest": FROZEN_EVIDENCE_DIGESTS[4],
    "surface_catalog_digest": (
        "sha256:5a96b552c58edf8e6d00095337039a8a81402d46de4778211cd6e88361f64519"
    ),
    "world_digest": FROZEN_STAGE3_IDENTITIES["world_digest"],
}
APPROVED_STAGE6_FILES = {
    "oracle.py",
    "oracle_evidence.py",
    "oracle_models.py",
    "oracle_trace.py",
    "security_oracle.py",
    "utility_oracle.py",
}
FORBIDDEN_PREFIXES = (
    "agent_image",
    "sandbox.coverage",
    "sandbox.engine",
    "sandbox.fuzzer",
    "sandbox.judge",
    "sandbox.mutation",
    "sandbox.scheduler",
    "sandbox.scenarios.office_campaign",
    "sandbox.scenarios.office_controls",
    "sandbox.scenarios.office_episode",
    "sandbox.scenarios.office_matrix",
    "sandbox.scenarios.office_mutation",
    "sandbox.scenarios.office_runtime",
    "sandbox.scenarios.office_v1",
)


def _load_evidence(stage: int) -> tuple[dict[str, Any], str]:
    payload = json.loads(EVIDENCE_PATHS[stage].read_text(encoding="utf-8"))
    digest = payload.pop("evidence_digest")
    return payload, digest


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def _selected(mapping: dict[str, Any], expected: dict[str, str]) -> dict[str, Any]:
    return {key: mapping[key] for key in expected}


def test_stage6_versions_are_frozen_before_oracle_implementation() -> None:
    assert OFFICE_V2_ORACLE_CONTRACT_VERSION == "office-v2-oracle-contract-v1"
    assert OFFICE_V2_ORACLE_EVIDENCE_VERSION == "office-v2-oracle-evidence-v1"


def test_stage2_through_stage5_evidence_chain_is_frozen() -> None:
    evidence = {stage: _load_evidence(stage) for stage in EVIDENCE_PATHS}

    for stage, (payload, digest) in evidence.items():
        assert sha256_digest(payload) == digest == FROZEN_EVIDENCE_DIGESTS[stage]

    assert _selected(evidence[3][0]["identity"], FROZEN_STAGE3_IDENTITIES) == (
        FROZEN_STAGE3_IDENTITIES
    )
    assert _selected(evidence[4][0]["identity"], FROZEN_STAGE4_IDENTITIES) == (
        FROZEN_STAGE4_IDENTITIES
    )
    assert evidence[5][0]["identity"] == FROZEN_STAGE5_IDENTITIES
    assert evidence[5][0]["limitations"]["stage6_oracle_used"] is False


def test_stage6_files_are_approved_and_cannot_import_forbidden_layers() -> None:
    stage6_files = tuple(
        sorted(
            (path for path in PACKAGE.glob("*oracle*.py") if path.name != "__init__.py"),
            key=lambda path: path.name,
        )
    )
    assert {path.name for path in stage6_files} <= APPROVED_STAGE6_FILES

    violations = [
        f"{path.name} -> {target}"
        for path in stage6_files
        for target in sorted(_imports(path))
        if target.startswith(FORBIDDEN_PREFIXES)
    ]
    assert violations == []
