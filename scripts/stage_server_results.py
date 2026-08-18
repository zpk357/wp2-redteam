#!/usr/bin/env python3
"""Stage only data referenced by one server Campaign and its Week 2 replay chain."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"required result file is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected a JSON object: {path}")
    return value


def contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def safe_id(value: object, label: str) -> str:
    text = str(value or "")
    if not text or Path(text).name != text or any(char in text for char in ("/", chr(92), ":")):
        raise SystemExit(f"unsafe {label}: {text!r}")
    return text


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def copy_trajectory(raw: str, source_root: Path, target_root: Path) -> Path:
    source = Path(raw)
    source = (Path.cwd() / source).resolve() if not source.is_absolute() else source.resolve()
    if not contained(source, source_root) or not source.is_file():
        raise SystemExit(f"trajectory path is missing or outside its store: {raw}")
    relative = source.relative_to(source_root)
    target = target_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def copy_trajectory_id(
    trajectory_id: object,
    source_root: Path,
    target_root: Path,
) -> list[Path]:
    identifier = safe_id(trajectory_id, "trajectory ID")
    matches = [
        path
        for suffix in (".jsonl", ".jsonl.partial")
        if (path := source_root / f"{identifier}{suffix}").is_file()
    ]
    if not matches:
        raise SystemExit(f"trajectory is missing for ID: {identifier}")
    return [copy_trajectory(str(path), source_root, target_root) for path in matches]


def artifact_references(value: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if {"relative_path", "sha256", "size_bytes"} <= value.keys():
            output.append(value)
        for child in value.values():
            output.extend(artifact_references(child))
    elif isinstance(value, list):
        for child in value:
            output.extend(artifact_references(child))
    return output


def backup_database(source: Path, target: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"required Campaign database is missing: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(target) as target_connection,
    ):
        source_connection.backup(target_connection)
        rows = [str(row[0]) for row in target_connection.execute("PRAGMA integrity_check")]
    if rows != ["ok"]:
        raise SystemExit(f"staged database integrity failure: {target}")


def main() -> int:
    args = parse_args()
    repository = Path.cwd().resolve()
    result_dir = args.result_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not contained(result_dir, repository) or not contained(output_dir, result_dir):
        raise SystemExit("staging paths must remain inside the Campaign result directory")
    if output_dir.exists():
        raise SystemExit(f"staging output already exists; inspect it before retrying: {output_dir}")

    campaign = load_json(result_dir / "campaign-export.json")
    manifest = campaign.get("manifest") or {}
    if manifest.get("campaign_id") != args.campaign_id:
        raise SystemExit("campaign export identity mismatch")
    agent = load_json(result_dir / "agent-run.json")
    week2_dir = result_dir / "weeks-1-5"
    record = load_json(week2_dir / "week2-record.json")
    fork = load_json(week2_dir / "week2-fork.json")

    trajectory_root = (repository / "data" / "trajectories").resolve()
    replay_root = (repository / "data" / "replays").resolve()
    artifact_root = (repository / "data" / "artifacts").resolve()
    staged_trajectories = output_dir / "trajectories"
    staged_replays = output_dir / "replays"
    staged_artifacts = output_dir / "artifacts"
    output_dir.mkdir(parents=True)

    copied_trajectories: set[Path] = set()
    direct_path = agent.get("trajectory_path")
    if direct_path:
        copied_trajectories.add(
            copy_trajectory(str(direct_path), trajectory_root, staged_trajectories)
        )
    for item in campaign.get("work_items") or []:
        if item.get("trajectory_path"):
            copied_trajectories.add(
                copy_trajectory(
                    str(item["trajectory_path"]),
                    trajectory_root,
                    staged_trajectories,
                )
            )
        elif item.get("trajectory_id"):
            copied_trajectories.update(
                copy_trajectory_id(
                    item["trajectory_id"],
                    trajectory_root,
                    staged_trajectories,
                )
            )
    for attempt in campaign.get("work_attempts") or []:
        outcome = attempt.get("outcome") or {}
        if outcome.get("trajectory_path"):
            copied_trajectories.add(
                copy_trajectory(
                    str(outcome["trajectory_path"]),
                    trajectory_root,
                    staged_trajectories,
                )
            )

    replay_queue = [record.get("replay_id"), fork.get("replay_id")]
    copied_replays: set[str] = set()
    copied_artifacts: set[Path] = set()
    while replay_queue:
        replay_id = safe_id(replay_queue.pop(), "replay ID")
        if replay_id in copied_replays:
            continue
        source_dir = (replay_root / replay_id).resolve()
        if not contained(source_dir, replay_root) or not source_dir.is_dir():
            raise SystemExit(f"replay directory is missing: {replay_id}")
        replay_manifest = load_json(source_dir / "manifest.json")
        if replay_manifest.get("replay_id") != replay_id:
            raise SystemExit(f"replay manifest identity mismatch: {replay_id}")
        shutil.copytree(source_dir, staged_replays / replay_id)
        copied_replays.add(replay_id)
        copied_trajectories.update(
            copy_trajectory_id(
                replay_manifest["trajectory_id"],
                trajectory_root,
                staged_trajectories,
            )
        )
        if replay_manifest.get("parent_replay_id"):
            replay_queue.append(replay_manifest["parent_replay_id"])
        for reference in artifact_references(replay_manifest):
            relative = Path(*str(reference["relative_path"]).split("/"))
            source = (artifact_root / relative).resolve()
            if not contained(source, artifact_root) or not source.is_file():
                raise SystemExit(f"replay artifact is missing or unsafe: {relative}")
            if source.stat().st_size != int(reference["size_bytes"]):
                raise SystemExit(f"replay artifact size mismatch: {relative}")
            if digest_file(source) != reference["sha256"]:
                raise SystemExit(f"replay artifact digest mismatch: {relative}")
            target = staged_artifacts / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(source, target)
            copied_artifacts.add(target)

    database_sources = {
        "fuzzer": repository / "data" / "fuzzing" / args.campaign_id / "fuzzer.db",
        "coverage": repository / "data" / "coverage" / args.campaign_id / "coverage.db",
        "mutation": repository / "data" / "mutations" / args.campaign_id / "mutation.db",
    }
    for name, source in database_sources.items():
        backup_database(source, output_dir / "databases" / f"{name}.db")

    files = sorted(path for path in output_dir.rglob("*") if path.is_file())
    staging_manifest = {
        "schema_version": "1.0",
        "campaign_id": args.campaign_id,
        "trajectory_count": len(copied_trajectories),
        "replay_count": len(copied_replays),
        "artifact_count": len(copied_artifacts),
        "files": {
            path.relative_to(output_dir).as_posix(): {
                "bytes": path.stat().st_size,
                "sha256": digest_file(path),
            }
            for path in files
        },
    }
    (output_dir / "staging-manifest.json").write_text(
        json.dumps(staging_manifest, ensure_ascii=False, indent=2) + chr(10),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "campaign_id": args.campaign_id,
                "trajectories": len(copied_trajectories),
                "replays": len(copied_replays),
                "artifacts": len(copied_artifacts),
                "output_dir": str(output_dir),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
