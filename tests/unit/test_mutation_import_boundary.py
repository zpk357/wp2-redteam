from __future__ import annotations

import subprocess
import sys


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
