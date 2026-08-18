#!/usr/bin/env python3
"""Verify Campaign databases and create an internal result-file integrity manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

CAMPAIGN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--include", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def inspect_database(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise SystemExit(f"required database is missing: {path}")
    connection = sqlite3.connect(path)
    try:
        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            raise SystemExit(f"database integrity failure for {path}: {integrity_rows}")
        checkpoint = list(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)"))[0]
    finally:
        connection.close()
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest_file(path),
        "integrity_check": "ok",
        "wal_checkpoint": list(checkpoint),
    }


def main() -> int:
    args = parse_args()
    if not CAMPAIGN_PATTERN.fullmatch(args.campaign_id):
        raise SystemExit("invalid campaign ID")
    repository = Path.cwd().resolve()
    result_dir = args.result_dir.resolve()
    output = args.output.resolve()
    if not contained(result_dir, repository) or not contained(output, repository):
        raise SystemExit("result paths must remain inside the repository")
    candidate_manifest = result_dir / "learning" / "golden-set-candidate-manifest.json"
    if not candidate_manifest.is_file():
        raise SystemExit("unlabeled golden-set candidate manifest is missing")
    candidate_metadata = json.loads(candidate_manifest.read_text(encoding="utf-8"))
    if candidate_metadata.get("campaign_id") != args.campaign_id:
        raise SystemExit("candidate manifest campaign identity mismatch")
    if candidate_metadata.get("is_golden_set") is not False:
        raise SystemExit("server output must remain an unlabeled candidate pool")

    database_paths = {
        "fuzzer": Path("data/fuzzing") / args.campaign_id / "fuzzer.db",
        "coverage": Path("data/coverage") / args.campaign_id / "coverage.db",
        "mutation": Path("data/mutations") / args.campaign_id / "mutation.db",
    }
    databases = {name: inspect_database(path.resolve()) for name, path in database_paths.items()}

    roots = [result_dir, *(path.resolve() for path in args.include)]
    files: dict[str, dict[str, object]] = {}
    for root in roots:
        if not contained(root, repository):
            raise SystemExit(f"include path escapes repository: {root}")
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved == output:
                continue
            relative = resolved.relative_to(repository).as_posix()
            files[relative] = {
                "bytes": resolved.stat().st_size,
                "sha256": digest_file(resolved),
            }
    partials = sorted(path for path in files if path.endswith(".jsonl.partial"))
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "campaign_id": args.campaign_id,
        "database_integrity": databases,
        "file_count": len(files),
        "total_bytes": sum(int(item["bytes"]) for item in files.values()),
        "partial_trajectory_count": len(partials),
        "partial_trajectories": partials,
        "files": dict(sorted(files.items())),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + chr(10),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "campaign_id": args.campaign_id,
                "file_count": len(files),
                "database_integrity": "ok",
                "output": str(output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
