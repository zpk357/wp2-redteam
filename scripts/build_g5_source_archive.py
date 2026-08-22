#!/usr/bin/env python3
"""Build a G5 source archive from Git-visible files without scanning ignored ACL dirs."""

from __future__ import annotations

import argparse
import io
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

EXCLUDED_ROOTS = {
    ".deps",
    ".git",
    ".trace-g",
    ".venv",
    "data",
    "reports",
}
SECRET_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def allowed_source_path(value: str) -> bool:
    path = PurePosixPath(value)
    parts = path.parts
    if not parts or path.is_absolute() or ".." in parts:
        return False
    if parts[0] in EXCLUDED_ROOTS or parts[0].startswith(".venv-"):
        return False
    if parts[0].startswith(".pytest-tmp-"):
        return False
    if any(part in {".pytest_cache", ".ruff_cache", "__pycache__"} for part in parts):
        return False
    if path.name in {".env", "authorized_keys"}:
        return False
    if path.name.startswith(("id_rsa", "id_ed25519")):
        return False
    return path.suffix.casefold() not in SECRET_SUFFIXES | DATABASE_SUFFIXES


def git_visible_files(repository: Path, revision: str = "HEAD") -> list[str]:
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", revision],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    return sorted(
        value.decode("utf-8")
        for value in result.stdout.split(b"\0")
        if value and allowed_source_path(value.decode("utf-8"))
    )


def git_blob(repository: Path, revision: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout


def build_archive(repository: Path, output: Path, revision: str = "HEAD") -> int:
    repository = repository.resolve()
    output = output.resolve()
    files = git_visible_files(repository, revision)
    if not files:
        raise RuntimeError("G5 source archive would be empty")
    archived = subprocess.run(
        ["git", "archive", "--format=tar", revision],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    output.parent.mkdir(parents=True, exist_ok=True)
    allowed = set(files)
    with (
        tarfile.open(fileobj=io.BytesIO(archived), mode="r:") as source_archive,
        tarfile.open(output, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        for member in source_archive:
            relative = member.name.rstrip("/")
            if relative not in allowed:
                continue
            member.name = f"./{relative}"
            member.mtime = 0
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            payload = None
            if member.isfile():
                raw = git_blob(repository, revision, relative)
                member.size = len(raw)
                payload = io.BytesIO(raw)
            archive.addfile(member, payload)
    return len(files)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--revision",
        default="HEAD",
        help="exact committed tree to archive; dirty and untracked files are excluded",
    )
    args = parser.parse_args()
    count = build_archive(args.repository, args.output, args.revision)
    print(f"G5 source archive files: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
