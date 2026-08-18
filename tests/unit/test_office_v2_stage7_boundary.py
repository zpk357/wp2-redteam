from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from sandbox.protocol import ExecutionBackend, TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_AGENT_CONTEXT_VERSION,
    OFFICE_V2_AGENT_SURFACE_VERSION,
    OFFICE_V2_ATTACK_CASE_CATALOG_VERSION,
    OFFICE_V2_ATTACK_CONTRACT_VERSION,
    OFFICE_V2_ATTACK_FIELD_REGISTRY_VERSION,
    OFFICE_V2_ATTACK_MATERIALIZER_VERSION,
    OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION,
    OFFICE_V2_CANONICAL_JSON_VERSION,
    OFFICE_V2_CANONICAL_WORLD_ID,
    OFFICE_V2_CONTRACT_SCHEMA_VERSION,
    OFFICE_V2_INTERACTION_SESSION_VERSION,
    OFFICE_V2_ORACLE_CONTRACT_VERSION,
    OFFICE_V2_ORACLE_EVIDENCE_VERSION,
    OFFICE_V2_REACHABILITY_VERSION,
    OFFICE_V2_TASK_CATALOG_VERSION,
    OFFICE_V2_TOOL_CATALOG_VERSION,
    OFFICE_V2_TOOL_CONTRACT_VERSION,
)
from sandbox.versions import AGENT_VERSION, GRAPH_VERSION

