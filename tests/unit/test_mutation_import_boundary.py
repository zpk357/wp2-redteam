from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_v2_mutator_contract_import_does_not_require_docker_sdk() -> None:
    script = r"""
import builtins

real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "docker" or name.startswith("docker."):
        raise ModuleNotFoundError("docker deliberately unavailable")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
from sandbox.mutation.v2_brief import MinimalFactBrief
from sandbox.mutation import MutationConfig

assert MinimalFactBrief.__name__ == "MinimalFactBrief"
assert MutationConfig.__name__ == "MutationConfig"
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_mutator_repair_layer_carries_the_import_boundary() -> None:
    dockerfile = Path("agent_image/Dockerfile.qwen-mutator-repair").read_text(
        encoding="utf-8"
    )

    assert "# syntax=" not in dockerfile
    assert (
        "MUTATOR_BASE_IMAGE=trace-g-office-v2-mutator-qwen:step6-baseline-20260819"
        in dockerfile
    )
    assert "src/sandbox/mutation/__init__.py" in dockerfile
    assert "src/sandbox/coverage/__init__.py" in dockerfile


def test_stage6_server_scripts_resolve_images_from_active_lock() -> None:
    preflight = Path("scripts/server_preflight_office_v2_step6.sh").read_text(
        encoding="utf-8"
    )
    run = Path("scripts/server_run_office_v2_step6.sh").read_text(encoding="utf-8")

    for script in (preflight, run):
        assert "LOCK_IMAGES" in script
        assert "step6-local" not in script
        assert 'TRACE_G_CONTROLLER_IMAGE="$CONTROLLER_IMAGE"' in script


def test_repair_kit_has_an_executable_server_application_path() -> None:
    installer = Path("scripts/server_apply_office_v2_step6_repair.sh").read_text(
        encoding="utf-8"
    )

    assert "sha256sum -c SHA256SUMS" in installer
    assert "docker build --network none" in installer
    assert "build_office_v2_stage6_repair_lock.py\" seal" in installer
    assert "stage6-repair-application.json" in installer
