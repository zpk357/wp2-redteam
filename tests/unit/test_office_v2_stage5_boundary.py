from __future__ import annotations

import ast
import json
from pathlib import Path

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_ATTACK_CONTRACT_VERSION,
    OFFICE_V2_ATTACK_FIELD_REGISTRY_VERSION,
    OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION,
    OFFICE_V2_REACHABILITY_VERSION,
)

ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "src" / "sandbox" / "scenarios" / "office_v2"
STAGE4_EVIDENCE = (
    ROOT / "reports" / "local-acceptance" / "office-v2-stage4" / "stage4-evidence.json"
)
STAGE5_FILES = (
    PACKAGE / "adversarial_conditions.py",
    PACKAGE / "attack_cases.py",
    PACKAGE / "attack_compatibility.py",
    PACKAGE / "attack_models.py",
    PACKAGE / "attack_objectives.py",
    PACKAGE / "attack_surface.py",
)
FORBIDDEN_PREFIXES = (
    "agent_image",
    "sandbox.coverage",
    "sandbox.engine",
    "sandbox.fuzzer",
    "sandbox.judge",
    "sandbox.mutation",
    "sandbox.scheduler",
    "sandbox.scenarios.office_v1",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def test_stage5_versions_and_stage4_evidence_are_frozen() -> None:
    assert OFFICE_V2_ATTACK_CONTRACT_VERSION == "office-v2-attack-contract-v1"
    assert OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION == "office-v2-attack-objectives-v1.1"
    assert OFFICE_V2_ATTACK_FIELD_REGISTRY_VERSION == "office-v2-attack-fields-v1"
    assert OFFICE_V2_REACHABILITY_VERSION == "office-v2-reachability-v1"

    payload = json.loads(STAGE4_EVIDENCE.read_text(encoding="utf-8"))
    evidence_digest = payload.pop("evidence_digest")
    assert sha256_digest(payload) == evidence_digest
    assert (
        evidence_digest == "sha256:022763e692d764ff0e9045da242bf1272074142e9f498afd5917e83a68788077"
    )


def test_stage5_core_respects_the_scenario_dependency_boundary() -> None:
    violations = [
        f"{path.name} -> {target}"
        for path in STAGE5_FILES
        for target in _imports(path)
        if target.startswith(FORBIDDEN_PREFIXES)
    ]
    assert violations == []
