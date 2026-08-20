from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

from scripts.build_g5_source_archive import build_archive


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
    )


def test_source_archive_reads_exact_commit_not_dirty_or_untracked_files(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Archive Test")
    _git(repository, "config", "core.autocrlf", "false")
    tracked = repository / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8", newline="\n")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "fixture")
    tracked.write_text("dirty\n", encoding="utf-8", newline="\n")
    (repository / "untracked.txt").write_text(
        "untracked\n", encoding="utf-8", newline="\n"
    )

    first = tmp_path / "first.tar"
    second = tmp_path / "second.tar"
    assert build_archive(repository, first) == 1
    assert build_archive(repository, second) == 1

    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        assert archive.getnames() == ["./tracked.txt"]
        extracted = archive.extractfile("./tracked.txt")
        assert extracted is not None
        assert extracted.read() == b"committed\n"
