from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

from sandbox.replay.digests import sha256_bytes, sha256_digest

ROOT = Path(__file__).resolve().parents[1]

V1_MODULE_PREFIXES = (
    "sandbox.scenarios.candidate_generation",
    "sandbox.scenarios.office_campaign",
    "sandbox.scenarios.office_controls",
    "sandbox.scenarios.office_docker_mutator",
    "sandbox.scenarios.office_episode",
    "sandbox.scenarios.office_fork",
    "sandbox.scenarios.office_matrix",
    "sandbox.scenarios.office_mutation",
    "sandbox.scenarios.office_runtime",
    "sandbox.scenarios.office_v1",
)


def _source_digest(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return tuple(sorted(imported))


def _imports_v1(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in V1_MODULE_PREFIXES
    )


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _v2_import_boundary() -> dict[str, Any]:
    roots = (
        ROOT / "src/sandbox/scenarios/office_v2",
        ROOT / "agent_image/app/office_v2_session.py",
    )
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(root.rglob("*.py")) if root.is_dir() else [root])
    violations = []
    for path in files:
        for module in _imports(path):
            if _imports_v1(module):
                violations.append({"path": _relative(path), "module": module})
    return {
        "passed": not violations,
        "python_file_count": len(files),
        "violations": violations,
    }


def _text_contains(path: Path, text: str) -> bool:
    return text in path.read_text(encoding="utf-8")


