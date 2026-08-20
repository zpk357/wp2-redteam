"""Deterministic identity for the installed Stage 6 Controller source tree."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from sandbox.replay.digests import sha256_digest

_EXCLUDED_TOP_LEVEL = {
    ".deps",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".trace-g",
    ".trace-g-data",
    "reports",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _source_files(root: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] in _EXCLUDED_TOP_LEVEL or "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError(f"Stage 6 source identity refuses symlink: {relative}")
        if path.is_file():
            files.append(
                {
                    "path": relative.as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    if not files:
        raise ValueError("Stage 6 source tree is empty")
    return files


def build_source_tree_identity(*, root: Path, source_revision: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "office-v2-stage6-source-tree-v1",
        "source_revision": source_revision,
        "files": _source_files(root),
    }
    payload["source_tree_digest"] = sha256_digest(payload)
    return payload


def write_source_tree_identity(
    *, root: Path, source_revision: str, output: Path
) -> dict[str, Any]:
    payload = build_source_tree_identity(root=root, source_revision=source_revision)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return payload


def verify_source_tree_identity(*, root: Path, identity: Path) -> dict[str, Any]:
    expected = json.loads(identity.read_text(encoding="utf-8"))
    digest = expected.get("source_tree_digest")
    if digest != sha256_digest(
        {key: value for key, value in expected.items() if key != "source_tree_digest"}
    ):
        raise ValueError("Stage 6 source identity digest differs")
    actual = build_source_tree_identity(
        root=root, source_revision=str(expected.get("source_revision", ""))
    )
    if actual != expected:
        raise ValueError("installed Stage 6 source tree differs from frozen identity")
    return expected


__all__ = [
    "build_source_tree_identity",
    "verify_source_tree_identity",
    "write_source_tree_identity",
]
