"""Deterministic matching of registered private values (SOC-SAF-09, SOC-SAF-12).

The safety contract fixes the matching rules for the first version: exact values
of the registered high-entropy units, with Unicode NFC and CRLF/LF normalisation
as the only permitted normalisation, plus original-file byte equality.  The same
rule is used here for two boundaries:

* a candidate must not carry a registered private value into the material; and
* the public projection must not carry one into a provider request.

Detection never returns the matched value itself, only the registered value's
identity, so a rejection record cannot become a second leak.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

from sandbox.replay.digests import sha256_digest


def normalize_match_text(text: str) -> str:
    """Apply only the normalisation allowed by the safety contract."""

    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def find_registered_value_leaks(
    texts: Iterable[str],
    registered_values: tuple[str, ...],
) -> tuple[str, ...]:
    """Return the registered values that occurred in ``texts``.

    The return value names the registered values, not their positions or the text
    that carried them.  Values are returned through a digest so that even the
    rejection detail does not restate the secret.
    """

    normalized = tuple(
        sorted({normalize_match_text(value) for value in registered_values if value})
    )
    if not normalized:
        return ()
    hits: set[str] = set()
    for text in texts:
        haystack = normalize_match_text(text)
        for value in normalized:
            if value in haystack:
                hits.add(value)
    return tuple(value_digest for value_digest in sorted(_digest(value) for value in hits))


def registered_units_present(
    texts: Iterable[str],
    units: Iterable[tuple[str, str]],
) -> tuple[str, ...]:
    """The ids of the registered units whose **exact value** occurs in ``texts``.

    This is the commit-time side of the same rule ``find_registered_value_leaks``
    enforces on the input side (`SOC-SAF-09`): full-unit matching with NFC and
    CRLF/LF normalisation as the only permitted normalisation.  Only unit ids are
    returned, so a recorded effect never restates the restricted value.
    """

    materialized = tuple(texts)
    hits: list[str] = []
    for unit_id, value in units:
        if not value:
            continue
        if find_registered_value_leaks(materialized, (value,)):
            hits.append(unit_id)
    return tuple(sorted(dict.fromkeys(hits)))


def registered_file_digest(content: str) -> str:
    """The digest a delivered payload must equal to be that registered original file.

    ``SOC-SAF-09`` allows the original file channel to be proven by byte equality;
    the digest is taken over the normalised text so a line-ending difference between
    the frozen asset and the delivered copy does not create a false negative.
    """

    return sha256_digest({"registered_file_content": normalize_match_text(content)})


def _digest(value: str) -> str:
    return sha256_digest({"registered_value": value})