def build_stage8_v1_disposition() -> dict[str, Any]:
    cli = ROOT / "src/sandbox/cli.py"
    coverage_input = ROOT / "src/sandbox/coverage/input.py"
    fuzzer_cli = ROOT / "src/sandbox/fuzzer/cli.py"
    agent_dockerfile = ROOT / "agent_image/Dockerfile"
    agent_factory = ROOT / "agent_image/app/adapter/factory.py"
    legacy_tool_bridge = ROOT / "agent_image/app/tools/office_episode.py"

    production_reachability = {
        "public_cli_exposes_office_v2_scenario": {
            "active": _text_contains(cli, 'add_parser("scenario"')
            and _text_contains(cli, "build_office_v2_public_request"),
            "path": _relative(cli),
        },
        "public_cli_exposes_legacy_run": {
            "active": _text_contains(cli, 'add_parser("run"'),
            "path": _relative(cli),
        },
        "public_cli_exposes_legacy_record": {
            "active": _text_contains(cli, 'add_parser("record"'),
            "path": _relative(cli),
        },
        "public_cli_exposes_legacy_coverage": {
            "active": _text_contains(cli, 'add_parser("coverage"'),
            "path": _relative(cli),
        },
        "public_cli_exposes_legacy_mutation": {
            "active": _text_contains(cli, "add_mutation_parser(subparsers)"),
            "path": _relative(cli),
        },
        "public_cli_exposes_legacy_campaign": {
            "active": _text_contains(cli, "add_campaign_parser(subparsers)"),
            "path": _relative(cli),
        },
        "legacy_coverage_implementation_present": {
            "active": coverage_input.is_file(),
            "path": _relative(coverage_input),
        },
        "legacy_campaign_implementation_present": {
            "active": fuzzer_cli.is_file(),
            "path": _relative(fuzzer_cli),
        },
        "agent_image_copies_all_sandbox_sources": {
            "active": _text_contains(agent_dockerfile, "COPY src/sandbox ./sandbox"),
            "path": _relative(agent_dockerfile),
        },
        "legacy_office_tool_bridge_present": {
            "active": legacy_tool_bridge.is_file()
            and _text_contains(legacy_tool_bridge, "OfficeRuntime"),
            "path": _relative(legacy_tool_bridge),
        },
        "formal_v2_factory_route_present": {
            "active": _text_contains(agent_factory, "request.office_v2_execution is not None"),
            "path": _relative(agent_factory),
        },
        "formal_factory_rejects_non_v2_live_request": {
            "active": _text_contains(
                agent_factory, "formal_agent_requires_office_v2"
            ),
            "path": _relative(agent_factory),
        },
    }
    legacy_entry_active = any(
        production_reachability[name]["active"]
        for name in (
            "public_cli_exposes_legacy_run",
            "public_cli_exposes_legacy_record",
            "public_cli_exposes_legacy_coverage",
            "public_cli_exposes_legacy_mutation",
            "public_cli_exposes_legacy_campaign",
        )
    ) or not production_reachability[
        "formal_factory_rejects_non_v2_live_request"
    ]["active"]

    preconditions = {
        "v2_deterministic_story_evidence": {
            "met": True,
            "source": "stage8-story-freeze and stage8 Docker index",
        },
        "v2_recording_strict_replay": {
            "met": True,
            "source": "stage7-9 evidence and stage8 Docker index",
        },
        "public_cli_exposes_v2_scenario_only": {
            "met": production_reachability["public_cli_exposes_office_v2_scenario"][
                "active"
            ]
            and not legacy_entry_active,
            "source": _relative(cli),
        },
        "formal_live_runtime_requires_v2": {
            "met": production_reachability[
                "formal_factory_rejects_non_v2_live_request"
            ]["active"],
            "source": _relative(agent_factory),
        },
        "legacy_implementation_retained_without_formal_entry": {
            "met": production_reachability["legacy_office_tool_bridge_present"][
                "active"
            ]
            and production_reachability["legacy_coverage_implementation_present"][
                "active"
            ],
            "source": "retained source and protected historical assets",
        },
    }
    formal_entry_disabled = all(item["met"] for item in preconditions.values())
    disposition = "formal_v1_entry_disabled" if formal_entry_disabled else "blocked"
    assets = (
        {
            "asset": "Office V2 package, execution envelope, Oracle and container session",
            "classification": "v2_production_core",
            "action": "retain",
        },
        {
            "asset": "candidate_generation.py and office_campaign_*/office_mutation*",
            "classification": "legacy_implementation_without_public_entry",
            "action": "retain_not_publicly_routable",
        },
        {
            "asset": "office_v1.py, office_matrix.py, office_runtime.py and controls",
            "classification": "legacy_fixture_and_runtime",
            "action": "retain_not_publicly_routable",
        },
        {
            "asset": "historical recordings, manifests, reports and server downloads",
            "classification": "protected_history",
            "action": "retain_read_only",
        },
        {
            "asset": "generic Docker scheduler, TRACE, replay, lifecycle and integrity tests",
            "classification": "shared_contract_regression",
            "action": "retain",
        },
    )
    source_paths = (
        cli,
        coverage_input,
        fuzzer_cli,
        agent_dockerfile,
        agent_factory,
        legacy_tool_bridge,
    )
    payload: dict[str, Any] = {
        "schema_version": "office-v2-stage8-v1-disposition-v2",
        "audit_kind": "static_production_reachability",
        "status": disposition,
        "deletion_performed": False,
        "formal_entry_disabled": formal_entry_disabled,
        "v2_import_boundary": _v2_import_boundary(),
        "legacy_production_entry_active": legacy_entry_active,
        "production_reachability": production_reachability,
        "formal_entry_preconditions": preconditions,
        "asset_disposition": list(assets),
        "source_digests": {_relative(path): _source_digest(path) for path in source_paths},
        "next_required_change": (
            "Begin V2 CoverageInput and mutation-space work after the Office V2 "
            "scenario freeze."
        ),
        "limitations": {
            "static_import_and_text_audit_only": True,
            "docker_run_performed": False,
            "real_qwen_run_performed": False,
            "historical_assets_modified": False,
            "legacy_source_deleted": False,
            "coverage_and_mutation_ready": False,
        },
    }
    validate_stage8_v1_disposition(payload)
    payload["evidence_digest"] = sha256_digest(payload)
    return payload


def validate_stage8_v1_disposition(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "office-v2-stage8-v1-disposition-v2":
        raise ValueError("Stage 8 V1 disposition schema is invalid")
    if payload.get("v2_import_boundary", {}).get("passed") is not True:
        raise ValueError("Office V2 imports legacy Office modules")
    preconditions = payload.get("formal_entry_preconditions")
    if not isinstance(preconditions, dict) or not preconditions:
        raise ValueError("formal entry preconditions are missing")
    expected_allowed = all(item.get("met") is True for item in preconditions.values())
    if payload.get("formal_entry_disabled") is not expected_allowed:
        raise ValueError("formal entry decision does not match preconditions")
    expected_status = "formal_v1_entry_disabled" if expected_allowed else "blocked"
    if payload.get("status") != expected_status:
        raise ValueError("disposition status does not match formal entry preconditions")
    if payload.get("deletion_performed") is not False:
        raise ValueError("audit must not claim a deletion")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check is not None:
        payload = json.loads(args.check.read_text(encoding="utf-8"))
        digest = payload.pop("evidence_digest")
        validate_stage8_v1_disposition(payload)
        if sha256_digest(payload) != digest:
            raise ValueError("Stage 8 V1 disposition digest is invalid")
        print(digest)
        return 0
    payload = build_stage8_v1_disposition()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