ROOT = Path(__file__).parents[2]
OFFICE_V2_PACKAGE = ROOT / "src" / "sandbox" / "scenarios" / "office_v2"
FROZEN_EVIDENCE_DIGESTS = {
    2: "sha256:fce39b28f536bd7d538e08d12c0b8796894b8959ae62ee5c6cd9a076f517b291",
    3: "sha256:7840411d116cffb17206708332edc7a57af9bf3f28b23f993d4a169e7d03b06c",
    4: "sha256:022763e692d764ff0e9045da242bf1272074142e9f498afd5917e83a68788077",
    5: "sha256:b44931cdd4e7e2173771f2b356860dc937fb253ff4a0fce8cf9a2f77bb50fe04",
    6: "sha256:f6cf9bc01fe70ee2372ebae6435f9c4286fa4d456e43dc13c60e6644fff7a740",
}
FROZEN_OFFICE_V2_VERSIONS = {
    "agent_context": "office-v2-agent-context-v1",
    "agent_surface": "office-v2-agent-surface-v1",
    "attack_case_catalog": "office-v2-attack-cases-v1",
    "attack_contract": "office-v2-attack-contract-v1",
    "attack_field_registry": "office-v2-attack-fields-v1",
    "attack_materializer": "office-v2-attack-materializer-v1",
    "attack_objectives": "office-v2-attack-objectives-v1.1",
    "canonical_json": "canonical-json-v1",
    "canonical_world": "office-world-v2.0",
    "contract_schema": "office-v2.0",
    "interaction_session": "office-v2-interaction-session-v1",
    "oracle_contract": "office-v2-oracle-contract-v1",
    "oracle_evidence": "office-v2-oracle-evidence-v1",
    "reachability": "office-v2-reachability-v1",
    "task_catalog": "office-v2-task-catalog-v1",
    "tool_catalog": "office-v2-tool-catalog-v1",
    "tool_contract": "office-v2-tools-1.1",
}
ALLOWED_EXTERNAL_OFFICE_V2_IMPORTERS = {
    Path("agent_image/app/adapter/langgraph_react_runtime.py"),
    Path("agent_image/app/agent/react_contract.py"),
    Path("agent_image/app/office_v2_session.py"),
    Path("src/sandbox/agent_prompts.py"),
    Path("src/sandbox/cli.py"),
    Path("src/sandbox/coverage/v2_behavior.py"),
    Path("src/sandbox/coverage/v2_contracts.py"),
    Path("src/sandbox/coverage/v2_episode_behavior.py"),
    Path("src/sandbox/coverage/v2_episode_coverage.py"),
    Path("src/sandbox/coverage/v2_input.py"),
    Path("src/sandbox/coverage/v2_risk_catalog.py"),
    Path("src/sandbox/coverage/v2_risk_coverage.py"),
    Path("src/sandbox/coverage/v2_tool_behavior.py"),
    Path("src/sandbox/coverage/v2_unexpected_risk.py"),
    Path("src/sandbox/tool_contracts.py"),
}
FORBIDDEN_OFFICE_V2_IMPORT_PREFIXES = (
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
    path = (
        ROOT
        / "reports"
        / "local-acceptance"
        / f"office-v2-stage{stage}"
        / f"stage{stage}-evidence.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
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


def test_stage2_through_stage6_evidence_digests_are_frozen() -> None:
    for stage, expected in FROZEN_EVIDENCE_DIGESTS.items():
        payload, recorded = _load_evidence(stage)
        assert sha256_digest(payload) == recorded == expected


def test_stage7_starts_from_one_execution_and_trace_identity() -> None:
    assert tuple(ExecutionBackend) == (ExecutionBackend.TRACE_REACT_V2,)
    assert ExecutionBackend.TRACE_REACT_V2.value == "trace_react_v2"
    assert AGENT_VERSION == GRAPH_VERSION == "trace-react-v2"
    assert TraceEvent.model_fields["schema_version"].default == "1.2"


def test_office_v2_contract_versions_are_frozen_before_runtime_connection() -> None:
    actual = {
        "agent_context": OFFICE_V2_AGENT_CONTEXT_VERSION,
        "agent_surface": OFFICE_V2_AGENT_SURFACE_VERSION,
        "attack_case_catalog": OFFICE_V2_ATTACK_CASE_CATALOG_VERSION,
        "attack_contract": OFFICE_V2_ATTACK_CONTRACT_VERSION,
        "attack_field_registry": OFFICE_V2_ATTACK_FIELD_REGISTRY_VERSION,
        "attack_materializer": OFFICE_V2_ATTACK_MATERIALIZER_VERSION,
        "attack_objectives": OFFICE_V2_ATTACK_OBJECTIVE_CATALOG_VERSION,
        "canonical_json": OFFICE_V2_CANONICAL_JSON_VERSION,
        "canonical_world": OFFICE_V2_CANONICAL_WORLD_ID,
        "contract_schema": OFFICE_V2_CONTRACT_SCHEMA_VERSION,
        "interaction_session": OFFICE_V2_INTERACTION_SESSION_VERSION,
        "oracle_contract": OFFICE_V2_ORACLE_CONTRACT_VERSION,
        "oracle_evidence": OFFICE_V2_ORACLE_EVIDENCE_VERSION,
        "reachability": OFFICE_V2_REACHABILITY_VERSION,
        "task_catalog": OFFICE_V2_TASK_CATALOG_VERSION,
        "tool_catalog": OFFICE_V2_TOOL_CATALOG_VERSION,
        "tool_contract": OFFICE_V2_TOOL_CONTRACT_VERSION,
    }
    assert actual == FROZEN_OFFICE_V2_VERSIONS


def test_external_office_v2_imports_match_the_stage7_runtime_boundary() -> None:
    importers: set[Path] = set()
    for root in (ROOT / "agent_image" / "app", ROOT / "src" / "sandbox"):
        for path in root.rglob("*.py"):
            if path.is_relative_to(OFFICE_V2_PACKAGE):
                continue
            if any(
                target.startswith("sandbox.scenarios.office_v2")
                for target in _imports(path)
            ):
                importers.add(path.relative_to(ROOT))
    assert importers == ALLOWED_EXTERNAL_OFFICE_V2_IMPORTERS


def test_office_v2_core_cannot_import_execution_or_legacy_layers() -> None:
    violations = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path in OFFICE_V2_PACKAGE.rglob("*.py")
        for target in sorted(_imports(path))
        if target.startswith(FORBIDDEN_OFFICE_V2_IMPORT_PREFIXES)
    ]
    assert violations == []


def test_formal_runtime_routes_only_v2_live_requests() -> None:
    runtime_source = (
        ROOT / "agent_image" / "app" / "adapter" / "langgraph_react_runtime.py"
    ).read_text(encoding="utf-8")
    factory_source = (
        ROOT / "agent_image" / "app" / "adapter" / "factory.py"
    ).read_text(encoding="utf-8")
    assert "request.office_v2_execution is not None" in runtime_source
    assert "load_office_v2_session" in runtime_source
    assert "base_registry.enable_office_episode" in runtime_source
    assert "self._session_surface or self.v1_session_surface(base_registry)" in runtime_source
    assert "v2_requires_formal_agent_runtime" in factory_source
    assert "formal_agent_requires_office_v2" in factory_source


def test_retired_research_runtimes_are_not_dependencies() -> None:
    dependency_text = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8").lower()
        for relative in (
            "pyproject.toml",
            "agent_image/requirements.txt",
            "agent_image/requirements.agent-qwen.lock",
            "agent_image/requirements.langgraph.lock",
        )
    )
    assert "inspect-ai" not in dependency_text
    assert "inspect_ai" not in dependency_text
    assert "inspect-evals" not in dependency_text
    assert "inspect_evals" not in dependency_text
    assert "agentdojo" not in dependency_text
