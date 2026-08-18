#!/usr/bin/env python3
"""Validate a self-contained 5.G5 result directory without calling Qwen."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.verify_g5_server_kit import G5KitError, load_lock


class G5ResultError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise G5ResultError(f"unable to load result JSON: {path}") from exc
    if not isinstance(value, dict):
        raise G5ResultError(f"expected JSON object: {path}")
    return value


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_integrity_manifest(root: Path) -> dict[str, Any]:
    excluded = {"result-integrity.json", "validation.json"}
    files = {
        path.relative_to(root).as_posix(): {
            "size_bytes": path.stat().st_size,
            "sha256": _file_digest(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in excluded
    }
    value = {
        "schema_version": "1.0",
        "file_count": len(files),
        "files": files,
    }
    (root / "result-integrity.json").write_text(
        json.dumps(value, indent=2) + "\n", encoding="utf-8"
    )
    return value


def _verify_integrity(root: Path) -> bool:
    manifest = _load(root / "result-integrity.json")
    files = manifest.get("files")
    if not isinstance(files, dict) or manifest.get("file_count") != len(files):
        return False
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, dict):
            return False
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            return False
        if path.stat().st_size != expected.get("size_bytes"):
            return False
        if _file_digest(path) != expected.get("sha256"):
            return False
    return True


def validate(root: Path, lock_path: Path) -> dict[str, Any]:
    root = root.resolve()
    lock = load_lock(lock_path)
    acceptance = _load(root / "acceptance.json")
    host = _load(root / "host-evidence.json")
    launches = acceptance.get("launches")
    checks = {
        "gate": acceptance.get("gate") == "5.G5",
        "image_identity": acceptance.get("image_id") == lock["agent_image"]["image_id"],
        "model_identity": (
            acceptance.get("model_name") == lock["model_name"]
            and acceptance.get("model_digest") == lock["model_digest"]
        ),
        "parent_strict_matched": acceptance.get("parent_strict_status") == "matched",
        "child_strict_matched": acceptance.get("child_strict_status") == "matched",
        "parent_immutable": acceptance.get("parent_immutable") is True,
        "four_launches": isinstance(launches, list) and len(launches) == 4,
        "host_cleanup": (
            host.get("passed") is True
            and host.get("agent_container_residue") == []
            and host.get("workspace_volume_residue") == []
        ),
        "integrity": _verify_integrity(root),
    }
    if isinstance(launches, list):
        modes = [item.get("mode") for item in launches if isinstance(item, dict)]
        checks["launch_modes"] = modes == ["live", "strict_replay", "live", "strict_replay"]
        checks["live_has_ollama"] = all(
            item.get("ollama_process_present") is True
            for item in launches
            if isinstance(item, dict) and item.get("mode") == "live"
        )
        checks["strict_has_no_ollama"] = all(
            item.get("ollama_process_present") is False
            for item in launches
            if isinstance(item, dict) and item.get("mode") == "strict_replay"
        )
        checks["all_removed"] = all(
            item.get("removed") is True for item in launches if isinstance(item, dict)
        )
        checks["isolated"] = all(
            isinstance(item.get("isolation"), dict)
            and item["isolation"].get("network_mode") == "none"
            and item["isolation"].get("read_only_rootfs") is True
            and item["isolation"].get("bind_mount_count") == 0
            and item["isolation"].get("docker_socket_mounted") is False
            and item["isolation"].get("gpu_device_requests")
            == (1 if item.get("mode") == "live" else 0)
            for item in launches
            if isinstance(item, dict)
        )
    failed = sorted(key for key, value in checks.items() if value is not True)
    return {
        "schema_version": "1.0",
        "gate": "5.G5",
        "checks": checks,
        "passed": not failed,
        "failed_checks": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--write-integrity", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.write_integrity:
            write_integrity_manifest(args.result_root.resolve())
        result = validate(args.result_root, args.lock)
    except (G5KitError, G5ResultError, OSError, ValueError) as exc:
        result = {
            "schema_version": "1.0",
            "gate": "5.G5",
            "passed": False,
            "failed_checks": ["validation_input_error"],
            "error": str(exc),
        }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
