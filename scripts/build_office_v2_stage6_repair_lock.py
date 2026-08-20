#!/usr/bin/env python3
"""Build the small-layer delivery identity for Office V2 Stage 6 roles."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from sandbox.replay.digests import sha256_digest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _image_id(reference: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower()


def _role(
    *,
    repository: Path,
    role: str,
    base_reference: str,
    final_reference: str,
    dockerfile: str,
    copied_files: tuple[str, ...],
) -> dict[str, object]:
    return {
        "role": role,
        "base_image_reference": base_reference,
        "base_image_id": _image_id(base_reference),
        "final_image_reference": final_reference,
        "final_image_id": _image_id(final_reference),
        "dockerfile": dockerfile,
        "dockerfile_sha256": _sha256(repository / dockerfile),
        "copied_files": [
            {"path": value, "sha256": _sha256(repository / value)}
            for value in copied_files
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--model-digest", required=True)
    parser.add_argument("--controller-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repository = args.repository.resolve()
    roles = [
        _role(
            repository=repository,
            role="agent",
            base_reference="trace-g-office-v2-agent-qwen:step6-baseline-20260819",
            final_reference="trace-g-office-v2-agent-qwen:step6-repair-core-v2",
            dockerfile="agent_image/Dockerfile.qwen-agent-repair",
            copied_files=(
                "agent_image/app/agent_qwen_bootstrap.py",
                "agent_image/app/replay/replay_adapter.py",
            ),
        ),
        _role(
            repository=repository,
            role="mutator",
            base_reference="trace-g-office-v2-mutator-qwen:step6-baseline-20260819",
            final_reference="trace-g-office-v2-mutator-qwen:step6-repair-core-v2",
            dockerfile="agent_image/Dockerfile.qwen-mutator-repair",
            copied_files=(
                "agent_image/app/agent_qwen_bootstrap.py",
                "agent_image/app/office_v2_mutator_worker.py",
                "src/sandbox/ollama_schema.py",
                "src/sandbox/mutation/__init__.py",
                "src/sandbox/coverage/__init__.py",
            ),
        ),
    ]
    payload = {
        "schema_version": "office-v2-stage6-repair-lock-v1",
        "source_revision": args.revision,
        "source_archive_sha256": _sha256(args.source_archive),
        "source_archive_bytes": args.source_archive.stat().st_size,
        "model_digest": args.model_digest,
        "controller_image_reference": args.controller_image,
        "controller_image_id": _image_id(args.controller_image),
        "roles": roles,
    }
    payload["lock_digest"] = sha256_digest(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(payload["lock_digest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
