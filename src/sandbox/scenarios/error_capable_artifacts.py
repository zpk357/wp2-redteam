"""Atomic, self-checking campaign artifacts alongside existing Episode journals."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sandbox.content_digests import decimalize_floats
from sandbox.replay.digests import sha256_digest
from sandbox.scenarios.error_capable_journal import JournalCorruptError, JournalIdentityError


def write_artifact(path: Path, payload: dict[str, Any]) -> None:
    """Write an artifact whose digest can be recomputed from the file that holds it.

    Canonical JSON v1 refuses a fractional float, and the campaign legitimately measures them
    (selector latency).  The conversion happens *before* both the digest and the write, so the
    stored bytes and the digest describe the same object; converting only for the digest would
    produce a file that fails its own verification.
    """

    payload = decimalize_floats(payload, label="campaign artifact")  # type: ignore[assignment]
    envelope = {"payload": payload, "digest": sha256_digest(payload)}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(envelope, handle, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def read_artifact(path: Path, *, identity: str) -> dict[str, Any]:
    """The artifact, provided it belongs to this identity.

    There is deliberately no way to read one that belongs to a different identity.  A run that has
    changed its selector is a different treatment, and this is where that is noticed.

    A `continuation` parameter sat here briefly and was removed.  It let the artifacts of a named
    earlier run be adopted, which sounds like continuing an arm across a deliberate change -- and
    would have been the first half of it.  What it could not do is skip the Episodes that earlier
    run had already settled: the Campaign has no such path, so a "continuation" re-executed every
    Episode from the beginning while adopting the frozen decisions of another identity.  That is
    the worst of both -- the cost of a full run, and a record made of two runs that nothing
    distinguishes -- so the parameter is gone rather than left for the next person to reach for.
    Continuing an arm needs the skip path first; until that exists, a fresh run is the honest way
    to run a changed harness.
    """

    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        payload = envelope["payload"]
        if envelope["digest"] != sha256_digest(payload):
            raise ValueError("artifact digest mismatch")
    except (ValueError, KeyError, TypeError) as error:
        raise JournalCorruptError(f"invalid campaign artifact {path}: {error}") from error
    if payload.get("identity") != identity:
        raise JournalIdentityError(("campaign_identity",))
    return payload
