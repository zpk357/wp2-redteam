"""Durable checkpoints for a running Agent Episode (`RA-CLOSE-01`).

Why this module exists
----------------------

Before it, an Episode lived only in memory and the caller wrote one file after every Episode had
already finished.  A `kill -9` therefore destroyed the whole Episode -- which is not a hypothetical:
it is how the range batch lost its eighth Episode to a machine reboot.

The contract enforced here:

* a checkpoint is written by **atomic replacement**, so a reader never sees half a file;
* every checkpoint carries a digest of its own contents, so a corrupted file is refused rather than
  interpreted;
* a checkpoint that does not describe the requested Episode is refused **with the field names that
  differ**, because "refused" on its own cannot be checked later;
* one run root has **one writer**, so two processes cannot interleave checkpoints;
* the world state and the tool results are written in the **same** checkpoint, so a restored Episode
  can never hold a tool result whose state change is missing;
* a settled Episode is **never re-run**: the sealed final trace comes back unchanged.

What recovery will not do: re-issue a call whose outcome is uncertain and which could have had an
effect.  Only the isolated synthetic Office world makes the alternative safe, and only for calls
whose outcome the restored state can prove absent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable_agent import EpisodeJournal, JournalPhase
from sandbox.scenarios.office_v2.world import EpisodeWorld

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sandbox.scenarios.error_capable import EpisodeScenarioPlan, MaterializedScenario
    from sandbox.scenarios.error_capable_identity import ModelIdentity
    from sandbox.scenarios.error_capable_registry import ErrorCapableFixture

JOURNAL_SUFFIX = ".journal.json"
LOCK_SUFFIX = ".writer"


class JournalError(RuntimeError):
    """Base class for a checkpoint that cannot be trusted."""


class JournalCorruptError(JournalError):
    """The file on disk does not match its own digest, or is not a journal at all."""


class JournalIdentityError(JournalError):
    """The journal is intact but describes a different Episode than the one requested."""

    def __init__(self, mismatches: tuple[str, ...]):
        self.mismatches = mismatches
        super().__init__(f"journal does not describe this Episode: {', '.join(mismatches)}")


class JournalConflictError(JournalError):
    """Another live writer owns this run root, or the Episode is already settled."""


class JournalStore:
    """One Episode's checkpoint file, its writer lock and its digest checks."""

    def __init__(self, root: Path | str, episode_id: str) -> None:
        self.root = Path(root)
        self.episode_id = episode_id
        self.path = self.root / f"{episode_id}{JOURNAL_SUFFIX}"
        self.lock_path = self.root / f"{episode_id}{LOCK_SUFFIX}"

    # -- writer lock ---------------------------------------------------------------------------

    def claim(self) -> None:
        """Take the single-writer lock, or refuse if a live process holds it."""

        self.root.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            holder = self.lock_path.read_text(encoding="utf-8").strip()
            if holder and holder != str(os.getpid()) and _process_is_alive(holder):
                raise JournalConflictError(
                    f"another live writer (pid {holder}) owns {self.path}"
                )
        self.lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    def release(self) -> None:
        if self.lock_path.exists():
            holder = self.lock_path.read_text(encoding="utf-8").strip()
            if holder == str(os.getpid()):
                self.lock_path.unlink()

    def writer_pid(self) -> str | None:
        if not self.lock_path.exists():
            return None
        return self.lock_path.read_text(encoding="utf-8").strip() or None

    # -- read / write --------------------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.exists()

    def write(self, journal: EpisodeJournal) -> Path:
        """Persist a sealed checkpoint by atomic replacement.

        An unsealed or internally inconsistent journal is refused before it can reach the disk: a
        checkpoint that lies about itself is worse than no checkpoint.
        """

        if journal.episode_id != self.episode_id:
            raise JournalIdentityError(("episode_id",))
        if not journal.digest_is_valid():
            raise JournalCorruptError("refusing to persist a journal whose digest does not match")
        payload = json.dumps(
            journal.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(handle, (payload + "\n").encode("utf-8"))
            os.fsync(handle)
        finally:
            os.close(handle)
        os.replace(temporary, self.path)
        # The rename is only durable once the directory entry is flushed.  Windows cannot open a
        # directory as a file descriptor, and the durability guarantee it offers differs anyway, so
        # this step is POSIX-only rather than faked on a platform that does not provide it.
        if os.name != "nt":
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return self.path

    def read(self) -> EpisodeJournal:
        if not self.path.exists():
            raise JournalError(f"no checkpoint at {self.path}")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise JournalCorruptError(f"{self.path} is not readable JSON: {error}") from error
        try:
            journal = EpisodeJournal.model_validate(raw)
        except Exception as error:  # noqa: BLE001 - a corrupt file is a report, not a crash
            raise JournalCorruptError(f"{self.path} is not a valid journal: {error}") from error
        if not journal.digest_is_valid():
            raise JournalCorruptError(f"{self.path} does not match its own digest")
        return journal

    def read_for(
        self,
        *,
        fixture: ErrorCapableFixture,
        plan: EpisodeScenarioPlan,
        material: MaterializedScenario,
        model_identity: ModelIdentity,
        adapter_version: str,
        actor_case: str,
        dropped_capabilities: tuple[str, ...],
        seed: int,
        max_tool_requests: int,
        max_continuations: int,
    ) -> EpisodeJournal:
        """Read the checkpoint and refuse it if it describes a different Episode."""

        journal = self.read()
        mismatches = journal.mismatches(
            fixture=fixture,
            plan=plan,
            material=material,
            model_identity=model_identity,
            adapter_version=adapter_version,
            actor_case=actor_case,
            dropped_capabilities=dropped_capabilities,
            seed=seed,
            max_tool_requests=max_tool_requests,
            max_continuations=max_continuations,
        )
        if mismatches:
            raise JournalIdentityError(mismatches)
        return journal

    def clear(self) -> None:
        for path in (self.path, self.path.with_name(self.path.name + ".tmp")):
            if path.exists():
                path.unlink()


def rebuild_world(journal: EpisodeJournal) -> EpisodeWorld:
    """Rehydrate the Episode world from a checkpoint, proving the transaction chain is whole.

    `EpisodeWorld.restore` rejects a duplicate transaction, a gap in the chain, and a final digest
    that disagrees with the state, so a checkpoint cannot smuggle in a state change that no
    transition accounts for.
    """

    return EpisodeWorld.restore(
        episode_id=journal.episode_id,
        base_world_digest=journal.base_world_digest,
        state=journal.world_state,
        history=journal.world_history,
        initial_state_digest=journal.initial_state_digest,
    )


def journal_digest_of(journal: EpisodeJournal) -> str:
    """The digest a reader should see for a checkpoint, independent of sealing order."""

    return sha256_digest(journal.digest_payload())


def summarise(journal: EpisodeJournal) -> dict[str, object]:
    """A compact, checkable description of a checkpoint -- used by reports and tests."""

    return {
        "episode_id": journal.episode_id,
        "phase": journal.phase.value,
        "issued": journal.issued,
        "turns": len(journal.turns),
        "steps": len(journal.steps),
        "pending": [item.call_id for item in journal.pending],
        "transactions": len(journal.world_history),
        "state_digest": journal.world_state.canonical_digest(),
        "settled": journal.settled,
        "unresolved": list(journal.unresolved),
        "journal_digest": journal.journal_digest,
    }


def phases_in_order() -> tuple[JournalPhase, ...]:
    return (
        JournalPhase.FIRST_INPUT,
        JournalPhase.AWAITING_MODEL,
        JournalPhase.MODEL_RETURNED,
        JournalPhase.BEFORE_CALL,
        JournalPhase.AFTER_CALL,
        JournalPhase.SETTLED,
    )


_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _process_is_alive(pid: str) -> bool:
    """Whether a process id is still running.

    Deliberately not `os.kill(pid, 0)`: on Windows every signal other than a console event is handed
    to `TerminateProcess`, so a liveness probe would kill the process it is asking about.  A lock
    check that terminates the lock holder is worse than having no lock at all.
    """

    if not pid.isdigit():
        return False
    number = int(pid)
    if os.name == "nt":
        return _windows_pid_is_alive(number)
    try:
        os.kill(number, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A process we may not signal still exists.
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


__all__ = [
    "JOURNAL_SUFFIX",
    "LOCK_SUFFIX",
    "JournalConflictError",
    "JournalCorruptError",
    "JournalError",
    "JournalIdentityError",
    "JournalStore",
    "journal_digest_of",
    "phases_in_order",
    "rebuild_world",
    "summarise",
]
