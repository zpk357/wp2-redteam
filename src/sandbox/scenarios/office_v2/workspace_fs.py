"""The workspace domain's bytes, on a real filesystem.

The world state stays the record of what the workspace *contains*: paths, owners, versions,
timestamps, and the content the score is computed from.  This module is where the bytes
actually live, and it is the only component in the scenario package that touches a disk.

Why it exists
-------------
An Episode's material used to exist only as `WorkspaceFile.content` strings inside a pydantic
object.  An Agent exploring that workspace was walking a tuple, not a directory tree: `read_file`
looked a path up in a collection and returned a field.  The scenario generation, the reading and
the scoring were all consistent -- and all of it was in memory, so "the Agent read the file"
was a statement about a data structure.

Attaching a real root changes only where the bytes live.  The generation is still a pure
function of the plan; it now lands on a disk.  The tools still go through the same policy, the
same transaction and the same evidence ledger; they now open a file.  What a reader gains is
that `ls` on the container shows the workspace the Agent worked in, and that the file the Agent
read is the file the run produced.

What it deliberately does not do
--------------------------------
* It does not enumerate.  `list_directory` and `search_files` stay driven by `visible_resources`,
  because a directory walk would show every file under the root and the observation policy is
  what keeps an actor to its own material.  A real `ls` would be a disclosure, not realism.
* It does not decide content.  Disk and state must agree; a disagreement is an integrity fault
  that fails the Episode rather than a source of truth that quietly wins.
* It does not go outside the root.  Paths are canonical workspace paths, and the resolved target
  is checked against the root so a symlink cannot turn a read into an escape.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from sandbox.scenarios.office_v2.models import WorkspaceFile

#: The prefix every workspace resource path carries.  It maps onto the root directory.
WORKSPACE_PREFIX = "/workspace"


class WorkspaceFileSystemError(Exception):
    """The disk copy cannot be used: a path escaped, a file is missing, or bytes disagree.

    Deliberately **not** a `RuntimeError`.  The tool runtime treats a `RuntimeError` from a
    handler as a transaction conflict and reports a failed call; a disagreement between the
    bytes the Agent read and the state the Episode is scored on is not a failed call, it is a
    broken experiment, and it must not be reportable as anything milder.
    """


class WorkspaceFileSystem:
    """A real directory holding the workspace domain's files.

    One instance serves one Episode.  `materialize` writes the whole workspace, `sync` brings
    the disk back into line with state after a tool call, and `read` is what a read tool calls.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise WorkspaceFileSystemError(f"workspace root is not a directory: {self.root}")
        self._materialized: frozenset[str] = frozenset()

    # -- path mapping ---------------------------------------------------------------------
    def local_path(self, path: str) -> Path:
        """Map a `/workspace/...` resource path onto a path under the root.

        The checks are the same ones the tool arguments already passed, repeated here because
        this is the last place before a real `open`: a path that reaches a filesystem without
        them is a path that can reach a filesystem outside the root.
        """

        pure = PurePosixPath(path)
        if (
            not path.startswith(WORKSPACE_PREFIX + "/")
            or pure.as_posix() != path
            or ".." in pure.parts
            or "." in pure.parts
        ):
            raise WorkspaceFileSystemError(f"not a canonical workspace path: {path!r}")
        relative = PurePosixPath(*pure.parts[2:])
        candidate = self.root / relative.as_posix()
        # `realpath` resolves whatever of the path already exists, so a symlinked directory
        # inside the root is caught here rather than by a read that follows it.
        resolved = Path(os.path.realpath(candidate))
        if resolved != self.root and self.root not in resolved.parents:
            raise WorkspaceFileSystemError(f"path escapes the workspace root: {path!r}")
        return candidate

    # -- materialisation ------------------------------------------------------------------
    def materialize(self, files: Iterable[WorkspaceFile]) -> int:
        """Write every file of a freshly built world.  Returns the number of files written."""

        wanted = {file.path: file for file in files}
        for path, file in sorted(wanted.items()):
            self.write(path, file.content)
        self._materialized = frozenset(wanted)
        return len(wanted)

    def sync(self, files: Iterable[WorkspaceFile]) -> int:
        """Make the disk equal to the state.  Returns the number of paths touched.

        Called after every tool invocation rather than only after a write: the state is
        restored on a rollback, so the same call that repairs a state also has to repair a
        disk, and a sync that only ran on the success path would leave the two apart exactly
        when something went wrong.
        """

        wanted = {file.path: file for file in files}
        touched = 0
        for path, file in sorted(wanted.items()):
            if self._read_or_none(path) != file.content:
                self.write(path, file.content)
                touched += 1
        for stale in sorted(self._materialized - set(wanted)):
            self.remove(stale)
            touched += 1
        self._materialized = frozenset(wanted)
        return touched

    # -- the three operations a tool needs ------------------------------------------------
    def read(self, path: str) -> str:
        content = self._read_or_none(path)
        if content is None:
            raise WorkspaceFileSystemError(f"workspace file is absent from disk: {path!r}")
        return content

    def write(self, path: str, content: str) -> None:
        target = self.local_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Bytes rather than text: `open(..., "w")` would translate newlines on the way out and
        # the file would no longer be the string the state holds.
        target.write_bytes(content.encode("utf-8"))

    def remove(self, path: str) -> None:
        target = self.local_path(path)
        target.unlink(missing_ok=True)

    def exists(self, path: str) -> bool:
        return self._read_or_none(path) is not None

    # -- evidence -------------------------------------------------------------------------
    def tree_digest(self) -> str:
        """A digest of the materialized tree, by resource path and bytes.

        Computed over the paths this object materialized, not over a directory walk: the
        mount can hold a run root and a journal beside the workspace, and a digest that swept
        them up would change for reasons that have nothing to do with the Agent.

        Host runs and container runs produce the same value, which is what makes "the disk
        copy did not change the Episode" checkable rather than asserted.
        """

        digest = hashlib.sha256()
        for path in sorted(self._materialized):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(self.read(path).encode("utf-8"))
            digest.update(b"\0")
        return "sha256:" + digest.hexdigest()

    # -- internals ------------------------------------------------------------------------
    def _read_or_none(self, path: str) -> str | None:
        try:
            return self.local_path(path).read_bytes().decode("utf-8")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError as exc:
            raise WorkspaceFileSystemError(
                f"workspace file is not valid UTF-8: {path!r}"
            ) from exc


__all__ = [
    "WORKSPACE_PREFIX",
    "WorkspaceFileSystem",
    "WorkspaceFileSystemError",
]
