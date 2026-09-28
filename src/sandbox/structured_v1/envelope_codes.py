"""Failure codes of the execution envelope (draft §7, §11.2).

Two kinds live here and they must never be confused:

* **Refusals** (:data:`REFUSAL_CODES`) - a check failed and the episode is aborted. The
  container records the code and **must not** fall back to the old envelope's execution
  path (``SOC-ENV-60``).
* **Outcomes** (:data:`OUTCOME_CODES`) - ``budget.exceeded`` ends an episode under its
  budget and ``usage.missing`` marks a cost report as incomplete. Neither may be turned
  into a violation, a pass, or an unmarked omission.

:data:`CODE_PHASE` names the implementation phase (TASK §14) that introduces the check
able to raise each refusal, so no code is declared without an owner. The names must match
the draft's §7 table exactly; ``tests/unit/test_structured_envelope.py`` reads the draft
and fails when the register and this enum disagree.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class FailureCode(StrEnum):
    """Every code the envelope can record, as a stable string."""

    # envelope.* - identity, payloads, original state
    IDENTITY_MISMATCH = "envelope.identity_mismatch"
    OBJECTIVE_CATALOGUE_PRESENT = "envelope.objective_catalogue_present"
    CONTEXT_MISMATCH = "envelope.context_mismatch"
    FIXTURE_MISMATCH = "envelope.fixture_mismatch"
    LOCATOR_OUTSIDE_STORE = "envelope.locator_outside_store"
    PAYLOAD_MISSING = "envelope.payload_missing"
    BASE_WORLD_MISMATCH = "envelope.base_world_mismatch"
    INITIAL_STATE_NOT_REPRODUCIBLE = "envelope.initial_state_not_reproducible"

    # material.* - the candidate's rendered material and its placement
    COVERAGE_MISMATCH = "material.coverage_mismatch"
    WRITE_CONFLICT = "material.write_conflict"
    FIELD_PATH_MISMATCH = "material.field_path_mismatch"

    # state.* / resume.* - the episode's transaction chain
    CHAIN_BROKEN = "state.chain_broken"
    RESUME_INCONSISTENT = "resume.inconsistent"

    # evidence.* / exposure.* - commit-time evidence and read exposure
    UNBOUND = "evidence.unbound"
    KEY_CONFLICT = "evidence.key_conflict"
    MISSING = "evidence.missing"
    COMMIT_UNKNOWN = "evidence.commit_unknown"
    INITIALIZATION_CITED_AS_DELIVERY = "evidence.initialization_cited_as_delivery"
    EXPOSURE_UNBOUND = "exposure.unbound"

    # tools.* - the executed tooling's two identities
    CATALOGUE_MISMATCH = "tools.catalogue_mismatch"
    IMAGE_MISMATCH = "tools.image_mismatch"

    # outcomes (not refusals)
    BUDGET_EXCEEDED = "budget.exceeded"
    USAGE_MISSING = "usage.missing"


REFUSAL_CODES: frozenset[FailureCode] = frozenset(
    {
        FailureCode.IDENTITY_MISMATCH,
        FailureCode.OBJECTIVE_CATALOGUE_PRESENT,
        FailureCode.CONTEXT_MISMATCH,
        FailureCode.FIXTURE_MISMATCH,
        FailureCode.LOCATOR_OUTSIDE_STORE,
        FailureCode.PAYLOAD_MISSING,
        FailureCode.BASE_WORLD_MISMATCH,
        FailureCode.INITIAL_STATE_NOT_REPRODUCIBLE,
        FailureCode.COVERAGE_MISMATCH,
        FailureCode.WRITE_CONFLICT,
        FailureCode.FIELD_PATH_MISMATCH,
        FailureCode.CHAIN_BROKEN,
        FailureCode.RESUME_INCONSISTENT,
        FailureCode.UNBOUND,
        FailureCode.KEY_CONFLICT,
        FailureCode.MISSING,
        FailureCode.COMMIT_UNKNOWN,
        FailureCode.INITIALIZATION_CITED_AS_DELIVERY,
        FailureCode.EXPOSURE_UNBOUND,
        FailureCode.CATALOGUE_MISMATCH,
        FailureCode.IMAGE_MISMATCH,
    }
)

OUTCOME_CODES: frozenset[FailureCode] = frozenset(
    {FailureCode.BUDGET_EXCEEDED, FailureCode.USAGE_MISSING}
)

CODE_PHASE: Mapping[FailureCode, str] = MappingProxyType(
    {
        FailureCode.IDENTITY_MISMATCH: "P1",
        FailureCode.CONTEXT_MISMATCH: "P1",
        FailureCode.FIXTURE_MISMATCH: "P1",
        FailureCode.OBJECTIVE_CATALOGUE_PRESENT: "P1",
        FailureCode.COVERAGE_MISMATCH: "P1",
        FailureCode.WRITE_CONFLICT: "P1",
        FailureCode.FIELD_PATH_MISMATCH: "P1",
        FailureCode.BASE_WORLD_MISMATCH: "P1",
        FailureCode.INITIAL_STATE_NOT_REPRODUCIBLE: "P1",
        FailureCode.CHAIN_BROKEN: "P3",
        FailureCode.LOCATOR_OUTSIDE_STORE: "P2",
        FailureCode.PAYLOAD_MISSING: "P2",
        FailureCode.CATALOGUE_MISMATCH: "P2",
        FailureCode.IMAGE_MISMATCH: "P6",
        FailureCode.UNBOUND: "P3",
        FailureCode.KEY_CONFLICT: "P3",
        FailureCode.INITIALIZATION_CITED_AS_DELIVERY: "P3",
        FailureCode.EXPOSURE_UNBOUND: "P4",
        FailureCode.MISSING: "P5",
        FailureCode.COMMIT_UNKNOWN: "P5",
        FailureCode.RESUME_INCONSISTENT: "P5",
        FailureCode.BUDGET_EXCEEDED: "P5",
        FailureCode.USAGE_MISSING: "P5",
    }
)


def is_refusal(code: FailureCode) -> bool:
    """True when the code aborts an episode rather than marking an outcome."""

    return code in REFUSAL_CODES


class EnvelopeRefusal(RuntimeError):
    """A refusal that names its check. Callers abort; they never fall back to the old path.

    It lives beside the register because every module that checks part of the envelope
    raises it: the register is the one thing they all depend on.
    """

    def __init__(self, code: FailureCode, detail: str) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail
