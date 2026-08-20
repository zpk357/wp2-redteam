from __future__ import annotations

import ast
import json
from pathlib import Path

from sandbox.agent_prompts import (
    OFFICE_AGENT_SYSTEM_PROMPT_DIGEST,
    OFFICE_AGENT_SYSTEM_PROMPT_VERSION,
)
from sandbox.protocol import TraceEvent
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.office_v2 import (
    OFFICE_V2_AGENT_CONTEXT_VERSION,
    OFFICE_V2_AGENT_SURFACE_VERSION,
    OFFICE_V2_INTERACTION_SESSION_VERSION,
)
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_EXCLUDED_TOOL_NAMES,
    OFFICE_V2_TOOL_NAMES,
)
from sandbox.tool_contracts import (
    OFFICE_SCENARIO_TOOL_SPECS,
    OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
OFFICE_V2_PACKAGE = REPOSITORY_ROOT / "src" / "sandbox" / "scenarios" / "office_v2"
STAGE3_EVIDENCE = (
    REPOSITORY_ROOT / "reports" / "local-acceptance" / "office-v2-stage3" / "stage3-evidence.json"
)
AGENT_CORE_FILES = (
    OFFICE_V2_PACKAGE / "agent_context.py",
    OFFICE_V2_PACKAGE / "agent_api.py",
    OFFICE_V2_PACKAGE / "interaction_session.py",
)
ALLOWED_STAGE4_PYTHON_FILES = {
    "__init__.py",
    "adversarial_conditions.py",
    "agent_api.py",
    "agent_context.py",
    "attack_cases.py",
    "attack_compatibility.py",
    "attack_models.py",
    "attack_objectives.py",
    "attack_surface.py",
    "canonical_world.py",
    "cli_entry.py",
    "clean_cases.py",
    "execution_contracts.py",
    "fork.py",
    "interaction.py",
    "interaction_session.py",
    "models.py",
    "observation.py",
    "oracle.py",
    "oracle_evidence.py",
    "oracle_models.py",
    "oracle_trace.py",
    "policy.py",
    "resolution.py",
    "security_oracle.py",
    "task_catalog.py",
    "tools/__init__.py",
    "tools/calendar.py",
    "tools/contracts.py",
    "tools/drive.py",
    "tools/mail.py",
    "tools/provenance.py",
    "tools/runtime.py",
    "tools/workspace.py",
    "world.py",
    "utility_oracle.py",
}
FORBIDDEN_AGENT_DEPENDENCY_PREFIXES = (
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
FROZEN_UPSTREAM_DIGESTS = {
    "clean_case_catalog_digest": (
        "sha256:fd06d46b562433f8a513192c5dc7299838c57205e131cf43643c9cc450f0ae06"
    ),
    "task_blueprint_catalog_digest": (
        "sha256:865b1a1d42d9485d3bbf5f990dcfbf0bf6a5ad196ddad414a32354fe5c4a3f00"
    ),
    "tool_spec_digest": ("sha256:fe9fdcad58adb09859c92ceb5200901962da81a80a3941753a2edaff47365750"),
    "world_digest": ("sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106"),
}
FROZEN_STAGE3_EVIDENCE_DIGEST = (
    "sha256:7840411d116cffb17206708332edc7a57af9bf3f28b23f993d4a169e7d03b06c"
)
FROZEN_V1_PROMPT_DIGEST = "sha256:92ae83233a88d52241b3c6bfa458e37dfeace167937310f60b36b64ae22cdaf1"
FROZEN_V1_OFFICE_TOOL_DIGEST = (
    "sha256:870bafcb1bd338806a4f78df7611cef60a9af9af5164b4d81c7232ec801134d4"
)


def _import_targets(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets.add(node.module)
            targets.update(f"{node.module}.{alias.name}" for alias in node.names)
    return targets


def test_stage4_identity_and_trace_schema_are_frozen() -> None:
    assert OFFICE_V2_AGENT_SURFACE_VERSION == "office-v2-agent-surface-v1"
    assert OFFICE_V2_AGENT_CONTEXT_VERSION == "office-v2-agent-context-v1"
    assert OFFICE_V2_INTERACTION_SESSION_VERSION == "office-v2-interaction-session-v1"
    assert TraceEvent.model_fields["schema_version"].default == "1.2"


def test_stage4_upstream_evidence_and_five_digests_are_frozen() -> None:
    payload = json.loads(STAGE3_EVIDENCE.read_text(encoding="utf-8"))
    evidence_digest = payload.pop("evidence_digest")

    assert sha256_digest(payload) == evidence_digest == FROZEN_STAGE3_EVIDENCE_DIGEST
    assert {
        key: payload["identity"][key] for key in FROZEN_UPSTREAM_DIGESTS
    } == FROZEN_UPSTREAM_DIGESTS
    assert payload["identity"]["tool_spec_digest"] == (OFFICE_V2_PUBLIC_TOOL_CONTRACT_DIGEST)


def test_stage4_v1_prompt_and_thirteen_office_tools_are_unchanged() -> None:
    assert OFFICE_AGENT_SYSTEM_PROMPT_VERSION == "office-agent-system-prompt-v1"
    assert OFFICE_AGENT_SYSTEM_PROMPT_DIGEST == FROZEN_V1_PROMPT_DIGEST
    assert tuple(spec.name for spec in OFFICE_SCENARIO_TOOL_SPECS) == (
        "search_email",
        "read_email",
        "send_email",
        "search_calendar_events",
        "create_calendar_event",
        "update_calendar_event",
        "cancel_calendar_event",
        "search_drive_files",
        "read_drive_file",
        "create_drive_file",
        "share_drive_file",
        "update_drive_permissions",
        "delete_drive_file",
    )
    assert (
        sha256_digest([spec.public_contract() for spec in OFFICE_SCENARIO_TOOL_SPECS])
        == FROZEN_V1_OFFICE_TOOL_DIGEST
    )


def test_stage4_v2_tool_and_file_boundaries_are_frozen() -> None:
    assert len(OFFICE_V2_TOOL_NAMES) == 17
    assert len(OFFICE_V2_EXCLUDED_TOOL_NAMES) == 7
    assert set(OFFICE_V2_TOOL_NAMES).isdisjoint(OFFICE_V2_EXCLUDED_TOOL_NAMES)

    actual = {
        path.relative_to(OFFICE_V2_PACKAGE).as_posix()
        for path in OFFICE_V2_PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    assert actual <= ALLOWED_STAGE4_PYTHON_FILES


def test_stage4_agent_core_cannot_import_forbidden_layers() -> None:
    violations: list[str] = []
    for path in AGENT_CORE_FILES:
        if not path.exists():
            continue
        for target in sorted(_import_targets(path)):
            is_other_scenario = target == "sandbox.scenarios" or (
                target.startswith("sandbox.scenarios.")
                and not target.startswith("sandbox.scenarios.office_v2")
            )
            is_forbidden_layer = target.startswith(FORBIDDEN_AGENT_DEPENDENCY_PREFIXES)
            if is_other_scenario or is_forbidden_layer:
                violations.append(f"{path.name} -> {target}")

    assert violations == []
