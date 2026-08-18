#!/usr/bin/env python3
"""Stage only evidence referenced by one successful TRACE workspace validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def _contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"unable to load JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def _safe_id(value: object, label: str) -> str:
    text = str(value or "")
    if not IDENTIFIER.fullmatch(text):
        raise SystemExit(f"unsafe {label}: {text!r}")
    return text


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return "sha256:" + checksum.hexdigest()


def _copy_file(source: Path, root: Path, target: Path, *, label: str) -> Path:
    source = source.resolve()
    if not _contained(source, root) or not source.is_file():
        raise SystemExit(f"{label} is missing or outside its store: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def _trajectory_path(raw: object, repository: Path) -> Path:
    if not isinstance(raw, str) or not raw:
        raise SystemExit("trajectory path is missing")
    path = Path(raw)
    return (repository / path).resolve() if not path.is_absolute() else path.resolve()


def _artifact_references(value: Any) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if {"relative_path", "sha256", "size_bytes"} <= value.keys():
            references.append(value)
        for child in value.values():
            references.extend(_artifact_references(child))
    elif isinstance(value, list):
        for child in value:
            references.extend(_artifact_references(child))
    return references


def _json_values(raw: bytes) -> list[Any]:
    values: list[Any] = []
    try:
        values.append(json.loads(raw))
        return values
    except (UnicodeError, ValueError):
        pass
    try:
        for line in raw.splitlines():
            if line.strip():
                values.append(json.loads(line))
    except (UnicodeError, ValueError):
        return []
    return values


def _copy_artifact_closure(
    manifest: dict[str, Any],
    artifact_root: Path,
    output_root: Path,
) -> list[Path]:
    pending = _artifact_references(manifest)
    copied: dict[str, Path] = {}
    while pending:
        reference = pending.pop()
        relative = reference.get("relative_path")
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise SystemExit("artifact reference has an unsafe path")
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise SystemExit("artifact reference has an unsafe path")
        if relative in copied:
            continue
        source = (artifact_root / Path(*parts)).resolve()
        if not _contained(source, artifact_root) or not source.is_file():
            raise SystemExit(f"referenced artifact is missing: {relative}")
        raw = source.read_bytes()
        if len(raw) != reference.get("size_bytes") or _digest(source) != reference.get("sha256"):
            raise SystemExit(f"referenced artifact failed integrity verification: {relative}")
        target = output_root / Path(*parts)
        _copy_file(source, artifact_root, target, label="artifact")
        copied[relative] = target
        for value in _json_values(raw):
            pending.extend(_artifact_references(value))
    return list(copied.values())


def stage_trace_workspace_results(
    campaign_id: str,
    result_dir: Path,
    repository_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    _safe_id(campaign_id, "campaign ID")
    repository = repository_root.resolve()
    result_dir = result_dir.resolve()
    output_dir = output_dir.resolve()
    if not _contained(result_dir, repository) or not result_dir.is_dir():
        raise SystemExit("result directory is missing or outside the repository")
    if not _contained(output_dir, result_dir):
        raise SystemExit("staging output must be inside the result directory")
    if output_dir.exists():
        raise SystemExit("staging output already exists; inspect it before retrying")

    validation = _load_object(result_dir / "validation.json")
    if validation.get("passed") is not True:
        raise SystemExit("TRACE workspace validation is not passing")
    replay_id = _safe_id(validation.get("replay_id"), "replay ID")
    replay_run_id = _safe_id(validation.get("replay_run_id"), "replay run ID")
    record = _load_object(result_dir / "injected-record.json")
    strict = _load_object(result_dir / "injected-strict.json")
    if record.get("replay_id") != replay_id or strict.get("replay_run_id") != replay_run_id:
        raise SystemExit("validation and replay identities do not match")

    trajectory_root = (repository / "data" / "trajectories").resolve()
    replay_root = (repository / "data" / "replays").resolve()
    artifact_root = (repository / "data" / "artifacts").resolve()
    output_dir.mkdir(parents=True)

    copied_trajectories: list[Path] = []
    for variant in ("clean", "injected"):
        run = _load_object(result_dir / f"{variant}-run.json")
        source = _trajectory_path(run.get("trajectory_path"), repository)
        copied_trajectories.append(
            _copy_file(
                source,
                trajectory_root,
                output_dir / "trajectories" / f"{variant}.jsonl",
                label=f"{variant} trajectory",
            )
        )

    replay_source = (replay_root / replay_id).resolve()
    if not _contained(replay_source, replay_root) or not replay_source.is_dir():
        raise SystemExit("referenced replay directory is missing")
    manifest = _load_object(replay_source / "manifest.json")
    if manifest.get("replay_id") != replay_id:
        raise SystemExit("replay manifest identity mismatch")
    run_source = (replay_source / "runs" / replay_run_id).resolve()
    if not _contained(run_source, replay_source) or not run_source.is_dir():
        raise SystemExit("strict replay run directory is missing")
    shutil.copytree(replay_source, output_dir / "replays" / replay_id)

    copied_artifacts = _copy_artifact_closure(
        manifest,
        artifact_root,
        output_dir / "artifacts",
    )
    events_reference = record.get("events")
    if not isinstance(events_reference, dict):
        raise SystemExit("recorded events reference is missing")
    if events_reference != manifest.get("events"):
        raise SystemExit("recorded events reference does not match the stored Manifest")
    events_relative = events_reference.get("relative_path")
    if not isinstance(events_relative, str):
        raise SystemExit("recorded events path is missing")
    recorded_source = (artifact_root / Path(*events_relative.split("/"))).resolve()
    copied_trajectories.append(
        _copy_file(
            recorded_source,
            artifact_root,
            output_dir / "trajectories" / "recorded-injected.jsonl",
            label="recorded trajectory",
        )
    )
    copied_trajectories.append(
        _copy_file(
            run_source / "trajectory.jsonl",
            replay_source,
            output_dir / "trajectories" / "strict-injected.jsonl",
            label="strict replay trajectory",
        )
    )

    files = sorted(path for path in output_dir.rglob("*") if path.is_file())
    staging_manifest = {
        "schema_version": "1.0",
        "campaign_id": campaign_id,
        "replay_id": replay_id,
        "replay_run_id": replay_run_id,
        "trajectory_count": len(copied_trajectories),
        "artifact_count": len(copied_artifacts),
        "files": {
            path.relative_to(output_dir).as_posix(): {
                "bytes": path.stat().st_size,
                "sha256": _digest(path),
            }
            for path in files
        },
    }
    (output_dir / "staging-manifest.json").write_text(
        json.dumps(staging_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return staging_manifest


def main() -> int:
    args = parse_args()
    summary = stage_trace_workspace_results(
        args.campaign_id,
        args.result_dir,
        args.repository_root,
        args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
