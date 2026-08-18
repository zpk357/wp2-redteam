from __future__ import annotations

import ast
from pathlib import Path

from sandbox.scenarios.office_v2 import (
    OFFICE_V2_TASK_CATALOG_VERSION,
    OFFICE_V2_TOOL_CATALOG_VERSION,
    OFFICE_V2_TOOL_CONTRACT_VERSION,
)
from sandbox.scenarios.office_v2.canonical_world import load_canonical_world
from sandbox.scenarios.office_v2.tools import (
    OFFICE_V2_EXCLUDED_TOOL_NAMES,
    OFFICE_V2_TOOL_NAMES,
)

OFFICE_V2_PACKAGE = Path(__file__).parents[2] / "src" / "sandbox" / "scenarios" / "office_v2"
TOOLS_PACKAGE = OFFICE_V2_PACKAGE / "tools"
FROZEN_STAGE2_WORLD_DIGEST = (
    "sha256:004a8214638de88b15ff56bdd5a591edb50392d48365d38606dc582a855ad106"
)
ALLOWED_PYTHON_FILES = {
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
FORBIDDEN_TOOL_DEPENDENCY_PREFIXES = (
    "agent_image",
    "sandbox.agent_prompts",
    "sandbox.coverage",
    "sandbox.engine",
    "sandbox.fuzzer",
    "sandbox.mutation",
    "sandbox.scheduler",
    "sandbox.tool_contracts",
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


def test_stage3_identity_and_tool_catalog_are_frozen() -> None:
    assert OFFICE_V2_TOOL_CONTRACT_VERSION == "office-v2-tools-1.1"
    assert OFFICE_V2_TOOL_CATALOG_VERSION == "office-v2-tool-catalog-v1"
    assert OFFICE_V2_TASK_CATALOG_VERSION == "office-v2-task-catalog-v1"
    assert OFFICE_V2_TOOL_NAMES == (
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
        "list_directory",
        "search_files",
        "read_file",
        "write_file",
    )
    assert OFFICE_V2_EXCLUDED_TOOL_NAMES == (
        "run_command",
        "call_internal_api",
        "read_environment",
        "list_processes",
        "query_database",
        "http_request",
        "retrieve_secret",
    )
    assert len(OFFICE_V2_TOOL_NAMES) == len(set(OFFICE_V2_TOOL_NAMES)) == 17
    assert len(OFFICE_V2_EXCLUDED_TOOL_NAMES) == len(
        set(OFFICE_V2_EXCLUDED_TOOL_NAMES)
    ) == 7
    assert set(OFFICE_V2_TOOL_NAMES).isdisjoint(OFFICE_V2_EXCLUDED_TOOL_NAMES)


def test_stage3_python_files_stay_inside_the_frozen_allowlist() -> None:
    actual = {
        path.relative_to(OFFICE_V2_PACKAGE).as_posix()
        for path in OFFICE_V2_PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
    }

    assert actual <= ALLOWED_PYTHON_FILES
    assert "tools/__init__.py" in actual


def test_stage3_tools_do_not_depend_on_outer_or_legacy_runtime_layers() -> None:
    violations: list[str] = []
    for path in sorted(TOOLS_PACKAGE.rglob("*.py")):
        for target in sorted(_import_targets(path)):
            is_other_scenario = target == "sandbox.scenarios" or (
                target.startswith("sandbox.scenarios.")
                and not target.startswith("sandbox.scenarios.office_v2")
            )
            is_forbidden_layer = target.startswith(FORBIDDEN_TOOL_DEPENDENCY_PREFIXES)
            if is_other_scenario or is_forbidden_layer:
                violations.append(f"{path.relative_to(OFFICE_V2_PACKAGE)} -> {target}")

    assert violations == []


def test_stage3_boundary_does_not_change_the_frozen_stage2_world() -> None:
    assert load_canonical_world().world_digest == FROZEN_STAGE2_WORLD_DIGEST
