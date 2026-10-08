"""The workspace backend: the one place in the scenario package that touches a disk.

Every test here is a way a real directory could stop being the state's equal.  Two properties
matter and they pull in opposite directions: the bytes on disk have to be exactly the bytes in
the state (or the Agent read a different world from the one it is scored on), and a path that
reaches this module must not be able to reach anything but the workspace root.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sandbox.scenarios.office_v2.models import WorkspaceFile
from sandbox.scenarios.office_v2.workspace_fs import (
    WorkspaceFileSystem,
    WorkspaceFileSystemError,
)

TIMESTAMP = datetime(2026, 9, 30, tzinfo=UTC)


def _file(path: str, content: str) -> WorkspaceFile:
    return WorkspaceFile(
        path=path,
        owner_id="user.maya.chen",
        content=content,
        media_type="text/markdown",
        created_at=TIMESTAMP,
        updated_at=TIMESTAMP,
    )


def test_materialize_writes_the_state_byte_for_byte(tmp_path: Path) -> None:
    """The file on disk is the string in the state, including line endings.

    Written as bytes rather than by `open(..., "w")`: text mode would translate newlines on a
    platform that uses another convention, and the file would then hold something the state
    does not -- which is exactly the disagreement the read path refuses.
    """

    content = "line one\nline two\n\nwith a gap\n"
    backend = WorkspaceFileSystem(tmp_path)
    backend.materialize([_file("/workspace/desk-0/notes.md", content)])

    written = (tmp_path / "desk-0" / "notes.md").read_bytes()
    assert written == content.encode("utf-8")
    assert backend.read("/workspace/desk-0/notes.md") == content


def test_non_ascii_survives_the_round_trip(tmp_path: Path) -> None:
    """An office document is not guaranteed to be ASCII, and a mis-decoded read would be silent."""

    content = "Résumé — 摘要 — ✅\n"
    backend = WorkspaceFileSystem(tmp_path)
    backend.materialize([_file("/workspace/summary.md", content)])

    assert backend.read("/workspace/summary.md") == content


def test_a_missing_file_is_an_error_and_not_an_empty_string(tmp_path: Path) -> None:
    backend = WorkspaceFileSystem(tmp_path)

    with pytest.raises(WorkspaceFileSystemError, match="absent from disk"):
        backend.read("/workspace/summary.md")


def test_sync_writes_what_changed_and_removes_what_left(tmp_path: Path) -> None:
    """`sync` is what makes the disk follow a commit, and a rollback, without being told which.

    The stale file matters as much as the changed one: a workspace that kept a file the state
    no longer has would let a later read return material from a world the Episode has left.
    """

    backend = WorkspaceFileSystem(tmp_path)
    backend.materialize(
        [_file("/workspace/a.md", "first"), _file("/workspace/b.md", "removed later")]
    )

    touched = backend.sync([_file("/workspace/a.md", "second")])

    assert touched == 2
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "second"
    assert not (tmp_path / "b.md").exists()


def test_sync_of_an_unchanged_workspace_writes_nothing(tmp_path: Path) -> None:
    """Called after every tool call, so a read must not rewrite the world it just read."""

    backend = WorkspaceFileSystem(tmp_path)
    files = [_file("/workspace/a.md", "same")]
    backend.materialize(files)
    before = (tmp_path / "a.md").stat().st_mtime_ns

    assert backend.sync(files) == 0
    assert (tmp_path / "a.md").stat().st_mtime_ns == before


def test_the_tree_digest_follows_the_content(tmp_path: Path) -> None:
    backend = WorkspaceFileSystem(tmp_path)
    files = [_file("/workspace/a.md", "one"), _file("/workspace/desk-0/b.md", "two")]
    backend.materialize(files)

    digest = backend.tree_digest()
    assert digest.startswith("sha256:")

    backend.sync([_file("/workspace/a.md", "one"), _file("/workspace/desk-0/b.md", "changed")])
    assert backend.tree_digest() != digest


def test_the_tree_digest_ignores_files_the_backend_did_not_materialize(tmp_path: Path) -> None:
    """A mount holds a run root and a journal beside the workspace.

    The digest covers what this Episode put there and nothing else, so a journal write cannot
    change what the Episode claims to have read.
    """

    backend = WorkspaceFileSystem(tmp_path)
    backend.materialize([_file("/workspace/a.md", "one")])
    digest = backend.tree_digest()

    (tmp_path / "campaign-journal.json").write_text("{}", encoding="utf-8")
    assert backend.tree_digest() == digest


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "/workspace",
        "/workspace/../outside.md",
        "/workspace/./inside.md",
        "/workspace/desk-0//double.md",
        "workspace/a.md",
        "/workspace/a/../b.md",
    ],
)
def test_a_path_that_is_not_a_canonical_workspace_path_is_refused(
    tmp_path: Path, path: str
) -> None:
    backend = WorkspaceFileSystem(tmp_path)

    with pytest.raises(WorkspaceFileSystemError, match="not a canonical workspace path"):
        backend.local_path(path)


def test_a_symlink_out_of_the_root_is_refused(tmp_path: Path) -> None:
    """The one check that cannot be done by looking at the string.

    `/workspace/escape/secret.md` is a perfectly canonical-looking path; it escapes because
    `escape` is a symlink.  Resolving the target is what catches it, and this is the test that
    says the resolution has to stay.
    """

    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "workspace"
    root.mkdir()
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - depends on the host's privileges
        # Creating a symlink on Windows needs a privilege that an ordinary account does not
        # have.  Skipped rather than worked around: the alternative is a test that passes on
        # the machine where it was written and nowhere else, or one that stops checking the
        # resolution this exists to check.
        pytest.skip(f"this host cannot create a symlink: {exc}")

    backend = WorkspaceFileSystem(root)
    with pytest.raises(WorkspaceFileSystemError, match="escapes the workspace root"):
        backend.local_path("/workspace/escape/secret.md")


def test_a_root_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceFileSystemError, match="not a directory"):
        WorkspaceFileSystem(tmp_path / "missing")
